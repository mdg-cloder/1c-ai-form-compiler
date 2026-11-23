"""Form.oform codec: EDT ordinary-form container <-> decoded FormElements27 JSON."""

from __future__ import annotations

import os
import shutil
import tempfile
import unittest
from pathlib import Path

from tests._support import HAS_SAMPLE, HAS_V8UNPACK, SAMPLE, SAMPLE_REASON

__all__ = ["HAS_SAMPLE", "HAS_V8UNPACK", "SAMPLE", "SAMPLE_REASON", "make_sample_oform"]

# Optional: a Form.oform of any EDT project, checked read-only for a byte-exact round trip.
_EXTERNAL = os.environ.get("ONE_C_FORM_TEST_EDT_FORM")
EXTERNAL_FORM = Path(_EXTERNAL) if _EXTERNAL else None


def make_sample_oform(target: Path) -> Path:
    """Build a Form.oform from the ordinary form of the sample EPF (tests/fixtures/sample_ordinary.epf)."""
    from one_c_form_compiler.core.oform import encode_oform
    from one_c_form_compiler.core.ordinary_backend import Backend
    from one_c_form_compiler.core.ordinary_form import find_form_dirs

    work = Path(tempfile.mkdtemp(prefix="oform-fx-"))
    try:
        Backend.discover().extract(SAMPLE, work / "src", work / "t")
        form_dir = find_form_dirs(work / "src")[0]
        target.parent.mkdir(parents=True, exist_ok=True)
        encode_oform(form_dir, target)
    finally:
        shutil.rmtree(work, ignore_errors=True)
    return target


class OformCodecTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="oform-test-"))

    def tearDown(self) -> None:
        shutil.rmtree(self.tmp, ignore_errors=True)

    def roundtrip(self, source: Path):
        from one_c_form_compiler.core.oform import decode_oform, encode_oform, read_oform
        form_dir = self.tmp / "decoded"
        decode_oform(source, form_dir)
        out = self.tmp / "out.oform"
        encode_oform(form_dir, out)
        return read_oform(source), read_oform(out), form_dir

    @unittest.skipUnless(HAS_SAMPLE, SAMPLE_REASON)
    def test_roundtrip_sample(self) -> None:
        source = make_sample_oform(self.tmp / "fx" / "Form.oform")
        before, after, form_dir = self.roundtrip(source)
        self.assertEqual(before.form, after.form)
        self.assertEqual(before.module, after.module)
        for name in ("Form.json", "Form.elem.json", "Form.obj.bsl"):
            self.assertTrue((form_dir / name).is_file(), name)

    @unittest.skipUnless(HAS_V8UNPACK and EXTERNAL_FORM is not None and EXTERNAL_FORM.is_file(),
                         "set ONE_C_FORM_TEST_EDT_FORM to a Form.oform of an EDT project to run this check")
    def test_roundtrip_bytes_equal_on_external_form(self) -> None:
        before, after, form_dir = self.roundtrip(EXTERNAL_FORM)
        self.assertEqual(before.form, after.form)
        self.assertEqual(before.module, after.module)
        from one_c_form_compiler.core.ordinary_form import OrdinaryForm
        self.assertGreater(len(OrdinaryForm.load(form_dir).element_keys()), 0)

    def test_crcrlf_base64_preserved(self) -> None:
        from one_c_form_compiler.core.oform import _restore_base64_breaks
        original = b'{1,{#base64:AAA\r\r\nBBB\r\r\nCC==},"x\r\ny"}'
        rebuilt = b'{1,{#base64:AAA\r\nBBB\r\nCC==},"x\r\ny"}'
        self.assertEqual(_restore_base64_breaks(rebuilt), original)

    @unittest.skipUnless(HAS_SAMPLE, SAMPLE_REASON)
    def test_module_line_endings_preserved_after_model_edit(self) -> None:
        from one_c_form_compiler.core.oform import decode_oform, encode_oform, read_oform
        source = make_sample_oform(self.tmp / "fx" / "Form.oform")
        form_dir = self.tmp / "decoded"
        decode_oform(source, form_dir)
        module = form_dir / "Form.obj.bsl"
        text = module.read_text(encoding="utf-8-sig")
        with open(module, "w", encoding="utf-8-sig", newline="") as stream:
            stream.write(text + "\nПроцедура Х()\nКонецПроцедуры\n")
        out = self.tmp / "out.oform"
        encode_oform(form_dir, out)
        rebuilt = read_oform(out).module
        self.assertIn("Процедура Х()\r\nКонецПроцедуры".encode("utf-8"), rebuilt)
        self.assertNotIn(b"\r\r\n", rebuilt)

    def test_not_container_raises(self) -> None:
        from one_c_form_compiler.core.oform import OformError, read_oform
        junk = self.tmp / "Form.oform"
        junk.write_bytes(b"not a container at all" * 10)
        with self.assertRaises(OformError) as ctx:
            read_oform(junk)
        self.assertEqual(ctx.exception.code, "E_OFORM_FORMAT")

    def test_managed_form_raises(self) -> None:
        from one_c_form_compiler.core.oform import OformError, read_oform
        managed = self.tmp / "Form.form"
        managed.write_text('<?xml version="1.0" encoding="UTF-8"?>\n<form:Form xmlns:form="http://g5.1c.ru/v8/dt/form"/>',
                           encoding="utf-8")
        with self.assertRaises(OformError) as ctx:
            read_oform(managed)
        self.assertEqual(ctx.exception.code, "E_OFORM_FORMAT")


if __name__ == "__main__":
    unittest.main()
