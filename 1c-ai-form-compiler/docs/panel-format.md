# Panel format (Панель, FormElements27, 8.3.27)

How a panel of an ordinary form is stored, and what the `add_panel`, `add_page`, `remove_page` plan
operations and `remove` of a panel write. Code: `src/one_c_form_compiler/core/panel.py` and
`OrdinaryForm.add_page` / `remove_element` / `container_size` in `core/ordinary_form.py`.

The statistics below were collected by `tools/add_panel_template.py` over the ordinary forms of a
typical configuration in an EDT project: 3695 forms, 1124 of them with panels, 1915 panels (not
counting the form's root panel), 251 of them nested on a page of another panel, 4905 pages. The
forms themselves are not part of the repository.

## Element record

v8unpack decodes a panel (type `09ccdc77-ea1a-4a6d-ab1c-3435eada2433`, the same type as the form's
root panel) with its pages and children moved out of the record:

```
raw = [PANEL_UUID, <id>, ["1", P, <events>], <layout>, ["14", "<Имя>", "4294967295", "0", "0", "0"], <children>]
```

* `raw[2][2]` — event list: `["0"]` (1688 panels) or the `ПриСменеСтраницы` handler (226).
* `raw[-2]` — the name; the other items are `"4294967295", "0", "0", "0"` on 1912 of 1915 panels.
* `raw[-1]` — the child list. v8unpack replaces it with `["Дочерние элементы отдельно"]` and keeps the
  children in the tree (`node["child"]`) and in `data["<panel key>/<page>/<name>"]`. A panel without
  children is left as `["0"]` (18 panels). On build v8unpack rewrites the count from the tree, so
  the tool only keeps this marker in step with the tree (`OrdinaryForm.save`): otherwise the rebuilt
  container would not decode to exactly the edited form.
* `raw[3]` — the usual layout record (rectangle, edge bindings, reverse anchors, tab/z order). Its
  last two flags (`tail[5:7]`) vary (`1 1`: 801, `0 0`: 631, `0 1`: 301, `1 0`: 181); the template
  keeps the value of the dominant panel shape (`0 0`).

## Panel record `P = raw[2][1]`

```
[style, "25", n0, e.., n1, e.., n2, e.., n3, e..,   # anchor index of the panel's children
 A, .., B, ..,                                      # A = B = 0 on every panel
 <pictures>, <tabs>, <x>, ["1"],                     # ["1"] - the page list (pages moved out)
 <x>, <x>, <x>, "в отдельном файле",                 # the page guides (moved to each page's "info")
 <flag>, 4294967295 × <pages>, "5", "64", "0", ["3", "4", ["0"]], "0", "0", "57", "0"]
```

* `P[1]` is `"25"` on 1898 panels (21: 9, 20: 6, 26: 1).
* The **anchor index** (`n0..n3` groups of `[kind, element id, edge]`) lists the children bound to the
  panel's top / bottom / left / right border, exactly like the form's root index. It is the
  "container index" of a nested panel: `Container.raw_index = raw[2][1]`.
* The position of `["1"]` is found the way v8unpack finds it (`calc_offset`): after the four anchor
  groups, the counts `A` and `B`, then 4 more items. The page list is always `["1"]` after decoding.
* `<tabs>` (two items before `["1"]`): `"1"` — tabs shown (694 panels), `"0"` — no tabs, the pages
  fill the panel (1201), other small numbers on 19 panels (they behave like `"1"`).
* After `"в отдельном файле"`: one flag (`"0"`: 1704, `"1"`: 210), then **exactly one value per page**
  (`4294967295` on 1900 panels; 14 panels have other numbers for some pages), then `"5"` (1914 of 1915;
  the remaining panel has a malformed record). Without a page's value 1C reports "Ошибка формата
  потока" when it reads the form; the tool writes `4294967295` for a new page and deletes the value at
  the page's position when a page is removed. When the flag is not below the new page count, it is
  reset to `"0"`.
* The tail after `"5"` is `["64", "0", ["3", "4", ["0"]], "0", "0", "57", "0"]` on 1887 panels.
* `<pictures>` is the three-picture block without pictures on 1797 panels.

`panel_record_issues()` checks this shape; `add_page` / `remove_page` refuse a panel that fails it
(`E_PANEL_FORMAT`), and every panel `add_panel` writes passes it.

## Pages

A page record (`data["<panel key>/<page>"]`, `page_format_version` `"4"` on 4877 of 4905 pages,
10 items):

```
["4", <caption loc>, <pictures>, "-1", <visible>, <enabled>, "\"<Имя>\"", "1", ["3", "4", ["0"]], ["3", "4", ["0"]]]
```

The page list of the panel (`data["<panel key>/-pages-"]`) gives the order; an element's page is its
`layout.tail[2]` (page index) and `data[key]["page"]` (`"<panel key>/<page name>"`).

### Guides (направляющие)

Each page has four border guides; they are stored after the pages in one flat list, which v8unpack
slices **positionally** into the pages' `info` (the first page gets four plus every extra guide, each
other page four). So a page's `info` is not necessarily its own (about a third of the pages hold guides of
another page); `entry[5]` — the page index — ties a guide to a page.

