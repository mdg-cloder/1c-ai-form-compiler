"""Element properties: buttons of existing command panels, ТолькоПросмотр of fields, checkbox and dotted columns
of tabular-section tables, the runtime column width tolerance."""

from __future__ import annotations

import shutil
import tempfile
import unittest
from pathlib import Path

from tests import test_table_field as _table
from tests.test_edt_metadata import CATALOG, UNITS, make_project
from tests.test_table_field import HAS_SAMPLE, PAGE, PANEL, SAMPLE_REASON

# The synthetic catalog of test_edt_metadata with more attributes in its tabular section and in the referenced
# catalog: a Булево one (checkbox columns), a document reference and a self reference (dotted paths).
SECTION_EXTRA = """    <attributes uuid="dddddddd-0000-0000-0000-000000000003">
      <name>Основная</name>
      <type><types>Boolean</types></type>
    </attributes>
    <attributes uuid="dddddddd-0000-0000-0000-000000000004">
      <name>Заказ</name>
      <type><types>DocumentRef.Заказ</types></type>
    </attributes>
    <attributes uuid="dddddddd-0000-0000-0000-000000000005">
      <name>Вариант</name>
      <type><types>String</types><types>Boolean</types></type>
    </attributes>
  </tabularSections>"""
UNITS_EXTRA = """  <name>ЕдиницыИзмерения</name>
  <codeLength>9</codeLength>
  <descriptionLength>25</descriptionLength>
  <attributes uuid="abababab-0000-0000-0000-000000000001">
    <name>ПолноеНаименование</name>
    <type><types>String</types><stringQualifiers><length>100</length></stringQualifiers></type>
  </attributes>
  <attributes uuid="abababab-0000-0000-0000-000000000002">
    <name>Базовая</name>
    <type><types>CatalogRef.ЕдиницыИзмерения</types></type>
  </attributes>"""
DOCUMENT_REF = """  <producedTypes>
    <objectType typeId="99999999-0000-0000-0000-000000000001" valueTypeId="99999999-0000-0000-0000-000000000002"/>
    <refType typeId="99999999-0000-0000-0000-000000000003" valueTypeId="99999999-0000-0000-0000-000000000004"/>
  </producedTypes>"""
BOOL_UUID = "dddddddd-0000-0000-0000-000000000003"


def make_rich_project(root: Path) -> Path:
    make_project(root)
    catalog = CATALOG.replace("  </tabularSections>", SECTION_EXTRA, 1)
    (root / "src/Catalogs/Номенклатура/Номенклатура.mdo").write_text(catalog, encoding="utf-8")
    units = UNITS.replace("  <name>ЕдиницыИзмерения</name>", UNITS_EXTRA, 1)
    (root / "src/Catalogs/ЕдиницыИзмерения/ЕдиницыИзмерения.mdo").write_text(units, encoding="utf-8")
    document = (root / "src/Documents/Заказ/Заказ.mdo").read_text(encoding="utf-8")
    start, end = document.index("  <producedTypes>"), document.index("</producedTypes>") + len("</producedTypes>")
    (root / "src/Documents/Заказ/Заказ.mdo").write_text(document[:start] + DOCUMENT_REF + document[end:],
                                                       encoding="utf-8")
    return root


class _SampleForm(unittest.TestCase):
    """The sample form as Form.oform of the synthetic catalog (see tests/test_table_field.py)."""
    setUpClass = classmethod(_table.TableTests.setUpClass.__func__)
    tearDownClass = classmethod(_table.TableTests.tearDownClass.__func__)
    tearDown = _table.TableTests.tearDown
    apply = _table.TableTests.apply
    columns = _table.TableTests.columns
    add_table = _table.TableTests.add_table

    def setUp(self) -> None:
        from one_c_form_compiler.core.oform_session import OformSession
        self.root = make_rich_project(Path(tempfile.mkdtemp(prefix="props-")))
        self.form_path = self.root / "src/Catalogs/Номенклатура/Forms/ФормаЭлемента/Form.oform"
        shutil.copyfile(self.fixture, self.form_path)
        self.session = OformSession(self.form_path).__enter__()
        self.form = self.session.form()

    def reopen(self):
        from one_c_form_compiler.core.oform_session import OformSession
        self.session.build_and_publish(None)
        session = OformSession(self.form_path).__enter__()
        self.addCleanup(session.__exit__, None, None, None)
        return session.form()

    def code(self, *operations) -> str:
        from one_c_form_compiler.core.edit_plan import PlanError
        with self.assertRaises(PlanError) as caught:
            self.apply(*operations)
        return caught.exception.code


