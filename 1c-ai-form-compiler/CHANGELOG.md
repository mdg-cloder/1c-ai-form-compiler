# Changelog

## 0.5.0 — 2026-11-21

### Added

* `add_panel` plan operation — a new panel (Панель) with tabs and pages: `name`, `pages` (names or
  `{"name", "caption"}` with `ru`/`uk` captions), `container`/`page`, `left`/`top`/`width`/`height`,
  `stretch`. Default placement like a table (full width, height 150). The record comes from the
  built-in `Panel` template extracted from a typical configuration by `tools/add_panel_template.py`
  (format: `docs/panel-format.md`). CLI: `add --type Panel --name … --page A --page B`.
* `remove_page` plan operation — removes a page of a panel (`force` removes the elements on it).
* `remove` now removes panels (with all pages and contents), table fields (with the command panels
  whose action source they are) and command panels; module references still need `force`.
* `add --stretch` in the CLI.
* `inspect`: panels list their `pages` with captions.
* `add_button` / `remove_button` plan operations — buttons (a form-module procedure with a
  `Процедура <proc>(Кнопка)` stub, or a standard table action) and separators in **existing** command
  panels, including the form's own `ДействияФормы` / `ОсновныеДействияФормы`: `before`/`after`
  placement, `separator`, `caption`, `tooltip`; removal needs `force` when the module uses
  `…Кнопки.<Имя>`; submenus are refused. Format: `docs/command-panel-format.md`.
* `read_only` for input fields and table fields in `add` and `modify` (CLI `--read-only` /
  `--no-read-only`): ТолькоПросмотр of an input field is `raw[2][2][0][13]`, of a table field the
  cleared bit 10 of the mask `raw[2][2][1][1]` — both confirmed by flipping them in 1C and reading
  `ТолькоПросмотр` of the opened form (`docs/field-format.md`; an earlier draft wrote `[12]`, which the
  platform ignores). A new element is editable unless asked; `inspect` shows `read_only: true`. Check
  boxes are refused: the ordinary Флажок has no ТолькоПросмотр.
* Checkbox columns of tabular-section tables: `add_column` with `"checkbox": true` and a Boolean
  attribute in `data` writes it to `ДанныеФлажка` (no `Данные`, no own cell control — as on typical
  forms); `E_DATA_REQUIRED` / `E_DATA_TYPE` otherwise.
* Dotted column data (`ЗаказКлиента.Контрагент`) for tabular-section tables of EDT configuration forms:
  the chain is checked against the `.mdo` of the referenced objects (reference attributes, their own and
  standard attributes); the column has no data and its text is set by a generated line in the table's
  `ПриВыводеСтроки` handler (typical forms have no multi-step column paths). `remove_column` removes the
  line. EDT metadata keeps attribute type names and the project root of tabular sections.
