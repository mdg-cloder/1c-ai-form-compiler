"""set_procedure: put real code into the form module (replace a stub or append)."""

from __future__ import annotations

import unittest

from tests import test_table_field as _table
from tests.test_table_field import HAS_SAMPLE, SAMPLE_REASON

BODY = "Процедура ПриОткрытииФормы()\n\tСообщить(\"Открыта\");\nКонецПроцедуры"


@unittest.skipUnless(HAS_SAMPLE, SAMPLE_REASON)
class SetProcedureTests(unittest.TestCase):
    setUpClass = classmethod(_table.TableTests.setUpClass.__func__)
    tearDownClass = classmethod(_table.TableTests.tearDownClass.__func__)
    setUp = _table.TableTests.setUp
    tearDown = _table.TableTests.tearDown
    apply = _table.TableTests.apply

    def test_replaces_the_generated_stub(self) -> None:
        self.apply({"op": "set_form_events", "events": {"ПриОткрытии": "ПриОткрытииФормы"}})
        self.assertIn("Сгенерировано one-c-form-compiler", self.form.module_text)
        result = self.apply({"op": "set_procedure", "name": "ПриОткрытииФормы", "text": BODY})
        self.assertEqual(result, [{"op": "set_procedure", "name": "ПриОткрытииФормы", "replaced": True}])
        text = self.form.module_text
        self.assertEqual(text.count("Процедура ПриОткрытииФормы("), 1)
        self.assertIn('\tСообщить("Открыта");', text)
        self.assertNotIn("Сгенерировано one-c-form-compiler: добавьте обработку события.\r\nКонецПроцедуры\r\n"
                         "\r\nПроцедура ПриОткрытииФормы", text)
        newline = "\r\n" if "\r\n" in text else "\n"
        self.assertIn("Процедура ПриОткрытииФормы()" + newline + '\tСообщить("Открыта");' + newline
                      + "КонецПроцедуры", text)

    def test_appends_a_function_before_the_module_body(self) -> None:
        text = "Функция Сумма(А, Б)\n\tВозврат А + Б;\nКонецФункции"
        result = self.apply({"op": "set_procedure", "name": "Сумма", "text": text})
        self.assertFalse(result[0]["replaced"])
        self.assertIn("Функция Сумма(А, Б)", self.form.module_text)
        self.assertEqual(self.form.module_text.count("Функция Сумма("), 1)

    def test_replace_keeps_the_rest_of_the_module(self) -> None:
        before = self.form.module_text
        self.apply({"op": "set_procedure", "name": "Новая", "text": "Процедура Новая()\nКонецПроцедуры"})
        self.apply({"op": "set_procedure", "name": "Новая", "text": "Процедура Новая()\n\tА = 1;\nКонецПроцедуры"})
        after = self.form.module_text
        self.assertEqual(after.count("Процедура Новая("), 1)
        self.assertIn("\tА = 1;", after)
        self.assertEqual(after.replace(after[after.index("Процедура Новая("):], ""),
                         after[:after.index("Процедура Новая(")])
        self.assertTrue(set(before.split()) <= set(after.split()))

    def test_add_variable_goes_before_methods(self) -> None:
        self.apply({"op": "set_procedure", "name": "Новая", "text": "Процедура Новая()" + chr(10) + "КонецПроцедуры"})
        result = self.apply({"op": "add_variable", "name": "мУдаленныеСтроки"})
        self.assertEqual(result, [{"op": "add_variable", "name": "мУдаленныеСтроки", "added": True}])
        text = self.form.module_text
        self.assertIn("Перем мУдаленныеСтроки;", text)
        self.assertLess(text.index("Перем мУдаленныеСтроки;"), text.index("Процедура "))
        again = self.apply({"op": "add_variable", "name": "мУдаленныеСтроки"})
        self.assertFalse(again[0]["added"])
        self.assertEqual(self.form.module_text.count("Перем мУдаленныеСтроки"), 1)
        from one_c_form_compiler.core.edit_plan import PlanError
        with self.assertRaises(PlanError) as caught:
            self.apply({"op": "add_variable", "name": "1Плохо"})
        self.assertEqual(caught.exception.code, "E_NAME_INVALID")

    def test_invalid_texts_are_refused(self) -> None:
        from one_c_form_compiler.core.edit_plan import PlanError
        bad = [
            "Процедура Другая()\nКонецПроцедуры",                                  # name mismatch
            "Процедура ПриОткрытииФормы()\nКонецФункции",                           # wrong end keyword
            "Процедура ПриОткрытииФормы()\nКонецПроцедуры\nПроцедура Х()\nКонецПроцедуры",  # two methods
            "Сообщить(1);",                                                          # not a method
        ]
        for text in bad:
            with self.subTest(text=text), self.assertRaises(PlanError) as caught:
                self.apply({"op": "set_procedure", "name": "ПриОткрытииФормы", "text": text})
            self.assertEqual(caught.exception.code, "E_PROCEDURE_TEXT")


if __name__ == "__main__":
    unittest.main()
