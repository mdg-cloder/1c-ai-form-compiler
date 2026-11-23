"""Bounded subprocess wrapper for the verified saby v8unpack CLI.

The wrapper supports container no-op verification only. It does not claim
semantic Form.bin editing or 1C runtime compatibility.
"""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import os
from pathlib import Path, PurePosixPath
import shutil
import subprocess
import tempfile
import threading
import time
import uuid
import ctypes
from ctypes import wintypes
from typing import Callable, Sequence


@dataclass(frozen=True, slots=True)
class BackendConfig:
    executable: Path
    expected_version: str = "1.2.13"
    metadata_file: Path | None = None
    timeout_seconds: float = 60.0
    max_output_bytes: int = 64 * 1024
    max_payload_files: int = 10_000
    max_payload_bytes: int = 64 * 1024 * 1024
    max_container_bytes: int = 256 * 1024 * 1024
    executable_args: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class ProcessResult:
    argv: tuple[str, ...]
    returncode: int
    stdout: bytes
    stderr: bytes
    stdout_total_bytes: int
    stderr_total_bytes: int
    elapsed_seconds: float

    @property
    def stdout_truncated(self) -> bool:
        return self.stdout_total_bytes > len(self.stdout)

    @property
    def stderr_truncated(self) -> bool:
        return self.stderr_total_bytes > len(self.stderr)


@dataclass(frozen=True, slots=True)
class CommandSummary:
    step: str
    returncode: int
    elapsed_seconds: float
    stdout_bytes: int
    stderr_bytes: int
    stdout_truncated: bool
    stderr_truncated: bool
    stdout_sha256: str
    stderr_sha256: str


@dataclass(frozen=True, slots=True)
class NoOpRoundTripResult:
    tool_version: str
    executable_sha256: str
    input_path: Path
    input_sha256: str
    artifact_path: Path
    artifact_sha256: str
    decoded_file_count: int
    decoded_payload_bytes: int
    decoded_tree_sha256: str
    commands: tuple[CommandSummary, ...]


class V8UnpackError(RuntimeError):
    def __init__(self, code: str, message: str, *, step: str | None = None,
                 process_result: ProcessResult | None = None) -> None:
        super().__init__(message)
        self.code = code
        self.step = step
        self.process_result = process_result


class _BoundedCapture:
    def __init__(self, pipe, capacity: int) -> None:
        self.pipe = pipe
        self.capacity = capacity
        self.data = bytearray()
        self.total = 0

    def drain(self) -> None:
        try:
            while True:
                chunk = self.pipe.read(64 * 1024)
                if not chunk:
                    break
                self.total += len(chunk)
                remaining = self.capacity - len(self.data)
                if remaining > 0:
                    self.data.extend(chunk[:remaining])
        finally:
            try:
                self.pipe.close()
            except OSError:
                pass


