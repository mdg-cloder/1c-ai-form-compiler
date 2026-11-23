"""Form-level event handlers (ПриОткрытии, ОбработкаОповещения, ПередЗаписью ...)."""

from __future__ import annotations

import unittest

from tests import test_table_field as _table
from tests.test_table_field import HAS_SAMPLE, SAMPLE_REASON

CATALOG_OBJECT_EVENTS = ["c5251418-7f99-4a2f-ba62-7f3c9e7bf914", "1", ["2", "0", ["0"], "1"]]


@unittest.skipUnless(HAS_SAMPLE, SAMPLE_REASON)
class FormEventTests(unittest.TestCase):
    setUpClass = classmethod(_table.TableTests.setUpClass.__func__)
    tearDownClass = classmethod(_table.TableTests.tearDownClass.__func__)
    setUp = _table.TableTests.setUp
    tearDown = _table.TableTests.tearDown
    apply = _table.TableTests.apply

    def form_root(self):
        return self.form.form_json["form"][0][0]

    def test_form_events_are_assigned_with_stubs(self) -> None:
        result = self.apply({"op": "set_form_events", "events": {
            "ОбработкаОповещения": "ОбработкаОповещения", "ПриОткрытии": "ПриОткрытии"}})
        self.assertEqual(result, [{"op": "set_form_events",
                                   "events": {"ПриОткрытии": "ПриОткрытии",
                                              "ОбработкаОповещения": "ОбработкаОповещения"}}])
        events = self.form.form_events()
        self.assertEqual((events["ПриОткрытии"], events["ОбработкаОповещения"]),
                         ("ПриОткрытии", "ОбработкаОповещения"))
        ids = [entry[0] for entry in self.form_root()[4][1:]]
        self.assertEqual(ids, sorted(ids, key=int))
        self.assertIn("70001", ids)
        self.assertIn("70007", ids)
        self.assertIn("Процедура ОбработкаОповещения(ИмяСобытия, Параметр, Источник)", self.form.module_text)
        self.assertIn("Процедура ПриОткрытии()", self.form.module_text)
        self.assertFalse(self.form.new_issues())

    def test_object_write_events_on_a_catalog_form(self) -> None:
        self.form_root()[3] = [CATALOG_OBJECT_EVENTS[0], "1", ["2", "0", ["0"], "1"]]
        self.apply({"op": "set_form_events", "events": {"ПриЗаписи": "ПриЗаписи", "ПослеЗаписи": "ПослеЗаписи"}})
        entries = self.form_root()[3][2][2]
        self.assertEqual(entries[0], "2")
        self.assertEqual([entry[0] for entry in entries[1:]], ["80001", "80002"])
        events = self.form.form_events()
        self.assertEqual((events["ПриЗаписи"], events["ПослеЗаписи"]), ("ПриЗаписи", "ПослеЗаписи"))
        self.assertIn("Процедура ПриЗаписи(Отказ)", self.form.module_text)

    def test_object_events_on_unknown_form_kind_are_refused(self) -> None:
        from one_c_form_compiler.core.edit_plan import PlanError
        self.form_root()[3] = ["00000000-0000-0000-0000-000000000000", "1", ["5", "1"]]
        with self.assertRaises(PlanError) as caught:
            self.apply({"op": "set_form_events", "events": {"ПриЗаписи": "ПриЗаписи"}})
        self.assertEqual(caught.exception.code, "E_HANDLER_UNSUPPORTED")

    def test_unknown_event_is_refused(self) -> None:
        from one_c_form_compiler.core.edit_plan import PlanError
        with self.assertRaises(PlanError) as caught:
            self.apply({"op": "set_form_events", "events": {"ПриЧемТо": "Икс"}})
        self.assertEqual(caught.exception.code, "E_HANDLER_UNSUPPORTED")

    def test_form_events_survive_rebuild(self) -> None:
        from one_c_form_compiler.core.oform_session import OformSession
        self.apply({"op": "set_form_events", "events": {"ПриОткрытии": "ПриОткрытииФормы"}})
        self.session.build_and_publish(None)
        with OformSession(self.form_path) as session:
            self.assertEqual(session.form().form_events()["ПриОткрытии"], "ПриОткрытииФормы")


if __name__ == "__main__":
    unittest.main()
