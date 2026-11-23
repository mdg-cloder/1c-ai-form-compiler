from __future__ import annotations

import hashlib
import ctypes
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import textwrap
import time
import unittest
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import Mock, patch
import zipfile

from one_c_form_compiler.core.v8wrapper import (
    BackendConfig,
    V8Unpack,
    V8UnpackError,
    run_bounded_process,
)

from tests._support import SAMPLE


FAKE_TOOL = r'''import pathlib, sys, zipfile
args = sys.argv[1:]
mode = next((x.split("=", 1)[1] for x in args if x.startswith("--mode=")), "ok")
args = [x for x in args if not x.startswith("--mode=")]
if args[0] == "-E":
    source, destination = pathlib.Path(args[1]), pathlib.Path(args[2])
    destination.mkdir(parents=True)
    with zipfile.ZipFile(source) as zf:
        zf.extractall(destination)
elif args[0] == "-B":
    source, destination = pathlib.Path(args[1]), pathlib.Path(args[2])
    if mode == "pack-error":
        print("intentional pack failure", file=sys.stderr)
        raise SystemExit(7)
    if mode != "missing-output":
        with zipfile.ZipFile(destination, "w") as zf:
            for path in sorted(source.rglob("*")):
                if path.is_file():
                    zf.write(path, path.relative_to(source).as_posix())
else:
    raise SystemExit(9)
'''

TIMEOUT_CLEANUP_OBSERVATIONS: list[dict[str, object]] = []


def _windows_pid_is_alive(pid: int) -> bool:
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.OpenProcess.argtypes = [ctypes.c_uint32, ctypes.c_int, ctypes.c_uint32]
    kernel.OpenProcess.restype = ctypes.c_void_p
    kernel.WaitForSingleObject.argtypes = [ctypes.c_void_p, ctypes.c_uint32]
    kernel.WaitForSingleObject.restype = ctypes.c_uint32
    kernel.CloseHandle.argtypes = [ctypes.c_void_p]
    kernel.CloseHandle.restype = ctypes.c_int
    handle = kernel.OpenProcess(0x00100000, False, pid)  # SYNCHRONIZE
    if not handle:
        return ctypes.get_last_error() not in {87, 1168}  # invalid PID / not found => exited
    try:
        result = kernel.WaitForSingleObject(handle, 0)
        if result == 0:
            return False  # WAIT_OBJECT_0
        if result == 0x00000102:
            return True  # WAIT_TIMEOUT
        raise AssertionError(f"WaitForSingleObject({pid}) returned {result}")
    finally:
        kernel.CloseHandle(handle)


