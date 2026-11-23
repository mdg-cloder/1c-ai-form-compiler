"""New panels with pages (add_panel), page client size, removing pages, panels and tables."""

from __future__ import annotations

import io
import json
import shutil
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path

from tests import test_table_field as _table
from tests._support import SAMPLE
from tests.test_table_field import HAS_SAMPLE, NESTED_PAGE, NESTED_PANEL, PAGE, PANEL, SAMPLE_REASON

NL = chr(13) + chr(10)
PAGES = ["Первая", {"name": "Вторая", "caption": {"ru": "Вторая", "uk": "Друга"}}]


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

    def add_panel(self, **extra):
        return self.apply({"op": "add_panel", "name": "ПанельПроверки", "pages": PAGES, **extra})

    def reopen(self):
        from one_c_form_compiler.core.oform_session import OformSession
        self.session.build_and_publish(None)
        self.session.__exit__(None, None, None)
        self.session = OformSession(self.form_path).__enter__()
        self.form = self.session.form()


@unittest.skipUnless(HAS_SAMPLE, SAMPLE_REASON)
class AddPanelTests(_Base):
    def test_panel_record_pages_and_guides(self) -> None:
        from one_c_form_compiler.core.panel import NO_VALUE, PANEL_UUID, panel_record_issues, panel_shape, tabs_shown
        passport = self.form.style_passport()
        result = self.add_panel()[0]
        self.assertEqual(result["op"], "add_panel")
        self.assertEqual([p["name"] for p in result["pages"]], ["Первая", "Вторая"])
        self.assertEqual(result["pages"][1]["caption"], {"ru": "Вторая", "uk": "Друга"})
        self.assertEqual(set(result["pages"][0]["caption"].values()), {"Первая"})
        # Default placement: like a table - full width, height 150, new row on the form.
        rect = result["rect"]
        self.assertEqual(rect["left"], passport["margin_left"])
        self.assertEqual(rect["width"], passport["client_width"] - 2 * passport["margin_left"])
        self.assertEqual(rect["height"], 150)
        key = self.form.find("ПанельПроверки")
        raw = self.form.data[key]["raw"]
        self.assertEqual(raw[0], PANEL_UUID)
        self.assertEqual(panel_record_issues(raw, 2), [])
        self.assertTrue(tabs_shown(raw))
        index = raw[2][1]
        marker = panel_shape(raw)["marker"]
        self.assertEqual(index[marker + 2:marker + 5], [NO_VALUE, NO_VALUE, "5"])
        panel = self.form.resolve_container("ПанельПроверки", "Вторая")[0]
        self.assertEqual(panel.pages, ["Первая", "Вторая"])
        for number, page in enumerate(panel.pages):
            info = self.form.data[panel.page_key(page)]["info"]
            self.assertEqual(sorted(entry[4] for entry in info), ["1", "2", "3", "4"])
            self.assertEqual({entry[5] for entry in info}, {str(number)})
            self.assertEqual(self.form.container_size(panel, page), (rect["width"] - 2, rect["height"] - 20))
        node = next(n for n in self.form.elem_json["tree"] if n["name"] == "ПанельПроверки")
        self.assertEqual(node["type"], "Panel")
        self.assertEqual(node["child"], [])
        self.assertEqual(self.form.info(key).tab_index, len(self.form.page_elements(*self.form.container_of(key))) - 1)
        self.assertFalse(self.form.new_issues())

    def test_explicit_rect_and_stretch(self) -> None:
        from one_c_form_compiler.core.ordinary_form import BOTTOM, RIGHT
        self.apply({"op": "add_page", "container": PANEL, "name": "Новая"})
        result = self.add_panel(container=PANEL, page="Новая", left=10, top=10, width=300, height=120,
                                stretch="both")[0]
        self.assertEqual(result["rect"], {"left": 10, "top": 10, "width": 300, "height": 120})
        layout = self.form._layout(self.form.find("ПанельПроверки"))
        self.assertEqual(layout.edges[RIGHT][1][1:3], ["0", str(RIGHT)])
        self.assertEqual(layout.edges[BOTTOM][1][1:3], ["0", str(BOTTOM)])
        self.assertFalse(self.form.new_issues())

    def test_elements_on_new_pages(self) -> None:
        self.add_panel(stretch="both")
        self.apply({"op": "add", "type": "Label", "name": "НадписьПервая", "caption": "Текст",
                    "container": "ПанельПроверки", "page": "Первая"},
                   {"op": "add", "type": "Table", "name": "ТаблицаВторая", "columns": ["А", "Б"],
                    "container": "ПанельПроверки", "page": "Вторая", "stretch": "both"},
                   {"op": "add", "type": "Label", "name": "НадписьЯвная", "caption": "Х",
                    "container": "ПанельПроверки", "page": "Первая", "left": 200, "top": 40, "width": 100,
                    "height": 19})
        panel = self.form.resolve_container("ПанельПроверки", "Первая")[0]
        label = self.form.info(self.form.find("НадписьПервая"))
        table = self.form.info(self.form.find("ТаблицаВторая"))
        self.assertEqual((label.container, label.page), (panel.key, "Первая"))
        self.assertEqual((table.container, table.page), (panel.key, "Вторая"))
        self.assertEqual(self.form._layout(table.key).page_index, 1)
        width, height = self.form.container_size(panel, "Вторая")
        self.assertLessEqual(table.rect.right, width)
        self.assertLessEqual(table.rect.bottom, height)
        self.assertFalse(self.form.new_issues())

    def test_nested_panel(self) -> None:
        self.apply({"op": "add_page", "container": PANEL, "name": "Новая"})
        self.add_panel(container=PANEL, page="Новая", left=6, top=6, width=400, height=150)
        nested = self.form.resolve_container("ПанельПроверки", "Первая")[0]
        owner, page = self.form.container_of(nested.key)
        self.assertEqual((owner.name, page), (PANEL, "Новая"))
        self.apply({"op": "add", "type": "Label", "name": "НадписьВложенная", "caption": "Текст",
                    "container": "ПанельПроверки", "page": "Вторая"})
        self.assertFalse(self.form.new_issues())
        # A nested parent panel does not grow: a default 150 px panel does not fit the hint page.
        self.assertEqual(self.error({"op": "add_panel", "name": "ЕщеПанель", "pages": ["А"],
                                     "container": NESTED_PANEL, "page": NESTED_PAGE}), "E_NO_SPACE")

    def test_errors(self) -> None:
        self.assertEqual(self.error({"op": "add_panel", "name": PANEL, "pages": ["А"]}), "E_NAME_EXISTS")
        self.assertEqual(self.error({"op": "add_panel", "name": "П", "pages": []}), "E_PLAN_FIELD")
        self.assertEqual(self.error({"op": "add_panel", "name": "П"}), "E_PLAN_FIELD")
        self.assertEqual(self.error({"op": "add_panel", "name": "П", "pages": ["1А"]}), "E_NAME_INVALID")
        self.assertEqual(self.error({"op": "add_panel", "name": "П", "pages": ["А", "а"]}), "E_NAME_EXISTS")
        self.assertEqual(self.error({"op": "add_panel", "name": "П", "pages": [{"title": "А"}]}), "E_PLAN_FIELD")
        self.assertEqual(self.error({"op": "add_panel", "name": "П", "pages": ["А"], "caption": "x"}), "E_PLAN_FIELD")
        self.assertEqual(self.error({"op": "add_panel", "name": "П", "pages": ["А"], "width": 20}),
                         "E_GEOMETRY_INVALID")
        # Explicit geometry outside a nested panel page: the nested panel does not grow (the form would).
        self.assertEqual(self.error({"op": "add_panel", "name": "П", "pages": ["А"], "left": 10, "top": 10,
                                     "width": 300, "height": 400, "container": NESTED_PANEL,
                                     "page": NESTED_PAGE}), "E_GEOMETRY_OUTSIDE")
        self.assertEqual(self.error({"op": "add", "type": "Панель", "name": "П"}), "E_TYPE_UNSUPPORTED")
        self.assertEqual(self.error({"op": "add_panel", "name": "П", "pages": ["А"], "container": "Нет"}),
                         "E_CONTAINER_UNKNOWN")

    def test_rebuild_and_reload_keep_everything_consistent(self) -> None:
        from one_c_form_compiler.core.panel import panel_record_issues
        baseline = set(self.form.integrity_issues())
        self.add_panel(stretch="both")
        self.reopen()
        panel = self.form.resolve_container("ПанельПроверки", "Вторая")[0]
        self.assertEqual(panel.pages, ["Первая", "Вторая"])
        self.apply({"op": "add", "type": "Label", "name": "НадписьВторая", "caption": "Текст",
                    "container": "ПанельПроверки", "page": "Вторая"},
                   {"op": "add", "type": "Table", "name": "ТаблицаВторая", "columns": ["А", "Б"],
                    "container": "ПанельПроверки", "page": "Вторая", "stretch": "both"})
        self.reopen()
        panel = self.form.resolve_container("ПанельПроверки", "Вторая")[0]
        self.assertEqual(panel_record_issues(self.form.data[panel.key]["raw"], 2), [])
        for name in ("НадписьВторая", "ТаблицаВторая"):
            info = self.form.info(self.form.find(name))
            self.assertEqual((info.container, info.page), (panel.key, "Вторая"))
        self.assertLessEqual(set(self.form.integrity_issues()), baseline)
        # Guides survive v8unpack's positional slicing: each page holds its own four border guides.
        for number, page in enumerate(panel.pages):
            info = self.form.data[panel.page_key(page)]["info"]
            self.assertEqual({entry[5] for entry in info}, {str(number)})