# --------------------------------------------------------------------------- command panel buttons

@unittest.skipUnless(HAS_SAMPLE, SAMPLE_REASON)
class ButtonTests(_SampleForm):
    def bar(self, panel: str):
        raw = self.form.data[self.form.find(panel)]["raw"]
        return raw, raw[2][1][7]

    def buttons(self, panel: str) -> list[dict]:
        from one_c_form_compiler.core.command_panel import panel_info
        return panel_info(self.form, self.form.find(panel))["buttons"]

    def test_add_button_to_form_panel(self) -> None:
        from one_c_form_compiler.core.command_panel import bar_issues
        raw, bar = self.bar("ДействияФормы")
        counter, records = int(bar[2]), int(bar[4])
        result = self.apply({"op": "add_button", "panel": "ДействияФормы", "name": "Пересчитать",
                             "caption": "Пересчитать всё", "tooltip": "Пересчитать движения",
                             "procedure": "ДействияФормыПересчитать"})
        self.assertEqual(result, [{"op": "add_button", "panel": "ДействияФормы", "name": "Пересчитать",
                                   "position": 6, "procedure": "ДействияФормыПересчитать"}])
        self.assertEqual(bar_issues(raw), [])
        self.assertEqual(int(bar[4]), records + 1)
        self.assertEqual(int(bar[2]), counter + 1)
        last = self.buttons("ДействияФормы")[-1]
        self.assertEqual(last, {"name": "Пересчитать", "caption": {"ru": "Пересчитать всё", "uk": "Пересчитать всё"},
                                "procedure": "ДействияФормыПересчитать"})
        record = bar[4 + int(bar[4])]                       # appended after the existing records
        self.assertEqual(record[5:], ["6", ["1", "2", ['"ru"', '"Пересчитать всё"'], ['"uk"', '"Пересчитать всё"']],
                                      ["1", "2", ['"ru"', '"Пересчитать движения"'], ['"uk"', '"Пересчитать движения"']]])
        self.assertIn("Процедура ДействияФормыПересчитать(Кнопка)", self.form.module_text)
        self.assertFalse(self.form.new_issues())

    def test_add_button_before_with_separator(self) -> None:
        self.apply({"op": "add_button", "panel": "ОсновныеДействияФормы", "name": "Проверить",
                    "procedure": "ОсновныеДействияФормыПроверить", "before": "Действие", "separator": True})
        names = [button["name"] for button in self.buttons("ОсновныеДействияФормы")]
        self.assertEqual(names[:5], ["Печать", "Разделитель2", "Разделитель3", "Проверить", "Действие"])
        _raw, bar = self.bar("ОсновныеДействияФормы")
        root = bar[-1]
        props = {root[6 + 2 * i][1]: root[6 + 2 * i] for i in range(int(root[4]))}
        self.assertEqual(props['"Разделитель3"'][9], "2")           # separator kind, invisible caption
        self.assertEqual(props['"Разделитель3"'][5], "0")
        self.assertEqual(props['"Проверить"'][10], "0")             # text display taken from Печать (auto)
        # After a text-only button of the panel the new one is shown as text too.
        self.apply({"op": "add_button", "panel": "ОсновныеДействияФормы", "name": "Справка",
                    "procedure": "ОсновныеДействияФормыСправка", "after": "Действие2"})
        _raw, bar = self.bar("ОсновныеДействияФормы")
        root = bar[-1]
        self.assertEqual(root[6 + 2 * (int(root[4]) - 1)][10], "1")
        self.assertFalse(self.form.new_issues())

    def test_standard_action_only_on_table_panels(self) -> None:
        self.apply({"op": "add_button", "panel": "КоманднаяПанельБУ", "name": "УдалитьСтроку", "standard": "Удалить"})
        self.assertEqual(self.buttons("КоманднаяПанельБУ")[-1]["standard"], "83")
        self.assertEqual(self.code({"op": "add_button", "panel": "ДействияФормы", "name": "Х", "standard": "Удалить"}),
                         "E_TYPE_UNSUPPORTED")

    def test_add_button_errors(self) -> None:
        base = {"op": "add_button", "panel": "ОсновныеДействияФормы", "name": "Новая", "procedure": "П"}
        self.assertEqual(self.code({**base, "name": "Печать"}), "E_NAME_EXISTS")
        self.assertEqual(self.code({**base, "before": "Нет"}), "E_ELEMENT_NOT_FOUND")
        self.assertEqual(self.code({**base, "before": "Печать", "after": "Печать"}), "E_PLAN_FIELD")
        self.assertEqual(self.code({**base, "standard": "Удалить"}), "E_PLAN_FIELD")
        self.assertEqual(self.code({**base, "panel": "Номер"}), "E_TYPE_UNSUPPORTED")
        self.assertEqual(self.code({**base, "panel": "НетТакой"}), "E_ELEMENT_NOT_FOUND")
        self.assertEqual(self.code({**base, "picture": "x"}), "E_PLAN_FIELD")
        self.assertEqual(self.code({**base, "separator": "yes"}), "E_PLAN_FIELD")
        self.assertEqual(self.code({**base, "name": "1Неверно"}), "E_NAME_INVALID")
        self.assertEqual(self.code({"op": "add_button", "panel": "ОсновныеДействияФормы", "name": "Х"}), "E_PLAN_FIELD")

    def test_remove_buttons(self) -> None:
        from one_c_form_compiler.core.command_panel import bar_issues
        raw, bar = self.bar("ОсновныеДействияФормы")
        records = int(bar[4])
        result = self.apply({"op": "remove_button", "panel": "ОсновныеДействияФормы", "name": "Печать"},
                            {"op": "remove_button", "panel": "ОсновныеДействияФормы", "name": "Разделитель2"})
        self.assertEqual([r["kind"] for r in result], ["button", "separator"])
        self.assertEqual([b["name"] for b in self.buttons("ОсновныеДействияФормы")][:1], ["Действие"])
        self.assertEqual(int(bar[4]), records - 2)
        self.assertEqual(bar_issues(raw), [])
        # A button inside a submenu.
        self.apply({"op": "remove_button", "panel": "ДействияФормы", "name": "СтруктураПодчиненностиДокумента"})
        self.assertEqual(bar_issues(self.bar("ДействияФормы")[0]), [])
        self.assertEqual(self.code({"op": "remove_button", "panel": "ДействияФормы", "name": "Подменю1"}),
                         "E_TYPE_UNSUPPORTED")
        self.assertEqual(self.code({"op": "remove_button", "panel": "ДействияФормы", "name": "Нет"}),
                         "E_ELEMENT_NOT_FOUND")
        self.assertFalse(self.form.new_issues())

    def test_remove_referenced_button_needs_force(self) -> None:
        self.form.module_text += "\r\n// ЭлементыФормы.ДействияФормы.Кнопки.Настройка.Доступность = Ложь;\r\n"
        self.assertEqual(self.code({"op": "remove_button", "panel": "ДействияФормы", "name": "Настройка"}),
                         "E_ELEMENT_REFERENCED")
        self.apply({"op": "remove_button", "panel": "ДействияФормы", "name": "Настройка", "force": True})
        self.assertNotIn("Настройка", [b["name"] for b in self.buttons("ДействияФормы")])

    def test_buttons_survive_rebuild(self) -> None:
        from one_c_form_compiler.core.command_panel import bar_issues, panel_info
        self.apply({"op": "add_button", "panel": "ОсновныеДействияФормы", "name": "Проверить",
                    "procedure": "ОсновныеДействияФормыПроверить", "separator": True},
                   {"op": "remove_button", "panel": "ДействияФормы", "name": "Разделитель5"})
        form = self.reopen()
        key = form.find("ОсновныеДействияФормы")
        self.assertEqual(bar_issues(form.data[key]["raw"]), [])
        self.assertEqual(panel_info(form, key)["buttons"][-1]["procedure"], "ОсновныеДействияФормыПроверить")
        self.assertNotIn("Разделитель5", [b["name"] for b in panel_info(form, form.find("ДействияФормы"))["buttons"]])

    def test_submenu_is_listed_as_submenu(self) -> None:
        first = self.buttons("ДействияФормы")[0]
        self.assertEqual(first["name"], "Подменю1")
        self.assertTrue(first.get("submenu"))
        self.assertNotIn("procedure", first)


