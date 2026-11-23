"""Byte-exact round trip of forms with constructs v8unpack 1.2.13 does not reproduce by itself.

Every variant is synthetic: built from the ordinary form of tests/fixtures/sample_ordinary.epf with one
construct changed (pages without names, too few page guides, CR CR LF in a literal, a long caption, ...).
"""

from __future__ import annotations

import json
import shutil
import tempfile
import unittest
import zlib
from pathlib import Path

from tests._support import HAS_SAMPLE, HAS_V8UNPACK, SAMPLE, SAMPLE_REASON

PANEL = "ПанельРегистров"
LONG = "_" * 73


def _build_container(target: Path, form: bytes, module: bytes) -> None:
    from v8unpack.container import Container
    with tempfile.TemporaryDirectory(prefix="oform-entries-") as tmp:
        (Path(tmp) / "form").write_bytes(form)
        (Path(tmp) / "module").write_bytes(module)
        target.parent.mkdir(parents=True, exist_ok=True)
        with open(target, "wb") as stream:
            Container().build(stream, tmp, nested=True)


def _replace_once(data: bytes, old: str, new: str) -> bytes:
    old_bytes = old.encode("utf-8")
    assert data.count(old_bytes) == 1, f"{old} occurs {data.count(old_bytes)} times"
    return data.replace(old_bytes, new.encode("utf-8"))


class WriterTests(unittest.TestCase):
    """The brace writer alone (no sample needed)."""

    @unittest.skipUnless(HAS_V8UNPACK, "v8unpack is not installed")
    def test_long_quoted_string_is_not_wrapped(self) -> None:
        from one_c_form_compiler.core.oform import read_brace, write_brace
        text = b'\xef\xbb\xbf{1,"' + LONG.encode() + b'",' + b'"' + b"\xd0\x96" * 70 + b'"}'
        self.assertEqual(write_brace(read_brace(text)), text)

    @unittest.skipUnless(HAS_V8UNPACK, "v8unpack is not installed")
    def test_cr_cr_lf_and_bare_cr_in_literal_kept(self) -> None:
        from one_c_form_compiler.core.oform import read_brace, write_brace
        text = b'\xef\xbb\xbf{1,\r\n{"a\r\r\nb","c\rd","e\r\nf"}\r\n}'
        data = read_brace(text)
        self.assertEqual(data[0][1], ['"a\r\nb"', '"c\rd"', '"e\nf"'])
        self.assertEqual(write_brace(data), text)

    @unittest.skipUnless(HAS_V8UNPACK, "v8unpack is not installed")
    def test_base64_block_line_breaks(self) -> None:
        from one_c_form_compiler.core.oform import read_brace, write_brace
        full = "A" * 64
        # Partial last line: no break before "}"; full last line: the platform's usual break.
        partial = f"{{0,0,\r\n{{#base64:{full}\r\n{full}\r\nAAAA}}\r\n}}".encode()
        self.assertEqual(write_brace(read_brace(b"\xef\xbb\xbf" + partial)), b"\xef\xbb\xbf" + partial)
        with_break = f"{{0,0,\r\n{{#base64:{full}\r\n{full}\r\n}}\r\n}}".encode()
        self.assertEqual(write_brace(read_brace(b"\xef\xbb\xbf" + with_break)), b"\xef\xbb\xbf" + with_break)

    def test_base64_full_last_line_without_break_restored(self) -> None:
        from one_c_form_compiler.core.oform import _base64_nobreak_keys, _restore_base64_endings
        full = "A" * 64
        original = f'{{1,{{#base64:{full}\r\n{full}}},{{#base64:{"B" * 64}\r\n{"B" * 64}\r\n}}}}'.encode()
        written = f'{{1,{{#base64:{full}\r\n{full}\r\n}},{{#base64:{"B" * 64}\r\n{"B" * 64}\r\n}}}}'.encode()
        keys = _base64_nobreak_keys(original)
        self.assertEqual(len(keys), 1)                      # only the block without the break is listed
        self.assertEqual(_restore_base64_endings(written, keys), original)


