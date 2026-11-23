# Test fixtures

## `sample_ordinary.epf`

Most model, plan and CLI tests work on one external data processor with an
ordinary (thick client) form, committed as `tests/fixtures/sample_ordinary.epf`.
The tests that need it are skipped with the reason
`sample tests/fixtures/sample_ordinary.epf is missing` when the file is absent, or
`v8unpack is not installed` when the `v8unpack` package is absent.

See it with:

```
python -m one_c_form_compiler inspect tests/fixtures/sample_ordinary.epf [--json]
```

### Content

External data processor `ПримерОбычнойФормы` (synonym «Пример обычной формы»):
no attributes, no tabular sections, an empty object module.

One ordinary form `Форма`, the default form of the processor. Its payload is a
typical document form «Корректировка записей регистров», 650x363, 37 elements:

| Element(s) | Type | Notes |
| --- | --- | --- |
| `Надпись1` + `Номер`, `Надпись2` + `Дата` | label + input field | top row |
| `Надпись4` + `Ответственный`, `Надпись3` + `Комментарий` | label + input field | bottom rows; `Комментарий` is the dominant plain field style |
| `ДействияФормы`, `ОсновныеДействияФормы` | command panel | top / bottom (docked) |
| `ПанельРегистров` | panel | stretches with the form; pages `РегистрыНакопления`, `РегистрыСведений`, `РегистрыБухгалтерии`, `ЗаполнениеДвижений`, `РегистрыРасчета`, each filled by a nested panel (no free room) |
| `ПанельРегистровСведений`, `ПанельРегистровНакопления`, `ПанельРегистровРасчета` | nested panel | page `Подсказка`: two labels and free room below |
| `ПанельРегистровБухгалтерии` | nested panel | pages `Хозрасчетный`, `Налоговый`, `Международный`, `Бюджетирование`, `Подсказка` |
| `ТабличноеПолеДвиженияБУ`/`НУ`/`МУ`/`Бюджетирование` + `КоманднаяПанельБУ`/`НУ`/`МУ`/`Бюджетирование` | table field + its command panel | on the pages of `ПанельРегистровБухгалтерии` |
| `ЗаполнениеДвижений` + `КоманднаяПанельЗаполнениеДвижений` | table field + command panel | inside `ПанельЗаполнениеДвижений` |
| `Гиперссылка…`, `Подсказка…` | label | hyperlinks and hint texts |

The bound elements (`Номер`, `Дата`, `Комментарий`, `Ответственный`, the table
fields) keep the data paths of the source document form. The processor has no
such attributes, so `inspect` shows them with `data: null`, `data_bound: true`.
Tests that need object attributes or tabular sections take them from in-memory
fakes or from the small EDT project built by `tests/test_edt_metadata.py`
(catalog `Номенклатура` with tabular section `Упаковки`).

The form module holds 24 empty event handlers (one per handler the form
references). The form keeps a few stale anchors left by Designer; the model
keeps them in its baseline, so they are not reported as new issues.

Tests look elements, panels and pages up by these names and pin some geometry
(size, row pitch, the dominant field). Update the tests when the sample changes.

### Rebuilding

The sample is built by `tools/build_sample_epf.py` from any EDT ordinary form
container (`Form.oform`); the source is only read. The processor comes from the
`probe_epf` skeleton shipped with the package, the form payload is copied
unchanged, and the form module is replaced by empty handler procedures.

```
set PYTHONPATH=src
python tools/build_sample_epf.py <path/to/Form.oform> tests/fixtures/sample_ordinary.epf ^
    --ids <path/to/sample_ordinary.ids.json> --overwrite
```

`--ids` reuses the UUIDs of the previous build (keys `file_uuid`, `uuid`,
`object_type_uuid`, `manager_type_uuid`, `form_uuid`); without it new UUIDs are
generated, and `--save-ids <file>` writes the ones used. The EPF is not
byte-for-byte reproducible: v8unpack writes fresh UUIDs into the container's
`versions` entry on every build. After a rebuild check that
`inspect tests/fixtures/sample_ordinary.epf` lists the same 37 elements and run
the test suite.
