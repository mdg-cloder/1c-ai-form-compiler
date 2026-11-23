"""Layout: two-column rows (label with beside/column/left/top), command panels that shift the table down,
the form size (set_form_size) and explicit geometry outside the client area."""

from __future__ import annotations

import io
import json
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path

from tests import test_table_field as _table
from tests.test_table_field import HAS_SAMPLE, NESTED_PAGE, NESTED_PANEL, PAGE, PANEL, SAMPLE_REASON


class _Base(unittest.TestCase):
    setUpClass = classmethod(_table.TableTests.setUpClass.__func__)
    tearDownClass = classmethod(_table.TableTests.tearDownClass.__func__)
    setUp = _table.TableTests.setUp
    tearDown = _table.TableTests.tearDown
    apply = _table.TableTests.apply

    def error(self, *operations) -> str:
        from one_c_form_compiler.core.edit_plan import PlanError
        with self.assertRaises(PlanError) as caught:
            self.apply(*operations)
        return caught.exception.code

    def rect(self, name):
        return self.form._layout(self.form.find(name)).rect

    def tab(self, name) -> int:
        return self.form._layout(self.form.find(name)).tab_index

    def bound(self, name, edge):
        """(target id, border) of the primary binding of an edge."""
        for edge_index, slot, bind in self.form._layout(self.form.find(name)).bindings():
            if edge_index == edge and slot == 1:
                return bind[1], int(bind[2])
        return None

    def reopen(self):
        from one_c_form_compiler.core.oform_session import OformSession
        self.session.build_and_publish(None)
        self.session.__exit__(None, None, None)
        self.session = OformSession(self.form_path).__enter__()
        self.form = self.session.form()