@unittest.skipUnless(HAS_SAMPLE, SAMPLE_REASON)
class PageSizeTests(_Base):
    """A new page gets the panel's client area, not 0x0."""

    def test_add_page_then_default_and_explicit_elements(self) -> None:
        panel = self.form.resolve_container(PANEL, PAGE)[0]
        sibling = self.form.container_size(panel, PAGE)
        self.apply({"op": "add_page", "container": PANEL, "name": "Итоги"})
        panel = self.form.resolve_container(PANEL, "Итоги")[0]
        self.assertEqual(self.form.container_size(panel, "Итоги"), sibling)
        self.apply({"op": "add", "type": "Label", "name": "Н1", "caption": "Текст", "container": PANEL, "page": "Итоги"},
                   {"op": "add", "type": "Table", "name": "Т1", "columns": ["А"], "container": PANEL,
                    "page": "Итоги"},
                   {"op": "add", "type": "Label", "name": "Н2", "caption": "Текст", "container": PANEL,
                    "page": "Итоги", "left": 400, "top": 10, "width": 100, "height": 19})
        self.assertFalse(self.form.new_issues())

    def test_degenerate_template_guides(self) -> None:
        from one_c_form_compiler.core.panel import border_guides, guide_size
        panel = self.form.resolve_container(PANEL, PAGE)[0]
        sibling = self.form.container_size(panel, PAGE)
        for page in panel.pages:                          # every guide collapsed to 0
            for entry in self.form.data[panel.page_key(page)]["info"]:
                entry[1], entry[7] = "0", "0"
        self.apply({"op": "add_page", "container": PANEL, "name": "Итоги"})
        panel = self.form.resolve_container(PANEL, "Итоги")[0]
        self.assertEqual(guide_size(border_guides(self.form, panel)[str(len(panel.pages) - 1)]), sibling)
        self.assertEqual(self.form.container_size(panel, "Итоги"), sibling)
        self.apply({"op": "add", "type": "Label", "name": "Н1", "caption": "Текст", "container": PANEL,
                    "page": "Итоги"})