@unittest.skipUnless(HAS_SAMPLE, SAMPLE_REASON)
class OformVariantTests(unittest.TestCase):
    base: Path
    root: Path

    @classmethod
    def setUpClass(cls) -> None:
        from one_c_form_compiler.core.oform import decode_oform
        from one_c_form_compiler.core.ordinary_form import OrdinaryForm
        from tests.test_oform import make_sample_oform
        cls.root = Path(tempfile.mkdtemp(prefix="oform-robust-"))
        sample = make_sample_oform(cls.root / "sample" / "Форма" / "Form.oform")
        cls.base = cls.root / "base" / "Форма"
        decode_oform(sample, cls.base)
        form = OrdinaryForm.load(cls.base)
        form.save()                     # with the load-time repair of the sample (a stale anchor) written

    @classmethod
    def tearDownClass(cls) -> None:
        shutil.rmtree(cls.root, ignore_errors=True)

    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="oform-robust-case-"))

    def tearDown(self) -> None:
        shutil.rmtree(self.tmp, ignore_errors=True)

    # ------------------------------------------------------------------ helpers
    def variant(self, mutate_json=None, mutate_bytes=None) -> Path:
        """Form.oform built from the cleaned sample with JSON and/or byte level changes."""
        from one_c_form_compiler.core.oform import encode_oform, read_oform
        work = self.tmp / "variant" / "Форма"
        shutil.copytree(self.base, work)
        if mutate_json:
            form_json = json.loads((work / "Form.json").read_text(encoding="utf-8"))
            elem_json = json.loads((work / "Form.elem.json").read_text(encoding="utf-8"))
            mutate_json(form_json, elem_json)
            (work / "Form.json").write_text(json.dumps(form_json, ensure_ascii=False), encoding="utf-8")
            (work / "Form.elem.json").write_text(json.dumps(elem_json, ensure_ascii=False), encoding="utf-8")
        target = self.tmp / "Forms" / "Форма" / "Form.oform"
        target.parent.mkdir(parents=True)
        encode_oform(work, target)
        if mutate_bytes:
            parts = read_oform(target)
            _build_container(target, mutate_bytes(parts.form), parts.module)
        return target

    def assert_editable(self, source: Path) -> None:
        """Opens; a no-op build is byte-identical; set_title changes only the title."""
        from one_c_form_compiler.core.oform import read_oform
        from one_c_form_compiler.core.oform_session import OformSession
        original = read_oform(source)
        out = self.tmp / "noop" / "Form.oform"
        out.parent.mkdir()
        with OformSession(source) as session:
            self.assertEqual(session.form().repairs, [])
            session.build_and_publish(out)
        rebuilt = read_oform(out)
        self.assertEqual(rebuilt.form, original.form)
        self.assertEqual(rebuilt.module, original.module)

        titled = self.tmp / "title" / "Form.oform"
        titled.parent.mkdir()
        with OformSession(source) as session:
            form = session.form()
            old = dict(form.title)
            form.set_title({lang: "Новый заголовок" for lang in old if lang != "#"})
            session.build_and_publish(titled)
        expected = original.form
        for lang, text in old.items():
            if lang != "#":
                expected = expected.replace(f'{{"{lang}","{text}"}}'.encode("utf-8").replace(b"\n", b"\r\n"),
                                            f'{{"{lang}","Новый заголовок"}}'.encode("utf-8"), 1)
        self.assertNotEqual(expected, original.form)
        self.assertEqual(read_oform(titled).form, expected)

    def assert_refused(self, source: Path, code: str) -> None:
        from one_c_form_compiler.core.ordinary_backend import BackendError
        from one_c_form_compiler.core.oform_session import OformSession
        with self.assertRaises(BackendError) as caught:
            with OformSession(source):
                pass
        self.assertEqual(caught.exception.code, code, str(caught.exception))

    @staticmethod
    def panel_pages(elem_json: dict) -> tuple[str, list[str]]:
        key = next(k for k in elem_json["data"] if k.endswith("/" + PANEL))
        return key, elem_json["data"][f"{key}/-pages-"]

    # ------------------------------------------------------------------ variants that must work
    def test_control_variant(self) -> None:
        self.assert_editable(self.variant())

    def test_pages_without_names(self) -> None:
        from one_c_form_compiler.core.oform import decode_oform
        from one_c_form_compiler.core.oform_session import OformSession

        def mutate(form_json, elem_json):
            key, pages = self.panel_pages(elem_json)
            for page in pages[:2]:
                elem_json["data"][f"{key}/{page}"]["raw"][6] = '""'
        source = self.variant(mutate)
        decode_oform(source, self.tmp / "decoded")
        _key, pages = self.panel_pages(json.loads((self.tmp / "decoded" / "Form.elem.json").read_text("utf-8")))
        self.assertEqual(pages[:2], ["#0", "#1"])
        self.assert_editable(source)
        out = self.tmp / "added" / "Form.oform"
        out.parent.mkdir()
        with OformSession(source) as session:
            form = session.form()
            info = form.add_element(element_type="Label", name="НадписьНаБезымяннойСтранице",
                                    rect=form.place("Label", container=PANEL, page="#0"), container=PANEL, page="#0")
            self.assertEqual(info.page, "#0")
            session.build_and_publish(out)
        with OformSession(out) as session:          # reopens: the guard passes on the result
            names = [c.name for c in session.form().controls() if c.page == "#0"]
        self.assertIn("НадписьНаБезымяннойСтранице", names)

    def test_fewer_guides_than_four_per_page(self) -> None:
        from one_c_form_compiler.core.oform_session import OformSession

        def mutate(form_json, elem_json):
            key, pages = self.panel_pages(elem_json)
            first = elem_json["data"][f"{key}/{pages[0]}"]
            first["info"] = first["info"][:4]
            for page in pages[1:]:
                elem_json["data"][f"{key}/{page}"]["info"] = []
        source = self.variant(mutate)
        self.assert_editable(source)
        out = self.tmp / "page" / "Form.oform"
        out.parent.mkdir()
        with OformSession(source) as session:
            session.form().add_page(PANEL, "НоваяСтраница")
            session.build_and_publish(out)
        with OformSession(out) as session:
            self.assertIn("НоваяСтраница", session.form().containers()[next(
                k for k in session.form().containers() if k.endswith(PANEL))].pages)

    def test_cr_cr_lf_in_title(self) -> None:
        from one_c_form_compiler.core.oform import read_oform

        def mutate(form_json, elem_json):
            form_json["form"][0][0][1][1][0][2][1] = '"Строка1\r\nСтрока2"'
        source = self.variant(mutate)
        self.assertIn("Строка1\r\r\nСтрока2".encode("utf-8"), read_oform(source).form)
        self.assert_editable(source)

    def test_long_caption_written_on_one_line(self) -> None:
        from one_c_form_compiler.core.oform import read_oform
        from one_c_form_compiler.core.oform_session import OformSession

        def mutate(form_json, elem_json):
            form_json["form"][0][0][1][1][0][2][1] = f'"{LONG}"'
        source = self.variant(mutate)
        self.assertIn(f'"{LONG}"'.encode(), read_oform(source).form)
        self.assert_editable(source)
        out = self.tmp / "long" / "Form.oform"
        out.parent.mkdir()
        caption = "Ж" * 80
        with OformSession(source) as session:
            form = session.form()
            form.add_element(element_type="Label", name="НадписьДлинная", rect=form.place("Label"), caption=caption)
            session.build_and_publish(out)
        self.assertIn(f'"{caption}"'.encode("utf-8"), read_oform(out).form)

    # ------------------------------------------------------------------ variants that are refused
    def test_duplicate_names_refused(self) -> None:
        source = self.variant(mutate_bytes=lambda form: _replace_once(form, '"Надпись2"', '"Надпись1"'))
        self.assert_refused(source, "E_FORM_DUPLICATE_NAMES")

    def test_empty_name_command_panel_refused(self) -> None:
        source = self.variant(mutate_bytes=lambda form: _replace_once(form, '"ДействияФормы"', '""'))
        self.assert_refused(source, "E_FORM_EMPTY_NAME")

    def test_attribute_name_not_a_string_refused(self) -> None:
        # A shorter attribute record: v8unpack takes the name from the fifth item, here the value type.
        def mutate(form_json, elem_json):
            raw = elem_json["props"][0]["raw"]
            elem_json["props"][0]["raw"] = raw[:1] + raw[2:]
        self.assert_refused(self.variant(mutate), "E_FORM_PROP_NAME")


