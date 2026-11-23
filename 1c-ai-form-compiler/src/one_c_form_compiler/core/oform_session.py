"""Edit session for a configuration ordinary form (``Form.oform`` in an EDT project).

Same contract as :class:`EditSession` (``form()``, ``build_and_publish()``)::

    Form.oform ──copy──▶ work/input.oform ──decode──▶ work/src/<Form>/Form.json …
                                                       │ OrdinaryForm edits + save
    work/out.oform ◀──encode── work/src/<Form> ──▶ verify: decode out → equal to the edit
                                                       ▼
    Form.oform (in place, backup outside src/) or -o <file>   ── atomic os.replace / os.link

The input is re-hashed before publishing: if EDT (or anybody) saved the form meanwhile,
nothing is written.
"""

from __future__ import annotations

import os
import shutil
import tempfile
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path

from .edt_metadata import EdtMetadataError, object_mdo_for_form, project_root_for, read_object_metadata
from .errors import FormRefusal
from .oform import META_KEY, OformError, decode_oform, encode_oform, form_defects, read_oform
from .ordinary_backend import BackendError, FormRef, _decoded_snapshot, sha256_file
from .ordinary_form import FormModelError, OrdinaryForm

BACKUP_DIR = ".one-c-form-backups"
REFRESH_HINT = ("refresh the project in EDT before working with it: F5 on the project, or AI-EDT "
                "infobase_admin update_database (refreshWorkspace=true) when updating a test infobase")


