"""New panel pages and unbound value-table fields (filled by the form module)."""

from __future__ import annotations

import unittest

from tests import test_table_field as _table
from tests.test_table_field import HAS_SAMPLE, PAGE, PANEL, SAMPLE_REASON

VT_PATTERN = ['"Pattern"', ['"#"', "acf6192e-81ca-46ef-93a6-5a6968b78663"]]


class _Base(unittest.TestCase):
    """TableTests fixture and helpers without re-running its tests."""
    setUpClass = classmethod(_table.TableTests.setUpClass.__func__)
    tearDownClass = classmethod(_table.TableTests.tearDownClass.__func__)
    setUp = _table.TableTests.setUp
    tearDown = _table.TableTests.tearDown
    apply = _table.TableTests.apply
    columns = _table.TableTests.columns
    add_table = _table.TableTests.add_table


@unittest.skipUnless(HAS_SAMPLE, SAMPLE_REASON)
class PageTests(_Base):
    def test_add_page_appends_a_page(self) -> None:
        panel = self.form.resolve_container(PANEL, PAGE)[0]
        before = list(panel.pages)
        result = self.apply({"op": "add_page", "container": PANEL, "name": "Итоги",
                             "caption": "Итоги"})
        self.assertEqual(result, [{"op": "add_page", "container": PANEL, "name": "Итоги",
                                   "caption": {lang: "Итоги" for lang in self.form.languages()}}])
        panel = self.form.resolve_container(PANEL, "Итоги")[0]
        self.assertEqual(panel.pages, [*before, "Итоги"])
        page = self.form.data[f"{panel.key}/Итоги"]
        self.assertEqual(page["raw"][6], '"Итоги"')
        self.assertEqual(page["raw"][4], "1")                       # visible
        self.assertEqual([info[5] for info in page["info"]], [str(len(before))] * 4)
        # After the guides the panel keeps one value per page (as on every panel checked in typical configurations); a missing one
        # gives "Ошибка формата потока" when 1C reads the form.
        record = self.form.data[panel.key]["raw"][2][1]
        marker = record.index("в отдельном файле")
        self.assertEqual(record[marker + 2:marker + 2 + len(panel.pages)], ["4294967295"] * len(panel.pages))
        self.assertEqual(record[marker + 2 + len(panel.pages)], "5")
        self.assertFalse(self.form.new_issues())

    def test_elements_go_on_the_new_page_and_survive_rebuild(self) -> None:
        from one_c_form_compiler.core.oform_session import OformSession
        self.apply({"op": "add_page", "container": PANEL, "name": "Итоги", "caption": "Итоги"},
                   {"op": "add", "type": "Label", "name": "НадписьНаНовой", "caption": "Текст",
                    "container": PANEL, "page": "Итоги"})
        info = self.form.info(self.form.find("НадписьНаНовой"))
        self.assertEqual(info.page, "Итоги")
        layout = self.form._layout(info.key)
        panel = self.form.resolve_container(PANEL, "Итоги")[0]
        self.assertEqual(layout.page_index, panel.pages.index("Итоги"))
        self.session.build_and_publish(None)
        with OformSession(self.form_path) as session:
            form = session.form()
            panel = form.resolve_container(PANEL, "Итоги")[0]
            self.assertEqual(panel.pages[-1], "Итоги")
            self.assertEqual(form.info(form.find("НадписьНаНовой")).page, "Итоги")

    def test_add_page_errors(self) -> None:
        from one_c_form_compiler.core.edit_plan import PlanError
        with self.assertRaises(PlanError) as caught:
            self.apply({"op": "add_page", "container": PANEL, "name": PAGE})
        self.assertEqual(caught.exception.code, "E_NAME_EXISTS")
        with self.assertRaises(PlanError) as caught:
            self.apply({"op": "add_page", "container": "НетТакой", "name": "Итоги"})
        self.assertEqual(caught.exception.code, "E_CONTAINER_UNKNOWN")
        with self.assertRaises(PlanError) as caught:
            self.apply({"op": "add_page", "container": PANEL, "name": "1Плохое"})
        self.assertEqual(caught.exception.code, "E_NAME_INVALID")


