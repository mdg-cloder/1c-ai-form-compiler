"""Runtime check of configuration forms: probe text, report parsing, column comparison (offline)."""

from __future__ import annotations

import importlib.util
import shutil
import tempfile
import unittest
from importlib import resources
from pathlib import Path

from one_c_form_compiler.core.runtime_check import (
    REPORT_DONE, config_probe_module, form_getter, parse_report, runtime_column_width)

HAS_V8UNPACK = importlib.util.find_spec("v8unpack") is not None


class ProbeTextTests(unittest.TestCase):
    def test_form_getter(self) -> None:
        self.assertEqual(form_getter("Catalog.Номенклатура.ФормаЭлемента"),
                         'Справочники.Номенклатура.ПолучитьФорму("ФормаЭлемента")')
        self.assertEqual(form_getter("Документ.Заказ.Form.ФормаДокумента"),
                         'Документы.Заказ.ПолучитьФорму("ФормаДокумента")')
        self.assertEqual(form_getter("CommonForm.Общая"), 'ПолучитьОбщуюФорму("Общая")')

    def test_config_probe_quits_and_dumps_columns(self) -> None:
        text = config_probe_module("Catalog.Номенклатура.ФормаЭлемента")
        self.assertIn('ПробаФорма = Справочники.Номенклатура.ПолучитьФорму("ФормаЭлемента");', text)
        self.assertIn("Для Каждого ПробаЭлемент Из ПробаФорма.ЭлементыФормы Цикл", text)
        self.assertIn('"#COL"', text)
        self.assertIn("ПриВыводеСтроки", text)          # table events are probed
        self.assertTrue(text.rstrip().endswith("ЗавершитьРаботуСистемы(Ложь);"))
        self.assertNotIn("\n", text.replace("\r\n", ""))

    def test_parse_report_with_columns(self) -> None:
        report = "\n".join([
            "#FORM\t500\t400",
            "Упаковки\tТабличноеПоле\t8\t40\t480\t150\t\tУпаковки\tПриВыводеСтроки,",
            "#COL\tУпаковки\tНомерСтроки\tN\t40\tНомерСтроки\t",
            "#COL\tУпаковки\tЕдиницаИзмерения\tЕдиница\t100\tЕдиницаИзмерения\tПриИзменении,",
            REPORT_DONE])
        result = parse_report(report)
        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["elements"]["Упаковки"]["events"], ["ПриВыводеСтроки"])
        self.assertEqual([c["name"] for c in result["columns"]["Упаковки"]], ["НомерСтроки", "ЕдиницаИзмерения"])
        self.assertEqual(result["columns"]["Упаковки"][1]["events"], ["ПриИзменении"])

    def test_probe_epf_sources_are_packaged(self) -> None:
        names = {p.name for p in resources.files("one_c_form_compiler.core").joinpath("probe_epf").iterdir()}
        self.assertEqual(names, {"ExternalDataProcessor.json", "ExternalDataProcessor.obj.bsl"})


@unittest.skipUnless(HAS_V8UNPACK, "v8unpack is required")
class ColumnCompareTests(unittest.TestCase):
    def setUp(self) -> None:
        from tests.test_oform import HAS_SAMPLE, SAMPLE_REASON
        if not HAS_SAMPLE:
            self.skipTest(SAMPLE_REASON)
        from tests.test_table_field import TableTests
        TableTests.setUpClass()
        self.addCleanup(TableTests.tearDownClass)
        self.case = TableTests("test_add_table_bound_to_tabular_section")
        self.case.setUp()
        self.addCleanup(self.case.tearDown)
        self.case.add_table(events={"ПриВыводеСтроки": "УпаковкиПриВыводеСтроки"})

    def runtime(self, columns):
        from one_c_form_compiler.core.runtime_check import compare
        form = self.case.form
        elements = {}
        for control in form.controls():
            rect = control.rect
            elements[control.name] = {"type": control.type, "left": rect.left, "top": rect.top,
                                      "width": rect.width, "height": rect.height, "caption": "",
                                      "data": control.data or "", "events": []}
        elements["Упаковки"]["events"] = ["ПриВыводеСтроки"]
        return [p for p in compare(form, {"elements": elements, "columns": {"Упаковки": columns}})
                if p.startswith("Упаковки")]

    def test_matching_columns_pass(self) -> None:
        columns = [{"name": c.name, "caption": c.caption.get("ru", ""), "width": runtime_column_width(c.width), "data": c.data or "",
                    "events": []} for c in self.case.columns()]
        self.assertEqual(self.runtime(columns), [])

    def test_runtime_width_is_in_characters(self) -> None:
        # Observed in 1C 8.3.27: file widths 40/150/154/120 come back as 7/24/24/19.
        from one_c_form_compiler.core.runtime_check import runtime_column_width
        self.assertEqual([runtime_column_width(w) for w in (40, 150, 154, 120)], [7, 24, 24, 19])
        columns = [{"name": c.name, "caption": c.caption.get("ru", ""), "width": runtime_column_width(c.width),
                    "data": c.data or "", "events": []} for c in self.case.columns()]
        self.assertEqual(self.runtime(columns), [])

    def test_runtime_width_scale_follows_the_table_font(self) -> None:
        # Another table font: 1C 8.3.27 returned 110 px -> 16 chars and 90 px -> 13 (sandbox value table),
        # while the formula gives 18 and 15. The scale is taken from the table's own columns.
        columns = [{"name": c.name, "caption": c.caption.get("ru", ""), "width": round(c.width * 0.145),
                    "data": c.data or "", "events": []} for c in self.case.columns()]
        self.assertEqual(self.runtime(columns), [])
        columns[2]["width"] += 4
        self.assertTrue(any("Упаковки.ЕдиницаИзмерения: width" in p for p in self.runtime(columns)))

    def test_column_differences_reported(self) -> None:
        columns = [{"name": c.name, "caption": c.caption.get("ru", ""), "width": runtime_column_width(c.width), "data": c.data or "",
                    "events": []} for c in self.case.columns()]
        columns[2]["width"] += 30
        columns[2]["data"] = "Другое"
        problems = self.runtime(columns)
        self.assertTrue(any("Упаковки.ЕдиницаИзмерения: width" in p for p in problems), problems)
        self.assertTrue(any("Упаковки.ЕдиницаИзмерения: data" in p for p in problems), problems)
        self.assertTrue(self.runtime(columns[:2])[0].startswith("Упаковки: columns"))


if __name__ == "__main__":
    unittest.main()