# --------------------------------------------------------------------------- ТолькоПросмотр

@unittest.skipUnless(HAS_SAMPLE, SAMPLE_REASON)
class ReadOnlyTests(_SampleForm):
    """ТолькоПросмотр: Field raw[2][2][0][13]; Table bit 10 of raw[2][2][1][1] cleared (confirmed in 1C)."""

    def flag(self, name: str) -> str:
        return self.form.data[self.form.find(name)]["raw"][2][2][0][13]

    def mask(self, name: str) -> int:
        return int(self.form.data[self.form.find(name)]["raw"][2][2][1][1])

    def read_only(self, name: str):
        return self.form.read_only(self.form.find(name))

    def test_sample_flags_match_the_platform(self) -> None:
        # In 1C the sample's Номер is read-only, the other input fields and the tables are editable.
        self.assertIs(self.read_only("Номер"), True)
        for name in ("Дата", "Комментарий", "Ответственный", "ЗаполнениеДвижений", "ТабличноеПолеДвиженияБУ"):
            self.assertIs(self.read_only(name), False, name)
        self.assertIsNone(self.read_only("ИспользоватьЗаполнениеДвижений"))
        self.assertTrue(self.form.info(self.form.find("Номер")).read_only)

    def test_add_and_modify_read_only_field(self) -> None:
        result = self.apply({"op": "add", "type": "Field", "name": "Итог", "value_type": "Число(15,2)",
                             "width": 120, "read_only": True})
        self.assertEqual(self.flag("Итог"), "1")
        self.assertTrue(result[-1]["read_only"])
        self.assertTrue(self.form.info(self.form.find("Итог")).read_only)
        result = self.apply({"op": "modify", "name": "Итог", "read_only": False})
        self.assertEqual(self.flag("Итог"), "0")
        self.assertNotIn("read_only", result[-1])
        before = list(self.form.data[self.form.find("Комментарий")]["raw"][2][2][0])
        self.apply({"op": "modify", "name": "Комментарий", "read_only": True},
                   {"op": "modify", "name": "Номер", "read_only": False})
        after = self.form.data[self.form.find("Комментарий")]["raw"][2][2][0]
        self.assertEqual([i for i, (a, b) in enumerate(zip(before, after)) if a != b], [13])
        self.assertEqual((self.flag("Комментарий"), self.flag("Номер")), ("1", "0"))
        self.assertFalse(self.form.new_issues())

    def test_new_field_is_editable_unless_template_given(self) -> None:
        self.apply({"op": "add", "type": "Field", "name": "Поле1", "width": 100})
        self.assertEqual(self.flag("Поле1"), "0")
        self.apply({"op": "add", "type": "Field", "name": "Поле2", "width": 100, "template": "Номер"})
        self.assertEqual(self.flag("Поле2"), "1")

    def test_table_read_only(self) -> None:
        mask = self.mask("ЗаполнениеДвижений")
        result = self.apply({"op": "modify", "name": "ЗаполнениеДвижений", "read_only": True})
        self.assertEqual(self.mask("ЗаполнениеДвижений"), mask & ~(1 << 10))
        self.assertTrue(result[-1]["read_only"])
        self.apply({"op": "modify", "name": "ЗаполнениеДвижений", "read_only": False})
        self.assertEqual(self.mask("ЗаполнениеДвижений"), mask)
        self.assertIs(self.read_only("ЗаполнениеДвижений"), False)

    def test_read_only_errors(self) -> None:
        self.assertEqual(self.code({"op": "modify", "name": "Надпись1", "read_only": True}), "E_TYPE_UNSUPPORTED")
        # The platform's ordinary check box has no ТолькоПросмотр property.
        self.assertEqual(self.code({"op": "modify", "name": "ИспользоватьЗаполнениеДвижений", "read_only": True}),
                         "E_TYPE_UNSUPPORTED")
        self.assertEqual(self.code({"op": "modify", "name": "Номер", "read_only": "да"}), "E_PLAN_FIELD")
        self.assertEqual(self.code({"op": "add", "type": "Label", "name": "Н", "read_only": True}),
                         "E_TYPE_UNSUPPORTED")

    def test_unknown_table_mask_is_refused(self) -> None:
        self.form.data[self.form.find("ЗаполнениеДвижений")]["raw"][2][2][1][1] = "1"
        self.assertIsNone(self.read_only("ЗаполнениеДвижений"))
        self.assertEqual(self.code({"op": "modify", "name": "ЗаполнениеДвижений", "read_only": True}),
                         "E_PROPERTY_FORMAT")

    def test_read_only_survives_rebuild(self) -> None:
        self.apply({"op": "add", "type": "Field", "name": "Итог", "width": 120, "read_only": True},
                   {"op": "modify", "name": "ЗаполнениеДвижений", "read_only": True})
        form = self.reopen()
        self.assertTrue(form.read_only(form.find("Итог")))
        self.assertTrue(form.read_only(form.find("Номер")))
        self.assertFalse(form.read_only(form.find("Комментарий")))
        self.assertTrue(form.read_only(form.find("ЗаполнениеДвижений")))


