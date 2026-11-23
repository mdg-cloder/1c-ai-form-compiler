"""EDT project lookup: forms by FQN, object attributes and tabular sections from .mdo."""

from __future__ import annotations

import shutil
import tempfile
import unittest
from pathlib import Path

from one_c_form_compiler.core.edt_metadata import (
    EdtMetadataError, read_object_metadata, resolve_form)

MDO_HEAD = ('<?xml version="1.0" encoding="UTF-8"?>\n'
            '<mdclass:{kind} xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance" '
            'xmlns:core="http://g5.1c.ru/v8/dt/mcore" xmlns:mdclass="http://g5.1c.ru/v8/dt/metadata/mdclass" '
            'uuid="{uuid}">\n')

CATALOG = MDO_HEAD.format(kind="Catalog", uuid="11111111-0000-0000-0000-000000000001") + """
  <producedTypes>
    <objectType typeId="aaaaaaaa-0000-0000-0000-000000000001" valueTypeId="aaaaaaaa-0000-0000-0000-000000000002"/>
    <refType typeId="aaaaaaaa-0000-0000-0000-000000000003" valueTypeId="aaaaaaaa-0000-0000-0000-000000000004"/>
  </producedTypes>
  <name>Номенклатура</name>
  <codeLength>9</codeLength>
  <descriptionLength>150</descriptionLength>
  <attributes uuid="bbbbbbbb-0000-0000-0000-000000000001">
    <name>Артикул</name>
    <type><types>String</types><stringQualifiers><length>50</length></stringQualifiers></type>
  </attributes>
  <attributes uuid="bbbbbbbb-0000-0000-0000-000000000002">
    <name>ЕдиницаИзмерения</name>
    <type><types>CatalogRef.ЕдиницыИзмерения</types></type>
  </attributes>
  <attributes uuid="bbbbbbbb-0000-0000-0000-000000000003">
    <name>Услуга</name>
    <type><types>Boolean</types></type>
  </attributes>
  <attributes uuid="bbbbbbbb-0000-0000-0000-000000000004">
    <name>Вес</name>
    <type><types>Number</types><numberQualifiers><precision>11</precision><scale>8</scale></numberQualifiers></type>
  </attributes>
  <attributes uuid="bbbbbbbb-0000-0000-0000-000000000005">
    <name>ВремяПоставки</name>
    <type><types>Date</types><dateQualifiers><dateFractions>Time</dateFractions></dateQualifiers></type>
  </attributes>
  <attributes uuid="bbbbbbbb-0000-0000-0000-000000000008">
    <name>ДатаСоздания</name>
    <type><types>Date</types><dateQualifiers><dateFractions>Date</dateFractions></dateQualifiers></type>
  </attributes>
  <attributes uuid="bbbbbbbb-0000-0000-0000-000000000007">
    <name>Штрихкод</name>
    <type><types>String</types><stringQualifiers><length>20</length><fixed>true</fixed></stringQualifiers></type>
  </attributes>
  <attributes uuid="bbbbbbbb-0000-0000-0000-000000000006">
    <name>Изображение</name>
    <type><types>ValueStorage</types></type>
  </attributes>
  <tabularSections uuid="cccccccc-0000-0000-0000-000000000001">
    <producedTypes>
      <objectType typeId="cccccccc-0000-0000-0000-000000000002" valueTypeId="cccccccc-0000-0000-0000-000000000003"/>
      <rowType typeId="cccccccc-0000-0000-0000-000000000004" valueTypeId="cccccccc-0000-0000-0000-000000000005"/>
    </producedTypes>
    <name>Упаковки</name>
    <attributes uuid="dddddddd-0000-0000-0000-000000000001">
      <name>Коэффициент</name>
      <type><types>Number</types><numberQualifiers><precision>1</precision><nonNegative>true</nonNegative></numberQualifiers></type>
    </attributes>
    <attributes uuid="dddddddd-0000-0000-0000-000000000002">
      <name>ЕдиницаИзмерения</name>
      <type><types>CatalogRef.ЕдиницыИзмерения</types></type>
    </attributes>
  </tabularSections>
  <forms uuid="eeeeeeee-0000-0000-0000-000000000001"><name>ФормаЭлемента</name><formType>Ordinary</formType></forms>
  <forms uuid="eeeeeeee-0000-0000-0000-000000000002"><name>ФормаУправляемая</name></forms>
</mdclass:Catalog>
"""