```
["2", <coord>, <orientation>, "1", <kind>, <page index>, "0", <margin>, "0"]
```

* kind `1`/`2`/`3`/`4` — left / top / right / bottom border, kind `0` — a user guide;
* for the right and bottom guides `coord + margin` is the **client width / height** of the page:
  checked against the elements bound to the page's right border (3928 of 3949 pages) and bottom
  border (2956 of 3006). The old formula `right + left guide` was wrong for pages whose left and
  right margins differ;
* 4846 of 4905 pages have all four border guides; typical margins with tabs: 6 px on every side.

When the tool grows a top-level panel, it moves every bottom guide by the same amount, so an empty
page keeps the right size.

### Client area

| Panel | panel width − client width | panel height − client height |
|---|---|---|
| tabs shown | 2 (2794 pages) | 20 (2321), 22 (37), 24 (29) |
| no tabs | 0 (1044) | 0 (539) |

`container_size()` of a page: the page's own right/bottom bindings; otherwise those of a sibling
page (the client area is the same on every page); otherwise the page guides; otherwise the panel
rectangle minus the frame above. A new page (`add_page`, pages of `add_panel`) gets four guides
fitted to that client area, so it never ends up with a 0x0 size.

## What `add_panel` writes

* The built-in `Panel` entry of `core/ordinary_templates.json`
  (`{"panel": raw, "page": page record, "guides": [4 border guides]}`) — a tabbed panel whose record
  matches the dominant shape of the 694 tabbed panels in every checked slot (249 panels share it
  exactly), and a page record of the dominant page shape (2623 of 2893 pages of tabbed panels).
  Regenerate with `python tools/add_panel_template.py <EDT project>`; `--stats-only` prints the
  statistics only. The template is neutral: id 0, name `Панель`, one page `Страница1`, no anchors,
  no children, no event handlers.
* A new id (form id counter + 1), the name, `["0"]` children, the per-page values `4294967295 × N`,
  flag `"0"`, empty anchor index.
* Layout as for every added element: rectangle, the four edges bound to the container (stretch as in
  `add`), tab index last on the page, z = count + 1.
* Pages: the template page with the given name and caption, visible; four border guides with the
  page index and the client area `width − 2, height − 20`, margins kept from the template.
* The tree node `{"name", "type": "Panel", "page", "child": []}` and `data["<key>/-pages-"]`.
* Default rectangle as for a table: a new row below the content of the target page, full width minus
  the margins, height 150; the form or a top-level panel grows, a nested panel does not
  (`E_NO_SPACE`). Smallest panel: 40x50.

## Removal

* `remove_page`: the page's guides are dropped (by page index), later page indices are decremented
  in the guides and in the elements' `layout.tail[2]`, the flat guide list is re-sliced the way
  v8unpack slices it, the page's per-page value is deleted. The last page cannot be removed.
* `remove` of a panel: every element inside (any depth), its pages and `-pages-` lists go; data
  sources of removed elements are dropped; command panels whose action source is a removed element
  are removed too; the panel itself is detached like any element (dependents rebound to the
  container, tab/z order compacted).
* `remove` of a table field: its command panels (action source = the table) are removed first, then
  the table with its columns (they live inside its record) and its data source.
