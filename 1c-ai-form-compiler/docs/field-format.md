# ТолькоПросмотр of input fields and table fields (FormElements27, 8.3.27)

What the `read_only` plan field (`add` / `modify`, CLI `--read-only` / `--no-read-only`) writes. Code:
`OrdinaryForm.read_only` / `set_read_only` in `core/ordinary_form.py`.

Both positions were confirmed in 1C:Enterprise: a copy of the test sample with one position flipped was
opened by `runtime-check`, which reads `ЭлементыФормы.<Имя>.ТолькоПросмотр` of the live form. The
statistics below were collected over the ordinary forms of a typical configuration in an EDT project
(3695 forms, 13422 input fields, 4184 table fields). The forms themselves are not part of the repository.

## Input field (ПолеВвода): `B[13]`

```
raw = [381ed624-9217-4e63-85db-c4c3cb87daae, <id>, ["9", <value type Pattern>, [B, …], …], <layout>, ["14", "<Имя>", …], …]
B   = raw[2][2][0] = [<style ["13", …]>, "29", <flags>…]
```

`B[1]` is `"29"` on 13315 fields (`"21"`: 91, `"30"`: 16); the flags up to `B[13]` have the same shape in
all three (the `Номер` field has `B[13] = "1"` in each).

| `B[13]` | Fields |
|---|---|
| `"0"` | 12473 |
| `"1"` | 949 |

Live evidence (sample form: `Номер` is read-only in 1C, `Дата`, `Комментарий`, `Ответственный` are editable;
in the sample only `Номер` has `B[13] = "1"`):

| Variant | Live ТолькоПросмотр |
|---|---|
| `Комментарий` `B[13]` 0→1 (with `Дата` `B[16]` 0→1, `Ответственный` `B[14]` 0→11) | only `Комментарий` became read-only |
| `Номер` `B[13]` 1→0, `Дата` and `Ответственный` `B[13]` 0→1, `Комментарий` `B[12]` 0→1 | `Номер` editable, `Дата` and `Ответственный` read-only, `Комментарий` stays editable |

Corpus sanity check:

* `B[13] = "1"` sits on fields that are read-only by design: `Номер` (311), `Код` (149), the totals
  `Всего` (45) and `ВсегоНДС` (40), settlement amounts (`СуммаВзаиморасчетов`, `ПоДокументуВал`, …);
* among fields whose form module only ever sets `ЭлементыФормы.<Поле>.ТолькоПросмотр = Истина`, 37 of 39
  have `B[13] = "0"` (the module turns read-only on at runtime).

`B[12]` (an earlier guess; `"1"` on 506 fields, mostly totals and presentations) is **not** ТолькоПросмотр:
flipping it does not change the live property. Its meaning is not established and the tool leaves it as is.

The tool writes only `B[13]` (`"1"` / `"0"`) and refuses a field whose block does not have the shape above
(`E_PROPERTY_FORMAT`).

## Table field (ТабличноеПоле): bit 10 of `T[1]`, inverted

```
T = raw[2][2][1] = ["15" | "17", <mask>, <colors>…, <columns at [23]>, …]
```

`T[1]` is a decimal bit mask of boolean properties. Bit 10 (`1024`) **set** means editable; **cleared**
means ТолькоПросмотр. The tool accepts the record when `T[0]` is `"15"` or `"17"` (4159 of 4184 tables)
and bits 11 and 12 of the mask are set (true for every table of the corpus), otherwise
`E_PROPERTY_FORMAT`.

Live evidence (all five tables of the sample are editable in 1C):

* clearing bit 10 of `ЗаполнениеДвижений` made it read-only; in another copy clearing it on
  `ТабличноеПолеДвиженияБУ` and `ТабличноеПолеДвиженияМУ` made those two read-only while the others stayed
  editable; setting the bit back (on a copy where it was cleared) made the table editable again;
* flipping the other candidates did not change the live property: bits 0–9, 11–15, 18–23, 27 and the
  scalars `T[12]`, `T[13]`, `T[14]`, `T[15]`, `T[17]`, `T[22]`, `T[24]`.

Corpus sanity check:

* bit 10 is cleared on 291 of 4184 tables: trees and lists of selection and helper forms
  (`СправочникДерево` 90, `РегистрНакопленияСписок` 19, `Дерево` 16, register lists, search results);
* all 16 tables whose module only sets `ТолькоПросмотр = Истина` have the bit set (editable in the
  record); 4 of 5 tables whose module only sets `ТолькоПросмотр = Ложь` have it cleared.

## Defaults and checks

A new field or table (`add` without `template`) is editable unless `read_only` is given, so it does not
inherit read-only from the element it was cloned from; with an explicit `template` the flag of the
template is kept unless `read_only` is given. `inspect` shows `read_only: true`, and `runtime-check`
compares the flag with `ЭлементыФормы.<Имя>.ТолькоПросмотр` of the opened form.

## Check boxes

The ordinary check box (Флажок) has no ТолькоПросмотр property: reading it in the opened form raises an
error, so `read_only` is refused for check boxes (`E_TYPE_UNSUPPORTED`) like for every type other than
input fields and table fields. Use `ЭлементыФормы.<Имя>.Доступность` in the form module (`set_procedure`).
