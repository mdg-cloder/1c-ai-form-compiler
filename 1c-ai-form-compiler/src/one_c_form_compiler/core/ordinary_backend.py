"""EPF/ERF container round-trip for ordinary-form editing.

Flow of :class:`EditSession`::

    input.epf ──copy──▶ work/input.epf ──v8unpack -E──▶ work/src/…
                                               │  OrdinaryForm edits + save
                                               ▼
    work/out.epf ◀──v8unpack -B── work/src ──▶ verify: -E work/out.epf → work/verify
                                               │  decoded JSON must equal the edited JSON,
                                               │  every other decoded file must equal the original
                                               ▼
    output.epf  ◀── atomic publish (os.link when absent / os.replace with --overwrite)

The input file is never opened for writing; its SHA-256 is re-checked before
publication. All subprocesses run without a shell, bounded in time and output.

Before the first form is handed out, the unedited tree is built once (``-B`` → work/noop.*) and the
``form``/``module`` entries of every ordinary form must equal the input's byte for byte; otherwise the
session refuses (``E_FORM_ROUNDTRIP`` or a specific code from :func:`oform.form_defects`): v8unpack would
silently change parts of the form the edit does not touch.
"""

from __future__ import annotations

import hashlib
import importlib.metadata
import importlib.util
import json
import os
import shutil
import sys
import tempfile
import uuid
from dataclasses import dataclass, field
from pathlib import Path

from .errors import FormRefusal
from .object_metadata import read_object_attributes, read_object_type_id, register_data_paths, root_file
from .oform import form_defects
from .ordinary_form import FormModelError, OrdinaryForm, find_form_dirs
from .v8wrapper import V8UnpackError, run_bounded_process

SUPPORTED_V8UNPACK = "1.2.13"
# A frozen (PyInstaller) build has no ``python -m``: the executable starts itself with this flag as the
# first argument and ``cli.main`` dispatches to the bundled v8unpack in-process.
FROZEN_V8UNPACK_FLAG = "--run-bundled-v8unpack"
CONTAINER_SUFFIXES = {".epf", ".erf"}
MAX_CONTAINER_BYTES = 256 * 1024 * 1024


class BackendError(RuntimeError):
    def __init__(self, code: str, message: str) -> None:
        self.code = code
        super().__init__(message)


@dataclass(frozen=True, slots=True)
class Backend:
    argv_prefix: tuple[str, ...]
    version: str
    timeout_seconds: float = 300.0

    @classmethod
    def discover(cls, timeout_seconds: float = 300.0) -> "Backend":
        override = os.environ.get("ONE_C_FORM_V8UNPACK")
        if override:
            path = Path(override)
            if not path.is_file():
                raise BackendError("E_V8_EXECUTABLE_MISSING", f"ONE_C_FORM_V8UNPACK points to a missing file: {path}")
            return cls((str(path),), "external", timeout_seconds)
        frozen = is_frozen()
        if importlib.util.find_spec("v8unpack") is None:
            if frozen:
                raise BackendError("E_V8_MISSING",
                                   "this build does not bundle v8unpack; rebuild the release or set "
                                   "ONE_C_FORM_V8UNPACK to an external v8unpack executable")
            raise BackendError("E_V8_MISSING",
                               "v8unpack is not installed; run: pip install v8unpack==" + SUPPORTED_V8UNPACK)
        version = _v8unpack_version()
        if version != SUPPORTED_V8UNPACK:
            raise BackendError("E_V8_VERSION_UNSUPPORTED",
                               f"v8unpack {SUPPORTED_V8UNPACK} is required, found {version}")
        if frozen:
            return cls((sys.executable, FROZEN_V8UNPACK_FLAG), version, timeout_seconds)
        return cls((sys.executable, "-m", "v8unpack"), version, timeout_seconds)

    def smoke(self, timeout_seconds: float = 60.0) -> str:
        """Start the backend once with ``--help`` (cheap, touches no files); raise when it does not run."""
        try:
            result = run_bounded_process([*self.argv_prefix, "--help"], timeout_seconds=timeout_seconds,
                                         max_output_bytes=64 * 1024)
        except V8UnpackError as error:
            raise BackendError(error.code, f"smoke run: {error}") from None
        output = (result.stdout + result.stderr).decode("utf-8", "replace")
        if result.returncode != 0 or "v8unpack" not in output:
            tail = " | ".join(output.strip().splitlines()[-3:])
            raise BackendError("E_V8_SMOKE", f"backend did not start (exit {result.returncode}): {tail}")
        return "ok"

    def _run(self, step: str, args: list[str], temp_dir: Path) -> None:
        argv = [*self.argv_prefix, *args, "--temp", str(temp_dir), "--processes", "1"]
        env_backup = os.environ.get("PYTHONIOENCODING")
        os.environ["PYTHONIOENCODING"] = "utf-8"
        try:
            result = run_bounded_process(argv, timeout_seconds=self.timeout_seconds, max_output_bytes=64 * 1024)
        except V8UnpackError as error:
            raise BackendError(error.code, f"{step}: {error}") from None
        finally:
            if env_backup is None:
                os.environ.pop("PYTHONIOENCODING", None)
            else:
                os.environ["PYTHONIOENCODING"] = env_backup
        if result.returncode != 0:
            raise BackendError("E_V8_EXIT_CODE", f"v8unpack {step} failed ({result.returncode}): "
                                                 + _failure_detail(step, result.stdout, result.stderr))

    def extract(self, container: Path, target: Path, temp_dir: Path) -> None:
        self._run("extract", ["-E", str(container), str(target)], temp_dir)
        if not find_form_dirs(target) and not any(target.iterdir()):
            raise BackendError("E_V8_EXTRACT_EMPTY", "v8unpack produced no files")

    def build(self, source: Path, container: Path, temp_dir: Path) -> None:
        self._run("build", ["-B", str(source), str(container)], temp_dir)
        if not container.is_file() or container.stat().st_size == 0:
            raise BackendError("E_V8_BUILD_EMPTY", "v8unpack produced no container")