if os.name == "nt":
    class _IoCounters(ctypes.Structure):
        _fields_ = [(name, ctypes.c_ulonglong) for name in (
            "ReadOperationCount", "WriteOperationCount", "OtherOperationCount",
            "ReadTransferCount", "WriteTransferCount", "OtherTransferCount",
        )]

    class _BasicLimitInformation(ctypes.Structure):
        _fields_ = [
            ("PerProcessUserTimeLimit", ctypes.c_longlong),
            ("PerJobUserTimeLimit", ctypes.c_longlong),
            ("LimitFlags", wintypes.DWORD),
            ("MinimumWorkingSetSize", ctypes.c_size_t),
            ("MaximumWorkingSetSize", ctypes.c_size_t),
            ("ActiveProcessLimit", wintypes.DWORD),
            ("Affinity", ctypes.c_size_t),
            ("PriorityClass", wintypes.DWORD),
            ("SchedulingClass", wintypes.DWORD),
        ]

    class _ExtendedLimitInformation(ctypes.Structure):
        _fields_ = [
            ("BasicLimitInformation", _BasicLimitInformation),
            ("IoInfo", _IoCounters),
            ("ProcessMemoryLimit", ctypes.c_size_t),
            ("JobMemoryLimit", ctypes.c_size_t),
            ("PeakProcessMemoryUsed", ctypes.c_size_t),
            ("PeakJobMemoryUsed", ctypes.c_size_t),
        ]

    class _BasicAccountingInformation(ctypes.Structure):
        _fields_ = [
            ("TotalUserTime", ctypes.c_longlong),
            ("TotalKernelTime", ctypes.c_longlong),
            ("ThisPeriodTotalUserTime", ctypes.c_longlong),
            ("ThisPeriodTotalKernelTime", ctypes.c_longlong),
            ("TotalPageFaultCount", wintypes.DWORD),
            ("TotalProcesses", wintypes.DWORD),
            ("ActiveProcesses", wintypes.DWORD),
            ("TotalTerminatedProcesses", wintypes.DWORD),
        ]

    class _WindowsKillJob:
        """Job Object that owns a suspended-start child tree until closed."""

        _KILL_ON_JOB_CLOSE = 0x00002000
        _JOB_OBJECT_EXTENDED_LIMIT_INFORMATION = 9
        _CREATE_SUSPENDED = 0x00000004

        def __init__(self) -> None:
            self._kernel = ctypes.WinDLL("kernel32", use_last_error=True)
            self._kernel.CreateJobObjectW.argtypes = [ctypes.c_void_p, wintypes.LPCWSTR]
            self._kernel.CreateJobObjectW.restype = wintypes.HANDLE
            self._kernel.SetInformationJobObject.argtypes = [
                wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD,
            ]
            self._kernel.SetInformationJobObject.restype = wintypes.BOOL
            self._kernel.AssignProcessToJobObject.argtypes = [wintypes.HANDLE, wintypes.HANDLE]
            self._kernel.AssignProcessToJobObject.restype = wintypes.BOOL
            self._kernel.TerminateJobObject.argtypes = [wintypes.HANDLE, wintypes.UINT]
            self._kernel.TerminateJobObject.restype = wintypes.BOOL
            self._kernel.QueryInformationJobObject.argtypes = [
                wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p,
                wintypes.DWORD, ctypes.POINTER(wintypes.DWORD),
            ]
            self._kernel.QueryInformationJobObject.restype = wintypes.BOOL
            self._kernel.CloseHandle.argtypes = [wintypes.HANDLE]
            self._kernel.CloseHandle.restype = wintypes.BOOL
            handle = self._kernel.CreateJobObjectW(None, None)
            if not handle:
                raise OSError(ctypes.get_last_error(), "CreateJobObjectW failed")
            self._handle = handle
            self.assigned = False
            limits = _ExtendedLimitInformation()
            limits.BasicLimitInformation.LimitFlags = self._KILL_ON_JOB_CLOSE
            if not self._kernel.SetInformationJobObject(
                    self._handle, self._JOB_OBJECT_EXTENDED_LIMIT_INFORMATION,
                    ctypes.byref(limits), ctypes.sizeof(limits)):
                error = ctypes.get_last_error()
                self.close()
                raise OSError(error, "SetInformationJobObject failed")

        @property
        def creation_flag(self) -> int:
            return self._CREATE_SUSPENDED

        @staticmethod
        def _raw_handle(handle) -> int:
            return int(handle) if not isinstance(handle, int) else handle

        def assign_and_resume(self, process: subprocess.Popen[bytes]) -> None:
            process_handle = self._raw_handle(process._handle)
            if not self._kernel.AssignProcessToJobObject(self._handle, process_handle):
                raise OSError(ctypes.get_last_error(), "AssignProcessToJobObject failed")
            self.assigned = True
            # Popen starts the process suspended. Locate its one initial thread,
            # assign the process to the kill-on-close job, then resume it. This
            # closes the race where it could spawn an unowned child before job assignment.
            self._kernel.CreateToolhelp32Snapshot.argtypes = [wintypes.DWORD, wintypes.DWORD]
            self._kernel.CreateToolhelp32Snapshot.restype = wintypes.HANDLE
            self._kernel.Thread32First.argtypes = [wintypes.HANDLE, ctypes.c_void_p]
            self._kernel.Thread32First.restype = wintypes.BOOL
            self._kernel.Thread32Next.argtypes = [wintypes.HANDLE, ctypes.c_void_p]
            self._kernel.Thread32Next.restype = wintypes.BOOL
            self._kernel.OpenThread.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
            self._kernel.OpenThread.restype = wintypes.HANDLE
            self._kernel.ResumeThread.argtypes = [wintypes.HANDLE]
            self._kernel.ResumeThread.restype = wintypes.DWORD

            class ThreadEntry32(ctypes.Structure):
                _fields_ = [
                    ("dwSize", wintypes.DWORD), ("cntUsage", wintypes.DWORD),
                    ("th32ThreadID", wintypes.DWORD), ("th32OwnerProcessID", wintypes.DWORD),
                    ("tpBasePri", wintypes.LONG), ("tpDeltaPri", wintypes.LONG),
                    ("dwFlags", wintypes.DWORD),
                ]

            snapshot = self._kernel.CreateToolhelp32Snapshot(0x00000004, 0)
            invalid = ctypes.c_void_p(-1).value
            snapshot_value = self._raw_handle(snapshot) if snapshot else 0
            if not snapshot or snapshot_value in {invalid, -1}:
                raise OSError(ctypes.get_last_error(), "CreateToolhelp32Snapshot failed")
            thread_handle = None
            try:
                entry = ThreadEntry32()
                entry.dwSize = ctypes.sizeof(entry)
                found = []
                more = self._kernel.Thread32First(snapshot, ctypes.byref(entry))
                while more:
                    if entry.th32OwnerProcessID == process.pid:
                        found.append(entry.th32ThreadID)
                    more = self._kernel.Thread32Next(snapshot, ctypes.byref(entry))
                if len(found) != 1:
                    raise OSError(f"expected one suspended initial thread, found {len(found)}")
                thread_handle = self._kernel.OpenThread(0x0002, False, found[0])
                if not thread_handle:
                    raise OSError(ctypes.get_last_error(), "OpenThread failed")
                previous_count = self._kernel.ResumeThread(thread_handle)
                if previous_count == 0xFFFFFFFF:
                    raise OSError(ctypes.get_last_error(), "ResumeThread failed")
            finally:
                if thread_handle:
                    self._kernel.CloseHandle(thread_handle)
                self._kernel.CloseHandle(snapshot)

        def active_process_count(self) -> int:
            information = _BasicAccountingInformation()
            returned = wintypes.DWORD()
            if not self._kernel.QueryInformationJobObject(
                    self._handle, 1, ctypes.byref(information), ctypes.sizeof(information),
                    ctypes.byref(returned)):
                raise OSError(ctypes.get_last_error(), "QueryInformationJobObject failed")
            return int(information.ActiveProcesses)

        def terminate_and_wait(self, timeout_seconds: float = 5.0, exit_code: int = 1) -> None:
            if not self._handle:
                return
            active = self.active_process_count()
            if active == 0:
                return
            if not self._kernel.TerminateJobObject(self._handle, exit_code):
                error = ctypes.get_last_error()
                if self.active_process_count() == 0:
                    return
                raise OSError(error, "TerminateJobObject failed while job still had active processes")
            deadline = time.monotonic() + timeout_seconds
            while True:
                active = self.active_process_count()
                if active == 0:
                    return
                if time.monotonic() >= deadline:
                    raise TimeoutError(f"{active} process(es) remained active in owned Job Object")
                time.sleep(0.01)

        def close(self) -> None:
            if getattr(self, "_handle", None):
                handle = self._handle
                if not self._kernel.CloseHandle(handle):
                    raise OSError(ctypes.get_last_error(), "CloseHandle(Job Object) failed")
                self._handle = None