class ReadOnlyCliTests(unittest.TestCase):
    def operation(self, *argv):
        from one_c_form_compiler.cli import _operation_from_args, build_parser
        return _operation_from_args(build_parser().parse_args(list(argv)))

    def test_cli_flags(self) -> None:
        self.assertEqual(self.operation("add", "x.epf", "--type", "Field", "--name", "А", "--read-only")["read_only"],
                         True)
        self.assertIs(self.operation("modify", "x.epf", "--name", "А", "--no-read-only")["read_only"], False)
        self.assertNotIn("read_only", self.operation("modify", "x.epf", "--name", "А", "--caption", "Б"))


# --------------------------------------------------------------------------- checkbox and dotted columns

@unittest.skipUnless(HAS_SAMPLE, SAMPLE_REASON)
class SectionColumnTests(_SampleForm):
    def column(self, name: str, table: str = "Упаковки"):
        from one_c_form_compiler.core import table_field as tf
        return tf.find_column(self.form, self.form.find(table), name)[1]

    def test_checkbox_column_bound_to_boolean_attribute(self) -> None:
        from one_c_form_compiler.core import table_field as tf
        self.add_table(columns=["НомерСтроки", "ЕдиницаИзмерения"])
        result = self.apply({"op": "add_column", "table": "Упаковки", "name": "Основная", "data": "Основная",
                             "checkbox": True, "width": 30, "after": "НомерСтроки"})
        self.assertEqual(result[0]["checkbox"], True)
        self.assertEqual(result[0]["data"], "Основная")
        inner = self.column("Основная")[1][1]
        self.assertEqual(inner[tf.PATH_DATA], ["-1"])                # Данные: none
        self.assertEqual(inner[tf.PATH_CHECK], ["0", BOOL_UUID])     # ДанныеФлажка: the attribute
        props = tf.column_props(self.column("Основная"))
        self.assertEqual(len(props), tf.P_CELL)                      # no own cell control
        self.assertEqual(props[tf.P_CELL_TYPE], tf.NO_CELL_TYPE)
        self.assertEqual(props[tf.P_PATTERN], ['"Pattern"', ['"B"']])
        info = next(c for c in self.columns() if c.name == "Основная")
        self.assertEqual((info.data, info.checkbox, info.editable), ("Основная", True, False))
        self.assertEqual([c.name for c in self.columns()], ["НомерСтроки", "Основная", "ЕдиницаИзмерения"])
        form = self.reopen()
        reread = next(c for c in tf.list_columns(form, form.find("Упаковки")) if c.name == "Основная")
        self.assertEqual((reread.data, reread.checkbox), ("Основная", True))

    def test_checkbox_column_errors_and_modify(self) -> None:
        from one_c_form_compiler.core import table_field as tf
        self.add_table(columns=["НомерСтроки"])
        base = {"op": "add_column", "table": "Упаковки", "name": "Флаг", "checkbox": True}
        self.assertEqual(self.code({**base, "data": "Коэффициент"}), "E_DATA_TYPE")
        self.assertEqual(self.code({**base, "data": "Вариант"}), "E_DATA_TYPE")       # String | Boolean
        self.assertEqual(self.code({**base, "data": "Нет"}), "E_DATA_UNKNOWN")
        self.assertEqual(self.code({**base, "data": "Основная", "events": {"ПриИзменении": "П"}}),
                         "E_HANDLER_UNSUPPORTED")
        self.apply({**base, "data": "Основная"})
        self.assertEqual(self.code({"op": "modify_column", "table": "Упаковки", "name": "Флаг",
                                    "data": "Коэффициент"}), "E_DATA_TYPE")
        self.apply({"op": "modify_column", "table": "Упаковки", "name": "Флаг", "data": "Основная", "width": 40})
        self.assertEqual(self.column("Флаг")[1][1][tf.PATH_CHECK], ["0", BOOL_UUID])

    def test_runtime_compare_of_checkbox_column(self) -> None:
        from one_c_form_compiler.core.runtime_check import _compare_columns, runtime_column_width
        self.add_table(columns=["НомерСтроки"])
        self.apply({"op": "add_column", "table": "Упаковки", "name": "Флаг", "data": "Основная", "checkbox": True})
        control = self.form.info(self.form.find("Упаковки"))
        got = [{"name": c.name, "caption": c.caption.get("ru", ""), "width": runtime_column_width(c.width),
                "data": "" if c.checkbox else (c.data or ""), "events": [], "check": c.data if c.checkbox else ""}
               for c in self.columns()]
        self.assertEqual(_compare_columns(self.form, control, got), [])
        got[-1]["check"] = ""
        self.assertTrue(any("checkbox data" in p for p in _compare_columns(self.form, control, got)))

    def test_dotted_column_is_filled_in_row_output(self) -> None:
        from one_c_form_compiler.core import table_field as tf
        self.add_table(columns=["НомерСтроки", "ЕдиницаИзмерения"])
        result = self.apply({"op": "add_column", "table": "Упаковки", "name": "ЕдиницаПолностью",
                             "data": "ЕдиницаИзмерения.ПолноеНаименование", "width": 150})
        self.assertEqual(result[0]["computed"], "ЕдиницаИзмерения.ПолноеНаименование")
        self.assertEqual(result[0]["caption"]["ru"], "ПолноеНаименование")
        self.assertNotIn("data", result[0])
        inner = self.column("ЕдиницаПолностью")[1][1]
        self.assertEqual((inner[tf.PATH_DATA], inner[tf.PATH_CHECK]), (["-1"], ["-1"]))
        self.assertEqual(inner[1][tf.P_PATTERN], ['"Pattern"', ['"S"', "100", "1"]])
        self.assertEqual(len(inner[1]), tf.P_CELL)
        key = self.form.find("Упаковки")
        self.assertEqual(tf.table_events(self.form, key), {"ПриВыводеСтроки": "УпаковкиПриВыводеСтроки"})
        module = self.form.module_text
        self.assertIn("Процедура УпаковкиПриВыводеСтроки(Элемент, ОформлениеСтроки, ДанныеСтроки)", module)
        self.assertIn("ОформлениеСтроки.Ячейки.ЕдиницаПолностью.УстановитьТекст("
                      "Строка(ДанныеСтроки.ЕдиницаИзмерения.ПолноеНаименование));", module)
        # Standard attributes and longer chains; the line goes into the same handler.
        self.apply({"op": "add_column", "table": "Упаковки", "name": "Код", "data": "ЕдиницаИзмерения.Код"},
                   {"op": "add_column", "table": "Упаковки", "name": "Базовая",
                    "data": "ЕдиницаИзмерения.Базовая.Наименование"},
                   {"op": "add_column", "table": "Упаковки", "name": "ДатаЗаказа", "data": "Заказ.Дата"})
        self.assertEqual(self.form.module_text.count("Процедура УпаковкиПриВыводеСтроки("), 1)
        self.assertEqual({c.name: c.computed for c in self.columns() if c.computed},
                         {"ЕдиницаПолностью": "ЕдиницаИзмерения.ПолноеНаименование", "Код": "ЕдиницаИзмерения.Код",
                          "Базовая": "ЕдиницаИзмерения.Базовая.Наименование", "ДатаЗаказа": "Заказ.Дата"})
        self.assertFalse(self.form.new_issues())
        form = self.reopen()
        self.assertIn("ДанныеСтроки.Заказ.Дата", form.module_text)
        self.assertEqual(tf.table_events(form, form.find("Упаковки")), {"ПриВыводеСтроки": "УпаковкиПриВыводеСтроки"})

    def test_dotted_column_uses_existing_handler(self) -> None:
        self.add_table(columns=["НомерСтроки"], events={"ПриВыводеСтроки": "ВыводСтроки"})
        self.form.module_text = self.form.module_text.replace(
            "Процедура ВыводСтроки(Элемент, ОформлениеСтроки, ДанныеСтроки)",
            "Процедура ВыводСтроки(Элемент, Оформление, Строка)")
        self.apply({"op": "add_column", "table": "Упаковки", "name": "Ед", "data": "ЕдиницаИзмерения.Наименование"})
        self.assertIn("Оформление.Ячейки.Ед.УстановитьТекст(Строка(Строка.ЕдиницаИзмерения.Наименование));",
                      self.form.module_text)
        self.assertNotIn("УпаковкиПриВыводеСтроки", self.form.module_text)

    def test_removing_dotted_column_removes_its_line(self) -> None:
        self.add_table(columns=["НомерСтроки"])
        self.apply({"op": "add_column", "table": "Упаковки", "name": "Ед", "data": "ЕдиницаИзмерения.Наименование"})
        self.apply({"op": "remove_column", "table": "Упаковки", "name": "Ед"})
        self.assertNotIn("Ячейки.Ед.", self.form.module_text)
        self.assertIn("Процедура УпаковкиПриВыводеСтроки(", self.form.module_text)
        # Other code that reads a cell of the column keeps it from being removed without force.
        self.apply({"op": "add_column", "table": "Упаковки", "name": "Ед2", "data": "ЕдиницаИзмерения.Наименование"})
        self.form.module_text += "\r\n// ОформлениеСтроки.Ячейки.Ед2.ЦветФона = Новый Цвет;\r\n"
        self.assertEqual(self.code({"op": "remove_column", "table": "Упаковки", "name": "Ед2"}), "E_ELEMENT_REFERENCED")

    def test_dotted_column_errors(self) -> None:
        self.add_table(columns=["НомерСтроки"])
        base = {"op": "add_column", "table": "Упаковки", "name": "Х"}
        self.assertEqual(self.code({**base, "data": "ЕдиницаИзмерения.Нет"}), "E_DATA_UNKNOWN")
        self.assertEqual(self.code({**base, "data": "Нет.Наименование"}), "E_DATA_UNKNOWN")
        self.assertEqual(self.code({**base, "data": "Коэффициент.Наименование"}), "E_DATA_TYPE")
        self.assertEqual(self.code({**base, "data": "ЕдиницаИзмерения.ПолноеНаименование.Длина"}), "E_DATA_TYPE")
        self.assertEqual(self.code({**base, "data": "ЕдиницаИзмерения.1Код"}), "E_NAME_INVALID")
        self.assertEqual(self.code({**base, "data": "ЕдиницаИзмерения.Код", "checkbox": True}), "E_DATA_UNSUPPORTED")
        self.assertEqual(self.code({**base, "data": "ЕдиницаИзмерения.Код", "events": {"ПриИзменении": "П"}}),
                         "E_HANDLER_UNSUPPORTED")
        self.apply({**base, "data": "ЕдиницаИзмерения"})
        self.assertEqual(self.code({"op": "modify_column", "table": "Упаковки", "name": "Х",
                                    "data": "ЕдиницаИзмерения.Код"}), "E_DATA_UNSUPPORTED")
        # Without the project (as in an EPF) the chain cannot be followed.
        section = self.form.tabular_sections["Упаковки"]
        self.form.tabular_sections["Упаковки"] = type(section)(section.name, section.uuid, section.attributes,
                                                              section.object_type_id, None)
        self.assertEqual(self.code({**base, "name": "У", "data": "ЕдиницаИзмерения.Код"}), "E_DATA_UNSUPPORTED")

    def test_value_table_rejects_dotted_data(self) -> None:
        self.apply({"op": "add_page", "container": PANEL, "name": "Новая"},
                   {"op": "add", "type": "Table", "name": "ВТ", "columns": ["А"], "container": PANEL,
                    "page": "Новая", "top": 40})
        self.assertEqual(self.code({"op": "add_column", "table": "ВТ", "name": "Б", "data": "А.Б"}),
                         "E_DATA_UNSUPPORTED")


