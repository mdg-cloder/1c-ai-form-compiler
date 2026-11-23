# Command panel format (КоманднаяПанель, FormElements27, 8.3.27)

How a command panel of an ordinary form is stored, and what the `add_command_panel` plan operation
writes. Code: `src/one_c_form_compiler/core/command_panel.py`.

The statistics below were collected over the ordinary forms of a typical configuration in an EDT
project (3691 forms, 7124 command panels in 3624 forms; 7123 panels parse). The forms themselves
are not part of the repository.

## Element record

v8unpack treats a command panel as a plain FormElements27 element: there is no panel-specific
class, and with auto-include off (as this tool runs it) the raw record is written back unchanged.
**Every count inside the record is copied verbatim**, so it must be correct when written; a wrong
count makes 1C fail with "Ошибка формата потока".

```
raw = [e69bf21d-97b2-4f37-86db-675aea9ec2cb, <id>, ["2", P], <layout>, ["14", "<Имя>", <action source>, "0", "0", "0"], ["0"]]
```

* `raw[-2][1]` — the name; `raw[-2][2]` — the action source:

  | Value | Meaning | Panels |
  |---|---|---|
  | `"0"` | the form | 4279 |
  | id of a table field (ТабличноеПоле) | the table | 1997 |
  | `"4294967295"` | none | 826 |
  | id of an Html field / list field / check box | that element | 10 |
  | id not present in the form | dangling | 11 |

* The link is one-way: neither the table record nor the form root refers to the panel.
* `P = raw[2][1]` has 14 items for version `"9"`; `P[3]` is the autofill flag (`"1"` / `"0"`),
  `P[7]` is the bar, `P[8:]` = `["b78f2e80-ec68-11d4-9dcf-0050bae2bc79", "4", "9d0a2e40-b978-11d4-84b6-008048da06df", "0", "0", "0"]`.
  Form-level panels (source `"0"`) differ in the style block and in `P[4]`, `P[10]`, `P[11]`, so
  they are not used as templates for table panels.

## Bar

```
["5", BAR_UUID, COUNTER, "1", N, REC × N, M, MENU × M]          len = 6 + N + M
MENU = ["5", menu_uuid, num, flag, K, (rec_uuid, PROPS) × K, TERM]  len = 6 + 2K
```

Invariants (held by all 7123 parsed panels; `bar_issues()` checks them on every panel the tool
writes):

* bar field `[3]` is `"1"`;
* the root menu `("b78f2e80-ec68-11d4-9dcf-0050bae2bc79", "4")` is always present, even with no
  buttons; it is not necessarily the last menu, so it is found by `(P[8], P[9])`;
* `PROPS` has 16 items and `PROPS[6] = BAR_UUID`; `PROPS[7]` is the button id;
* button ids are unique within a panel and `COUNTER` ≥ the largest id;
* menu pair uuids match the REC uuids one to one;
* a REC is `["6", rec_uuid, "1", KIND, ACTION, MASK, optional…]` with `popcount(MASK)` optional
  fields.

```
PROPS = ["8", "<ИмяКнопки>", "0", "1", <caption loc>, <visible>, BAR_UUID, "<id>", "1e2", <type>, "0", "1", "0", "0", "0", "0"]
```

`<type>` is `"0"` for a button, `"2"` for a separator, `"1"` for a submenu; `<visible>` is `"1"`
for buttons and `"0"` for separators.

### Record kinds

* **Separator:** KIND `abde0c9a-18a6-4e0c-bbaa-af26b911b3e6`,
  `["6", u, "1", KIND, ["1", "9d0a2e40-b978-11d4-84b6-008048da06df", "0"], "0"]`; PROPS name
  `Разделитель`, caption `["1", "0"]`.
* **Button calling a form-module procedure:** KIND `e1692cc2-605b-4535-84dd-28440238746c`
  (the same kind as element event handlers),
  `["6", u, "1", KIND, ["3", "<Процедура>", ["1", "<Процедура>", S, S, S, PICTURE_NONE, ["0", "0", "0"]]], "6", <Текст loc>, <Подсказка loc>]`
  where `S = ["1", "1", ["\"ru\"", "<caption>"]]`. Mask `6` means two optional fields follow; mask
  `7` adds a third one with a picture. The procedure takes one parameter, `Кнопка` (13355 of 13708
  handler buttons in the corpus).
* **Standard table action:** KIND `fbe38877-b914-4fd5-8540-07dde06ba2e1`,
  `["6", u, "1", KIND, ["6", "4294967295", GROUP, CODE, ["1", "99", "b78f2e80-…", "4", "1"], "0", "1"], "6", <Текст loc>, <Подсказка loc>]`.
  GROUP is `00000000-0000-0000-0000-000000000000` for the codes below. The inner
  `["1", "99", …]` record does not depend on the code; the tool writes its most common value.

| Plan name | Code | Текст | Button name | Caption |
|---|---|---|---|---|
| `Добавить` | 80 | Добавить | Действие | &Добавить |
| `Скопировать` | 82 | Добавить копированием | Действие1 | &Скопировать |
| `Изменить` | 84 | Изменить текущий элемент | Действие2 | &Изменить |
| `Удалить` | 83 | Удалить текущий | Действие3 | &Удалить |
| `ПереместитьВверх` | 85 | Переместить вверх | Действие4 | Переместить вверх |
| `ПереместитьВниз` | 86 | Переместить вниз | Действие5 | Переместить вниз |