else:
    _WindowsKillJob = None


def _kill_process_tree(process: subprocess.Popen[bytes], job=None) -> None:
    """Terminate only the owned process tree, then reap the direct child."""
    cleanup_error = None
    if job is not None and getattr(job, "assigned", True):
        try:
            job.terminate_and_wait()
        except (OSError, TimeoutError) as exc:
            cleanup_error = exc
            # Closing a KILL_ON_JOB_CLOSE job is the fallback if explicit job
            # termination is denied. The direct child is also killed below;
            # without an ActiveProcesses==0 confirmation this still fails closed.
            try:
                job.close()
            except OSError as close_exc:
                cleanup_error = close_exc
    elif job is not None:
        # Assignment denial happened while the child was still suspended; the
        # empty job needs no termination call. Closing it and killing the direct
        # suspended Popen child is sufficient and does not wait for a timer.
        try:
            job.close()
        except OSError as close_exc:
            cleanup_error = close_exc
    if process.poll() is None:
        # Windows processes are started suspended and assigned to a Job Object
        # before resume. Reaching this fallback means no descendant could have
        # run before the failed ownership setup; terminate only that suspended
        # direct child.
        try:
            process.kill()
        except OSError:
            pass
    try:
        process.wait(timeout=5.0)
    except subprocess.TimeoutExpired:
        try:
            process.kill()
        finally:
            process.wait(timeout=2.0)
    if cleanup_error is not None:
        raise V8UnpackError("E_V8_PROCESS_TREE_CLEANUP",
                            f"could not confirm owned process-tree termination: {cleanup_error}") from cleanup_error