class ResolvePathTests(unittest.TestCase):
    def setUp(self) -> None:
        self.root = make_rich_project(Path(tempfile.mkdtemp(prefix="props-meta-")))
        self.addCleanup(shutil.rmtree, self.root, True)

    def test_type_names_and_chain(self) -> None:
        from one_c_form_compiler.core.edt_metadata import read_object_metadata, resolve_path
        meta = read_object_metadata(self.root / "src/Catalogs/Номенклатура/Номенклатура.mdo")
        section = meta.tabular_sections["Упаковки"]
        self.assertEqual(Path(section.project_dir), self.root.resolve())
        unit = section.attributes["ЕдиницаИзмерения"]
        self.assertEqual(unit.type_names, ("CatalogRef.ЕдиницыИзмерения",))
        self.assertEqual(section.attributes["Вариант"].type_names, ("String", "Boolean"))
        chain = resolve_path(section.project_dir, unit, ["Базовая", "ПолноеНаименование"])
        self.assertEqual([step.name for step in chain], ["ЕдиницаИзмерения", "Базовая", "ПолноеНаименование"])
        self.assertEqual(chain[-1].pattern, ['"Pattern"', ['"S"', "100", "1"]])
        self.assertEqual(resolve_path(section.project_dir, section.attributes["Заказ"], ["Номер"])[-1].name, "Номер")
        self.assertEqual(meta.attributes["Код"].type_names, ("String",))
        self.assertEqual(meta.attributes["Наименование"].type_names, ("String",))