def _failure_detail(step: str, stdout: bytes, stderr: bytes, limit: int = 400) -> str:
    """Last ``limit`` characters of the v8unpack output (it reports errors on stdout), one line."""
    output = stderr.decode("utf-8", "replace").splitlines() + stdout.decode("utf-8", "replace").splitlines()
    text = " | ".join(line.strip() for line in output if line.strip())
    if len(text) > limit:
        text = "..." + text[-limit:]
    detail = text or "no output"
    if step == "extract":
        detail += " (input is probably not a valid EPF/ERF file)"
    return detail


def is_frozen() -> bool:
    """True inside a frozen (PyInstaller) executable."""
    return bool(getattr(sys, "frozen", False))


def _v8unpack_version() -> str:
    try:
        return importlib.metadata.version("v8unpack")
    except importlib.metadata.PackageNotFoundError:
        # A frozen build without bundled distribution metadata: use the package's own version module.
        from v8unpack.version import __version__
        return str(__version__)


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _decoded_snapshot(root: Path) -> dict[str, object]:
    """Comparable view of a decoded tree: JSON parsed, text normalized."""
    result: dict[str, object] = {}
    for path in sorted(root.rglob("*")):
        if not path.is_file():
            continue
        relative = path.relative_to(root).as_posix()
        data = path.read_bytes()
        if path.suffix == ".json":
            try:
                result[relative] = json.loads(data.decode("utf-8-sig"))
                continue
            except ValueError:
                pass
        if path.suffix in {".bsl", ".txt"}:
            result[relative] = data.decode("utf-8-sig", "replace").replace("\r\n", "\n")
        else:
            result[relative] = hashlib.sha256(data).hexdigest()
    return result


@dataclass
class FormRef:
    name: str
    path: Path

    @property
    def relative(self) -> str:
        return self.path.as_posix()