UNITS = MDO_HEAD.format(kind="Catalog", uuid="11111111-0000-0000-0000-000000000002") + """
  <producedTypes>
    <objectType typeId="ffffffff-0000-0000-0000-000000000001" valueTypeId="ffffffff-0000-0000-0000-000000000002"/>
    <refType typeId="ffffffff-0000-0000-0000-000000000003" valueTypeId="ffffffff-0000-0000-0000-000000000004"/>
  </producedTypes>
  <name>ЕдиницыИзмерения</name>
</mdclass:Catalog>
"""

DOCUMENT = MDO_HEAD.format(kind="Document", uuid="11111111-0000-0000-0000-000000000003") + """
  <producedTypes>
    <objectType typeId="99999999-0000-0000-0000-000000000001" valueTypeId="99999999-0000-0000-0000-000000000002"/>
  </producedTypes>
  <name>Заказ</name>
  <numberLength>11</numberLength>
</mdclass:Document>
"""


def make_project(root: Path) -> Path:
    def put(relative: str, text: str | bytes) -> None:
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        if isinstance(text, bytes):
            path.write_bytes(text)
        else:
            path.write_text(text, encoding="utf-8")

    put("DT-INF/PROJECT.PMF", "Manifest-Version: 1.0\nRuntime-Version: 8.3.27\n")
    put("src/Catalogs/Номенклатура/Номенклатура.mdo", CATALOG)
    put("src/Catalogs/Номенклатура/Forms/ФормаЭлемента/Form.oform", b"\xff\xff\xff\x7f")
    put("src/Catalogs/Номенклатура/Forms/ФормаУправляемая/Form.form", "<form:Form/>")
    put("src/Catalogs/ЕдиницыИзмерения/ЕдиницыИзмерения.mdo", UNITS)
    put("src/Documents/Заказ/Заказ.mdo", DOCUMENT)
    put("src/Documents/Заказ/Forms/ФормаДокумента/Form.oform", b"\xff\xff\xff\x7f")
    put("src/CommonForms/Общая/Form.oform", b"\xff\xff\xff\x7f")
    return root


