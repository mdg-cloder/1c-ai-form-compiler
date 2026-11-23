"""Ordinary form model, edit plans, AI planning and CLI on the sample EPF.

The sample EPF (tests/fixtures/sample_ordinary.epf, see tests/fixtures/README.md) is
decoded once with v8unpack 1.2.13; the tests are skipped when v8unpack is not
installed or the sample is missing.
"""

from __future__ import annotations

import copy
import hashlib
import io
import json
import shutil
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch

from one_c_form_compiler.core.ai import AIConfig, AIError
from one_c_form_compiler.core.ai_plan import ScriptedAdapter, extract_json, generate_plan
from one_c_form_compiler.core.edit_plan import PlanError, apply_plan, parse_plan
from one_c_form_compiler.core.ordinary_form import (
    BOTTOM, LEFT, RIGHT, TOP, FormModelError, OrdinaryForm, Rect, find_form_dirs,
)
from one_c_form_compiler.core.runtime_check import REPORT_DONE, inject_probe, parse_report
from one_c_form_compiler.core.standards import standard_template, standard_types

from tests._support import HAS_SAMPLE, SAMPLE, SAMPLE_REASON

# The sample's form (see tests/fixtures/README.md): a typical document form, 650x363, 37 elements.
SAMPLE_TITLE = "Корректировка записей регистров"
SAMPLE_SIZE = (650, 363)
SAMPLE_ELEMENT_COUNT = 37
SAMPLE_ELEMENTS = {"Надпись1", "Номер", "Надпись2", "Дата", "Надпись3", "Комментарий", "Надпись4", "Ответственный",
                   "ПанельРегистров", "ДействияФормы", "ОсновныеДействияФормы", "ТабличноеПолеДвиженияБУ"}
SAMPLE_FIELDS = {"Номер", "Дата", "Комментарий", "Ответственный"}
PANEL = "ПанельРегистров"                     # top-level panel; every page is filled by a nested panel
NESTED_PANEL = "ПанельРегистровСведений"      # nested panel; its page has free room below two labels
NESTED_PAGE = "Подсказка"


def binding_violations(form: OrdinaryForm) -> list[str]:
    """Every binding offset must equal own edge coordinate minus target border coordinate."""
    problems = []
    by_id = {form.data[key]["raw"][1]: key for key in form.element_keys()}
    for key in form.element_keys():
        container, page = form.container_of(key)
        width, height = form.container_size(container, page)
        layout = form._layout(key)
        for edge_index, _slot, bind in layout.bindings():
            if edge_index > 3:
                continue
            border = int(bind[2])
            if bind[1] == "0":
                coord = {TOP: 0, LEFT: 0, BOTTOM: height, RIGHT: width}[border]
            else:
                coord = form._layout(by_id[bind[1]]).rect.edge(border)
            if layout.rect.edge(edge_index) - coord != int(bind[3]):
                problems.append(f"{key} edge {edge_index}: {bind}")
    return problems


def anchor_target(form: OrdinaryForm) -> str | None:
    """Name of a plain element other elements are anchored to and the module does not use, if any."""
    by_id = {form.data[key]["raw"][1]: key for key in form.element_keys()}
    targets = []
    for key in form.element_keys():
        for edge_index, _slot, bind in form._layout(key).bindings():
            target = by_id.get(bind[1])
            if edge_index <= 3 and bind[1] != "0" and target is not None and target != key:
                targets.append(target)
    module = form.module_text or ""
    plain = [key for key in targets if form._type(key) in ("Label", "Field", "CheckBox", "Button")
             and f"ЭлементыФормы.{form._name(key)}" not in module]
    return form._name(plain[0]) if plain else None