@unittest.skipUnless(HAS_SAMPLE, SAMPLE_REASON)
class EpfGuardTests(unittest.TestCase):
    """EPF/ERF: v8unpack runs as a subprocess without these fixes, so such forms are refused."""

    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="epf-robust-"))

    def tearDown(self) -> None:
        shutil.rmtree(self.tmp, ignore_errors=True)

    def epf_variant(self, mutate) -> Path:
        from v8unpack.container import Container
        top = self.tmp / "top"
        with open(SAMPLE, "rb") as stream:
            reader = Container()
            reader.read(stream)
            reader.extract(str(top), deflate=True, recursive=False)
        form_entry = next(p for p in top.iterdir() if p.name.endswith(".0") and p.read_bytes()[:4] == b"\xff\xff\xff\x7f"
                          and self._has_form(p))
        inner = self.tmp / "inner"
        with open(form_entry, "rb") as stream:
            reader = Container()
            reader.read(stream)
            reader.extract(str(inner), deflate=False, recursive=False)
        (inner / "form").write_bytes(mutate((inner / "form").read_bytes()))
        _build_container(form_entry, (inner / "form").read_bytes(), (inner / "module").read_bytes())
        packed = self.tmp / "packed"            # top-level entries are raw-deflated, as in the sample
        packed.mkdir()
        for entry in top.iterdir():
            compressor = zlib.compressobj(9, zlib.DEFLATED, -15)
            (packed / entry.name).write_bytes(compressor.compress(entry.read_bytes()) + compressor.flush())
        target = self.tmp / "variant.epf"
        with open(target, "wb") as stream:
            Container().build(stream, str(packed), nested=True)
        return target

    @staticmethod
    def _has_form(path: Path) -> bool:
        from v8unpack.container import Container
        with open(path, "rb") as stream:
            reader = Container()
            reader.read(stream)
            return "form" in reader.files

    def open_form(self, source: Path):
        from one_c_form_compiler.core.ordinary_backend import Backend, EditSession
        with EditSession(source, Backend.discover()) as session:
            return session.form()

    def test_unchanged_sample_opens(self) -> None:
        self.assertGreater(len(self.open_form(self.epf_variant(lambda form: form)).element_keys()), 0)

    def test_cr_cr_lf_literal_refused(self) -> None:
        from one_c_form_compiler.core.ordinary_backend import BackendError
        source = self.epf_variant(lambda form: _replace_once(form, '"Корректировка записей регистров"',
                                                             '"Строка1\r\r\nСтрока2"'))
        with self.assertRaises(BackendError) as caught:
            self.open_form(source)
        self.assertEqual(caught.exception.code, "E_FORM_ROUNDTRIP", str(caught.exception))

    def test_pages_without_names_refused(self) -> None:
        from one_c_form_compiler.core.ordinary_backend import BackendError

        def mutate(form: bytes) -> bytes:
            form = _replace_once(form, '"РегистрыБухгалтерии"', '""')
            return _replace_once(form, '"РегистрыНакопления"', '""')
        with self.assertRaises(BackendError) as caught:
            self.open_form(self.epf_variant(mutate))
        self.assertIn(caught.exception.code, {"E_FORM_DUPLICATE_PAGES", "E_FORM_ROUNDTRIP"}, str(caught.exception))


if __name__ == "__main__":
    unittest.main()