@dataclass
class OformSession:
    input_path: Path
    keep_workdir: bool = False
    workdir: Path = field(init=False)
    source_dir: Path = field(init=False)
    input_sha256: str = field(init=False)
    _form: OrdinaryForm | None = field(init=False, default=None)
    _original: dict[str, object] = field(init=False, default_factory=dict)

    def __post_init__(self) -> None:
        self.input_path = Path(self.input_path).resolve()
        if not self.input_path.is_file():
            raise BackendError("E_INPUT_MISSING", f"input file not found: {self.input_path}")
        if self.input_path.suffix.lower() != ".oform":
            raise BackendError("E_INPUT_UNSUPPORTED", "input must be a Form.oform file")

    @property
    def form_name(self) -> str:
        return self.input_path.parent.name

    def __enter__(self) -> "OformSession":
        self.input_sha256 = sha256_file(self.input_path)
        self.workdir = Path(tempfile.mkdtemp(prefix="1c-oform-"))
        try:
            local = self.workdir / "input.oform"
            shutil.copyfile(self.input_path, local)
            if sha256_file(local) != self.input_sha256:
                raise BackendError("E_INPUT_CHANGED", "input changed while copying")
            self.source_dir = self.workdir / "src"
            try:
                decode_oform(local, self.source_dir / self.form_name)
            except OformError as error:
                raise BackendError(error.code, str(error)) from None
            self._check_round_trip(local)
            self._original = _decoded_snapshot(self.source_dir)
        except BaseException:
            # __exit__ is not called when __enter__ raises: always clean up here (also with
            # keep_workdir, whose callers remove the directory themselves inside the with-block).
            shutil.rmtree(self.workdir, ignore_errors=True)
            raise
        return self

    def _check_round_trip(self, local: Path) -> None:
        """Refuse forms the decoder cannot reproduce: an edit would silently change other parts.

        A construct known to break the round trip (:func:`form_defects`) is refused with its own code even
        when the bytes happen to come back, since edits of such a form could reach the wrong element."""
        defects = form_defects(self.source_dir / self.form_name)
        if defects:
            code, message = defects[0]
            raise BackendError(code, f"{self.form_name}: {message}; this tool cannot edit such a form without "
                                     "changing other parts, edit it in Designer/EDT")
        rebuilt = self.workdir / "roundtrip.oform"
        try:
            encode_oform(self.source_dir / self.form_name, rebuilt)
            same = read_oform(rebuilt)
        except OformError as error:
            raise BackendError(FormRefusal.OFORM_ROUNDTRIP.value,
                               f"{self.form_name}: the form cannot be rebuilt unchanged ({error}); "
                               "edit it in Designer/EDT") from None
        original = read_oform(local)
        if same.form != original.form or same.module != original.module:
            part = "form text" if same.form != original.form else "module"
            offset = _first_difference(same.form, original.form) if part == "form text" else None
            where = f" (first difference at byte {offset})" if offset is not None else ""
            raise BackendError(FormRefusal.OFORM_ROUNDTRIP.value,
                               f"{self.form_name}: rebuilding the unchanged form does not reproduce its {part} byte "
                               f"for byte{where}: unsupported construct in the form; editing it would change other "
                               "parts, edit it in Designer/EDT")

    def __exit__(self, *exc) -> None:
        if not self.keep_workdir:
            shutil.rmtree(self.workdir, ignore_errors=True)

    # ------------------------------------------------------------------ forms
    def form_refs(self) -> list[FormRef]:
        return [FormRef(self.form_name, Path(self.form_name))]

    def form(self, name: str | None = None) -> OrdinaryForm:
        if name and name not in {self.form_name, str(self.input_path)} and not name.endswith("." + self.form_name):
            raise FormModelError("E_FORM_NOT_FOUND", f"this file holds the form {self.form_name}, not {name}")
        if self._form is None:
            form = OrdinaryForm.load(self.source_dir / self.form_name)
            mdo = object_mdo_for_form(self.input_path)
            if mdo is not None:
                try:
                    meta = read_object_metadata(mdo)
                except EdtMetadataError as error:
                    raise BackendError(error.code, str(error)) from None
                form.object_attributes = meta.attributes
                form.object_type_id = meta.object_type_id
                form.tabular_sections = meta.tabular_sections
            self._form = form
        return self._form

    # ------------------------------------------------------------------ build
    def build_and_publish(self, output_path: Path | None = None, *, overwrite: bool = False) -> dict:
        in_place = output_path is None or Path(output_path).resolve() == self.input_path
        output = self.input_path if in_place else Path(output_path).resolve()
        if not in_place:
            if output.suffix.lower() != ".oform":
                raise BackendError("E_OUTPUT_SUFFIX", "output must have the .oform extension")
            if output.exists() and not overwrite:
                raise BackendError("E_OUTPUT_EXISTS", f"{output} exists; pass --overwrite to replace it")
            if not output.parent.is_dir():
                raise BackendError("E_OUTPUT_DIR_MISSING", f"output directory does not exist: {output.parent}")
        if self._form is not None:
            self._form.save()
        edited = _decoded_snapshot(self.source_dir)
        built = self.workdir / "out.oform"
        try:
            encode_oform(self.source_dir / self.form_name, built)
            decode_oform(built, self.workdir / "verify" / self.form_name)
        except OformError as error:
            raise BackendError(error.code, str(error)) from None
        verified = _decoded_snapshot(self.workdir / "verify")
        # The codec's own meta (line-break bookkeeping of the source bytes) is recomputed from the rebuilt
        # bytes and may differ after an edit (e.g. a multi-line title replaced): not part of the form.
        for snapshot in (edited, verified):
            form_json = snapshot.get(f"{self.form_name}/Form.json")
            if isinstance(form_json, dict):
                form_json.pop(META_KEY, None)
        mismatched = sorted(path for path in set(edited) | set(verified) if edited.get(path) != verified.get(path))
        if mismatched:
            raise BackendError("E_VERIFY_MISMATCH",
                               "rebuilt form decodes differently from the edit: " + ", ".join(mismatched[:5]))
        changed = sorted(path for path in set(edited) | set(self._original)
                         if edited.get(path) != self._original.get(path))
        if sha256_file(self.input_path) != self.input_sha256:
            raise BackendError("E_INPUT_CHANGED", "Form.oform changed during the edit (saved in EDT?); nothing written")
        result = {"output": str(output), "changed_files": changed, "input_sha256": self.input_sha256}
        if self._form is not None and self._form.repairs:
            result["repairs"] = list(self._form.repairs)
        if in_place:
            backup = self._backup_path()
            backup.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(self.input_path, backup)
            result["backup"] = str(backup)
        staged = output.with_name(f".{output.name}.{uuid.uuid4().hex}.tmp")
        shutil.copyfile(built, staged)
        try:
            if in_place or overwrite:
                os.replace(staged, output)
            else:
                try:
                    os.link(staged, output)
                except FileExistsError:
                    raise BackendError("E_OUTPUT_EXISTS", f"{output} appeared during the edit") from None
        finally:
            if staged.exists():
                staged.unlink()
        result["sha256"] = sha256_file(output)
        if in_place:
            result["refresh_hint"] = REFRESH_HINT
        return result

    def _backup_path(self) -> Path:
        stamp = time.strftime("%Y%m%d-%H%M%S")
        project = project_root_for(self.input_path)
        if project is None:
            return self.input_path.with_name(f"{self.input_path.name}.bak-{stamp}")
        relative = self.input_path.relative_to(project)
        target = project / BACKUP_DIR / relative.parent / f"{relative.name}.{stamp}"
        counter = 1
        while target.exists():
            target = target.with_name(f"{relative.name}.{stamp}-{counter}")
            counter += 1
        return target


def _first_difference(a: bytes, b: bytes) -> int | None:
    for index, (left, right) in enumerate(zip(a, b)):
        if left != right:
            return index
    return None if len(a) == len(b) else min(len(a), len(b))