@unittest.skipUnless(HAS_SAMPLE, SAMPLE_REASON)
class ValueTableTests(_Base):
    def add_value_table(self, **extra):
        return self.apply({"op": "add", "type": "Table", "name": "ТаблицаИтогов",
                           "columns": ["Показатель", "Значение"], "container": PANEL,
                           "page": PAGE, **extra})

    def test_unbound_table_is_a_value_table(self) -> None:
        from one_c_form_compiler.core.table_field import VALUE_TABLE_COLUMN_KIND, columns_node, is_value_table
        result = self.add_value_table()
        key = self.form.find("ТаблицаИтогов")
        raw = self.form.data[key]["raw"]
        self.assertIsNone(self.form._datasource(raw[1]))
        self.assertEqual(raw[2][1], VT_PATTERN)
        self.assertTrue(is_value_table(self.form, key))
        self.assertEqual({c[0] for c in columns_node(self.form, key)[1:]}, {VALUE_TABLE_COLUMN_KIND})
        self.assertEqual([(c.name, c.data) for c in self.columns("ТаблицаИтогов")],
                         [("Показатель", "Показатель"), ("Значение", "Значение")])
        self.assertTrue(result[-1]["value_table"])
        # The settings block after the value type must be the value-table one (a tabular-section block
        # left from the template gives "Ошибка формата потока" when 1C reads the form).
        self.assertEqual(raw[2][3], ["342cf854-134c-42bb-8af9-a2103d5d9723", ["5", "0", "0", "1"]])
        from one_c_form_compiler.core.table_field import P_PATTERN, column_props, decode_cell
        for column in columns_node(self.form, key)[1:]:
            props = column_props(column)
            self.assertEqual(props[P_PATTERN], ['"Pattern"', ['"S"']])      # never an empty type
            self.assertEqual(decode_cell(props)[1], ['"Pattern"', ['"S"']])
        self.assertFalse(self.form.new_issues())

    def test_value_table_needs_columns(self) -> None:
        from one_c_form_compiler.core.edit_plan import PlanError
        with self.assertRaises(PlanError) as caught:
            self.apply({"op": "add", "type": "Table", "name": "Пустая", "container": PANEL,
                        "page": PAGE})
        self.assertEqual(caught.exception.code, "E_DATA_REQUIRED")

    def test_value_table_columns_checkbox_and_events(self) -> None:
        self.add_value_table(events={"ПередНачаломДобавления": "ТаблицаИтоговПередНачаломДобавления"})
        self.apply({"op": "add_column", "table": "ТаблицаИтогов", "name": "Использование", "checkbox": True,
                    "width": 40, "after": "Показатель"},
                   {"op": "add_column", "table": "ТаблицаИтогов", "name": "ДатаС", "data": "ДатаНачала",
                    "caption": "Дата с"},
                   {"op": "modify_column", "table": "ТаблицаИтогов", "name": "Показатель",
                    "events": {"ПриИзменении": "ТаблицаИтоговПоказательПриИзменении"}})
        columns = {c.name: c for c in self.columns("ТаблицаИтогов")}
        self.assertEqual(list(columns), ["Показатель", "Использование", "Значение", "ДатаС"])
        self.assertEqual(columns["Использование"].data, "Использование")
        self.assertTrue(columns["Использование"].checkbox)
        self.assertEqual(columns["ДатаС"].data, "ДатаНачала")
        self.assertEqual(columns["Показатель"].events, {"ПриИзменении": "ТаблицаИтоговПоказательПриИзменении"})
        self.assertIn("Процедура ТаблицаИтоговПоказательПриИзменении(Элемент)", self.form.module_text)
        self.assertIn("Процедура ТаблицаИтоговПередНачаломДобавления(", self.form.module_text)
        self.assertFalse(self.form.new_issues())

    def test_value_table_survives_rebuild(self) -> None:
        from one_c_form_compiler.core.oform_session import OformSession
        from one_c_form_compiler.core.table_field import list_columns
        self.add_value_table()
        self.apply({"op": "add_column", "table": "ТаблицаИтогов", "name": "Использование", "checkbox": True})
        self.session.build_and_publish(None)
        with OformSession(self.form_path) as session:
            form = session.form()
            columns = list_columns(form, form.find("ТаблицаИтогов"))
            self.assertEqual([(c.name, c.data, c.checkbox) for c in columns],
                             [("Показатель", "Показатель", False), ("Значение", "Значение", False),
                              ("Использование", "Использование", True)])

    def test_runtime_compare_reads_checkbox_data(self) -> None:
        # 1C keeps a checkbox column's name in ДанныеФлажка; Данные stays empty.
        from one_c_form_compiler.core.runtime_check import compare, parse_report, REPORT_DONE, runtime_column_width
        self.add_value_table()
        self.apply({"op": "add_column", "table": "ТаблицаИтогов", "name": "Использование", "checkbox": True})
        lines = []
        for c in self.columns("ТаблицаИтогов"):
            data, check = ("", c.data) if c.checkbox else (c.data, "")
            lines.append(chr(9).join(["#COL", "ТаблицаИтогов", c.name, c.caption.get("ru", ""),
                                     str(runtime_column_width(c.width)), data, "", check]))
        report = parse_report(chr(10).join([chr(9).join(["#FORM", "1", "1"]), *lines, REPORT_DONE]))
        self.assertEqual(report["columns"]["ТаблицаИтогов"][-1]["check"], "Использование")
        elements = {}
        for control in self.form.controls():
            rect = control.rect
            elements[control.name] = {"type": control.type, "left": rect.left, "top": rect.top,
                                      "width": rect.width, "height": rect.height, "caption": "",
                                      "data": control.data or "", "events": []}

        def column_problems():
            return [p for p in compare(self.form, {"elements": elements, "columns": report["columns"]})
                    if p.startswith("ТаблицаИтогов")]
        self.assertEqual(column_problems(), [])
        report["columns"]["ТаблицаИтогов"][-1]["check"] = ""
        problems = column_problems()
        self.assertTrue(any("Использование: checkbox data" in p for p in problems), problems)

    def test_tabular_section_checkbox_column_needs_boolean_data(self) -> None:
        # Checkbox columns of tabular-section tables themselves: tests/test_properties.py.
        from one_c_form_compiler.core.edit_plan import PlanError
        self.add_table()
        with self.assertRaises(PlanError) as caught:
            self.apply({"op": "add_column", "table": "Упаковки", "name": "Флаг", "checkbox": True})
        self.assertEqual(caught.exception.code, "E_DATA_REQUIRED")
        with self.assertRaises(PlanError) as caught:
            self.apply({"op": "add_column", "table": "Упаковки", "name": "Флаг", "checkbox": True,
                        "data": "Коэффициент"})
        self.assertEqual(caught.exception.code, "E_DATA_TYPE")


if __name__ == "__main__":
    unittest.main()