def _close_process_pipes(process: subprocess.Popen[bytes]) -> None:
    for stream in (process.stdin, process.stdout, process.stderr):
        if stream is not None and not stream.closed:
            try:
                stream.close()
            except OSError:
                pass


def run_bounded_process(
    argv: Sequence[str],
    *,
    timeout_seconds: float,
    max_output_bytes: int,
) -> ProcessResult:
    """Run argv without a shell, drain pipes with bounded retained output."""
    if (not isinstance(argv, (tuple, list)) or not argv
            or not all(isinstance(part, str) and "\x00" not in part for part in argv)):
        raise V8UnpackError("E_V8_ARGUMENTS_INVALID", "argv must be a nonempty sequence of NUL-free strings")
    if (isinstance(timeout_seconds, bool) or not isinstance(timeout_seconds, (int, float))
            or timeout_seconds <= 0 or timeout_seconds > 900):
        raise V8UnpackError("E_V8_ARGUMENTS_INVALID", "timeout must be in (0, 900] seconds")
    if type(max_output_bytes) is not int or not 0 <= max_output_bytes <= 1024 * 1024:
        raise V8UnpackError("E_V8_ARGUMENTS_INVALID", "max_output_bytes must be between 0 and 1 MiB")

    started = time.monotonic()
    job = None
    process = None
    try:
        creationflags = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
        if os.name == "nt":
            job = _WindowsKillJob()
            creationflags |= job.creation_flag
        process = subprocess.Popen(
            list(argv),
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            shell=False,
            close_fds=True,
            creationflags=creationflags,
        )
        if job is not None:
            job.assign_and_resume(process)
    except FileNotFoundError as exc:
        if job is not None:
            job.close()
        raise V8UnpackError("E_V8_EXECUTABLE_MISSING", f"executable not found: {argv[0]}") from exc
    except OSError as exc:
        try:
            if process is not None and process.poll() is None:
                _kill_process_tree(process, job)
        finally:
            if job is not None:
                job.close()
            if process is not None:
                _close_process_pipes(process)
        raise V8UnpackError("E_V8_PROCESS_START", f"could not start process: {exc}") from exc
    except Exception as exc:
        try:
            if process is not None and process.poll() is None:
                _kill_process_tree(process, job)
        finally:
            if job is not None:
                job.close()
            if process is not None:
                _close_process_pipes(process)
        raise V8UnpackError("E_V8_PROCESS_START", f"could not establish owned process tree: {exc}") from exc

    assert process.stdout is not None and process.stderr is not None
    stdout_capture = _BoundedCapture(process.stdout, max_output_bytes)
    stderr_capture = _BoundedCapture(process.stderr, max_output_bytes)
    stdout_thread = threading.Thread(target=stdout_capture.drain, name="v8unpack-stdout", daemon=True)
    stderr_thread = threading.Thread(target=stderr_capture.drain, name="v8unpack-stderr", daemon=True)
    stdout_thread.start()
    stderr_thread.start()
    try:
        process.wait(timeout=float(timeout_seconds))
    except subprocess.TimeoutExpired as exc:
        cleanup_error = None
        try:
            _kill_process_tree(process, job)
        except Exception as cleanup_exc:
            cleanup_error = cleanup_exc
        finally:
            if job is not None:
                try:
                    job.close()
                except Exception as close_exc:
                    cleanup_error = close_exc
                else:
                    job = None
        stdout_thread.join(timeout=5.0)
        stderr_thread.join(timeout=5.0)
        if stdout_thread.is_alive() or stderr_thread.is_alive():
            _close_process_pipes(process)
            stdout_thread.join(timeout=1.0)
            stderr_thread.join(timeout=1.0)
        result = ProcessResult(
            tuple(argv), process.returncode if process.returncode is not None else -1,
            bytes(stdout_capture.data), bytes(stderr_capture.data),
            stdout_capture.total, stderr_capture.total, time.monotonic() - started,
        )
        if cleanup_error is not None or stdout_thread.is_alive() or stderr_thread.is_alive():
            raise V8UnpackError(
                "E_V8_PROCESS_TREE_CLEANUP",
                f"could not confirm cleanup after timeout: {cleanup_error or 'output pipes remained open'}",
                step="process", process_result=result,
            ) from cleanup_error
        raise V8UnpackError("E_V8_TIMEOUT", f"process exceeded {timeout_seconds:g}s timeout",
                            step="process", process_result=result) from exc
    if job is not None:
        # The wrapper owns the whole tree, not only the main process. Closing
        # the kill-on-close job reaps any child a tool forgot to wait for.
        try:
            job.terminate_and_wait()
            job.close()
            job = None
        except (OSError, TimeoutError) as exc:
            try:
                _kill_process_tree(process, job)
            finally:
                if job is not None:
                    job.close()
                    job = None
            raise V8UnpackError("E_V8_PROCESS_TREE_CLEANUP",
                                f"could not confirm owned process tree after exit: {exc}") from exc
    stdout_thread.join(timeout=5.0)
    stderr_thread.join(timeout=5.0)
    if stdout_thread.is_alive() or stderr_thread.is_alive():
        _kill_process_tree(process, job)
        _close_process_pipes(process)
        raise V8UnpackError("E_V8_PIPE_HANG", "process exited but an owned output pipe did not close")
    return ProcessResult(
        tuple(argv), process.returncode if process.returncode is not None else -1,
        bytes(stdout_capture.data), bytes(stderr_capture.data),
        stdout_capture.total, stderr_capture.total, time.monotonic() - started,
    )


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _metadata_version(metadata_file: Path, package_name: str = "v8unpack") -> str:
    try:
        data = metadata_file.read_bytes()
    except OSError as exc:
        raise V8UnpackError("E_V8_VERSION_METADATA_MISSING", f"cannot read package metadata: {metadata_file}") from exc
    if len(data) > 256 * 1024:
        raise V8UnpackError("E_V8_VERSION_METADATA_INVALID", "package metadata exceeds 256 KiB")
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise V8UnpackError("E_V8_VERSION_METADATA_INVALID", "package metadata is not UTF-8") from exc
    fields: dict[str, str] = {}
    for line in text.splitlines():
        if ": " in line:
            key, value = line.split(": ", 1)
            if key in {"Name", "Version"}:
                fields[key] = value.strip()
    if fields.get("Name", "").lower().replace("_", "-") != package_name:
        raise V8UnpackError("E_V8_VERSION_METADATA_INVALID", "metadata belongs to another package")
    version = fields.get("Version")
    if not version:
        raise V8UnpackError("E_V8_VERSION_METADATA_INVALID", "package metadata has no Version field")
    return version


