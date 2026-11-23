"""Table fields: adding a table bound to a tabular section, columns, events."""

from __future__ import annotations

import shutil
import tempfile
import unittest
from pathlib import Path

from tests.test_edt_metadata import make_project
from tests.test_oform import HAS_SAMPLE, SAMPLE_REASON, make_sample_oform

# Top-level panel of the sample and one of its pages. The page is filled by a nested panel, so a new
# table goes below it and the panel (and the form) grows.
PANEL = "ПанельРегистров"
PAGE = "РегистрыСведений"
# A nested panel page of the sample with free room below its two labels (no growth needed).
NESTED_PANEL = "ПанельРегистровСведений"
NESTED_PAGE = "Подсказка"
SECTION_UUID = "cccccccc-0000-0000-0000-000000000001"
SECTION_TYPE = "cccccccc-0000-0000-0000-000000000002"


class WidthFormatTests(unittest.TestCase):
    def test_platform_number_format(self) -> None:
        from one_c_form_compiler.core.table_field import format_width, parse_width
        self.assertEqual(format_width(100), "1e2")
        self.assertEqual(format_width(120), "1.2e2")
        self.assertEqual(format_width(123), "123")
        self.assertEqual(format_width(80), "8e1")
        self.assertEqual(format_width(250), "2.5e2")
        self.assertEqual(parse_width("1.2e2"), 120)
        self.assertEqual(parse_width("112.2807017543861"), 112)