# --------------------------------------------------------------------------- runtime check

class RuntimeToleranceTests(unittest.TestCase):
    def test_tolerance(self) -> None:
        from one_c_form_compiler.core.runtime_check import runtime_column_width, width_tolerance
        # 1C 8.3.27 returned 22 chars for 154 px and for 163 px columns (estimates 24 and 25).
        for pixels, chars in ((154, 22), (163, 22), (154, 24), (40, 7), (120, 19)):
            estimate = runtime_column_width(pixels)
            self.assertLessEqual(abs(chars - estimate), width_tolerance(estimate), (pixels, chars))
        # A width that was not applied (100 px left instead of 154 px) is still reported.
        self.assertGreater(abs(runtime_column_width(100) - runtime_column_width(154)),
                           width_tolerance(runtime_column_width(154)))

    def test_report_with_buttons_and_read_only(self) -> None:
        from one_c_form_compiler.core.runtime_check import REPORT_DONE, parse_report
        report = parse_report("\n".join([
            "#FORM\t500\t400",
            "Поле\tПолеВвода\t8\t40\t100\t19\t\t\t\t1",
            "#BTN\tДействияФормы\tПечать\tПечать",
            REPORT_DONE]))
        self.assertEqual(report["elements"]["Поле"]["read_only"], "1")
        self.assertEqual(report["buttons"], {"ДействияФормы": [{"name": "Печать", "caption": "Печать"}]})