def _metadata_candidates(executable: Path) -> tuple[Path, ...]:
    scripts = executable.resolve().parent
    site_packages = scripts.parent / "Lib" / "site-packages"
    return tuple(sorted(site_packages.glob("v8unpack-*.dist-info/METADATA")))


def _within(path: Path, root: Path) -> bool:
    try:
        path.resolve().relative_to(root.resolve())
        return True
    except (OSError, ValueError):
        return False


def _payload_snapshot(root: Path, *, max_files: int, max_bytes: int) -> dict[str, bytes]:
    root = root.resolve()
    if not root.is_dir():
        raise V8UnpackError("E_V8_ARTIFACT_MISSING", f"unpack directory missing: {root}")
    files: dict[str, bytes] = {}
    total = 0
    try:
        paths = sorted(root.rglob("*"), key=lambda path: path.relative_to(root).as_posix())
        for path in paths:
            if path.is_symlink() or not _within(path, root):
                raise V8UnpackError("E_V8_PATH_INVALID", "unpacked tree contains a symlink or escaping path")
            if not path.is_file():
                continue
            relative = PurePosixPath(path.relative_to(root).as_posix())
            if relative.is_absolute() or ".." in relative.parts:
                raise V8UnpackError("E_V8_PATH_INVALID", "unpacked file path is not contained")
            if len(files) >= max_files:
                raise V8UnpackError("E_V8_PAYLOAD_LIMIT", "unpacked file count exceeds configured limit")
            size = path.stat().st_size
            total += size
            if total > max_bytes:
                raise V8UnpackError("E_V8_PAYLOAD_LIMIT", "unpacked payload exceeds configured byte limit")
            files[relative.as_posix()] = path.read_bytes()
    except V8UnpackError:
        raise
    except OSError as exc:
        raise V8UnpackError("E_V8_ARTIFACT_INVALID", f"cannot read unpacked payload: {exc}") from exc
    if not files:
        raise V8UnpackError("E_V8_ARTIFACT_MISSING", "unpack produced no payload files")
    return files