@unittest.skipUnless(HAS_SAMPLE, SAMPLE_REASON)
class TableTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.fixture_dir = Path(tempfile.mkdtemp(prefix="table-fx-"))
        cls.fixture = make_sample_oform(cls.fixture_dir / "Form.oform")

    @classmethod
    def tearDownClass(cls) -> None:
        shutil.rmtree(cls.fixture_dir, ignore_errors=True)

    def setUp(self) -> None:
        from one_c_form_compiler.core.oform_session import OformSession
        self.root = make_project(Path(tempfile.mkdtemp(prefix="table-")))
        self.form_path = self.root / "src/Catalogs/Номенклатура/Forms/ФормаЭлемента/Form.oform"
        shutil.copyfile(self.fixture, self.form_path)
        self.session = OformSession(self.form_path).__enter__()
        self.form = self.session.form()

    def tearDown(self) -> None:
        self.session.__exit__(None, None, None)
        shutil.rmtree(self.root, ignore_errors=True)

    def apply(self, *operations):
        from one_c_form_compiler.core.edit_plan import apply_plan
        return apply_plan(self.form, {"operations": list(operations)})

    def columns(self, table="Упаковки"):
        from one_c_form_compiler.core.table_field import list_columns
        return list_columns(self.form, self.form.find(table))

    def add_table(self, **extra):
        return self.apply({"op": "add", "type": "Table", "name": "Упаковки", "data": "Упаковки",
                           "container": PANEL, "page": PAGE, **extra})

    def test_add_table_bound_to_tabular_section(self) -> None:
        result = self.add_table()
        key = self.form.find("Упаковки")
        raw = self.form.data[key]["raw"]
        main = self.form._main_prop()["id"]
        record = next(entry for entry in self.form.datasources[1:] if entry[0] == raw[1])
        self.assertEqual(record, [raw[1], ["2", [main], ["0", SECTION_UUID]]])
        self.assertEqual(raw[2][1], ['"Pattern"', ['"#"', SECTION_TYPE]])
        self.assertEqual([c.name for c in self.columns()], ["НомерСтроки", "Коэффициент", "ЕдиницаИзмерения"])
        self.assertEqual([c.data for c in self.columns()], ["НомерСтроки", "Коэффициент", "ЕдиницаИзмерения"])
        self.assertEqual(result[-1]["data"], "Упаковки")
        self.assertEqual([c["name"] for c in result[-1]["columns"]], ["НомерСтроки", "Коэффициент", "ЕдиницаИзмерения"])
        self.assertFalse(any(c.events for c in self.columns()))
        self.assertFalse(self.form.new_issues())

    def test_columns_subset_and_free_column(self) -> None:
        self.add_table(columns=["ЕдиницаИзмерения", "Примечание"])
        columns = self.columns()
        self.assertEqual([c.name for c in columns], ["ЕдиницаИзмерения", "Примечание"])
        self.assertEqual([c.data for c in columns], ["ЕдиницаИзмерения", None])

    def test_add_column_after(self) -> None:
        self.add_table(columns=["НомерСтроки", "ЕдиницаИзмерения"])
        self.apply({"op": "add_column", "table": "Упаковки", "name": "Коэффициент", "data": "Коэффициент",
                    "caption": "Коэф.", "width": 60, "after": "НомерСтроки"})
        columns = self.columns()
        self.assertEqual([c.name for c in columns], ["НомерСтроки", "Коэффициент", "ЕдиницаИзмерения"])
        self.assertEqual(columns[1].caption.get("ru"), "Коэф.")
        self.assertEqual(columns[1].width, 60)

    def test_modify_column_caption_width(self) -> None:
        self.add_table()
        self.apply({"op": "modify_column", "table": "Упаковки", "name": "ЕдиницаИзмерения", "caption": "Единица хранения",
                    "width": 120})
        from one_c_form_compiler.core import table_field as tf
        _index, column = tf.find_column(self.form, self.form.find("Упаковки"), "ЕдиницаИзмерения")
        self.assertEqual(tf.column_props(column)[tf.P_WIDTH], "1.2e2")
        self.assertEqual(self.columns()[2].caption.get("ru"), "Единица хранения")

    def test_remove_column_referenced_in_module_requires_force(self) -> None:
        from one_c_form_compiler.core.edit_plan import PlanError
        self.add_table()
        self.form.module_text += "\r\n// ЭлементыФормы.Упаковки.Колонки.ЕдиницаИзмерения.Видимость = Ложь;\r\n"
        with self.assertRaises(PlanError) as ctx:
            self.apply({"op": "remove_column", "table": "Упаковки", "name": "ЕдиницаИзмерения"})
        self.assertEqual(ctx.exception.code, "E_ELEMENT_REFERENCED")
        self.apply({"op": "remove_column", "table": "Упаковки", "name": "ЕдиницаИзмерения", "force": True})
        self.assertEqual([c.name for c in self.columns()], ["НомерСтроки", "Коэффициент"])

    def test_table_unknown_tabular_section(self) -> None:
        from one_c_form_compiler.core.edit_plan import PlanError
        with self.assertRaises(PlanError) as ctx:
            self.apply({"op": "add", "type": "Table", "name": "Т", "data": "НетТакой"})
        self.assertEqual(ctx.exception.code, "E_DATA_UNKNOWN")
        self.assertIn("Упаковки", str(ctx.exception))

    def test_table_requires_data(self) -> None:
        from one_c_form_compiler.core.edit_plan import PlanError
        with self.assertRaises(PlanError) as ctx:
            self.apply({"op": "add", "type": "Table", "name": "Т"})
        self.assertEqual(ctx.exception.code, "E_DATA_REQUIRED")

    def test_column_unknown_attribute(self) -> None:
        from one_c_form_compiler.core.edit_plan import PlanError
        self.add_table()
        with self.assertRaises(PlanError) as ctx:
            self.apply({"op": "add_column", "table": "Упаковки", "name": "Х", "data": "Нет"})
        self.assertEqual(ctx.exception.code, "E_DATA_UNKNOWN")

    def test_table_events_stubs(self) -> None:
        from one_c_form_compiler.core.table_field import table_events
        self.add_table(events={"ПриВыводеСтроки": "УпаковкиПриВыводеСтроки",
                               "ПередНачаломДобавления": "УпаковкиПередНачаломДобавления"})
        self.apply({"op": "modify_column", "table": "Упаковки", "name": "ЕдиницаИзмерения",
                    "events": {"ПриИзменении": "УпаковкиЕдиницаИзмеренияПриИзменении"}})
        key = self.form.find("Упаковки")
        self.assertEqual(table_events(self.form, key), {"ПередНачаломДобавления": "УпаковкиПередНачаломДобавления",
                                                        "ПриВыводеСтроки": "УпаковкиПриВыводеСтроки"})
        # Event ids as stored by 1C Designer in table fields of typical configurations.
        self.assertEqual([entry[0] for entry in self.form.data[key]["raw"][2][4][1:]], ["40", "47"])
        from one_c_form_compiler.core import table_field as tf
        _index, column = tf.find_column(self.form, key, "ЕдиницаИзмерения")
        cell = tf.decode_cell(tf.column_props(column))
        self.assertEqual([entry[0] for entry in cell[tf.CELL_EVENT_SLOT][1:]], ["2147483647"])
        self.assertEqual(self.columns()[2].events, {"ПриИзменении": "УпаковкиЕдиницаИзмеренияПриИзменении"})
        module = self.form.module_text
        self.assertIn("Процедура УпаковкиПриВыводеСтроки(Элемент, ОформлениеСтроки, ДанныеСтроки)", module)
        self.assertIn("Процедура УпаковкиПередНачаломДобавления(Элемент, Отказ, Копирование)", module)
        self.assertIn("Процедура УпаковкиЕдиницаИзмеренияПриИзменении(Элемент)", module)

    def test_unsupported_events(self) -> None:
        from one_c_form_compiler.core.edit_plan import PlanError
        with self.assertRaises(PlanError) as ctx:
            self.add_table(events={"Нажатие": "Х"})
        self.assertEqual(ctx.exception.code, "E_HANDLER_UNSUPPORTED")

    def test_table_on_full_panel_page_grows_panel(self) -> None:
        from one_c_form_compiler.core.ordinary_form import FormModelError
        with self.assertRaises(FormModelError) as caught:          # the page has no free room
            self.form.place("Table", container=PANEL, page=PAGE, grow=False)
        self.assertEqual(caught.exception.code, "E_NO_SPACE")
        before = self.form.size
        panel_before = self.form.info(self.form.find(PANEL)).rect
        self.add_table(height=400)
        panel_after = self.form.info(self.form.find(PANEL)).rect
        self.assertGreater(self.form.size[1], before[1])
        self.assertEqual(self.form.size[1] - before[1], panel_after.height - panel_before.height)
        self.assertFalse(self.form.new_issues())

    def test_table_in_free_room_of_nested_page_keeps_sizes(self) -> None:
        before = self.form.size
        outer_before = self.form.info(self.form.find(PANEL)).rect
        self.apply({"op": "add", "type": "Table", "name": "Упаковки", "data": "Упаковки",
                    "container": NESTED_PANEL, "page": NESTED_PAGE, "height": 60})
        info = self.form.info(self.form.find("Упаковки"))
        self.assertEqual((info.container.split("/")[-1], info.page), (NESTED_PANEL, NESTED_PAGE))
        self.assertEqual(self.form.size, before)
        self.assertEqual(self.form.info(self.form.find(PANEL)).rect, outer_before)
        self.assertFalse(self.form.new_issues())

    def test_built_form_keeps_table(self) -> None:
        from one_c_form_compiler.core.oform_session import OformSession
        from one_c_form_compiler.core.table_field import list_columns, table_events
        self.add_table(events={"ПриВыводеСтроки": "УпаковкиПриВыводеСтроки"})
        self.apply({"op": "modify_column", "table": "Упаковки", "name": "ЕдиницаИзмерения",
                    "events": {"ПриИзменении": "УпаковкиЕдиницаИзмеренияПриИзменении"}})
        self.session.build_and_publish(None)
        with OformSession(self.form_path) as session:
            form = session.form()
            key = form.find("Упаковки")
            self.assertEqual([c.name for c in list_columns(form, key)], ["НомерСтроки", "Коэффициент", "ЕдиницаИзмерения"])
            self.assertEqual(list_columns(form, key)[2].events, {"ПриИзменении": "УпаковкиЕдиницаИзмеренияПриИзменении"})
            self.assertEqual(table_events(form, key), {"ПриВыводеСтроки": "УпаковкиПриВыводеСтроки"})
            self.assertEqual(form.info(key).data, "Упаковки")

    def test_describe_form_lists_tables(self) -> None:
        from one_c_form_compiler.core.edit_plan import describe_form
        self.add_table()
        description = describe_form(self.form)
        table = next(e for e in description["elements"] if e["name"] == "Упаковки")
        self.assertEqual([c["name"] for c in table["columns"]], ["НомерСтроки", "Коэффициент", "ЕдиницаИзмерения"])
        self.assertEqual(description["tabular_sections"], {"Упаковки": ["НомерСтроки", "Коэффициент", "ЕдиницаИзмерения"]})
        self.assertIn("ПриВыводеСтроки", description["events"]["Table"])
        self.assertIn("ПриИзменении", description["events"]["TableColumn"])

    def test_builtin_templates(self) -> None:
        from one_c_form_compiler.core import table_field as tf
        from one_c_form_compiler.core.standards import standard_template
        table = standard_template("Table")
        self.assertEqual(table[2][2][1][23], ["0"])
        column = standard_template("TableColumn")
        cell = tf.decode_cell(tf.column_props(column))
        self.assertIsNotNone(cell)
        self.assertEqual(cell[tf.CELL_EVENT_SLOT], ["0"])


if __name__ == "__main__":
    unittest.main()
