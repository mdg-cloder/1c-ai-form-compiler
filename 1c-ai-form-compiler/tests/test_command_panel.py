"""Command panels bound to a table field (КоманднаяПанель with the table as action source)."""

from __future__ import annotations

import unittest

from tests import test_table_field as _table
from tests.test_table_field import HAS_SAMPLE, PAGE, PANEL, SAMPLE_REASON


@unittest.skipUnless(HAS_SAMPLE, SAMPLE_REASON)
class CommandPanelTests(unittest.TestCase):
    setUpClass = classmethod(_table.TableTests.setUpClass.__func__)
    tearDownClass = classmethod(_table.TableTests.tearDownClass.__func__)
    setUp = _table.TableTests.setUp
    tearDown = _table.TableTests.tearDown
    apply = _table.TableTests.apply

    def add_table(self, top=40):
        return self.apply({"op": "add_page", "container": PANEL, "name": "Новая"},
                          {"op": "add", "type": "Table", "name": "ВТ", "columns": ["А", "Б"],
                           "container": PANEL, "page": "Новая", "left": 6, "top": top,
                           "width": 300, "height": 120})

    def panel(self, **extra):
        op = {"op": "add_command_panel", "name": "КоманднаяПанельВТ", "table": "ВТ",
              "buttons": [{"name": "Подобрать", "caption": "Подобрать строки",
                           "procedure": "КоманднаяПанельВТПодобрать"}], **extra}
        return self.apply(op)

    def test_autofill_panel_with_a_button(self) -> None:
        from one_c_form_compiler.core.command_panel import bar_issues, panel_info
        from one_c_form_compiler.core.ordinary_form import BOTTOM, TOP
        self.add_table()
        result = self.panel()
        self.assertEqual(result[0]["rect"], {"left": 6, "top": 13, "width": 300, "height": 24})
        key = self.form.find("КоманднаяПанельВТ")
        raw = self.form.data[key]["raw"]
        table_id = self.form.data[self.form.find("ВТ")]["raw"][1]
        self.assertEqual(raw[-2][2], table_id)                         # action source = the table
        self.assertEqual(raw[2][1][3], "1")                            # autofill
        self.assertEqual(bar_issues(raw), [])
        layout = self.form._layout(key)
        self.assertEqual(layout.edges[BOTTOM][1], ["2", raw[1], str(TOP), "24"])
        info = panel_info(self.form, key)
        self.assertEqual(info["source"], "ВТ")
        self.assertEqual([b.get("procedure") for b in info["buttons"]], [None, "КоманднаяПанельВТПодобрать"])
        self.assertTrue(info["buttons"][0]["separator"])
        self.assertIn("Процедура КоманднаяПанельВТПодобрать(Кнопка)", self.form.module_text)
        table_layout = self.form._layout(self.form.find("ВТ"))
        self.assertEqual(layout.tab_index, table_layout.tab_index + 1)
        self.assertEqual(layout.z_index, table_layout.z_index - 1)
        self.assertFalse(self.form.new_issues())

    def test_explicit_standard_actions(self) -> None:
        from one_c_form_compiler.core.command_panel import bar_issues, panel_info
        self.add_table()
        self.panel(autofill=False, standard=["Добавить", "Удалить"])
        key = self.form.find("КоманднаяПанельВТ")
        self.assertEqual(self.form.data[key]["raw"][2][1][3], "0")
        self.assertEqual(bar_issues(self.form.data[key]["raw"]), [])
        info = panel_info(self.form, key)
        self.assertEqual([b.get("standard") for b in info["buttons"][:2]], ["80", "83"])

    def test_panel_survives_rebuild(self) -> None:
        from one_c_form_compiler.core.command_panel import bar_issues, panel_info
        from one_c_form_compiler.core.oform_session import OformSession
        self.add_table()
        self.panel()
        self.session.build_and_publish(None)
        with OformSession(self.form_path) as session:
            form = session.form()
            key = form.find("КоманднаяПанельВТ")
            self.assertEqual(bar_issues(form.data[key]["raw"]), [])
            self.assertEqual(panel_info(form, key)["source"], "ВТ")

    def test_errors(self) -> None:
        from one_c_form_compiler.core.edit_plan import PlanError
        self.add_table(top=10)
        for extra, code in (({"shift": False}, "E_NO_SPACE"),
                            ({"standard": ["Добавить"]}, "E_PLAN_FIELD"),
                            ({"autofill": False, "standard": ["Взлететь"]}, "E_PLAN_FIELD"),
                            ({"table": "НетТакой"}, "E_ELEMENT_NOT_FOUND"),
                            ({"buttons": [{"name": "Х"}]}, "E_PLAN_FIELD")):
            with self.subTest(extra=extra), self.assertRaises(PlanError) as caught:
                self.panel(**extra)
            self.assertEqual(caught.exception.code, code)

    def test_built_in_template_when_the_form_has_no_table_panel(self) -> None:
        from unittest.mock import patch
        from one_c_form_compiler.core import command_panel
        from one_c_form_compiler.core.standards import standard_template
        self.add_table()
        with patch.object(command_panel, "panel_template", lambda _form: standard_template("CommandPanel")):
            self.panel()
        key = self.form.find("КоманднаяПанельВТ")
        self.assertEqual(command_panel.bar_issues(self.form.data[key]["raw"]), [])
        self.assertEqual(command_panel.panel_info(self.form, key)["source"], "ВТ")
        self.assertFalse(self.form.new_issues())

    def test_stretched_table_and_its_panel_follow_the_page(self) -> None:
        # A table that fills its page: bottom bound to the page bottom, right to the page right.
        from one_c_form_compiler.core.ordinary_form import BOTTOM, LEFT, RIGHT, TOP
        self.apply({"op": "add_page", "container": PANEL, "name": "Новая"})
        panel, page = self.form.resolve_container(PANEL, "Новая")
        width, height = self.form.container_size(panel, page)
        self.apply({"op": "add", "type": "Table", "name": "ВТ", "columns": ["А"], "container": PANEL,
                    "page": "Новая", "left": 6, "top": 33, "width": width - 12, "height": height - 33 - 18,
                    "stretch": "both"})
        key = self.form.find("ВТ")
        binds = {edge: bind for edge, _slot, bind in self.form._layout(key).bindings() if edge <= 3}
        self.assertEqual(binds[BOTTOM], ["2", "0", str(BOTTOM), "-18"])
        self.assertEqual(binds[RIGHT], ["2", "0", str(RIGHT), "-6"])
        self.assertEqual(binds[TOP][2], str(TOP))
        self.assertEqual(binds[LEFT][2], str(LEFT))
        self.panel()
        panel_binds = {edge: bind for edge, _slot, bind in self.form._layout(self.form.find("КоманднаяПанельВТ")).bindings()
                       if edge <= 3}
        self.assertEqual(panel_binds[RIGHT], ["2", "0", str(RIGHT), "-6"])
        self.assertFalse(self.form.new_issues())

    def test_empty_page_size_comes_from_its_guides(self) -> None:
        # Page guides are margin lines inside the client area: size = right/bottom guide + its margin (entry[7]).
        self.apply({"op": "add_page", "container": PANEL, "name": "Новая"})
        panel, page = self.form.resolve_container(PANEL, "Новая")
        guides = {entry[4]: entry for entry in self.form.data[panel.page_key(page)]["info"]}
        self.assertEqual(self.form.container_size(panel, page),
                         (int(guides["3"][1]) + int(guides["3"][7]), int(guides["4"][1]) + int(guides["4"][7])))
        self.assertEqual(self.form.container_size(panel, page), self.form.container_size(panel, PAGE))

    def test_stretch_is_validated(self) -> None:
        from one_c_form_compiler.core.edit_plan import PlanError
        with self.assertRaises(PlanError) as caught:
            self.apply({"op": "add", "type": "Label", "name": "Н", "caption": "x", "stretch": "diagonal"})
        self.assertEqual(caught.exception.code, "E_PLAN_FIELD")

    def test_moving_a_panel_keeps_its_height_binding(self) -> None:
        # Self binding (height bound to own top): a move must not change its offset.
        from one_c_form_compiler.core.ordinary_form import BOTTOM
        self.add_table(top=60)
        self.panel()
        key = self.form.find("КоманднаяПанельВТ")
        rect = self.form.info(key).rect
        self.apply({"op": "modify", "name": "КоманднаяПанельВТ", "top": rect.top - 2})
        self.assertEqual(self.form._layout(key).edges[BOTTOM][1][3], "24")
        self.assertEqual(self.form.info(key).rect.height, 24)
        self.assertFalse(self.form.new_issues())


if __name__ == "__main__":
    unittest.main()