def _tree_digest(files: dict[str, bytes]) -> str:
    digest = hashlib.sha256()
    for name in sorted(files):
        path_bytes = name.encode("utf-8")
        data_digest = hashlib.sha256(files[name]).digest()
        digest.update(len(path_bytes).to_bytes(8, "big"))
        digest.update(path_bytes)
        digest.update(len(files[name]).to_bytes(8, "big"))
        digest.update(data_digest)
    return digest.hexdigest()


class V8Unpack:
    """Configured `v8unpack` executable and verified no-op round-trip route."""

    def __init__(self, config: BackendConfig,
                 process_runner: Callable[..., ProcessResult] = run_bounded_process) -> None:
        self.config = config
        self._process_runner = process_runner

    def package_version(self) -> str:
        executable = Path(self.config.executable)
        if not executable.is_file():
            raise V8UnpackError("E_V8_EXECUTABLE_MISSING", f"configured executable is missing: {executable}")
        metadata = Path(self.config.metadata_file) if self.config.metadata_file is not None else None
        if metadata is None:
            candidates = _metadata_candidates(executable)
            if len(candidates) != 1:
                raise V8UnpackError("E_V8_VERSION_METADATA_MISSING", "could not identify exactly one adjacent v8unpack METADATA file")
            metadata = candidates[0]
        version = _metadata_version(metadata)
        if version != self.config.expected_version:
            raise V8UnpackError("E_V8_VERSION_UNSUPPORTED", f"expected v8unpack {self.config.expected_version}, found {version}")
        return version

    def _command(self, step: str, args: Sequence[str]) -> CommandSummary:
        if not self.config.executable.is_file():
            raise V8UnpackError("E_V8_EXECUTABLE_MISSING", f"configured executable is missing: {self.config.executable}", step=step)
        argv = (str(self.config.executable), *self.config.executable_args, *args)
        result = self._process_runner(
            argv,
            timeout_seconds=self.config.timeout_seconds,
            max_output_bytes=self.config.max_output_bytes,
        )
        summary = CommandSummary(
            step=step,
            returncode=result.returncode,
            elapsed_seconds=result.elapsed_seconds,
            stdout_bytes=result.stdout_total_bytes,
            stderr_bytes=result.stderr_total_bytes,
            stdout_truncated=result.stdout_truncated,
            stderr_truncated=result.stderr_truncated,
            stdout_sha256=hashlib.sha256(result.stdout).hexdigest(),
            stderr_sha256=hashlib.sha256(result.stderr).hexdigest(),
        )
        if result.returncode != 0:
            raise V8UnpackError("E_V8_EXIT_CODE", f"{step} exited with code {result.returncode}",
                                step=step, process_result=result)
        return summary

    def roundtrip_noop(self, source_epf: Path, *, workspace_root: Path,
                       artifact_name: str = "NoOpRoundtrip.epf") -> NoOpRoundTripResult:
        version = self.package_version()
        workspace = Path(workspace_root).resolve()
        if not workspace.is_dir():
            raise V8UnpackError("E_V8_PATH_INVALID", "workspace_root must be an existing directory")
        source = Path(source_epf).resolve()
        if not source.is_file():
            raise V8UnpackError("E_V8_SOURCE_MISSING", f"input EPF is missing: {source}")
        if (not isinstance(artifact_name, str) or not artifact_name or artifact_name in {".", ".."}
                or Path(artifact_name).name != artifact_name or "/" in artifact_name or "\\" in artifact_name
                or Path(artifact_name).suffix.lower() != ".epf"):
            raise V8UnpackError("E_V8_PATH_INVALID", "artifact_name must be a simple .epf filename")

        temp_root = workspace / ".tmp" / "v8unpack-roundtrip"
        artifact_root = workspace / "v8unpack-noop"
        # Reject existing symlink parents before mkdir: checking only after
        # mkdir could already have created directories outside the workspace.
        if not _within(temp_root, workspace) or not _within(artifact_root, workspace):
            raise V8UnpackError("E_V8_PATH_INVALID", "managed work/artifact root resolves outside workspace")
        temp_root.mkdir(parents=True, exist_ok=True)
        artifact_root.mkdir(parents=True, exist_ok=True)
        if not _within(temp_root, workspace) or not _within(artifact_root, workspace):
            raise V8UnpackError("E_V8_PATH_INVALID", "managed work/artifact root escapes workspace")
        final_artifact = artifact_root / artifact_name
        if final_artifact.exists():
            raise V8UnpackError("E_V8_OUTPUT_EXISTS", f"refusing to overwrite existing artifact: {final_artifact}")

        source_before = _sha256_file(source)
        if source.stat().st_size > self.config.max_container_bytes:
            raise V8UnpackError("E_V8_PAYLOAD_LIMIT", "input EPF exceeds configured container-size limit")
        commands: list[CommandSummary] = []
        staged_artifact: Path | None = None
        try:
            with tempfile.TemporaryDirectory(prefix="roundtrip-", dir=temp_root) as temp_name:
                run_root = Path(temp_name).resolve()
                if not _within(run_root, temp_root):
                    raise V8UnpackError("E_V8_PATH_INVALID", "owned temporary directory escaped managed root")
                source_tree = run_root / "source-unpacked"
                packed_epf = run_root / "no-op.epf"
                decoded_tree = run_root / "re-unpacked"
                # v8unpack 1.2.13 parses -E as (input EPF, output directory),
                # then calls extract(in_filename, out_dir_name).
                commands.append(self._command("unpack_source", ("-E", str(source), str(source_tree))))
                original_files = _payload_snapshot(
                    source_tree,
                    max_files=self.config.max_payload_files,
                    max_bytes=self.config.max_payload_bytes,
                )
                commands.append(self._command("pack_noop", ("-B", str(source_tree), str(packed_epf))))
                if packed_epf.is_symlink() or not packed_epf.is_file() or packed_epf.stat().st_size == 0:
                    raise V8UnpackError("E_V8_ARTIFACT_MISSING", "pack command exited successfully but created no nonempty EPF", step="pack_noop")
                if packed_epf.stat().st_size > self.config.max_container_bytes:
                    raise V8UnpackError("E_V8_PAYLOAD_LIMIT", "packed EPF exceeds configured size limit")
                commands.append(self._command("unpack_packed", ("-E", str(packed_epf), str(decoded_tree))))
                decoded_files = _payload_snapshot(
                    decoded_tree,
                    max_files=self.config.max_payload_files,
                    max_bytes=self.config.max_payload_bytes,
                )
                if original_files.keys() != decoded_files.keys():
                    raise V8UnpackError("E_V8_PAYLOAD_MISMATCH", "no-op round-trip changed the decoded file set")
                changed = [name for name in sorted(original_files) if original_files[name] != decoded_files[name]]
                if changed:
                    raise V8UnpackError("E_V8_PAYLOAD_MISMATCH", f"no-op round-trip changed decoded payload files: {', '.join(changed[:8])}")
                source_after = _sha256_file(source)
                if source_after != source_before:
                    raise V8UnpackError("E_V8_SOURCE_CHANGED", "input EPF changed during read-only round-trip")
                staged_artifact = artifact_root / f".{artifact_name}.{uuid.uuid4().hex}.partial"
                shutil.copyfile(packed_epf, staged_artifact)
                if _sha256_file(staged_artifact) != _sha256_file(packed_epf):
                    raise V8UnpackError("E_V8_ARTIFACT_INVALID", "staged artifact digest differs from verified pack output")
                try:
                    os.rename(staged_artifact, final_artifact)
                except FileExistsError as exc:
                    raise V8UnpackError("E_V8_OUTPUT_EXISTS", "refusing to overwrite concurrently created artifact") from exc
                staged_artifact = None
        finally:
            if staged_artifact is not None:
                try:
                    staged_artifact.unlink(missing_ok=True)
                except OSError:
                    pass
        if _sha256_file(source) != source_before:
            try:
                final_artifact.unlink(missing_ok=True)
            finally:
                raise V8UnpackError("E_V8_SOURCE_CHANGED", "input EPF changed before round-trip completion")
        return NoOpRoundTripResult(
            tool_version=version,
            executable_sha256=_sha256_file(self.config.executable),
            input_path=source,
            input_sha256=source_before,
            artifact_path=final_artifact,
            artifact_sha256=_sha256_file(final_artifact),
            decoded_file_count=len(original_files),
            decoded_payload_bytes=sum(len(data) for data in original_files.values()),
            decoded_tree_sha256=_tree_digest(original_files),
            commands=tuple(commands),
        )