@dataclass
class EditSession:
    """Extract → edit forms → build → verify → publish. Use as a context manager."""

    input_path: Path
    backend: Backend
    keep_workdir: bool = False
    workdir: Path = field(init=False)
    source_dir: Path = field(init=False)
    input_sha256: str = field(init=False)
    _forms: dict[str, OrdinaryForm] = field(init=False, default_factory=dict)
    _original: dict[str, object] = field(init=False, default_factory=dict)
    _round_trip_checked: bool = field(init=False, default=False)

    def __post_init__(self) -> None:
        self.input_path = Path(self.input_path).resolve()
        if not self.input_path.is_file():
            raise BackendError("E_INPUT_MISSING", f"input file not found: {self.input_path}")
        if self.input_path.suffix.lower() not in CONTAINER_SUFFIXES:
            raise BackendError("E_INPUT_UNSUPPORTED", "input must be an .epf or .erf file")
        if self.input_path.stat().st_size > MAX_CONTAINER_BYTES:
            raise BackendError("E_INPUT_TOO_LARGE", "input container exceeds 256 MiB")

    def __enter__(self) -> "EditSession":
        self.input_sha256 = sha256_file(self.input_path)
        self.workdir = Path(tempfile.mkdtemp(prefix="1c-form-"))
        try:
            (self.workdir / "tmp").mkdir()
            local = self.workdir / ("input" + self.input_path.suffix.lower())
            shutil.copyfile(self.input_path, local)
            if sha256_file(local) != self.input_sha256:
                raise BackendError("E_INPUT_CHANGED", "input changed while copying")
            self.source_dir = self.workdir / "src"
            self.backend.extract(local, self.source_dir, self.workdir / "tmp")
            self._original = _decoded_snapshot(self.source_dir)
        except BaseException:
            # __exit__ is not called when __enter__ raises: clean up here.
            if not self.keep_workdir:
                shutil.rmtree(self.workdir, ignore_errors=True)
            raise
        return self

    def __exit__(self, *exc) -> None:
        if not self.keep_workdir:
            shutil.rmtree(self.workdir, ignore_errors=True)

    # ------------------------------------------------------------------ forms
    def form_refs(self) -> list[FormRef]:
        refs = []
        for path in find_form_dirs(self.source_dir):
            refs.append(FormRef(path.name, path.relative_to(self.source_dir)))
        return refs

    def form(self, name: str | None = None) -> OrdinaryForm:
        refs = self.form_refs()
        if not refs:
            raise FormModelError("E_FORM_NOT_FOUND", "the container has no ordinary (FormElements27) forms")
        if name:
            matches = [ref for ref in refs if ref.name == name or ref.relative == name]
            if len(matches) != 1:
                raise FormModelError("E_FORM_NOT_FOUND",
                                     f"form {name!r} not found; available: {', '.join(r.name for r in refs)}")
            ref = matches[0]
        else:
            if len(refs) != 1:
                raise FormModelError("E_FORM_REQUIRED",
                                     f"several forms; choose one with --form: {', '.join(r.name for r in refs)}")
            ref = refs[0]
        key = ref.relative
        if key not in self._forms:
            self._check_round_trip()
            form = OrdinaryForm.load(self.source_dir / ref.path)
            form.object_attributes = read_object_attributes(self.source_dir)
            form.object_type_id = read_object_type_id(self.source_dir)
            self._forms[key] = form
        return self._forms[key]

    # ------------------------------------------------------------------ build
    def build_and_publish(self, output_path: Path, *, overwrite: bool = False) -> dict:
        output = Path(output_path).resolve()
        if output == self.input_path:
            raise BackendError("E_OUTPUT_IS_INPUT", "output must differ from the input file")
        if output.suffix.lower() != self.input_path.suffix.lower():
            raise BackendError("E_OUTPUT_SUFFIX", f"output must have the {self.input_path.suffix} extension")
        if output.exists() and not overwrite:
            raise BackendError("E_OUTPUT_EXISTS", f"{output} exists; pass --overwrite to replace it")
        if not output.parent.is_dir():
            raise BackendError("E_OUTPUT_DIR_MISSING", f"output directory does not exist: {output.parent}")
        self._check_round_trip()
        for form in self._forms.values():
            form.save()
        paths = [item for form in self._forms.values() for item in form.bound_attributes()]
        try:
            metadata_changed = register_data_paths(self.source_dir, paths)
        except ValueError as error:
            raise BackendError("E_METADATA_FORMAT", str(error)) from None
        edited = _decoded_snapshot(self.source_dir)
        built = self.workdir / ("out" + output.suffix.lower())
        self.backend.build(self.source_dir, built, self.workdir / "tmp")
        verify_dir = self.workdir / "verify"
        self.backend.extract(built, verify_dir, self.workdir / "tmp")
        verified = _decoded_snapshot(verify_dir)
        mismatched = sorted(path for path in set(edited) | set(verified) if edited.get(path) != verified.get(path))
        if mismatched:
            raise BackendError("E_VERIFY_MISMATCH",
                               "rebuilt container decodes differently from the edit: " + ", ".join(mismatched[:5]))
        changed = sorted(path for path in set(edited) | set(self._original)
                         if edited.get(path) != self._original.get(path))
        allowed_dirs = {str(Path(form.form_dir).relative_to(self.source_dir).as_posix()) for form in self._forms.values()}
        metadata_file = root_file(self.source_dir)
        allowed_files = {metadata_file.name} if metadata_changed and metadata_file else set()
        unexpected = [path for path in changed
                      if path.rsplit("/", 1)[0] not in allowed_dirs and path not in allowed_files]
        if unexpected:
            raise BackendError("E_VERIFY_SCOPE", "files outside edited forms changed: " + ", ".join(unexpected[:5]))
        if sha256_file(self.input_path) != self.input_sha256:
            raise BackendError("E_INPUT_CHANGED", "input file changed during the edit; nothing published")
        staged = output.with_name(f".{output.name}.{uuid.uuid4().hex}.tmp")
        shutil.copyfile(built, staged)
        try:
            if overwrite:
                os.replace(staged, output)
            else:
                try:
                    os.link(staged, output)
                except FileExistsError:
                    raise BackendError("E_OUTPUT_EXISTS", f"{output} appeared during the edit") from None
        finally:
            if staged.exists():
                staged.unlink()
        result = {"output": str(output), "sha256": sha256_file(output), "changed_files": changed,
                  "input_sha256": self.input_sha256}
        repairs = [f"{form.form_dir.name}: {item}" for form in self._forms.values() for item in form.repairs]
        if repairs:
            result["repairs"] = repairs
        return result

    # ------------------------------------------------------------------ no-op round trip
    def _check_round_trip(self) -> None:
        """Refuse a container whose unedited ordinary forms v8unpack does not rebuild byte for byte.

        Runs once, while the decoded tree is still unedited (before any form is loaded or saved)."""
        if self._round_trip_checked:
            return
        for path in find_form_dirs(self.source_dir):
            defects = form_defects(path)
            if defects:
                code, message = defects[0]
                raise BackendError(code, f"{path.name}: {message}; this tool cannot edit such a form without "
                                         "changing other parts, edit it in Designer")
        if importlib.util.find_spec("v8unpack") is None:
            # An external backend (ONE_C_FORM_V8UNPACK) without v8unpack in this interpreter: the entries
            # cannot be read here; the decoded-tree verification at publish still runs.
            self._round_trip_checked = True
            return
        noop = self.workdir / ("noop" + self.input_path.suffix.lower())
        self.backend.build(self.source_dir, noop, self.workdir / "tmp")
        before = container_form_entries(self.workdir / ("input" + self.input_path.suffix.lower()))
        after = container_form_entries(noop)
        uuids = _form_uuids(self.source_dir)
        changed = sorted(name for name in set(before) | set(after)
                         if (uuids is None or name.split(".")[0] in uuids) and before.get(name) != after.get(name))
        if changed:
            names = [uuids.get(name.split(".")[0], name) if uuids else name for name in changed]
            raise BackendError(FormRefusal.FORM_ROUNDTRIP.value,
                               f"rebuilding the unchanged container does not reproduce the form(s) "
                               f"{', '.join(names[:5])} byte for byte (a construct v8unpack does not keep); "
                               "editing would change other parts, edit the form in Designer")
        self._round_trip_checked = True