@unittest.skipUnless(HAS_SAMPLE, SAMPLE_REASON)
class RuntimeCompareTests(_SampleForm):
    def runtime(self, **changes):
        from one_c_form_compiler.core.command_panel import panel_info
        from one_c_form_compiler.core.runtime_check import compare, runtime_column_width
        from one_c_form_compiler.core.table_field import list_columns
        elements, columns, buttons = {}, {}, {}
        for control in self.form.controls():
            rect = control.rect
            read_only = self.form.read_only(control.key)
            elements[control.name] = {"type": control.type, "left": rect.left, "top": rect.top, "width": rect.width,
                                      "height": rect.height, "caption": "", "data": control.data or "",
                                      "events": sorted(n for n in self.form.events(control.key) if not n.startswith("#"))
                                      if control.type != "Table" else [],
                                      "read_only": "" if read_only is None else ("1" if read_only else "0")}
            if control.type == "Table":
                from one_c_form_compiler.core.table_field import table_events
                elements[control.name]["events"] = sorted(table_events(self.form, control.key))
                columns[control.name] = [
                    {"name": c.name, "caption": c.caption.get("ru", ""), "width": runtime_column_width(c.width),
                     "data": "" if c.checkbox else (c.data or ""), "events": sorted(c.events),
                     "check": c.data if c.checkbox else ""} for c in list_columns(self.form, control.key)]
            if control.type == "CommandPanel":
                buttons[control.name] = [{"name": b["name"], "caption": ""}
                                         for b in panel_info(self.form, control.key)["buttons"]]
        report = {"elements": elements, "columns": columns, "buttons": buttons}
        for name, value in changes.items():
            target, _, field = name.partition("__")
            report["elements"][target][field] = value
        return report

    def test_equal_default_width_columns_pass(self) -> None:
        from one_c_form_compiler.core.runtime_check import _compare_columns
        self.apply({"op": "add_page", "container": PANEL, "name": "Новая"},
                   {"op": "add", "type": "Table", "name": "ВТ", "columns": ["А", "Б"], "container": PANEL,
                    "page": "Новая", "top": 40})
        self.apply({"op": "modify_column", "table": "ВТ", "name": "А", "width": 154},
                   {"op": "modify_column", "table": "ВТ", "name": "Б", "width": 154})
        control = self.form.info(self.form.find("ВТ"))
        got = [{"name": name, "caption": name, "width": 22, "data": name, "events": []} for name in ("А", "Б")]
        self.assertEqual(_compare_columns(self.form, control, got), [])
        got[1]["width"] = 16
        self.assertTrue(any("ВТ.Б: width" in p for p in _compare_columns(self.form, control, got)))

    def test_read_only_and_buttons_are_compared(self) -> None:
        from one_c_form_compiler.core.runtime_check import compare
        self.apply({"op": "modify", "name": "Комментарий", "read_only": True},
                   {"op": "modify", "name": "ЗаполнениеДвижений", "read_only": True},
                   {"op": "add_button", "panel": "ДействияФормы", "name": "Пересчитать", "procedure": "П"})
        report = self.runtime()
        self.assertEqual([p for p in compare(self.form, report) if "ТолькоПросмотр" in p or "Пересчитать" in p], [])
        report["elements"]["Комментарий"]["read_only"] = "0"
        report["elements"]["ЗаполнениеДвижений"]["read_only"] = "0"
        report["elements"]["Номер"]["read_only"] = "0"
        report["buttons"]["ДействияФормы"] = report["buttons"]["ДействияФормы"][:-1]
        problems = compare(self.form, report)
        self.assertIn("Комментарий: ТолькоПросмотр False != True", problems)
        self.assertIn("ЗаполнениеДвижений: ТолькоПросмотр False != True", problems)
        self.assertIn("Номер: ТолькоПросмотр False != True", problems)
        self.assertIn("ДействияФормы: button Пересчитать missing at runtime", problems)


if __name__ == "__main__":
    unittest.main()