@unittest.skipUnless(HAS_SAMPLE, SAMPLE_REASON)
class RemoveTests(_Base):
    def test_remove_page(self) -> None:
        from one_c_form_compiler.core.panel import panel_record_issues
        self.add_panel()
        self.apply({"op": "add_page", "container": "ПанельПроверки", "name": "Третья"},
                   {"op": "add", "type": "Label", "name": "НадписьВторая", "caption": "Т", "container": "ПанельПроверки",
                    "page": "Вторая"},
                   {"op": "add", "type": "Label", "name": "НадписьТретья", "caption": "Т", "container": "ПанельПроверки",
                    "page": "Третья"})
        self.assertEqual(self.error({"op": "remove_page", "container": "ПанельПроверки", "page": "Вторая"}),
                         "E_ELEMENT_REFERENCED")
        result = self.apply({"op": "remove_page", "container": "ПанельПроверки", "page": "Вторая", "force": True})
        self.assertEqual(result[0]["removed"], ["НадписьВторая"])
        self.assertFalse(self.form.has_element("НадписьВторая"))
        panel = self.form.resolve_container("ПанельПроверки", "Первая")[0]
        self.assertEqual(panel.pages, ["Первая", "Третья"])
        self.assertEqual(panel_record_issues(self.form.data[panel.key]["raw"], 2), [])
        self.assertEqual(self.form._layout(self.form.find("НадписьТретья")).page_index, 1)
        self.assertFalse(self.form.new_issues())
        self.reopen()
        info = self.form.info(self.form.find("НадписьТретья"))
        self.assertEqual(info.page, "Третья")
        self.assertEqual(self.error({"op": "remove_page", "container": "ПанельПроверки", "page": "Нет"}),
                         "E_PAGE_UNKNOWN")

    def test_remove_last_page_and_empty_page(self) -> None:
        self.apply({"op": "add_panel", "name": "П", "pages": ["А", "Б"]},
                   {"op": "remove_page", "container": "П", "page": "А"})
        self.assertEqual(self.form.resolve_container("П", "Б")[0].pages, ["Б"])
        self.assertEqual(self.error({"op": "remove_page", "container": "П", "page": "Б"}), "E_PLAN_FIELD")

    def test_remove_sample_page_with_nested_panel(self) -> None:
        panel = self.form.resolve_container(PANEL, PAGE)[0]
        before = list(panel.pages)
        self.apply({"op": "remove_page", "container": PANEL, "page": PAGE, "force": True})
        panel = self.form.resolve_container(PANEL, before[0])[0]
        self.assertEqual(panel.pages, [p for p in before if p != PAGE])
        self.assertFalse(self.form.has_element(NESTED_PANEL))
        self.assertFalse(self.form.new_issues())
        self.reopen()

    def test_remove_panel_with_contents(self) -> None:
        self.add_panel()
        self.apply({"op": "add", "type": "Table", "name": "ТаблицаВторая", "columns": ["А"],
                    "container": "ПанельПроверки", "page": "Вторая", "top": 40, "height": 60},
                   {"op": "add_command_panel", "name": "КоманднаяПанельВторая", "table": "ТаблицаВторая"})
        result = self.apply({"op": "remove", "name": "ПанельПроверки"})
        self.assertEqual(sorted(result[0]["also_removed"]), ["КоманднаяПанельВторая", "ТаблицаВторая"])
        self.assertFalse(any(key.startswith(("ПанельПроверки", "Страница1/ПанельПроверки")) for key in self.form.data))
        self.assertFalse(self.form.new_issues())
        self.reopen()
        self.assertFalse(self.form.has_element("ТаблицаВторая"))

    def test_remove_sample_panel_needs_force_when_referenced(self) -> None:
        names = [n for n in (PANEL, NESTED_PANEL) if self.form.module_references(n)]
        if names:
            self.assertEqual(self.error({"op": "remove", "name": PANEL}), "E_ELEMENT_REFERENCED")
        self.apply({"op": "remove", "name": PANEL, "force": True})
        self.assertFalse(self.form.has_element(NESTED_PANEL))
        self.assertFalse(self.form.new_issues())
        self.reopen()

    def test_remove_table_with_its_command_panel(self) -> None:
        self.apply({"op": "add_page", "container": PANEL, "name": "Новая"},
                   {"op": "add", "type": "Table", "name": "ВТ", "columns": ["А", "Б"], "container": PANEL,
                    "page": "Новая", "top": 40},
                   {"op": "add_command_panel", "name": "КоманднаяПанельВТ", "table": "ВТ"})
        result = self.apply({"op": "remove", "name": "ВТ"})
        self.assertEqual(result[0]["also_removed"], ["КоманднаяПанельВТ"])
        self.assertFalse(self.form.has_element("КоманднаяПанельВТ"))
        table_id = str(self.form.id_counter - 1)
        self.assertFalse(any(entry[0] == table_id for entry in self.form.datasources[1:]))
        self.assertFalse(self.form.new_issues())
        self.reopen()

    def test_module_references_block_removal_without_force(self) -> None:
        self.apply({"op": "add_panel", "name": "П", "pages": ["А", "Б"]},
                   {"op": "add", "type": "Table", "name": "ВТ", "columns": ["А"], "container": "П", "page": "Б",
                    "top": 40, "height": 60},
                   {"op": "add_command_panel", "name": "КоманднаяПанельВТ", "table": "ВТ"})
        self.form.module_text += NL + "ЭлементыФормы.КоманднаяПанельВТ.Доступность = Ложь;" + NL
        self.assertEqual(self.error({"op": "remove", "name": "ВТ"}), "E_ELEMENT_REFERENCED")
        self.form.module_text += "ЭлементыФормы.П.ТекущаяСтраница = ЭлементыФормы.П.Страницы.А;" + NL
        self.assertEqual(self.error({"op": "remove_page", "container": "П", "page": "А"}), "E_ELEMENT_REFERENCED")
        self.apply({"op": "remove_page", "container": "П", "page": "А", "force": True})
        self.assertEqual(self.error({"op": "remove", "name": "П"}), "E_ELEMENT_REFERENCED")
        result = self.apply({"op": "remove", "name": "П", "force": True})
        self.assertEqual(sorted(result[0]["also_removed"]), ["ВТ", "КоманднаяПанельВТ"])
        self.assertFalse(self.form.new_issues())


