"""Regression tests for pages, value tables and the form module."""

from __future__ import annotations

import unittest

from tests import test_table_field as _table
from tests.test_table_field import HAS_SAMPLE, PAGE, PANEL, SAMPLE_REASON

TS_SETTINGS_KIND = "51d1e122-c0f3-496f-901e-806df8206ba9"
VT_SETTINGS_KIND = "342cf854-134c-42bb-8af9-a2103d5d9723"


@unittest.skipUnless(HAS_SAMPLE, SAMPLE_REASON)
class PagesTablesRegressionTests(unittest.TestCase):
    setUpClass = classmethod(_table.TableTests.setUpClass.__func__)
    tearDownClass = classmethod(_table.TableTests.tearDownClass.__func__)
    setUp = _table.TableTests.setUp
    tearDown = _table.TableTests.tearDown
    apply = _table.TableTests.apply
    columns = _table.TableTests.columns

    def add_value_table(self, name="ВТ"):
        return self.apply({"op": "add", "type": "Table", "name": name, "columns": ["А", "Б"],
                           "container": PANEL, "page": PAGE})

    def test_section_table_cloned_from_value_table_gets_section_block(self) -> None:
        from one_c_form_compiler.core.table_field import COLUMN_KIND, columns_node
        self.add_value_table()
        for template in (None, "ВТ"):
            name = "ТЧ" if template is None else "ТЧ2"
            op = {"op": "add", "type": "Table", "name": name, "data": "Упаковки",
                  "container": PANEL, "page": PAGE}
            if template:
                op["template"] = template
            self.apply(op)
            key = self.form.find(name)
            self.assertEqual(self.form.data[key]["raw"][2][3][0], TS_SETTINGS_KIND)
            self.assertEqual({c[0] for c in columns_node(self.form, key)[1:]}, {COLUMN_KIND})
        self.assertFalse(self.form.new_issues())

    def test_value_table_cloned_from_section_table_gets_value_table_block(self) -> None:
        self.apply({"op": "add", "type": "Table", "name": "ТЧ", "data": "Упаковки",
                    "container": PANEL, "page": PAGE})
        self.apply({"op": "add", "type": "Table", "name": "ВТ", "columns": ["А"], "template": "ТЧ",
                    "container": PANEL, "page": PAGE})
        self.assertEqual(self.form.data[self.form.find("ВТ")]["raw"][2][3][0], VT_SETTINGS_KIND)

    def test_modify_column_data_keeps_checkbox(self) -> None:
        self.add_value_table()
        self.apply({"op": "add_column", "table": "ВТ", "name": "Флаг", "checkbox": True},
                   {"op": "modify_column", "table": "ВТ", "name": "Флаг", "data": "Использование"})
        column = next(c for c in self.columns("ВТ") if c.name == "Флаг")
        self.assertEqual((column.data, column.checkbox), ("Использование", True))
        # A column with both Данные and ДанныеФлажка (seen on forms of typical configurations): changing data keeps the checkbox.
        from one_c_form_compiler.core.table_field import VT_CHECK, VT_DATA, find_column
        _index, raw = find_column(self.form, self.form.find("ВТ"), "А")
        raw[1][VT_CHECK] = '"Отметка"'
        self.apply({"op": "modify_column", "table": "ВТ", "name": "А", "data": "Другое"})
        self.assertEqual((raw[1][VT_DATA], raw[1][VT_CHECK]), ('"Другое"', '"Отметка"'))

    def test_handler_stub_is_not_added_when_a_function_has_that_name(self) -> None:
        self.apply({"op": "set_procedure", "name": "ПриОткрытииФормы",
                    "text": "Функция ПриОткрытииФормы()" + chr(10) + "КонецФункции"},
                   {"op": "set_form_events", "events": {"ПриОткрытии": "ПриОткрытииФормы"}})
        self.assertEqual(self.form.module_text.count("ПриОткрытииФормы("), 1)

    def test_set_procedure_replace_does_not_duplicate_leading_comments(self) -> None:
        text = chr(10).join(["// Комментарий метода", "Процедура Х()", "КонецПроцедуры"])
        self.apply({"op": "set_procedure", "name": "Х", "text": text},
                   {"op": "set_procedure", "name": "Х", "text": text})
        self.assertEqual(self.form.module_text.count("// Комментарий метода"), 1)
        self.assertEqual(self.form.module_text.count("Процедура Х("), 1)

    def test_set_procedure_reports_code_after_the_method(self) -> None:
        from one_c_form_compiler.core.edit_plan import PlanError
        text = chr(10).join(["Процедура Х()", "КонецПроцедуры", "Сообщить(1);"])
        with self.assertRaises(PlanError) as caught:
            self.apply({"op": "set_procedure", "name": "Х", "text": text})
        self.assertEqual(caught.exception.code, "E_PROCEDURE_TEXT")
        self.assertIn("after", str(caught.exception))

    def test_add_page_name_is_case_insensitive(self) -> None:
        from one_c_form_compiler.core.edit_plan import PlanError
        with self.assertRaises(PlanError) as caught:
            self.apply({"op": "add_page", "container": PANEL, "name": PAGE.lower()})
        self.assertEqual(caught.exception.code, "E_NAME_EXISTS")

    def test_column_ops_validate_field_types(self) -> None:
        from one_c_form_compiler.core.edit_plan import PlanError
        self.add_value_table()
        for op in ({"op": "add_column", "table": "ВТ", "name": "В", "data": ["x"]},
                   {"op": "add_column", "table": "ВТ", "name": "В", "after": 5},
                   {"op": "modify_column", "table": "ВТ", "name": "А", "width": "10"}):
            with self.subTest(op=op), self.assertRaises(PlanError) as caught:
                self.apply(op)
            self.assertEqual(caught.exception.code, "E_PLAN_FIELD")

    def test_add_page_guides_come_from_one_pages_borders(self) -> None:
        # v8unpack splits the flat guide list positionally; the last page's slice may hold another page's
        # guides or user guides (entry[4] == 0). The new page must get borders 1..4 of its own id.
        panel = self.form.resolve_container(PANEL, PAGE)[0]
        last = self.form.data[panel.page_key(panel.pages[-1])]
        own_id = last["info"][-1][5]
        user_guide = list(last["info"][-1])
        user_guide[4], user_guide[1] = "0", "77"
        last["info"] = last["info"][1:] + [user_guide]          # positional slice now mixes kinds
        self.apply({"op": "add_page", "container": PANEL, "name": "Новая"})
        info = self.form.data[panel.page_key("Новая")]["info"]
        self.assertEqual(sorted(entry[4] for entry in info), ["1", "2", "3", "4"])
        ids = {entry[5] for page in panel.pages for entry in self.form.data[panel.page_key(page)].get("info", [])}
        self.assertEqual(len({entry[5] for entry in info}), 1)
        self.assertNotIn(info[0][5], ids - {info[0][5]})
        self.assertNotEqual(info[0][5], own_id)


class ProbeTextTests(unittest.TestCase):
    def test_probe_dumps_checkbox_data(self) -> None:
        from one_c_form_compiler.core.runtime_check import config_probe_module
        self.assertIn("ПробаКолонка.ДанныеФлажка", config_probe_module("Catalog.Номенклатура.ФормаЭлемента"))


if __name__ == "__main__":
    unittest.main()