class V8WrapperTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory(prefix="v8wrapper-test-")
        self.root = Path(self.temp.name)
        self.scripts = self.root / "fake tools"
        self.scripts.mkdir()
        self.tool_script = self.scripts / "fake_v8unpack.py"
        self.tool_script.write_text(FAKE_TOOL, encoding="utf-8")
        self.metadata = self.root / "v8unpack-1.2.13.dist-info" / "METADATA"
        self.metadata.parent.mkdir()
        self.metadata.write_text("Metadata-Version: 2.1\nName: v8unpack\nVersion: 1.2.13\n", encoding="utf-8")
        self.executable = Path(sys.executable)
        self.input = self.root / "Дані Форма.epf"
        self.make_container(self.input)

    def tearDown(self) -> None:
        self.temp.cleanup()

    @staticmethod
    def make_container(path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        with zipfile.ZipFile(path, "w") as zf:
            zf.writestr("Form.json", b'{"Controls": []}')
            zf.writestr("Nested/unknown.bin", b"opaque\x00payload")

    def backend(self, *, mode: str = "ok", timeout: float = 8.0) -> V8Unpack:
        return V8Unpack(BackendConfig(
            executable=self.executable,
            executable_args=("-u", str(self.tool_script), f"--mode={mode}"),
            metadata_file=self.metadata,
            timeout_seconds=timeout,
            max_output_bytes=256,
        ))

    def test_metadata_pin_and_missing_executable(self) -> None:
        missing = self.root / "missing.exe"
        with self.assertRaisesRegex(V8UnpackError, "missing"):
            V8Unpack(BackendConfig(executable=missing, metadata_file=self.metadata)).package_version()
        wrong = self.root / "wrong.METADATA"
        wrong.write_text("Name: other\nVersion: 1.2.13\n", encoding="utf-8")
        with self.assertRaisesRegex(V8UnpackError, "another package"):
            V8Unpack(BackendConfig(executable=self.executable, metadata_file=wrong)).package_version()
        wrong.write_text("Name: v8unpack\nVersion: 9.9\n", encoding="utf-8")
        with self.assertRaisesRegex(V8UnpackError, "expected v8unpack"):
            V8Unpack(BackendConfig(executable=self.executable, metadata_file=wrong)).package_version()
        digest = hashlib.sha256(self.input.read_bytes()).hexdigest()
        untouched = self.root / "unrelated.dat"
        untouched.write_bytes(b"untouched")
        with self.assertRaises(V8UnpackError) as raised:
            V8Unpack(BackendConfig(executable=missing, metadata_file=self.metadata)).roundtrip_noop(
                self.input, workspace_root=self.root)
        self.assertEqual(raised.exception.code, "E_V8_EXECUTABLE_MISSING")
        self.assertEqual(hashlib.sha256(self.input.read_bytes()).hexdigest(), digest)
        self.assertEqual(untouched.read_bytes(), b"untouched")

    def test_noop_roundtrip_with_spaces_cyrillic_and_opaque_payload(self) -> None:
        before = hashlib.sha256(self.input.read_bytes()).hexdigest()
        workspace = self.root / "Робоча папка"
        workspace.mkdir()
        result = self.backend().roundtrip_noop(self.input, workspace_root=workspace)
        self.assertEqual(result.tool_version, "1.2.13")
        self.assertEqual(result.decoded_file_count, 2)
        self.assertEqual(result.input_sha256, before)
        self.assertEqual(hashlib.sha256(self.input.read_bytes()).hexdigest(), before)
        self.assertEqual([c.step for c in result.commands], ["unpack_source", "pack_noop", "unpack_packed"])
        self.assertTrue(all(c.returncode == 0 for c in result.commands))
        self.assertTrue(result.artifact_path.is_file())
        # Owned temporary run directories are removed; only the published artifact stays.
        self.assertFalse(list(workspace.rglob("roundtrip-*")))
        self.assertTrue(result.artifact_path.resolve().is_relative_to(workspace.resolve()))

    def test_pack_failure_and_missing_output_publish_nothing(self) -> None:
        for mode, expected in (("pack-error", "E_V8_EXIT_CODE"), ("missing-output", "E_V8_ARTIFACT_MISSING")):
            workspace = self.root / mode
            workspace.mkdir()
            with self.assertRaises(V8UnpackError) as raised:
                self.backend(mode=mode).roundtrip_noop(self.input, workspace_root=workspace)
            self.assertEqual(raised.exception.code, expected)
            self.assertFalse(list(workspace.rglob("*.epf")))
            self.assertFalse(list(workspace.rglob("*.partial")))

    def test_refuses_artifact_escape_and_overwrite(self) -> None:
        with self.assertRaises(V8UnpackError):
            self.backend().roundtrip_noop(self.input, workspace_root=self.root, artifact_name="..\\outside.epf")
        workspace = self.root / "existing"
        workspace.mkdir()
        output = self.backend().roundtrip_noop(self.input, workspace_root=workspace,
                                               artifact_name="same.epf").artifact_path
        output.write_bytes(b"do not overwrite")
        with self.assertRaisesRegex(V8UnpackError, "overwrite"):
            self.backend().roundtrip_noop(self.input, workspace_root=workspace, artifact_name="same.epf")
        self.assertEqual(output.read_bytes(), b"do not overwrite")

    @unittest.skipUnless(os.name == "nt", "symlink path behavior is Windows-specific")
    def test_rejects_symlink_parent_before_creating_managed_dirs(self) -> None:
        workspace = self.root / "symlink-workspace"
        outside = self.root / "outside-target"
        workspace.mkdir()
        outside.mkdir()
        try:
            (workspace / ".tmp").symlink_to(outside, target_is_directory=True)
        except OSError as exc:
            self.skipTest(f"directory symlink creation unavailable: {exc}")
        with self.assertRaises(V8UnpackError) as raised:
            self.backend().roundtrip_noop(self.input, workspace_root=workspace)
        self.assertEqual(raised.exception.code, "E_V8_PATH_INVALID")
        self.assertEqual(list(outside.iterdir()), [])

    def test_bounded_capture_records_totals(self) -> None:
        code = "import sys; sys.stdout.write('x'*200000); sys.stderr.write('y'*180000)"
        result = run_bounded_process((sys.executable, "-c", code), timeout_seconds=10, max_output_bytes=128)
        self.assertEqual(result.returncode, 0)
        self.assertEqual(len(result.stdout), 128)
        self.assertEqual(len(result.stderr), 128)
        self.assertEqual(result.stdout_total_bytes, 200000)
        self.assertEqual(result.stderr_total_bytes, 180000)
        self.assertTrue(result.stdout_truncated and result.stderr_truncated)

    @unittest.skipUnless(os.name == "nt", "Windows Job Object startup is platform-specific")
    def test_job_assignment_denial_fails_closed_before_child_resumes(self) -> None:
        from one_c_form_compiler.core import v8wrapper

        marker = self.root / "must-not-run.txt"
        fake_job = Mock()
        fake_job.creation_flag = 0x00000004
        fake_job.assigned = False
        fake_job.assign_and_resume.side_effect = OSError("simulated nested-job denial")
        fake_job.terminate_and_wait.return_value = None
        code = "import pathlib,sys; pathlib.Path(sys.argv[1]).write_text('ran')"
        started = time.monotonic()
        with patch.object(v8wrapper, "_WindowsKillJob", return_value=fake_job):
            with self.assertRaises(V8UnpackError) as raised:
                run_bounded_process((sys.executable, "-c", code, str(marker)),
                                    timeout_seconds=2, max_output_bytes=64)
        self.assertEqual(raised.exception.code, "E_V8_PROCESS_START")
        self.assertLess(time.monotonic() - started, 2.0, "suspended startup failure cleanup was not immediate")
        self.assertFalse(fake_job.terminate_and_wait.called)
        self.assertTrue(fake_job.close.called)
        self.assertFalse(marker.exists(), "child executed despite failed Job Object assignment")

    @unittest.skipUnless(os.name == "nt", "child-tree cleanup implementation is Windows-specific")
    def test_timeout_kills_owned_process_tree(self) -> None:
        marker = self.root / "late-child-marker.txt"
        child_pid_file = self.root / "child.pid"
        child = (
            "import os,time,pathlib,sys; pathlib.Path(sys.argv[1]).write_text(str(os.getpid())); "
            "time.sleep(1.4); pathlib.Path(sys.argv[2]).write_text('alive')"
        )
        parent = (
            "import pathlib,subprocess,sys,time; subprocess.Popen([sys.executable,'-c',sys.argv[1],"
            "sys.argv[2],sys.argv[3]]); deadline=time.monotonic()+5; pidfile=pathlib.Path(sys.argv[2]); "
            "exec('while not pidfile.exists() and time.monotonic()<deadline: time.sleep(0.01)'); time.sleep(30)"
        )
        started = time.monotonic()
        with self.assertRaises(V8UnpackError) as raised:
            run_bounded_process((sys.executable, "-c", parent, child,
                                 str(child_pid_file), str(marker)),
                               timeout_seconds=0.5, max_output_bytes=64)
        self.assertEqual(raised.exception.code, "E_V8_TIMEOUT")
        self.assertLess(time.monotonic() - started, 8)
        self.assertTrue(child_pid_file.is_file(), "child did not start before the timeout")
        child_pid = int(child_pid_file.read_text(encoding="ascii"))
        deadline = time.monotonic() + 3
        while _windows_pid_is_alive(child_pid) and time.monotonic() < deadline:
            time.sleep(0.05)
        alive_after_timeout = _windows_pid_is_alive(child_pid)
        self.assertFalse(alive_after_timeout, f"owned descendant PID {child_pid} survived timeout")
        time.sleep(1.5)
        late_marker_exists = marker.exists()
        self.assertFalse(late_marker_exists, "descendant process survived timeout cleanup")
        TIMEOUT_CLEANUP_OBSERVATIONS.append({
            "child_pid": child_pid,
            "alive_after_timeout": alive_after_timeout,
            "late_marker_exists": late_marker_exists,
        })

    def test_parallel_roundtrips_use_isolated_workdirs(self) -> None:
        workspace = self.root / "parallel" / "Дані з пробілами"
        workspace.mkdir(parents=True)
        backend = self.backend()
        def run(name: str):
            return backend.roundtrip_noop(self.input, workspace_root=workspace, artifact_name=name)
        with ThreadPoolExecutor(max_workers=2) as pool:
            first, second = list(pool.map(run, ("one.epf", "two.epf")))
        self.assertNotEqual(first.artifact_path, second.artifact_path)
        self.assertEqual(first.input_sha256, second.input_sha256)
        self.assertEqual(first.decoded_tree_sha256, second.decoded_tree_sha256)
        self.assertTrue(first.artifact_path.is_file() and second.artifact_path.is_file())


class RealBackendNoOpTests(unittest.TestCase):
    @unittest.skipUnless(os.environ.get("ONE_C_FORM_TEST_V8UNPACK_EXE") and SAMPLE.is_file(),
                         "set ONE_C_FORM_TEST_V8UNPACK_EXE to a v8unpack executable; "
                         "the sample tests/fixtures/sample_ordinary.epf is also required")
    def test_real_backend_payload_roundtrip(self) -> None:
        executable = Path(os.environ["ONE_C_FORM_TEST_V8UNPACK_EXE"])
        source = SAMPLE
        before = hashlib.sha256(source.read_bytes()).hexdigest()
        with tempfile.TemporaryDirectory(prefix="v8wrapper-real-") as temp_name:
            result = V8Unpack(BackendConfig(executable=executable, timeout_seconds=60)).roundtrip_noop(
                source, workspace_root=Path(temp_name), artifact_name="NoOpRoundtrip.epf")
        self.assertEqual(result.tool_version, "1.2.13")
        self.assertGreater(result.decoded_file_count, 0)
        self.assertEqual(result.input_sha256, before)
        self.assertEqual(hashlib.sha256(source.read_bytes()).hexdigest(), before)


if __name__ == "__main__":
    unittest.main()