class EdtMetadataTests(unittest.TestCase):
    def setUp(self) -> None:
        self.root = make_project(Path(tempfile.mkdtemp(prefix="edt-md-")))

    def tearDown(self) -> None:
        shutil.rmtree(self.root, ignore_errors=True)

    def test_resolve_catalog_form(self) -> None:
        path = resolve_form(self.root, "Catalog.Номенклатура.ФормаЭлемента")
        self.assertEqual(path, self.root / "src/Catalogs/Номенклатура/Forms/ФормаЭлемента/Form.oform")
        self.assertEqual(resolve_form(self.root, "Catalog.Номенклатура.Form.ФормаЭлемента"), path)

    def test_resolve_russian_type(self) -> None:
        self.assertEqual(resolve_form(self.root, "Справочник.Номенклатура.ФормаЭлемента"),
                         self.root / "src/Catalogs/Номенклатура/Forms/ФормаЭлемента/Form.oform")
        self.assertEqual(resolve_form(self.root, "Документ.Заказ.ФормаДокумента"),
                         self.root / "src/Documents/Заказ/Forms/ФормаДокумента/Form.oform")
        self.assertEqual(resolve_form(self.root, "ОбщаяФорма.Общая"), self.root / "src/CommonForms/Общая/Form.oform")

    def test_resolve_register_and_chart_kinds(self) -> None:
        from one_c_form_compiler.core.runtime_check import form_getter
        cases = [("РегистрБухгалтерии", "AccountingRegister", "AccountingRegisters", "РегистрыБухгалтерии"),
                 ("РегистрРасчета", "CalculationRegister", "CalculationRegisters", "РегистрыРасчета"),
                 ("ПланВидовРасчета", "ChartOfCalculationTypes", "ChartsOfCalculationTypes", "ПланыВидовРасчета")]
        for ru, en, folder, manager in cases:
            with self.subTest(kind=en):
                form = self.root / "src" / folder / "Основной" / "Forms" / "ФормаТабло" / "Form.oform"
                form.parent.mkdir(parents=True)
                form.write_bytes(b"")
                self.assertEqual(resolve_form(self.root, f"{ru}.Основной.ФормаТабло"), form)
                self.assertEqual(resolve_form(self.root, f"{en}.Основной.ФормаТабло"), form)
                self.assertEqual(form_getter(f"{ru}.Основной.ФормаТабло"),
                                 f'{manager}.Основной.ПолучитьФорму("ФормаТабло")')

    def test_resolve_missing_form(self) -> None:
        with self.assertRaises(EdtMetadataError) as ctx:
            resolve_form(self.root, "Catalog.Номенклатура.ФормаНет")
        self.assertEqual(ctx.exception.code, "E_FORM_NOT_FOUND")
        self.assertIn("ФормаЭлемента", str(ctx.exception))

    def test_resolve_managed_form(self) -> None:
        with self.assertRaises(EdtMetadataError) as ctx:
            resolve_form(self.root, "Catalog.Номенклатура.ФормаУправляемая")
        self.assertEqual(ctx.exception.code, "E_FORM_MANAGED")
        self.assertIn("AI-EDT", str(ctx.exception))

    def test_resolve_bad_fqn(self) -> None:
        with self.assertRaises(EdtMetadataError) as ctx:
            resolve_form(self.root, "Номенклатура")
        self.assertEqual(ctx.exception.code, "E_FORM_FQN")

    def test_read_attributes_and_tabular_sections(self) -> None:
        meta = read_object_metadata(self.root / "src/Catalogs/Номенклатура/Номенклатура.mdo", self.root)
        self.assertEqual(meta.attributes["Артикул"].uuid, "bbbbbbbb-0000-0000-0000-000000000001")
        self.assertEqual(meta.attributes["Артикул"].pattern, ['"Pattern"', ['"S"', "50", "1"]])
        self.assertEqual(meta.attributes["ЕдиницаИзмерения"].pattern,
                         ['"Pattern"', ['"#"', "ffffffff-0000-0000-0000-000000000003"]])
        self.assertTrue(meta.attributes["Услуга"].is_boolean)
        self.assertEqual(meta.attributes["Вес"].pattern, ['"Pattern"', ['"N"', "11", "8", "0"]])
        self.assertEqual(meta.attributes["ВремяПоставки"].pattern, ['"Pattern"', ['"D"', '"T"']])
        self.assertEqual(meta.attributes["ДатаСоздания"].pattern, ['"Pattern"', ['"D"', '"D"']])
        self.assertIsNone(meta.attributes["Изображение"].pattern)
        section = meta.tabular_sections["Упаковки"]
        self.assertEqual(section.uuid, "cccccccc-0000-0000-0000-000000000001")
        self.assertEqual(list(section.attributes), ["Коэффициент", "ЕдиницаИзмерения"])
        self.assertEqual(section.attributes["Коэффициент"].pattern, ['"Pattern"', ['"N"', "1", "0", "1"]])

    def test_standard_attributes(self) -> None:
        meta = read_object_metadata(self.root / "src/Catalogs/Номенклатура/Номенклатура.mdo", self.root)
        self.assertEqual(meta.attributes["Код"].uuid, "-2")
        self.assertEqual(meta.attributes["Код"].pattern, ['"Pattern"', ['"S"', "9", "0"]])  # fixed by default
        self.assertEqual(meta.attributes["Штрихкод"].pattern, ['"Pattern"', ['"S"', "20", "0"]])
        self.assertEqual(meta.attributes["Наименование"].uuid, "-3")
        self.assertEqual(meta.attributes["Наименование"].pattern, ['"Pattern"', ['"S"', "150", "1"]])
        self.assertNotIn("Родитель", meta.attributes)   # not hierarchical (see test_review_fixes)
        document = read_object_metadata(self.root / "src/Documents/Заказ/Заказ.mdo", self.root)
        self.assertEqual(document.attributes["Номер"].uuid, "-2")
        self.assertEqual(document.attributes["Дата"].uuid, "-3")

    def test_object_type_id(self) -> None:
        meta = read_object_metadata(self.root / "src/Catalogs/Номенклатура/Номенклатура.mdo", self.root)
        self.assertEqual(meta.object_type_id, "aaaaaaaa-0000-0000-0000-000000000001")
        self.assertEqual(meta.kind, "Catalog")
        self.assertEqual(meta.name, "Номенклатура")


if __name__ == "__main__":
    unittest.main()