def container_form_entries(container: Path) -> dict[str, tuple[bytes, bytes]]:
    """``form`` and ``module`` bytes of every nested form container (``<uuid>.0``) of an EPF/ERF."""
    from v8unpack.container import Container
    result: dict[str, tuple[bytes, bytes]] = {}
    with tempfile.TemporaryDirectory(prefix="1c-entries-") as tmp:
        try:
            reader = Container()
            with open(container, "rb") as stream:
                reader.read(stream)
                reader.extract(tmp, deflate=True, recursive=True)
        except Exception as error:  # v8unpack raises bare exceptions on damaged input
            raise BackendError("E_V8_EXTRACT", f"cannot read {Path(container).name}: {error}") from None
        for form in sorted(Path(tmp).glob("*/form")):
            module = form.with_name("module")
            result[form.parent.name] = (form.read_bytes(), module.read_bytes() if module.is_file() else b"")
    return result


def _form_uuids(source_dir: Path) -> dict[str, str] | None:
    """uuid -> form name of the ordinary forms of an extracted tree (``Form.id.json``); None when unknown."""
    result: dict[str, str] = {}
    for path in find_form_dirs(source_dir):
        try:
            result[json.loads((path / "Form.id.json").read_text(encoding="utf-8-sig"))["uuid"]] = path.name
        except (OSError, ValueError, KeyError, TypeError):
            return None
    return result
