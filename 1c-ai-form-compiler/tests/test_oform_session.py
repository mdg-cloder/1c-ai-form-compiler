"""Editing configuration ordinary forms (Form.oform) in an EDT project."""

from __future__ import annotations

import hashlib
import io
import json
import shutil
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch

from tests.test_edt_metadata import make_project
from tests.test_oform import HAS_SAMPLE, SAMPLE_REASON, make_sample_oform

FQN = "Catalog.Номенклатура.ФормаЭлемента"


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


@unittest.skipUnless(HAS_SAMPLE, SAMPLE_REASON)
class OformSessionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.fixture_dir = Path(tempfile.mkdtemp(prefix="oform-sess-fx-"))
        cls.fixture = make_sample_oform(cls.fixture_dir / "Form.oform")

    @classmethod
    def tearDownClass(cls) -> None:
        shutil.rmtree(cls.fixture_dir, ignore_errors=True)

    def setUp(self) -> None:
        self.root = make_project(Path(tempfile.mkdtemp(prefix="oform-sess-")))
        self.form_path = self.root / "src/Catalogs/Номенклатура/Forms/ФормаЭлемента/Form.oform"
        shutil.copyfile(self.fixture, self.form_path)

    def tearDown(self) -> None:
        shutil.rmtree(self.root, ignore_errors=True)

    def session(self):
        from one_c_form_compiler.core.oform_session import OformSession
        return OformSession(self.form_path)

    def add_label(self, session, name="НадписьТест"):
        from one_c_form_compiler.core.edit_plan import apply_plan
        return apply_plan(session.form(), {"operations": [{"op": "add", "type": "Label", "name": name,
                                                           "caption": "Тест"}]})

    def test_add_label_in_place_creates_backup(self) -> None:
        before = sha(self.form_path)
        with self.session() as session:
            self.add_label(session)
            result = session.build_and_publish(None)
        self.assertNotEqual(sha(self.form_path), before)
        backup = Path(result["backup"])
        self.assertTrue(backup.is_file())
        self.assertEqual(sha(backup), before)
        self.assertNotIn(self.root / "src", backup.parents)  # EDT must not see the backup as a source
        self.assertIn("refresh", result["refresh_hint"])
        with self.session() as session:
            self.assertTrue(session.form().has_element("НадписьТест"))

    def test_output_elsewhere_keeps_input(self) -> None:
        before = sha(self.form_path)
        out = self.root / "out.oform"
        with self.session() as session:
            self.add_label(session)
            session.build_and_publish(out)
        self.assertEqual(sha(self.form_path), before)
        self.assertTrue(out.is_file())

    def test_rerun_same_plan_fails_and_keeps_file(self) -> None:
        from one_c_form_compiler.core.edit_plan import PlanError
        with self.session() as session:
            self.add_label(session)
            session.build_and_publish(None)
        after_first = sha(self.form_path)
        with self.session() as session:
            with self.assertRaises(PlanError) as ctx:
                self.add_label(session)
        self.assertEqual(ctx.exception.code, "E_NAME_EXISTS")
        self.assertEqual(sha(self.form_path), after_first)

    def test_external_change_detected(self) -> None:
        from one_c_form_compiler.core.ordinary_backend import BackendError
        with self.session() as session:
            self.add_label(session)
            self.form_path.write_bytes(self.form_path.read_bytes() + b" ")
            changed = sha(self.form_path)
            with self.assertRaises(BackendError) as ctx:
                session.build_and_publish(None)
        self.assertEqual(ctx.exception.code, "E_INPUT_CHANGED")
        self.assertEqual(sha(self.form_path), changed)

    def test_bind_config_attribute(self) -> None:
        from one_c_form_compiler.core.edit_plan import apply_plan
        with self.session() as session:
            form = session.form()
            apply_plan(form, {"operations": [
                {"op": "add", "type": "Field", "name": "АртикулПоле", "data": "Артикул"},
                {"op": "add", "type": "Field", "name": "НаименованиеПоле", "data": "Наименование"}]})
            records = {entry[0]: entry[1] for entry in form.datasources[1:]}
            phone = form.data[form.find("АртикулПоле")]["raw"][1]
            name = form.data[form.find("НаименованиеПоле")]["raw"][1]
            main = form._main_prop()["id"]
            self.assertEqual(records[phone], ["2", [main], ["0", "bbbbbbbb-0000-0000-0000-000000000001"]])
            self.assertEqual(records[name], ["2", [main], ["-3"]])
            self.assertEqual(form.data[form.find("АртикулПоле")]["raw"][2][1], ['"Pattern"', ['"S"', "50", "1"]])
            session.build_and_publish(None)
        with self.session() as session:
            info = session.form().info(session.form().find("НаименованиеПоле"))
            self.assertEqual(info.data, "Наименование")

    def test_cli_project_form_fqn(self) -> None:
        from one_c_form_compiler.cli import main
        buffer = io.StringIO()
        with redirect_stdout(buffer):
            code = main(["inspect", "--project", str(self.root), "--form", FQN, "--json"])
        self.assertEqual(code, 0, buffer.getvalue())
        payload = json.loads(buffer.getvalue())
        self.assertTrue(payload["elements"])
        self.assertIn("Артикул", [a["name"] for a in payload["object_attributes"]])

    def test_cli_apply_in_place_prints_refresh_hint(self) -> None:
        from one_c_form_compiler.cli import main
        plan = self.root / "plan.json"
        plan.write_text(json.dumps({"operations": [{"op": "add", "type": "Label", "name": "Н1", "caption": "x"}]}),
                        encoding="utf-8")
        buffer = io.StringIO()
        with redirect_stdout(buffer):
            code = main(["apply", str(self.form_path), "--plan", str(plan)])
        self.assertEqual(code, 0, buffer.getvalue())
        self.assertIn("refresh", buffer.getvalue())

    def test_cli_dry_run_writes_nothing(self) -> None:
        from one_c_form_compiler.cli import main
        before = sha(self.form_path)
        with redirect_stdout(io.StringIO()):
            code = main(["add", "--project", str(self.root), "--form", FQN, "--type", "Label", "--name", "Н2",
                         "--caption", "x", "--dry-run"])
        self.assertEqual(code, 0)
        self.assertEqual(sha(self.form_path), before)

    def test_new_row_goes_above_docked_bottom_command_bar(self) -> None:
        # The sample keeps the default ОсновныеДействияФормы command bar at the bottom of the form.
        from one_c_form_compiler.core.edit_plan import apply_plan
        with self.session() as session:
            form = session.form()
            footer_before = form.info(form.find("ОсновныеДействияФормы")).rect
            gap_before = form.size[1] - footer_before.bottom
            apply_plan(form, {"operations": [{"op": "add", "type": "Field", "name": "Т1", "label": "Арт.:",
                                              "data": "Артикул"}]})
            footer = form.info(form.find("ОсновныеДействияФормы")).rect
            field = form.info(form.find("Т1")).rect
            self.assertLessEqual(field.bottom, footer.top)
            self.assertEqual(form.size[1] - footer.bottom, gap_before)
            self.assertEqual(footer.height, footer_before.height)
            self.assertFalse(form.new_issues())

    def test_cli_managed_form_refused(self) -> None:
        from one_c_form_compiler.cli import main
        err = io.StringIO()
        with redirect_stdout(io.StringIO()), patch("sys.stderr", err):
            code = main(["inspect", "--project", str(self.root), "--form", "Catalog.Номенклатура.ФормаУправляемая"])
        self.assertEqual(code, 3)
        self.assertIn("E_FORM_MANAGED", err.getvalue())


if __name__ == "__main__":
    unittest.main()