Other codes seen in the corpus (not offered by the plan): 88/89 sort ascending/descending, 90
refresh, 91 filter by current value, 93 disable filter, 94 end editing, 97 list settings, 99 output
list, 120/121 check/uncheck all, 53 filter and sort (GROUP `6b7291bf-bcd2-41af-bac7-414d47cc6e6a`).

Autofill against bar contents, table-bound panels:

| Autofill | Bar contents | Panels |
|---|---|---|
| 1 | empty | 699 |
| 1 | handler buttons only | 559 |
| 1 | standard actions (+ handlers) | 14 |
| 0 | standard actions + handlers | 366 |
| 0 | standard actions only | 254 |
| 0 | handler buttons only | 97 |

## Layout of a table panel

* The panel height is bound to its own top edge: `edges[BOTTOM] = ["2", <own id>, "0", "<height>"]`,
  with the matching reverse entry `["0", <own id>, "1"]` on the TOP list. Moving such a panel
  must change the offset by `delta[edge] − delta[target border]`; the tool's move does this.
* Typical placement (1997 table-bound panels): on the same page as the table (1937), above it
  (1779) with a gap of 0 (1357) or 3 (55), height 24 (1668) or 25 (142).
* The tree order is not required: 514 panels come before their table. The common pattern is the
  panel right after the table in the tree, panel tab = table tab + 1, panel z = table z (the table
  and everything with z ≥ that shift up by one).

## What `add_command_panel` writes

* Template: a table-bound panel of the same form (14 items in `P`, version `"9"`, consistent bar);
  otherwise the built-in `CommandPanel` entry of `core/ordinary_templates.json`
  (`tools/generate_ordinary_templates.py`).
* A new id (form id counter + 1), `raw[-2] = ["14", name, <table id>, "0", "0", "0"]`, empty
  tooltip, `P[3]` = autofill, `P[7]` = a new bar.
* New bar: a random BAR_UUID; the listed standard actions, then a separator and the custom buttons
  if there are any; button ids 1..n in display order; `COUNTER = max(n, 1)`; the root menu holds
  all pairs, `TERM = ["0", "0", ["0"]]`.
* Geometry: the table's left/right, bottom = table top − gap, height bound to its own top; TOP is
  bound to the same container border as the table's TOP when that one is bound to the container;
  LEFT and RIGHT copy the table's container bindings (with the panel anchor index entries). The
  panel must not go above 0 or overlap another element of the page (`E_NO_SPACE`).
* Tab and z order as in the common pattern above; the tree node goes right after the table.
* Data sources are not touched (command panels have none).
* For each custom button the form module gets `Процедура <procedure>(Кнопка)` unless the
  procedure exists.

## Values copied without a known meaning

These are taken from templates or the majority of the corpus: `P[2]`, `P[4]`, `P[5]`;
`PROPS[2]`, `[3]`, `[5]`, `[10]`, `[13]`; the menu TERM; the inner record of standard actions.
Button names are kept unique within a panel. Not supported: the "История отборов" submenu (needs
a second menu), form-level panels (buttons are added to them with `add_button`, below).

## Buttons of an existing panel: `add_button`, `remove_button`

Code: `add_button` / `remove_button` in `core/command_panel.py`. Both refuse a panel whose bar fails
`bar_issues()` (`E_PANEL_FORMAT`) and check the bar again after the change.

Statistics over the same corpus (5509 panels with buttons; "root" = the panel's root menu):

| Slot | Values |
|---|---|
| `PROPS[10]` of handler buttons in root menus of form-level panels | `0` 1635, `1` 1017, `3` 38 |
| `PROPS[10]` of handler buttons of table panels | `0` 617, `2` 75, `1` 15 |
| `PROPS[3]` of handler buttons in root menus of form-level panels | `1` 1723, `2` 515, `3` 194, … (up to 901 on submenus) |
| `COUNTER` = largest button id | 2521 panels; larger in 2988 |

* `PROPS[10]` is how the button is shown: `0` auto, `1` text, `2` picture, `3` picture and text (the
  OK/Записать/Закрыть buttons of a bottom form panel use `1`; buttons with a picture use `2`/`3`). A new
  button has no picture: it takes `0`/`1` from the nearest button of the root menu (before the insertion
  point first), else `0`.
* `PROPS[3]` has no known meaning (it looks like a per-button change counter); new buttons get `1`, the
  most common value.

What `add_button` writes:

* REC appended after the existing records (`bar[4]` + 1); the order of records does not matter, the
  display order is the order of pairs in the menu;
* button id = `COUNTER` + 1, and `COUNTER` is set to it (ids are local to the panel's bar, they are not
  form element ids);
* the pair `(rec_uuid, PROPS)` inserted into the root menu at `before`/`after` or at the end; the menu
  count `menu[4]` follows; the menu TERM is kept;
* a handler button: KIND `e1692cc2-…`, mask `6`, Текст = caption, Подсказка = tooltip (the same record as
  `add_command_panel` buttons); a standard table action: the `STANDARD_ACTIONS` record (only for a panel
  whose action source is a table field); with `separator` a separator REC/PROPS (name `Разделитель<N>`,
  first free) goes in front of the button and takes its own id;
* `Процедура <procedure>(Кнопка)` in the form module unless it exists.

`remove_button` drops the pair from whatever menu holds it and the REC when no other pair uses it;
submenus (KIND `c93a51ed-b2d1-47f1-948d-38373f8710af`, PROPS type `1`, their own menu) are refused.
`inspect` lists a submenu as `submenu: true`.