@unittest.skipUnless(HAS_SAMPLE, SAMPLE_REASON)
class TwoColumnTests(_Base):
    def test_label_beside_joins_the_row_of_the_element(self) -> None:
        date = self.rect("Дата")
        date_tab = self.tab("Дата")
        result = self.apply({"op": "add", "type": "Field", "name": "Сумма", "label": "Сумма:",
                             "value_type": "Число(15,2)", "beside": "Дата", "width": 100})
        label, field = self.rect("НадписьСумма"), self.rect("Сумма")
        self.assertEqual((label.top, field.top), (date.top, date.top))
        self.assertEqual(label.left, date.right + 4)
        self.assertEqual(field.left, label.right + 4)
        self.assertEqual(field.width, 100)
        # Row-major tab order: Дата, its new label, the new field, then what followed Дата before.
        self.assertEqual((self.tab("НадписьСумма"), self.tab("Сумма")), (date_tab + 1, date_tab + 2))
        tabs = sorted(self.form._layout(key).tab_index for key in self.form.page_elements(
            *self.form.container_of(self.form.find("Сумма"))))
        self.assertEqual(tabs, list(range(len(tabs))))
        self.assertEqual([r["tab_index"] for r in result], [date_tab + 1, date_tab + 2])
        self.assertFalse(self.form.new_issues())

    def test_two_column_header_rows(self) -> None:
        passport = self.form.style_passport()
        width = passport["client_width"]
        self.apply(
            {"op": "add", "type": "Field", "name": "Организация", "label": "Организация:", "column": 1,
             "value_type": "Строка(50)"},
            {"op": "add", "type": "Field", "name": "Наименование", "label": "Наименование:", "column": 2,
             "beside": "Организация", "value_type": "Строка(100)"},
            {"op": "add", "type": "Field", "name": "Склад", "label": "Склад:", "column": 1, "value_type": "Строка(50)"},
            {"op": "add", "type": "Field", "name": "Ответ", "label": "Ответ:", "column": 2, "beside": "Склад",
             "value_type": "Строка(50)"})
        org, name = self.rect("Организация"), self.rect("Наименование")
        self.assertEqual(self.rect("НадписьОрганизация").left, passport["label_left"])
        self.assertEqual(org.left, passport["field_left"])
        self.assertLessEqual(org.right, width // 2 - 12)
        for label, field in (("НадписьНаименование", "Наименование"), ("НадписьОтвет", "Ответ")):
            self.assertEqual(self.rect(label).left, width // 2)          # right column: labels aligned
            self.assertEqual(self.rect(field).right, width - passport["label_left"])
            self.assertEqual(self.bound(field, 3), ("0", 3))               # stretches with the form width
        self.assertEqual(name.top, org.top)
        self.assertEqual(self.rect("Ответ").top, self.rect("Склад").top)
        self.assertGreater(self.rect("Склад").top, org.top)
        self.assertEqual(self.rect("Ответ").left, self.rect("Наименование").left)
        order = ["НадписьОрганизация", "Организация", "НадписьНаименование", "Наименование",
                 "НадписьСклад", "Склад", "НадписьОтвет", "Ответ"]
        tabs = [self.tab(name) for name in order]
        self.assertEqual(tabs, list(range(tabs[0], tabs[0] + 8)))          # row-major, pairs in order
        self.assertFalse(self.form.new_issues())
        self.reopen()
        self.assertEqual(self.rect("Наименование"), name)

    def test_column_on_a_panel_page_and_single_elements(self) -> None:
        passport = self.form.style_passport(PANEL, PAGE)
        self.apply({"op": "add_page", "container": PANEL, "name": "Шапка"},
                   {"op": "add", "type": "CheckBox", "name": "Флаг", "caption": "Флаг", "container": PANEL,
                    "page": "Шапка"},
                   {"op": "add", "type": "Field", "name": "Справа", "column": 2, "beside": "Флаг",
                    "value_type": "Строка(10)", "width": 80, "container": PANEL, "page": "Шапка"})
        right = self.rect("Справа")
        self.assertEqual(right.top, self.rect("Флаг").top)
        self.assertEqual(right.left, passport["client_width"] // 2 + passport["field_left"] - passport["label_left"])
        self.assertEqual(right.width, 80)
        self.assertEqual(self.bound("Справа", 3), ("0", 2))                # explicit width: no stretch
        self.assertEqual(self.tab("Справа"), self.tab("Флаг") + 1)

    def test_label_with_left_and_top(self) -> None:
        passport = self.form.style_passport()
        self.apply({"op": "add", "type": "Field", "name": "Код", "label": "Код:", "left": 300, "top": 33,
                    "width": 60, "value_type": "Строка(9)"})
        label, field = self.rect("НадписьКод"), self.rect("Код")
        self.assertEqual((label.left, label.top), (300, 33))
        self.assertEqual(field.left, 300 + passport["field_left"] - passport["label_left"])
        self.assertEqual((field.top, field.width), (33, 60))
        self.assertEqual(self.tab("НадписьКод"), self.tab("Дата") + 1)

    def test_errors(self) -> None:
        # Надпись2 sits right after Номер: no room for a new label there.
        self.assertEqual(self.error({"op": "add", "type": "Field", "name": "Х", "label": "Х:", "beside": "Номер"}),
                         "E_NO_SPACE")
        self.assertEqual(self.error({"op": "add", "type": "Field", "name": "Х", "column": 3}), "E_PLAN_FIELD")
        self.assertEqual(self.error({"op": "add", "type": "Field", "name": "Х", "column": True}), "E_PLAN_FIELD")
        # The right half of the row of Номер is free, but the new field may not overlap: Дата reaches 277.
        self.assertEqual(self.error({"op": "add", "type": "Field", "name": "Х", "label": "Х:", "column": 1,
                                     "beside": "Номер"}), "E_NO_SPACE")


@unittest.skipUnless(HAS_SAMPLE, SAMPLE_REASON)
class CommandPanelShiftTests(_Base):
    def panel(self, table="ВТ", **extra):
        return self.apply({"op": "add_command_panel", "name": "КоманднаяПанель" + table, "table": table, **extra})

    def test_root_table_under_a_label_moves_down_and_the_form_grows(self) -> None:
        self.apply({"op": "add", "type": "Label", "name": "НадписьВТ", "caption": "Строки:"},
                   {"op": "add", "type": "Table", "name": "ВТ", "columns": ["А", "Б"], "height": 100})
        label, table = self.rect("НадписьВТ"), self.rect("ВТ")
        height = self.form.size[1]
        footer = self.rect("ОсновныеДействияФормы")
        result = self.panel()[0]
        moved = self.rect("ВТ")
        panel = self.rect("КоманднаяПанельВТ")
        self.assertGreater(result["shifted"], 0)
        self.assertEqual(moved.top, table.top + result["shifted"])
        self.assertEqual(moved.height, table.height)
        self.assertEqual(panel.top, label.bottom + 3)
        self.assertEqual(panel.bottom, moved.top - 3)
        self.assertEqual(self.form.size[1], height + result["shifted"])
        self.assertEqual(self.rect("ОсновныеДействияФормы").top, footer.top + result["shifted"])
        self.assertEqual(self.tab("КоманднаяПанельВТ"), self.tab("ВТ") + 1)
        self.assertFalse(self.form.new_issues())
        self.reopen()
        self.assertEqual(self.rect("КоманднаяПанельВТ"), panel)

    def test_free_room_on_a_panel_page_is_used(self) -> None:
        size = self.form.size
        self.apply({"op": "add_page", "container": PANEL, "name": "Новая"},
                   {"op": "add", "type": "Label", "name": "НадписьВТ", "caption": "Строки:", "container": PANEL,
                    "page": "Новая"},
                   {"op": "add", "type": "Table", "name": "ВТ", "columns": ["А"], "container": PANEL, "page": "Новая",
                    "height": 100})
        table = self.rect("ВТ")
        result = self.panel()[0]
        self.assertEqual(self.rect("ВТ").top, table.top + result["shifted"])
        self.assertEqual(self.form.size, size)                    # room below the table: nothing grows
        self.assertFalse(self.form.new_issues())

    def test_top_level_panel_grows_when_the_page_is_full(self) -> None:
        self.apply({"op": "add_page", "container": PANEL, "name": "Новая"},
                   {"op": "add", "type": "Label", "name": "НадписьВТ", "caption": "Строки:", "container": PANEL,
                    "page": "Новая"})
        panel, page = self.form.resolve_container(PANEL, "Новая")
        client = self.form.container_size(panel, page)
        top = self.rect("НадписьВТ").bottom + 5
        self.apply({"op": "add", "type": "Table", "name": "ВТ", "columns": ["А"], "container": PANEL,
                    "page": "Новая", "left": 6, "top": top, "width": 300, "height": client[1] - top - 2})
        height = self.form.size[1]
        result = self.panel()[0]
        self.assertEqual(self.form.size[1], height + result["shifted"])
        self.assertEqual(self.form.container_size(panel, page)[1], client[1] + result["shifted"])
        self.assertFalse(self.form.new_issues())

    def test_nested_panel_without_room_is_refused(self) -> None:
        panel, page = self.form.resolve_container(NESTED_PANEL, NESTED_PAGE)
        client = self.form.container_size(panel, page)
        top = max(self.form._layout(key).rect.bottom for key in self.form.page_elements(panel, page)) + 5
        self.apply({"op": "add", "type": "Table", "name": "ВТ", "columns": ["А"], "container": NESTED_PANEL,
                    "page": NESTED_PAGE, "left": 6, "top": top, "width": 300, "height": client[1] - top - 2})
        self.assertEqual(self.error({"op": "add_command_panel", "name": "КП", "table": "ВТ"}), "E_NO_SPACE")


@unittest.skipUnless(HAS_SAMPLE, SAMPLE_REASON)
class FormSizeTests(_Base):
    def test_set_form_size_moves_and_stretches_anchored_elements(self) -> None:
        width, height = self.form.size
        dw, dh = 100, 60
        before = {name: self.rect(name) for name in ("ОсновныеДействияФормы", "Комментарий", "ПанельРегистров",
                                                     "Номер", "Надпись3", "ТабличноеПолеДвиженияБУ")}
        panel, page = self.form.resolve_container(PANEL, PAGE)
        client = self.form.container_size(panel, page)
        result = self.apply({"op": "set_form_size", "width": width + dw, "height": height + dh})[0]
        self.assertEqual((result["width"], result["height"]), (width + dw, height + dh))
        self.assertEqual(result["previous"], {"width": width, "height": height})
        self.assertEqual(self.form.size, (width + dw, height + dh))
        footer = self.rect("ОсновныеДействияФормы")
        self.assertEqual((footer.top, footer.width), (before["ОсновныеДействияФормы"].top + dh,
                                                       before["ОсновныеДействияФормы"].width + dw))
        self.assertEqual(self.rect("Комментарий").width, before["Комментарий"].width + dw)
        self.assertEqual(self.rect("Комментарий").top, before["Комментарий"].top + dh)   # below the panel
        self.assertEqual(self.rect("Надпись3").left, before["Надпись3"].left)
        self.assertEqual(self.rect("Номер"), before["Номер"])
        grown = self.rect("ПанельРегистров")
        self.assertEqual((grown.width, grown.height), (before["ПанельРегистров"].width + dw,
                                                        before["ПанельРегистров"].height + dh))
        self.assertEqual(self.form.container_size(panel, page), (client[0] + dw, client[1] + dh))
        self.assertIn("ТабличноеПолеДвиженияБУ", result["moved"])
        self.assertNotIn("Номер", result["moved"])
        self.assertFalse(self.form.new_issues())
        self.reopen()
        self.assertEqual(self.form.size, (width + dw, height + dh))
        self.assertEqual(self.rect("ОсновныеДействияФормы"), footer)

    def test_shrinking_below_the_content_is_refused(self) -> None:
        width, height = self.form.size
        self.assertEqual(self.error({"op": "set_form_size", "height": 150}), "E_GEOMETRY_INVALID")
        self.assertEqual(self.error({"op": "set_form_size", "width": 200}), "E_GEOMETRY_INVALID")
        self.assertEqual(self.error({"op": "set_form_size", "width": 10}), "E_GEOMETRY_INVALID")
        self.assertEqual(self.error({"op": "set_form_size"}), "E_PLAN_FIELD")
        self.assertEqual(self.error({"op": "set_form_size", "width": "700"}), "E_PLAN_FIELD")
        self.assertEqual(self.form.size, (width, height))
        self.apply({"op": "set_form_size", "width": width + 30, "height": height + 30},
                   {"op": "set_form_size", "width": width, "height": height})     # back to the content size
        self.assertEqual(self.form.size, (width, height))
        self.assertFalse(self.form.new_issues())

    def test_explicit_rectangle_below_the_form_grows_it(self) -> None:
        width, height = self.form.size
        footer = self.rect("ОсновныеДействияФормы")
        self.apply({"op": "add", "type": "Field", "name": "Внизу", "value_type": "Строка(10)", "left": 8,
                    "top": height + 20, "width": 200, "height": 19})
        field = self.rect("Внизу")
        self.assertEqual((field.left, field.top, field.width), (8, height + 20, 200))
        moved = self.rect("ОсновныеДействияФормы")
        self.assertGreaterEqual(moved.top, field.bottom)
        self.assertEqual(moved.bottom, self.form.size[1])
        self.assertEqual(moved.height, footer.height)
        self.assertEqual(self.rect("Комментарий").top, 311)                 # the content above stays
        self.assertEqual(self.form.size[0], width)
        self.assertFalse(self.form.new_issues())

    def test_explicit_rectangle_right_of_the_form_widens_it(self) -> None:
        width, height = self.form.size
        comment = self.rect("Комментарий")
        self.apply({"op": "add", "type": "Field", "name": "Справа", "value_type": "Строка(10)",
                    "left": width - 50, "top": 33, "width": 150})
        self.assertEqual(self.rect("Справа").right, width + 100)
        self.assertEqual(self.form.size, (width + 108, height))
        self.assertEqual(self.rect("Комментарий").right, comment.right + 108)     # anchored to the right
        self.assertFalse(self.form.new_issues())

    def test_explicit_rectangle_on_a_top_level_panel_page_grows_the_panel(self) -> None:
        panel, page = self.form.resolve_container(PANEL, PAGE)
        client = self.form.container_size(panel, page)
        height = self.form.size[1]
        self.apply({"op": "add", "type": "Field", "name": "Ниже", "value_type": "Строка(10)", "container": PANEL,
                    "page": PAGE, "left": 8, "top": client[1] + 10, "width": 120})
        self.assertGreater(self.form.container_size(panel, page)[1], client[1])
        self.assertGreater(self.form.size[1], height)
        self.assertLessEqual(self.rect("Ниже").bottom, self.form.container_size(panel, page)[1])
        self.assertFalse(self.form.new_issues())

    def test_nested_panel_still_refuses_outside_geometry(self) -> None:
        panel, page = self.form.resolve_container(NESTED_PANEL, NESTED_PAGE)
        client = self.form.container_size(panel, page)
        self.assertEqual(self.error({"op": "add", "type": "Field", "name": "Ниже", "value_type": "Строка(10)",
                                     "container": NESTED_PANEL, "page": NESTED_PAGE, "left": 8,
                                     "top": client[1] + 10, "width": 120}), "E_GEOMETRY_OUTSIDE")

    def test_cli_size_command(self) -> None:
        from one_c_form_compiler.cli import main
        width, height = self.form.size
        stream = io.StringIO()
        with redirect_stdout(stream):
            code = main(["size", str(self.form_path), "--width", str(width + 20), "--dry-run", "--json"])
        self.assertEqual(code, 0)
        change = json.loads(stream.getvalue())["changes"][0]
        self.assertEqual((change["op"], change["width"], change["height"]), ("set_form_size", width + 20, height))
        stream = io.StringIO()
        with redirect_stdout(stream):
            code = main(["size", str(self.form_path), "--dry-run", "--json"])
        self.assertEqual((code, json.loads(stream.getvalue())["code"]), (3, "E_PLAN_FIELD"))


@unittest.skipUnless(HAS_SAMPLE, SAMPLE_REASON)
class GuideExampleTests(unittest.TestCase):
    """The layout examples of AGENT_GUIDE.md pass apply --dry-run on the sample EPF."""

    def test_layout_examples(self) -> None:
        from one_c_form_compiler.cli import main
        from tests._support import SAMPLE
        guide = (Path(__file__).resolve().parents[1] / "src/one_c_form_compiler/AGENT_GUIDE.md").read_text(
            encoding="utf-8")
        blocks = [block.split("```", 1)[0] for block in guide.split("```json\n")[1:]]
        examples = [block for block in blocks if '"column"' in block or "set_form_size" in block
                    or '"shift"' in block]
        self.assertGreaterEqual(len(examples), 2)
        with tempfile.TemporaryDirectory() as tmp:
            for number, block in enumerate(examples):
                plan = Path(tmp) / f"plan{number}.json"
                plan.write_text(block, encoding="utf-8")
                stream = io.StringIO()
                with redirect_stdout(stream):
                    code = main(["apply", str(SAMPLE), "--plan", str(plan), "--dry-run", "--json"])
                self.assertEqual(code, 0, stream.getvalue())


if __name__ == "__main__":
    unittest.main()