def anchor_edge(form: OrdinaryForm, dependent: str, edge: int, target: str, border: int) -> None:
    """Bind ``edge`` of ``dependent`` to ``border`` of ``target`` the way Designer stores it.

    The container binding of that edge (and its panel-index entry) is replaced by an element binding
    with offset = own edge - target border, and the target gets the matching reverse entry.
    """
    from one_c_form_compiler.core.ordinary_form import _panel_index_groups, _panel_index_write
    dep_key, target_key = form.find(dependent), form.find(target)
    dep_id, target_id = form.data[dep_key]["raw"][1], form.data[target_key]["raw"][1]
    layout, target_layout = form._layout(dep_key), form._layout(target_key)
    old = layout.edges[edge][1]
    if old[1] == "0":
        container, _page = form.container_of(dep_key)
        groups = _panel_index_groups(container.raw_index)
        groups[int(old[2])] = [e for e in groups[int(old[2])] if (e[1], e[2]) != (dep_id, str(edge))]
        _panel_index_write(container.raw_index, groups)
    offset = layout.rect.edge(edge) - target_layout.rect.edge(border)
    layout.edges[edge][1] = ["2", target_id, str(border), str(offset)]
    target_layout.reverse[border].append([layout.edges[edge][0], dep_id, str(edge)])
    layout.write()
    target_layout.write()