* `runtime-check` also compares ТолькоПросмотр of input fields and table fields and the buttons of command panels.
* Two-column rows without pixels: `add` with `label` now combines with `beside` (the label + field
  pair goes on that element's row, right after it), with `column` and with `left`/`top`. New `column`
  field (`1`/`2`, CLI `--layout-column`): the left/right half of the page; the right column's labels
  start at the middle, its fields are aligned and stretch with the form (`stretch: width` unless a
  `width` is given). Row placements must not overlap neighbours (`E_NO_SPACE`). A label, field,
  button or check box placed in an existing row goes right after the elements to its left in the tab
  order (row-major, as in typical document headers).
* `set_form_size` plan operation and `size` command (`--width`/`--height`): resizes the form by the
  anchor model — edges bound to the right/bottom border move with it, edges bound to other elements
  follow them, resized panels lay out their pages (and move their page guides); docked bottom command
  bars follow the bottom. A size at which an element would leave its container, collapse or newly
  overlap another is refused (`E_GEOMETRY_INVALID`); 50..3000 px. Result lists the `moved` elements.
* `add_command_panel` shifts the table down when there is no room above it (`shift`, default `true`):
  the table and the elements of its page that start at or below its top move down just enough for the
  panel and its gap; free room at the bottom of the page is used first, otherwise the form or a
  top-level panel grows; a nested panel without room still gives `E_NO_SPACE`. The result reports
  `shifted`.

### Changed

* Explicit `left`/`top`/`width`/`height` of `add` (and `add_panel`) below or right of the client area
  grow the form instead of failing: a band opens above the bottom strip (the bottom command bar moves
  below the element, the content above stays); the width grows by the anchor model. A top-level panel
  grows in height; nested panels and panel widths still give `E_GEOMETRY_OUTSIDE`. An explicit width
  with an explicit left is no longer clipped to the client area.
* `runtime-check`: column widths are compared with a tolerance of ±2 characters (±12 % for wide
  columns) — the platform converts pixels with the table's font, so the same 154 px came back as 22 as
  well as 24 characters and default-width columns no longer give false problems.
* `remove_column` without `force` is refused also when the module uses `…Ячейки.<Колонка>`.
* `inspect`: a submenu of a command panel is listed as `submenu: true` (was a bogus `procedure`).

### Fixed

* A page added to a panel (and every page of a new panel) gets the panel's client area: its size
  comes from the bindings of sibling pages, then from page guides (right/bottom guide + margin),
  then from the panel rectangle minus the tab frame. The new page's guides are fitted to that
  area, and growing a panel moves the bottom guides with it.
* A nested panel's child list is written as v8unpack decodes it (`["0"]` when the panel is empty),
  so adding an empty panel survives the rebuild check.
* `Form.oform` round trip: the form text is read and written without newline translation and with
  fixed v8unpack writer/panel code (`core/oform.py`): CR CR LF and bare CR in string literals are kept,
  quoted strings over 64 characters are no longer wrapped as base64 (also for new long captions), the
  ending of base64 blocks with a full last line is kept per block, pages with empty or repeated names
  no longer overwrite each other (addressed as `#0`, `#1`, … / `<name>#<index>`), and panels with fewer
  than four guides per page keep their guide list. On a typical configuration 3689 of 3695 ordinary
  forms now open for editing (3626 before); `tools/scan_forms.py` repeats the scan on any EDT project.
* The remaining refusals have their own codes instead of `E_OFORM_ROUNDTRIP`:
  `E_FORM_DUPLICATE_NAMES`, `E_FORM_EMPTY_NAME`, `E_FORM_PROP_NAME`.
* EPF/ERF: a no-op rebuild must reproduce the `form`/`module` entries of every ordinary form byte for
  byte before a form is edited (`E_FORM_ROUNDTRIP`, `E_FORM_DUPLICATE_PAGES`), so v8unpack can no
  longer silently change untouched parts of a form.
* Element ids come from one allocator: above the counter and above every id the form mentions
  (anchors, panel indexes, data sources, command panel action sources, default button). On load the
  counter is raised to the highest element id and anchor entries of deleted elements are dropped;
  the repairs are listed in `repairs` of the command output (`--json`) and as `!` lines. A stale anchor no longer attaches to a new element and
  a form written with a too low counter no longer fails the first add with a duplicate id.
* Integrity check: anchors are compared as multisets (`duplicate … anchor`), self-bindings written
  twice by the platform are accepted, default button `4294967295` means none.

## 0.4.0 — first public release

Command-line tool `one-c-form-compiler` for editing ordinary forms of 1C:Enterprise 8.3
(`FormElements27`) in EPF/ERF files and in EDT projects (`Form.oform`).

### Commands

* `inspect` — elements, coordinates, pages and style passport, data bindings, events, table
  columns, command panels, object attributes and tabular sections (`--json` for agents).
* `add`, `modify`, `remove`, `title`, `add-column`, `modify-column`, `remove-column` — single
  edits.
* `apply --plan` — JSON edit plan, all-or-nothing, up to 100 operations; `--dry-run`.
* `runtime-check` / `--runtime-check` — open the result in the 1C thick client on a test file
  infobase and compare elements, geometry, captions, `Данные`, events and table columns.
* `guide` — the built-in guide for AI agents; `doctor` — environment check.
* `ai` (optional) — natural-language request to a plan through the `anthropic` or `openai`
  provider, validated on a copy and repaired at most 3 times.

### Plan operations

* `add` — Label, Field, Button, CheckBox, Group, Table; captions with a label column, tooltips,
  value types, data binding, event handlers with module stubs, automatic placement in the page
  style, `beside`, explicit geometry, `template`, `stretch` (`width` / `height` / `both`).
* `modify`, `remove` (refused when the module references the element unless `force`),
  `set_title`.
* Table fields bound to tabular sections, to value-table form attributes, or free value tables;
  `add_column`, `modify_column`, `remove_column`, check-box columns, cell events.
* `add_command_panel` — a command panel above a table field: autofill, standard actions, buttons
  bound to form-module procedures.
* `add_page` — a new page at the end of a panel.
* `set_form_events` — form events, including write events of catalog item forms.
* `set_procedure` — replace or add a whole procedure/function of the form module;
  `add_variable` — declare a module variable.

### Safety

* The input EPF/ERF is never written; output is published atomically after a rebuild and
  re-extract comparison.
* `Form.oform` is edited in place only after the same check, with a backup in
  `<project>/.one-c-form-backups/` (or `Form.oform.bak-<timestamp>` next to a file outside an EDT
  project), and only if the file did not change during the edit; `-o new.oform` writes elsewhere.
* Integrity of ids, anchors, tab/z order, data sources and the default button is checked after
  every operation.
* Temporary folders are removed also when extracting the input fails.

### Errors and exit codes

* A missing `--plan` file or `ai --recorded` file is reported as `E_INPUT_MISSING` (exit code 4)
  instead of a traceback.
* Command-line errors (for example `apply` without `-o` or `--dry-run`) are `E_USAGE` with exit
  code 2, as a JSON object with `--json`.
* `E_V8_EXIT_CODE` includes the tail of the v8unpack output and, when extracting fails, a hint
  that the input is probably not a valid EPF/ERF file.
* `ai`: provider `claude` is an alias of `anthropic`; an unknown provider and `openai` without a
  model give specific `E_AI_CONFIG_INVALID` messages; the release build explains that the
  `anthropic` provider needs an installation from source (`pip install ".[claude]"`).

### Documentation and tests

* Synthetic sample `tests/fixtures/sample_ordinary.epf` (an external data processor with one
  ordinary form); the EPF examples in the READMEs and in `guide` run on it as-is.
* `guide`: the complete error table with exit codes, plan-only features, `runtime-check --timeout`,
  backup rules, `refreshWorkspace=true` for AI-EDT `update_database`.
* Documented the exact `ai` timeout rules (`ONE_C_FORM_AI_TIMEOUT_SECONDS`: default 30, raised to
  at least 120 while `ONE_C_FORM_AI_MAX_OUTPUT_TOKENS` is not set, at most 300).

### Distribution

* MIT license.
* Windows release build (PyInstaller, one folder) with end-user instructions and third-party
  notices. The archive has one top-level folder `one-c-form-compiler-<version>-win-x64` with
  `LICENSE`, the READMEs, `THIRD_PARTY_NOTICES.txt`, `set-env.example.cmd` and the program folder
  `one-c-form-compiler\`.
* `THIRD_PARTY_NOTICES.txt` carries the full license texts of the native libraries shipped with
  the Python runtime (OpenSSL, libffi, Expat, mpdecimal, liblzma, zlib-ng, bzip2, Zstandard).