@unittest.skipUnless(HAS_SAMPLE, SAMPLE_REASON)
class PanelCliTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="panel-cli-"))

    def tearDown(self) -> None:
        shutil.rmtree(self.tmp, ignore_errors=True)

    def run_cli(self, *argv) -> tuple[int, dict]:
        from one_c_form_compiler.cli import main
        buffer = io.StringIO()
        with redirect_stdout(buffer):
            code = main([str(a) for a in argv])
        return code, json.loads(buffer.getvalue())

    def test_short_form_on_epf_and_inspect(self) -> None:
        out = self.tmp / "out.epf"
        code, payload = self.run_cli("add", SAMPLE, "-o", out, "--type", "Panel", "--name", "ПанельПроверки",
                                     "--page", "Первая", "--page", "Вторая", "--stretch", "both", "--json")
        self.assertEqual(code, 0, payload)
        self.assertEqual(payload["changes"][0]["op"], "add_panel")
        code, payload = self.run_cli("inspect", out, "--json")
        self.assertEqual(code, 0)
        panel = next(e for e in payload["elements"] if e["name"] == "ПанельПроверки")
        self.assertEqual(panel["type"], "Panel")
        self.assertEqual([p["name"] for p in panel["pages"]], ["Первая", "Вторая"])
        self.assertEqual(set(panel["pages"][1]["caption"].values()), {"Вторая"})
        pages = [(p["container"], p["page"]) for p in payload["pages"]]
        self.assertIn(("ПанельПроверки", "Вторая"), pages)
        self.assertEqual(payload["known_issues"], self.known_issues())

    def known_issues(self) -> list:
        from one_c_form_compiler.cli import main
        buffer = io.StringIO()
        with redirect_stdout(buffer):
            main(["inspect", str(SAMPLE), "--json"])
        return json.loads(buffer.getvalue())["known_issues"]

    def test_short_form_errors(self) -> None:
        code, payload = self.run_cli("add", SAMPLE, "--dry-run", "--type", "Panel", "--name", "П", "--json")
        self.assertEqual((code, payload["code"]), (3, "E_PLAN_FIELD"))
        code, payload = self.run_cli("add", SAMPLE, "--dry-run", "--type", "Label", "--name", "Н", "--page", "А",
                                     "--page", "Б", "--json")
        self.assertEqual((code, payload["code"]), (2, "E_USAGE"))


if __name__ == "__main__":
    unittest.main()