@unittest.skipUnless(HAS_SAMPLE, SAMPLE_REASON)
class OrdinaryFormTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        from one_c_form_compiler.core.ordinary_backend import Backend
        cls.tmp = Path(tempfile.mkdtemp(prefix="ofm-test-"))
        (cls.tmp / "t").mkdir()
        Backend.discover().extract(SAMPLE, cls.tmp / "src", cls.tmp / "t")
        cls.form_dir = find_form_dirs(cls.tmp / "src")[0]

    @classmethod
    def tearDownClass(cls) -> None:
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def setUp(self) -> None:
        self.work = Path(tempfile.mkdtemp(prefix="ofm-case-"))
        shutil.copytree(self.form_dir, self.work / "Form")
        self.form = OrdinaryForm.load(self.work / "Form")

    def tearDown(self) -> None:
        shutil.rmtree(self.work, ignore_errors=True)

    def assertConsistent(self, form: OrdinaryForm | None = None) -> None:
        form = form or self.form
        self.assertEqual(form.new_issues(), [])
        self.assertEqual(binding_violations(form), [])

    # ---------------------------------------------------------------- reading
    def test_sample_is_read_and_binding_formula_holds(self) -> None:
        names = {control.name for control in self.form.controls()}
        self.assertLessEqual(SAMPLE_ELEMENTS, names)
        self.assertEqual(len(names), SAMPLE_ELEMENT_COUNT)
        self.assertEqual(self.form.size, SAMPLE_SIZE)
        self.assertEqual(self.form.title["ru"], SAMPLE_TITLE)
        self.assertEqual(binding_violations(self.form), [])
        # A designer-made form may keep stale panel and reverse anchors; they stay in the baseline.
        issues = self.form.integrity_issues()
        self.assertTrue(issues)
        self.assertTrue(all(issue.startswith(("stale panel anchor", "stale reverse anchor")) for issue in issues),
                        issues)
        self.assertEqual(self.form.new_issues(), [])

    def test_style_passport_of_root_page(self) -> None:
        passport = self.form.style_passport()

        def rect(name: str) -> Rect:
            return self.form.info(self.form.find(name)).rect
        # Root page: labels Надпись1..4 at the left edge; the rows Ответственный and Комментарий
        # (label + field each) set the field column and the row pitch.
        self.assertEqual(passport["page"], "Страница1")
        self.assertEqual(passport["label_left"], rect("Надпись1").left)
        self.assertEqual(passport["label_left"], rect("Надпись3").left)
        self.assertEqual(passport["field_left"], rect("Комментарий").left)
        self.assertEqual(passport["field_left"], rect("Ответственный").left)
        self.assertEqual(passport["row_height"], rect("Комментарий").height)
        self.assertEqual(passport["row_pitch"], rect("Комментарий").top - rect("Ответственный").top)

    # ---------------------------------------------------------------- add
    def test_add_every_type_with_auto_placement(self) -> None:
        counter = self.form.id_counter
        height = self.form.size[1]
        for element_type in ("Label", "Field", "Button", "CheckBox", "Group"):
            rect = self.form.place(element_type)
            info = self.form.add_element(element_type=element_type, name="Новый" + element_type, rect=rect,
                                         value_type="Число(10,2)" if element_type == "Field" else None)
            self.assertEqual(info.rect, rect)
            self.assertConsistent()
        self.assertEqual(self.form.id_counter, counter + 5)
        # The root page of the sample has no free row: the new rows make the form taller.
        self.assertGreater(self.form.size[1], height)
        new = [c for c in self.form.controls() if c.name.startswith("Новый")]
        others = [c for c in self.form.controls() if not c.name.startswith("Новый") and not c.container]
        for control in new:
            self.assertFalse(any(control.rect.overlaps(o.rect) for o in others if o.type != "Panel"),
                             control.name)

    def test_new_row_below_stretching_panel_follows_form_bottom(self) -> None:
        # ПанельРегистров stretches with the form (its bottom follows the form bottom), so a row added
        # below it must follow the form bottom too.
        panel_bottom = self.form._layout(self.form.find(PANEL)).edges[BOTTOM][1]
        self.assertEqual((panel_bottom[1], int(panel_bottom[2])), ("0", BOTTOM))
        rect = self.form.place("Field")
        info = self.form.add_element(element_type="Field", name="ПолеВнизу", rect=rect)
        layout = self.form._layout(info.key)
        top_bind = layout.edges[TOP][1]
        self.assertEqual((top_bind[1], int(top_bind[2])), ("0", BOTTOM))

    def test_add_on_panel_page_and_full_page_grows_panel(self) -> None:
        # A page of a nested panel with free room.
        info = self.form.add_element(element_type="Label", name="НаСтранице",
                                     rect=self.form.place("Label", container=NESTED_PANEL, page=NESTED_PAGE),
                                     container=NESTED_PANEL, page=NESTED_PAGE, caption="Текст")
        self.assertEqual((info.container.split("/")[-1], info.page), (NESTED_PANEL, NESTED_PAGE))
        self.assertConsistent()
        # A new empty page Дополнительно of the top-level panel: fill it without growing the panel until
        # there is no room left.
        apply_plan(self.form, {"operations": [{"op": "add_page", "container": PANEL, "name": "Дополнительно"}]})
        for index in range(200):
            try:
                rect = self.form.place("Label", container=PANEL, page="Дополнительно", grow=False)
            except FormModelError as error:
                self.assertEqual(error.code, "E_NO_SPACE")
                break
            self.form.add_element(element_type="Label", name=f"Заполнитель{index}", rect=rect,
                                  container=PANEL, page="Дополнительно")
        else:
            self.fail("page Дополнительно never ran out of room")
        self.assertGreater(index, 0)
        self.assertConsistent()
        panel_before = self.form.info(self.form.find(PANEL)).rect
        form_height = self.form.size[1]
        rect = self.form.place("Label", container=PANEL, page="Дополнительно")
        self.form.add_element(element_type="Label", name="НаПолнойСтранице", rect=rect,
                              container=PANEL, page="Дополнительно")
        panel_after = self.form.info(self.form.find(PANEL)).rect
        growth = panel_after.height - panel_before.height
        self.assertGreater(growth, 0)
        self.assertEqual(self.form.size[1], form_height + growth)
        self.assertEqual(panel_after.top, panel_before.top)
        self.assertConsistent()

    def test_add_validates_name_type_and_geometry(self) -> None:
        cases = [
            (dict(element_type="Label", name="1Плохое", rect=Rect(8, 8, 50, 27)), "E_NAME_INVALID"),
            (dict(element_type="Label", name="Номер", rect=Rect(8, 8, 50, 27)), "E_NAME_EXISTS"),
            (dict(element_type="Panel", name="Т", rect=Rect(8, 8, 50, 27)), "E_TYPE_UNSUPPORTED"),
            (dict(element_type="Table", name="Т2", rect=Rect(8, 8, 50, 27)), "E_DATA_REQUIRED"),
            (dict(element_type="Label", name="Широкая", rect=Rect(8, 8, self.form.size[0] + 100, 27)),
             "E_GEOMETRY_OUTSIDE"),
            (dict(element_type="Group", name="Обр", rect=Rect(8, 8, 50, 27), handler="Х"), "E_HANDLER_UNSUPPORTED"),
            (dict(element_type="Field", name="Обр2", rect=Rect(8, 8, 50, 27), events={"Нажатие": "Х"}),
             "E_HANDLER_UNSUPPORTED"),
            (dict(element_type="Label", name="Обр3", rect=Rect(8, 8, 50, 27), data="Х"), "E_DATA_UNSUPPORTED"),
        ]
        for kwargs, code in cases:
            with self.subTest(code=code), self.assertRaises(FormModelError) as caught:
                self.form.add_element(**kwargs)
            self.assertEqual(caught.exception.code, code)

    def test_template_is_the_dominant_plain_field_style(self) -> None:
        raw = self.form._template_raw("Field", None)
        # Комментарий and Ответственный share the dominant look (548x19, no choice button); among them
        # the one with fewer handlers and the lower id wins.
        self.assertIn(raw[-2][1], {f'"{name}"' for name in SAMPLE_FIELDS})
        self.assertEqual(raw[-2][1], '"Комментарий"')

    def test_built_in_templates_work_without_examples_in_form(self) -> None:
        from one_c_form_compiler.core.edt_metadata import TabularSection
        self.assertEqual(set(standard_types()), {"Label", "Field", "Button", "CheckBox", "Group", "Table",
                                                 "TableColumn", "CommandPanel", "Panel"})
        self.form.tabular_sections = {"Строки": TabularSection("Строки", "dddddddd-0000-0000-0000-00000000000a", {})}
        for element_type in standard_types():
            if element_type in ("TableColumn", "CommandPanel", "Panel"):   # add_column / add_command_panel / add_panel
                continue
            data = {"data": "Строки"} if element_type == "Table" else {}
            with patch.object(OrdinaryForm, "_template_raw", lambda _self, kind, _t: standard_template(kind)):
                self.form.add_element(element_type=element_type, name="Станд" + element_type,
                                      rect=self.form.place(element_type), **data)
            self.assertConsistent()

    def test_button_handler_stub_goes_before_module_body(self) -> None:
        self.form.module_text = ("Перем А;\r\n\r\nПроцедура Существующая()\r\nКонецПроцедуры\r\n\r\n"
                                 "// ОПЕРАТОРЫ ОСНОВНОЙ ПРОГРАММЫ\r\nА = 1;\r\n")
        self.form.add_element(element_type="Button", name="КнопкаТест", rect=self.form.place("Button"),
                              handler="КнопкаТестНажатие")
        text = self.form.module_text
        stub = text.index("Процедура КнопкаТестНажатие(Элемент)")
        body = text.index("ОПЕРАТОРЫ ОСНОВНОЙ ПРОГРАММЫ")
        self.assertLess(stub, body)
        self.assertEqual(self.form.info(self.form.find("КнопкаТест")).handlers, ["КнопкаТестНажатие"])
        self.assertFalse(self.form.ensure_procedure("КнопкаТестНажатие", "Элемент"))

    def test_events_are_written_sorted_with_stubs(self) -> None:
        info = self.form.add_element(element_type="Field", name="ПолеС", rect=self.form.place("Field"),
                                     handler="ПолеСПриИзменении",
                                     events={"НачалоВыбора": "ПолеСНачалоВыбора", "ОбработкаВыбора": "ПолеСОбработка"})
        self.assertEqual(self.form.events(info.key), {"НачалоВыбора": "ПолеСНачалоВыбора",
                                                      "ОбработкаВыбора": "ПолеСОбработка",
                                                      "ПриИзменении": "ПолеСПриИзменении"})
        events = self.form.data[info.key]["raw"][2][4]
        self.assertEqual([entry[0] for entry in events[1:]], ["2", "7", "2147483647"])
        self.assertIn("Процедура ПолеСОбработка(Элемент, ВыбранноеЗначение, СтандартнаяОбработка)",
                      self.form.module_text)
        self.form.add_element(element_type="Label", name="НадписьС", rect=self.form.place("Label"), handler="НадписьСНажатие")
        self.assertConsistent()

    def _fake_object(self) -> None:
        from one_c_form_compiler.core.object_metadata import ObjectAttribute
        self.form.object_type_id = "11111111-2222-3333-4444-555555555555"
        self.form.object_attributes = {
            "Скидка": ObjectAttribute("Скидка", "aaaaaaaa-0000-0000-0000-000000000001", ['"Pattern"', ['"N"', "5", "2", "0"]]),
            "Флаг": ObjectAttribute("Флаг", "aaaaaaaa-0000-0000-0000-000000000002", ['"Pattern"', ['"B"']]),
        }

    def test_data_binding_record_matches_designer_format(self) -> None:
        self._fake_object()
        self.form.elem_json["props"][0]["raw"][5] = ['"Pattern"', ['"#"', self.form.object_type_id]]
        info = self.form.add_element(element_type="Field", name="ПолеСкидка", rect=self.form.place("Field"), data="Скидка")
        self.assertEqual(info.data, "Скидка")
        self.assertEqual(info.value_type, ['"Pattern"', ['"N"', "5", "2", "0"]])
        record = self.form._datasource(str(info.id))
        main_id = self.form._root[2][0][0]
        self.assertEqual(record, [str(info.id), ["2", [main_id], ["0", "aaaaaaaa-0000-0000-0000-000000000001"]]])
        self.assertEqual(self.form.data[info.key]["prop"], self.form._main_prop()["name"])
        self.assertEqual(int(self.form.datasources[0]), len(self.form.datasources) - 1)
        with self.assertRaises(FormModelError) as caught:
            self.form.add_element(element_type="CheckBox", name="ФлагСкидка", rect=self.form.place("CheckBox"),
                                  data="Скидка")
        self.assertEqual(caught.exception.code, "E_DATA_TYPE")
        self.form.add_element(element_type="CheckBox", name="ФлагОк", rect=self.form.place("CheckBox"), data="Флаг")
        with self.assertRaises(FormModelError) as caught:
            self.form.add_element(element_type="Field", name="ПолеНет", rect=self.form.place("Field"), data="Нет")
        self.assertEqual(caught.exception.code, "E_DATA_UNKNOWN")
        self.assertConsistent()

    def test_data_binding_warns_on_form_of_another_object(self) -> None:
        self._fake_object()        # main attribute keeps the object type of the source form
        self.form.add_element(element_type="Field", name="ПолеСкидка", rect=self.form.place("Field"), data="Скидка")
        self.assertEqual(len(self.form.warnings), 1)
        owner = self.form._main_prop()["raw"][5][1][1]
        self.assertEqual([(a.name, o) for a, o in self.form.bound_attributes()], [("Скидка", owner)])

    def test_rows_added_in_one_session_keep_their_order(self) -> None:
        tops = []
        for index in range(3):
            info = self.form.add_element(element_type="Label", name=f"Строка{index}", rect=self.form.place("Label"))
            tops.append(info.rect.top)
        current = [self.form.info(self.form.find(f"Строка{index}")).rect.top for index in range(3)]
        self.assertEqual(current, sorted(current))
        self.assertEqual(len(set(current)), 3)
        self.assertConsistent()

    def test_width_follows_caption_text(self) -> None:
        short = self.form.place("CheckBox", text="Да")
        long = self.form.place("CheckBox", text="Очень длинный заголовок флажка для проверки")
        self.assertGreater(long.width, short.width)

    # ---------------------------------------------------------------- modify / remove
    def test_modify_caption_tooltip_and_move(self) -> None:
        info = self.form.modify_element("Надпись1", caption="№:", tooltip="Номер документа",
                                        rect=Rect.from_xywh(8, 30, 44, 19))
        self.assertEqual(info.caption, {lang: "№:" for lang in self.form.languages()})
        self.assertEqual(info.tooltip["ru"], "Номер документа")
        self.assertEqual(info.rect, Rect.from_xywh(8, 30, 44, 19))
        self.assertConsistent()

    def test_modify_value_type_of_bound_field_refused(self) -> None:
        with self.assertRaises(FormModelError) as caught:
            self.form.modify_element("Номер", value_type="Число(5,0)")
        self.assertEqual(caught.exception.code, "E_VALUE_TYPE_BOUND")

    def test_remove_element_with_dependents(self) -> None:
        # In the sample only panels are anchors of other elements; bind the field Номер to the right
        # border of its label Надпись1, as Designer does for a "label + field" row.
        self.assertIsNone(anchor_target(self.form))
        anchor_edge(self.form, "Номер", LEFT, "Надпись1", RIGHT)
        self.assertConsistent()
        target = anchor_target(self.form)        # other elements are anchored to it
        self.assertEqual(target, "Надпись1")
        count = len(self.form.element_keys())
        number_rect = self.form.info(self.form.find("Номер")).rect
        self.form.remove_element(target)
        self.assertConsistent()
        self.assertEqual(len(self.form.element_keys()), count - 1)
        # The dependent edge is rebound to the form and the field stays where it was.
        left_bind = self.form._layout(self.form.find("Номер")).edges[LEFT][1]
        self.assertEqual(left_bind[1:3], ["0", str(LEFT)])
        self.assertEqual(self.form.info(self.form.find("Номер")).rect, number_rect)

    def test_remove_referenced_by_module_needs_force(self) -> None:
        self.assertNotIn("ЭлементыФормы.Надпись3", self.form.module_text)
        self.form.module_text += "\nЭлементыФормы.Надпись3.Видимость = Ложь;\n"
        with self.assertRaises(FormModelError) as caught:
            self.form.remove_element("Надпись3")
        self.assertEqual(caught.exception.code, "E_ELEMENT_REFERENCED")
        self.form.remove_element("Надпись3", force=True)
        self.assertFalse(self.form.has_element("Надпись3"))
        self.assertConsistent()

    def test_remove_default_button_refused(self) -> None:
        key = self.form._key_by_id(self.form.default_button_id)
        if key is None:
            self.skipTest("default button is inside a command panel")
        with self.assertRaises(FormModelError):
            self.form.remove_element(self.form._name(key))

    def test_set_title_keeps_languages(self) -> None:
        languages = self.form.languages()
        self.form.set_title("Тест")
        self.assertEqual(self.form.title, {lang: "Тест" for lang in languages})

    def test_save_and_reload_roundtrip(self) -> None:
        self.form.add_element(element_type="Label", name="Сохр", rect=self.form.place("Label"))
        self.form.save()
        again = OrdinaryForm.load(self.work / "Form")
        self.assertTrue(again.has_element("Сохр"))
        self.assertConsistent(again)

    # ---------------------------------------------------------------- plans & AI
    def test_plan_with_label_row_and_beside(self) -> None:
        field_left = self.form.style_passport()["field_left"]
        results = apply_plan(self.form, {"operations": [
            # The sample's fields span the whole form width; a narrower field leaves room for the button.
            {"op": "add", "type": "ПолеВвода", "name": "Наценка", "label": "Наценка:", "value_type": "Число(5,2)",
             "width": 120},
            {"op": "add", "type": "Кнопка", "name": "Пересчитать", "beside": "Наценка"},
        ]})
        label, field, button = results
        self.assertEqual(label["name"], "НадписьНаценка")
        self.assertEqual(label["top"], field["top"])
        self.assertEqual(field["left"], field_left)
        self.assertGreater(button["left"], field["left"] + field["width"])
        self.assertConsistent()

    def test_plan_errors_are_located(self) -> None:
        with self.assertRaises(PlanError) as caught:
            apply_plan(self.form, [{"op": "add", "type": "Label", "name": "А"},
                                   {"op": "remove", "name": "НетТакого"}])
        self.assertEqual(caught.exception.index, 1)
        self.assertEqual(caught.exception.code, "E_ELEMENT_NOT_FOUND")
        for bad in ("{", {"x": 1}, {"operations": []}, [{"op": "drop"}], [{"op": "add", "type": "Panel"}],
                    [{"op": "modify", "name": "А", "color": 1}]):
            with self.subTest(bad=bad), self.assertRaises(PlanError):
                parse_plan(copy.deepcopy(bad))

    def test_ai_plan_repairs_then_succeeds(self) -> None:
        adapter = ScriptedAdapter([
            '{"operations":[{"op":"modify","name":"НетТакого","caption":"x"}]}',
            '```json\n{"operations":[{"op":"add","type":"Field","name":"Наценка","label":"Наценка:"}]}\n```',
        ])
        config = AIConfig(provider="recorded", model="recorded")
        result = generate_plan(self.form, "добавь поле наценки", config=config, adapter=adapter)
        self.assertEqual(len(result.attempts), 2)
        self.assertIn("НетТакого", result.attempts[0].error)
        self.assertIn("ОШИБКА ПРИ ПРИМЕНЕНИИ", adapter.requests[1].user_content)
        self.assertIn("FORM:", adapter.requests[0].user_content)
        self.assertFalse(self.form.has_element("Наценка"), "generation must not touch the real form")

    def test_ai_plan_gives_up_after_three_repairs(self) -> None:
        adapter = ScriptedAdapter(["не json"] * 4)
        with self.assertRaises(AIError) as caught:
            generate_plan(self.form, "x", config=AIConfig(provider="recorded", model="r"), adapter=adapter)
        self.assertEqual(caught.exception.code, "E_AI_REPAIR_EXHAUSTED")
        self.assertEqual(len(adapter.requests), 4)

    def test_ai_declined_request(self) -> None:
        adapter = ScriptedAdapter(['{"operations": [], "reason": "нужна таблица"}'])
        with self.assertRaises(AIError) as caught:
            generate_plan(self.form, "добавь таблицу", config=AIConfig(provider="recorded", model="r"),
                          adapter=adapter)
        self.assertEqual(caught.exception.code, "E_AI_DECLINED")


