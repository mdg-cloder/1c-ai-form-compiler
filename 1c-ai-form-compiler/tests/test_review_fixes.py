"""Regression tests: base64 line breaks, CLI project mode, CLI error contract, probe compatibility,
standard attributes."""

from __future__ import annotations

import io
import json
import shutil
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from tests.test_edt_metadata import make_project
from tests.test_oform import HAS_SAMPLE, SAMPLE_REASON, make_sample_oform
from tests.test_table_field import PAGE, PANEL


class CliErrorContractTests(unittest.TestCase):
    """Error codes and exit codes that do not need the sample (nothing is opened)."""

    def run_cli(self, *argv: str) -> tuple[int, str, str]:
        from one_c_form_compiler.cli import main
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), patch("sys.stderr", err):
            code = main(list(argv))
        return code, out.getvalue(), err.getvalue()

    def test_missing_plan_file(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            plan = Path(tmp) / "нет-плана.json"
            code, out, _err = self.run_cli("apply", str(Path(tmp) / "in.epf"), "--plan", str(plan), "--dry-run",
                                           "--json")
        self.assertEqual(code, 4)
        payload = json.loads(out)
        self.assertEqual((payload["status"], payload["code"]), ("error", "E_INPUT_MISSING"))
        self.assertEqual(payload["message"], f"plan file not found: {plan}")

    def test_usage_error_with_json_is_json(self) -> None:
        for argv in (("add", "--json"), ("no-such-command", "--json"), ("apply", "in.epf", "--json")):
            with self.subTest(argv=argv):
                code, out, err = self.run_cli(*argv)
                self.assertEqual(code, 2)
                payload = json.loads(out)
                self.assertEqual((payload["status"], payload["code"]), ("error", "E_USAGE"))
                self.assertTrue(payload["message"])
                self.assertEqual(err, "")

    def test_usage_error_without_json_prints_usage(self) -> None:
        code, out, err = self.run_cli("add")
        self.assertEqual(code, 2)
        self.assertEqual(out, "")
        self.assertIn("usage:", err)
        self.assertIn("error:", err)

    def test_v8unpack_failure_message_has_output_tail(self) -> None:
        from one_c_form_compiler.core import ordinary_backend
        from one_c_form_compiler.core.ordinary_backend import Backend, BackendError
        backend = Backend(("v8unpack",), "test")
        output = "\n".join(f"line {index}" for index in range(200)) + "\nValueError: bad container\n"
        result = SimpleNamespace(returncode=1, stdout=output.encode("utf-8"), stderr=b"")
        with tempfile.TemporaryDirectory() as tmp, \
                patch.object(ordinary_backend, "run_bounded_process", return_value=result):
            for step, call in (("extract", lambda: backend.extract(Path(tmp) / "a.epf", Path(tmp) / "s", Path(tmp))),
                               ("build", lambda: backend.build(Path(tmp) / "s", Path(tmp) / "b.epf", Path(tmp)))):
                with self.subTest(step=step), self.assertRaises(BackendError) as caught:
                    call()
                message = str(caught.exception)
                self.assertEqual(caught.exception.code, "E_V8_EXIT_CODE")
                self.assertTrue(message.startswith(f"v8unpack {step} failed (1): "), message)
                self.assertIn("ValueError: bad container", message)
                self.assertNotIn("line 0 ", message)                 # only the tail is kept
                self.assertNotIn("\n", message)
                self.assertEqual(step == "extract", "not a valid EPF/ERF file" in message)
        empty = SimpleNamespace(returncode=2, stdout=b"", stderr=b"")
        with tempfile.TemporaryDirectory() as tmp, \
                patch.object(ordinary_backend, "run_bounded_process", return_value=empty), \
                self.assertRaises(BackendError) as caught:
            backend.build(Path(tmp) / "s", Path(tmp) / "b.epf", Path(tmp))
        self.assertIn("no output", str(caught.exception))


class Base64BreaksTests(unittest.TestCase):
    def test_only_blocks_that_had_crcrlf_get_it_back(self) -> None:
        from one_c_form_compiler.core.oform import _base64_keys, _restore_base64_breaks
        original = b'{{#base64:AAA\r\r\nBBB==},{#base64:CCC\r\nDDD==}}'
        keys = _base64_keys(original)
        rebuilt = b'{{#base64:AAA\r\nBBB==},{#base64:CCC\r\nDDD==}}'
        self.assertEqual(_restore_base64_breaks(rebuilt, keys), original)


@unittest.skipUnless(HAS_SAMPLE, SAMPLE_REASON)
class CliProjectTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.fixture_dir = Path(tempfile.mkdtemp(prefix="review-fx-"))
        cls.fixture = make_sample_oform(cls.fixture_dir / "Form.oform")

    @classmethod
    def tearDownClass(cls) -> None:
        shutil.rmtree(cls.fixture_dir, ignore_errors=True)

    def setUp(self) -> None:
        self.root = make_project(Path(tempfile.mkdtemp(prefix="review-")))
        shutil.copyfile(self.fixture, self.root / "src/Catalogs/Номенклатура/Forms/ФормаЭлемента/Form.oform")
        self.form = ["--form", "Catalog.Номенклатура.ФормаЭлемента"]

    def tearDown(self) -> None:
        shutil.rmtree(self.root, ignore_errors=True)

    def run_cli(self, *argv) -> int:
        from one_c_form_compiler.cli import main
        with redirect_stdout(io.StringIO()), patch("sys.stderr", io.StringIO()):
            return main(list(argv))

    def test_column_commands_accept_project(self) -> None:
        p = str(self.root)
        self.assertEqual(self.run_cli("add", "--project", p, *self.form, "--type", "Table", "--name", "Упаковки",
                                      "--data", "Упаковки", "--container", PANEL, "--page", PAGE), 0)
        self.assertEqual(self.run_cli("add-column", "--project", p, *self.form, "--table", "Упаковки",
                                      "--name", "Прим", "--dry-run"), 0)
        self.assertEqual(self.run_cli("modify-column", "--project", p, *self.form, "--table", "Упаковки",
                                      "--name", "ЕдиницаИзмерения", "--width", "90", "--dry-run"), 0)
        self.assertEqual(self.run_cli("remove-column", "--project", p, *self.form, "--table", "Упаковки",
                                      "--name", "ЕдиницаИзмерения", "--dry-run"), 0)

    def test_project_equals_syntax(self) -> None:
        self.assertEqual(self.run_cli("title", f"--project={self.root}", *self.form, "Новый заголовок",
                                      "--dry-run"), 0)

    def test_malformed_cell_gives_error_code_not_traceback(self) -> None:
        from one_c_form_compiler.core import table_field as tf
        from one_c_form_compiler.core.ordinary_form import FormModelError
        props = [None] * 40
        props[tf.P_CELL] = [["#base64:" + "QUJD"], "0"]
        with self.assertRaises(FormModelError) as ctx:
            tf.decode_cell(props)
        self.assertEqual(ctx.exception.code, "E_TABLE_FORMAT")


class ProbeCompatibilityTests(unittest.TestCase):
    def test_probe_avoids_83_only_string_functions(self) -> None:
        from one_c_form_compiler.core.runtime_check import config_probe_module, inject_probe
        for text in (config_probe_module("Catalog.А.Ф"), inject_probe("Процедура ПриОткрытии()\nКонецПроцедуры\n")):
            for name in ("СтрРазделить", "СтрСоединить", "СтрНайти", "СтрШаблон"):
                self.assertNotIn(name, text)
            self.assertIn('ПробаИменаСобытий.Добавить("ПриИзменении");', text)


class StandardAttributesTests(unittest.TestCase):
    def setUp(self) -> None:
        self.root = make_project(Path(tempfile.mkdtemp(prefix="review-md-")))

    def tearDown(self) -> None:
        shutil.rmtree(self.root, ignore_errors=True)

    def test_parent_and_owner_only_when_object_has_them(self) -> None:
        from one_c_form_compiler.core.edt_metadata import read_object_metadata
        flat = read_object_metadata(self.root / "src/Catalogs/ЕдиницыИзмерения/ЕдиницыИзмерения.mdo", self.root)
        self.assertNotIn("Родитель", flat.attributes)
        self.assertNotIn("Владелец", flat.attributes)
        mdo = self.root / "src/Catalogs/Номенклатура/Номенклатура.mdo"
        mdo.write_text(mdo.read_text(encoding="utf-8").replace(
            "<codeLength>", "<hierarchical>true</hierarchical><owners>Catalog.ЕдиницыИзмерения</owners><codeLength>"),
            encoding="utf-8")
        meta = read_object_metadata(mdo, self.root)
        self.assertEqual(meta.attributes["Родитель"].uuid, "-4")
        self.assertEqual(meta.attributes["Родитель"].pattern, ['"Pattern"', ['"#"', "aaaaaaaa-0000-0000-0000-000000000003"]])
        self.assertEqual(meta.attributes["Владелец"].uuid, "-5")
        self.assertEqual(meta.attributes["Владелец"].pattern, ['"Pattern"', ['"#"', "ffffffff-0000-0000-0000-000000000003"]])


if __name__ == "__main__":
    unittest.main()