@unittest.skipUnless(HAS_SAMPLE, SAMPLE_REASON)
class CliTests(unittest.TestCase):
    def run_cli(self, *argv: str) -> tuple[int, dict]:
        from one_c_form_compiler.cli import main
        buffer = io.StringIO()
        with redirect_stdout(buffer):
            code = main([*argv, "--json"])
        return code, json.loads(buffer.getvalue())

    def test_add_publishes_new_file_and_keeps_input(self) -> None:
        before = hashlib.sha256(SAMPLE.read_bytes()).hexdigest()
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "out.epf"
            code, payload = self.run_cli("add", str(SAMPLE), "-o", str(out), "--type", "Флажок",
                                         "--name", "Флаг", "--caption", "Флаг")
            self.assertEqual(code, 0, payload)
            self.assertTrue(out.is_file())
            self.assertEqual(payload["publish"]["changed_files"][0].split("/")[-1], "Form.elem.json")
            code, info = self.run_cli("inspect", str(out))
            self.assertIn("Флаг", [e["name"] for e in info["elements"]])
            code, payload = self.run_cli("remove", str(SAMPLE), "-o", str(out), "--name", "Флаг")
            self.assertEqual((code, payload["code"]), (3, "E_ELEMENT_NOT_FOUND"))
            code, payload = self.run_cli("title", str(SAMPLE), "-o", str(out), "Т")
            self.assertEqual((code, payload["code"]), (4, "E_OUTPUT_EXISTS"))
        self.assertEqual(hashlib.sha256(SAMPLE.read_bytes()).hexdigest(), before)

    def test_output_equal_to_input_refused_and_dry_run_writes_nothing(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            copy_path = Path(tmp) / "in.epf"
            shutil.copyfile(SAMPLE, copy_path)
            code, payload = self.run_cli("title", str(copy_path), "-o", str(copy_path), "--overwrite", "Т")
            self.assertEqual((code, payload["code"]), (4, "E_OUTPUT_IS_INPUT"))
            code, payload = self.run_cli("title", str(copy_path), "--dry-run", "Т")
            self.assertEqual((code, payload["status"]), (0, "dry-run"))
            self.assertEqual(sorted(p.name for p in Path(tmp).iterdir()), ["in.epf"])

    def test_ai_command_with_recorded_response(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            recorded = Path(tmp) / "r.json"
            recorded.write_text(json.dumps([{"operations": [{"op": "set_title", "title": "ИИ"}]}],
                                           ensure_ascii=False), encoding="utf-8")
            code, payload = self.run_cli("ai", str(SAMPLE), "--dry-run", "--recorded", str(recorded), "смени заголовок")
            self.assertEqual(code, 0, payload)
            self.assertEqual(payload["changes"], [{"op": "set_title", "title": "ИИ"}])


class RuntimeProbeTests(unittest.TestCase):
    def test_probe_is_injected_at_start_of_on_open(self) -> None:
        module = "Перем А;\nПроцедура ПриОткрытии()\n\tА = 1;\nКонецПроцедуры\nА = 0;\n"
        result = inject_probe(module)
        self.assertLess(result.index("ПробаТекст"), result.index("А = 1;"))
        self.assertEqual(inject_probe(result), result)

    def test_probe_creates_on_open_when_missing(self) -> None:
        self.assertIn("Процедура ПриОткрытии()", inject_probe("Процедура Х()\nКонецПроцедуры\n"))

    def test_report_parsing(self) -> None:
        text = f"#FORM\t664\t508\nПоле\tПолеВвода\t8\t10\t100\t19\tЗаг\n{REPORT_DONE}\n"
        report = parse_report(text)
        self.assertEqual(report["form_size"], (664, 508))
        self.assertEqual(report["elements"]["Поле"]["width"], 100)
        self.assertEqual(parse_report("#ERROR\tбум")["status"], "error")
        self.assertEqual(parse_report("Поле\t")["status"], "incomplete")

    def test_extract_json(self) -> None:
        self.assertEqual(json.loads(extract_json('Вот план: {"operations": []} готово')), {"operations": []})



class ObjectMetadataTests(unittest.TestCase):
    def test_attributes_type_id_and_data_path_registration(self) -> None:
        from one_c_form_compiler.core.object_metadata import (
            read_object_attributes, read_object_type_id, register_data_paths)
        attr = ["0", ["27", ["2", ["3", ["1", "0", "aaaa0000-0000-0000-0000-000000000001"], '"Скидка"',
                                    ["1", '"ru"', '"Скидка"'], '""', "0", "0", "00000000-0000-0000-0000-000000000000", "0"],
                             ['"Pattern"', ['"N"', "5", "2", "0"]]]]]
        header = [["1", ["x"], "1", ["c3831ec8-d8d5-4f93-8a22-f9bfae07327f",
                                     ["1", ["4", "bbbb0000-0000-0000-0000-000000000001", "cccc0000-0000-0000-0000-000000000001"],
                                      "1", ["ec6bb5e5-b7a8-4d75-bec9-658107a699cf", "1", [attr]]]]]]
        copyinfo = [["4", ["0"], ["0"], ["0"], ["0", "0"], ["0"]]]
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "ExternalDataProcessor.json"
            path.write_text(json.dumps({"header": header, "copyinfo": copyinfo}, ensure_ascii=False), encoding="utf-8")
            attributes = read_object_attributes(Path(tmp))
            self.assertEqual(list(attributes), ["Скидка"])
            self.assertEqual(attributes["Скидка"].pattern, ['"Pattern"', ['"N"', "5", "2", "0"]])
            self.assertEqual(read_object_type_id(Path(tmp)), "bbbb0000-0000-0000-0000-000000000001")
            owner = "bbbb0000-0000-0000-0000-000000000001"
            self.assertTrue(register_data_paths(Path(tmp), [(attributes["Скидка"], owner)]))
            self.assertFalse(register_data_paths(Path(tmp), [(attributes["Скидка"], owner)]))
            info = json.loads(path.read_text(encoding="utf-8"))["copyinfo"][0]
            self.assertEqual(info[1][0], "1")
            self.assertEqual(info[3], ["1", [["0", "aaaa0000-0000-0000-0000-000000000001"], owner, ["-1"]]])


if __name__ == "__main__":
    unittest.main()
