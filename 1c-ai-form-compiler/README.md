# 1c-ai-form-compiler

[Русская версия](README.ru.md)

`one-c-form-compiler` is a Windows command-line tool that edits **ordinary forms of
1C:Enterprise 8.3** (thick client, element profile `FormElements27`):

* inside external data processors and reports (`.epf` / `.erf`);
* in **configuration forms of an EDT project** (`Forms/<Form>/Form.oform`), for example the item
  form of a catalog.

You describe a change with command options or a JSON edit plan — elements, data binding, event
handlers, table fields and their columns, command panels, pages, form module code — and the tool
keeps every internal cross-reference of the form consistent. It can then open the result in a
**test** 1C infobase and check that the platform shows the form as expected.

The tool is designed to be driven by AI coding agents (Claude Code, Codex and others) **without
any API keys**: the agent runs `one-c-form-compiler guide`, reads the rules and writes the plan
itself.

Managed forms are out of scope.

## Features

* `inspect` — elements with coordinates, pages and their style passport, data bindings, events,
  table columns, command panels, object attributes and tabular sections.
* Add, modify and remove elements: labels, input fields, buttons, check boxes, group frames and
  table fields. Without explicit geometry, new elements are placed in the page's own style (label
  and field columns, typical sizes and row pitch); the form or a top-level panel grows when needed,
  also for explicit coordinates below or right of the form.
* Rows and two-column headers without pixels: a label + field pair beside an element (`label` +
  `beside`), in the left or right half of the page (`column`: 1/2, the right field stretches with the
  form), or at `left`/`top`; tab order follows the rows.
* Form size (`set_form_size`, `size --width/--height`): elements follow their anchors as in 1C
  (bottom bar, stretching panels and tables, nested pages); shrinking below the content is refused.
* Data binding of fields and check boxes to object attributes; table fields bound to tabular
  sections, to value-table form attributes, or free value tables filled by the form module.
* «Только просмотр» (read-only) of input fields and table fields (`read_only`, `--read-only`).
* Table columns: add, modify, remove; check-box columns of value tables and of tabular sections
  (Boolean attribute in `ДанныеФлажка`); dotted columns (`ЗаказКлиента.Контрагент`) resolved through
  the EDT metadata and shown via `ПриВыводеСтроки`; cell events.
* Event handlers for elements, tables, columns and the form itself, with procedure stubs added
  to the form module.
* Command panels above a table field (the table and what is below it move down when there is no
  room above it): autofill, standard actions and buttons bound to form-module procedures; buttons and separators added to or removed from existing command panels, including
  the form's own (`add_button`, `remove_button`).
* New panels with pages (`add_panel`, also `add --type Panel --page …`), new pages (tabs) of a panel;
  removing pages (`remove_page`), panels with their contents, and table fields with their command
  panels; `stretch` anchoring for elements that grow with the form.
* Form module code: replace or add a whole procedure/function, declare module variables.
* All-or-nothing JSON edit plans (up to 100 operations) with stable error codes.
* Runtime check in the 1C thick client on a test infobase.
* Optional built-in `ai` command (natural-language request → plan) for use without an agent.

## Requirements

Windows only.

| Feature | What you need |
|---|---|
| Everything (from source) | Python 3.14 and `v8unpack==1.2.13` (pulls in `tqdm` and `colorama`) |
| Everything (release build) | Nothing: Python and v8unpack are bundled in the executable |
| `runtime-check`, `--runtime-check` | 1C:Enterprise 8.3 thick client (`1cv8.exe`) and a **test file infobase** |
| Configuration forms | An EDT project on disk; to check them at runtime, a test infobase loaded from that project |
| `ai` command (optional) | Provider `anthropic` (alias `claude`): the `anthropic` Python SDK (`pip install -e ".[claude]"`; not bundled in the release build) and an API key or SDK credentials. Provider `openai`: an OpenAI API key (built-in HTTPS client to the OpenAI Responses API, no SDK) |

Checked with platform 8.3.27 and v8unpack 1.2.13.

## Install

### A. Release build (no Python needed)

1. Download `one-c-form-compiler-<version>-win-x64.zip` from the releases (check it against
   `SHA256SUMS.txt`) and unzip it to any folder, for example `C:\Tools`. The archive has one
   top-level folder, so you get:

   ```
   C:\Tools\one-c-form-compiler-<version>-win-x64\
     LICENSE, README.md, README.ru.md, THIRD_PARTY_NOTICES.txt, set-env.example.cmd
     one-c-form-compiler\one-c-form-compiler.exe   (+ the _internal folder next to it)
   ```

2. Open `cmd` or PowerShell in
   `C:\Tools\one-c-form-compiler-<version>-win-x64\one-c-form-compiler` and run
   `one-c-form-compiler.exe doctor`.
3. Optionally add that folder to `PATH`.

The top folder holds the license (`LICENSE`), the user instructions (`README.md` /
`README.ru.md`), an example environment script `set-env.example.cmd` and
`THIRD_PARTY_NOTICES.txt`.

### B. From source

```powershell
python -m venv .venv
.venv\Scripts\python -m pip install -e ".[test]"
.venv\Scripts\one-c-form-compiler doctor
```

`doctor` reports the Python and v8unpack versions and whether `runtime-check` and `ai` are
configured. If it reports v8unpack as missing, install it with
`.venv\Scripts\python -m pip install v8unpack==1.2.13`.

## Quick start

The EPF examples run as-is on the synthetic sample `tests/fixtures/sample_ordinary.epf` (data
processor `ПримерОбычнойФормы`, one ordinary form «Корректировка записей регистров» with 37
elements: `Надпись1`/`Номер`, `Надпись2`/`Дата`, the panel `ПанельРегистров` with pages, the table
field `ЗаполнениеДвижений` and others). The input is never modified; each command writes a new file.

```powershell
copy tests\fixtures\sample_ordinary.epf Sample.epf

# What is on the form (elements, coordinates, pages, style passport)
one-c-form-compiler inspect Sample.epf
one-c-form-compiler inspect Sample.epf --json

# Add a field with a caption label in a new row, in the form's own style
# (--width 120 leaves room for the button on its right)
one-c-form-compiler add Sample.epf -o Out1.epf --type Field --name Скидка --label "Скидка, %:" --value-type "Число(5,2)" --width 120

# A button to the right of it, with a click handler stub in the module
one-c-form-compiler add Out1.epf -o Out2.epf --type Кнопка --name Пересчитать --caption Пересчитать --beside Скидка --handler ПересчитатьНажатие

# Change, remove, retitle
one-c-form-compiler modify Sample.epf -o Out3.epf --name Надпись1 --caption "Номер док.:"
one-c-form-compiler remove Sample.epf -o Out4.epf --name ПодсказкаРегистрыСведений3
one-c-form-compiler title  Sample.epf -o Out5.epf "Корректировка записей (пример)"

# Columns of a table field
one-c-form-compiler add-column    Sample.epf -o Out6.epf --table ЗаполнениеДвижений --name Сумма --after Документ --width 80
one-c-form-compiler modify-column Sample.epf -o Out7.epf --table ЗаполнениеДвижений --name Примечание --caption Комментарий

# Configuration form in an EDT project (edited in place, backup in <project>\.one-c-form-backups)
one-c-form-compiler inspect --project C:\path\to\edt-project --form Catalog.Номенклатура.ФормаЭлемента --json
one-c-form-compiler apply   --project C:\path\to\edt-project --form Catalog.Номенклатура.ФормаЭлемента --plan plan.json --dry-run --json

# Table field bound to a tabular section, and its columns
one-c-form-compiler add --project C:\path\to\edt-project --form Document.Заказ.ФормаДокумента --type Table --name Товары --data Товары --column НомерСтроки --column Номенклатура
one-c-form-compiler add-column --project C:\path\to\edt-project --form Document.Заказ.ФормаДокумента --table Товары --name Цена --data Цена --after Номенклатура
```

Common options: `--form <name>` (a form inside an EPF/ERF with several forms, or the full form
name with `--project`), `--dry-run` (validate and report, write nothing), `-o/--output`,
`--overwrite`, `--json` (machine-readable output). Every command has `--help`.

### Workflow

1. `inspect <input> --json` — read the form.
2. Write a plan (`plan.json`). The full format is printed by `one-c-form-compiler guide`.
3. `apply <input> --plan plan.json --dry-run --json` — validate without writing.
4. `apply <input> --plan plan.json [-o <output>] --json` — write.
5. `runtime-check <output> --json` (EPF/ERF) or `runtime-check --project <project> --form <form> --json`.

A small plan for the sample (`apply Sample.epf --plan plan.json -o Out8.epf --json`): a field
with an event handler, a new page of `ПанельРегистров` with a free value-table field stretched
with the form, a command panel above it, and a new title:

```json
{"operations": [
  {"op": "add", "type": "Field", "name": "Скидка", "label": "Скидка, %:", "value_type": "Число(5,2)",
   "width": 120, "events": {"ПриИзменении": "СкидкаПриИзменении"}},
  {"op": "add_page", "container": "ПанельРегистров", "name": "Итоги", "caption": "Итоги"},
  {"op": "add", "type": "Table", "name": "ТаблицаИтогов", "columns": ["Показатель", "Значение"],
   "container": "ПанельРегистров", "page": "Итоги", "top": 36, "stretch": "both"},
  {"op": "add_command_panel", "name": "КоманднаяПанельИтоги", "table": "ТаблицаИтогов",
   "buttons": [{"name": "Заполнить", "procedure": "КоманднаяПанельИтогиЗаполнить"}]},
  {"op": "set_title", "title": "Корректировка записей (пример)"}
]}
```

Binding to data (`"data": "<attribute>"`, a table field on a tabular section) needs an object
with attributes — typically a configuration form; `guide` has an example.

Exit codes:

| Code | Meaning |
|---|---|
| `0` | ok (for `runtime-check`: `passed: true`) |
| `2` | usage error (`E_USAGE`): bad command line, e.g. `apply` on an EPF without `-o` or `--dry-run` |
| `3` | form or plan error (names, geometry, unsupported operation, …) |
| `4` | file, v8unpack, build or environment error, including a missing input or plan file (`E_INPUT_MISSING`), `E_RUNTIME_CONFIG` and `E_INTERNAL` |
| `5` | error of the built-in `ai` command |
| `6` | the runtime check did not pass: differences, `timeout` or an error while running the check |

Errors are printed as `Ошибка <code>: <message>` on stderr, or with `--json` as
`{"status": "error", "code": ..., "message": ...}` on stdout (also for command-line errors).
`one-c-form-compiler guide` lists every code and what to do about it.

### Configuration forms

* Address a form as `--project <EDT project> --form <Type>.<Object>.<Form>` (English or Russian
  type names: `Catalog`/`Справочник`, `Document`/`Документ`, `DataProcessor`/`Обработка`,
  `CommonForm`/`ОбщаяФорма`, …) or pass the path to `Form.oform`.
* `Form.oform` is edited **in place** with a backup:
  * inside an EDT project (a parent folder with `DT-INF` and `src`) the backup goes to
    `<project>/.one-c-form-backups/<form path>.<timestamp>` (outside `src/`, so EDT does not pick
    it up);
  * a `Form.oform` given directly with no EDT project root above it gets its backup next to the
    file: `Form.oform.bak-<YYYYMMDD-HHMMSS>`.

  The backup path is returned in `publish.backup`. Alternatively write to another file with
  `-o new.oform` — then the input is left unchanged and no backup is made.
* The write happens only if the rebuilt form decodes back to exactly the edited one and the file
  did not change during the edit (`E_INPUT_CHANGED`).
* After a write, refresh the project in EDT: F5 on the project, or AI-EDT
  `infobase_admin update_database` with `refreshWorkspace=true` (the same hint comes back in
  `publish.refresh_hint`). Do not edit a form that is open with unsaved changes in the EDT editor.
* Before `runtime-check --project …`, update the **test** infobase from the project (in EDT, or
  with AI-EDT `infobase_admin update_database` with `refreshWorkspace=true`).
* `runtime-check --timeout <seconds>` sets how long to wait for the report from 1C (default 180).

## Environment variables

The tool reads **only real environment variables**; it does not load `.env` files.
`.env.example` (source tree) and `set-env.example.cmd` (release) list the variables for reference.

| Variable | Used by | Meaning |
|---|---|---|
| `ONE_C_FORM_1CV8` | `runtime-check` | Full path to `1cv8.exe` (thick client) |
| `ONE_C_FORM_INFOBASE` | `runtime-check` | Directory of the **test file infobase** (passed as `/F`) |
| `ONE_C_FORM_INFOBASE_USER` | `runtime-check` | Infobase user name (optional) |
| `ONE_C_FORM_INFOBASE_PASSWORD_ENV` | `runtime-check` | **Name** of another variable that holds the password (optional) |
| `ONE_C_FORM_V8UNPACK` | all commands | Optional path to an external v8unpack executable instead of the bundled/installed module |
| `ONE_C_FORM_AI_PROVIDER` | `ai` | `anthropic` (alias `claude`) or `openai` |
| `ONE_C_FORM_AI_MODEL` | `ai` | Model id; default for `anthropic` is `claude-opus-5-5`, required for `openai` |
| `ONE_C_FORM_AI_API_KEY_ENV` | `ai` | **Name** of the variable that holds the API key; default `ANTHROPIC_API_KEY` / `OPENAI_API_KEY` (for `anthropic` an empty key lets the SDK use its own credentials) |
| `ONE_C_FORM_AI_TIMEOUT_SECONDS` | `ai` | Request timeout in seconds, greater than 0 and at most 300. Default 30; while `ONE_C_FORM_AI_MAX_OUTPUT_TOKENS` is not set, the `ai` command raises the timeout to at least 120 (together with the 16000-token output limit) |
| `ONE_C_FORM_AI_MAX_INPUT_BYTES` | `ai` | Limit for the form description sent to the model (256000) |
| `ONE_C_FORM_AI_MAX_OUTPUT_BYTES` | `ai` | Limit for the model response (64000) |
| `ONE_C_FORM_AI_MAX_OUTPUT_TOKENS` | `ai` | Output token limit (16000 when not set; an explicit value is used as given, and the timeout is then not raised) |

The infobase password is passed to `1cv8.exe` on its command line (`/P`), where other processes
of the same machine can see it. Use a dedicated test user with a throw-away password, and turn
off "Защита от опасных действий" for that user, otherwise every run shows a warning window.

## Safety guarantees

* The input `.epf`/`.erf` is never opened for writing; the result goes to a new file and is
  published atomically. Its SHA-256 is re-checked before publishing.
* A result is published only when the rebuilt container decodes to exactly the edited form and
  nothing outside the edited form changed (`E_VERIFY_*` otherwise).
* `Form.oform` is written in place only after the same round-trip check, with a backup, and only
  if the file did not change during the edit (`E_INPUT_CHANGED`).
* Plans are all-or-nothing. After each operation the form's integrity check (ids, anchors, tab
  and z order, data sources, default button) is compared with the original state; an operation
  that would add an inconsistency is rejected (`E_INTEGRITY`).
* Removing an element or column that the form module references is refused without `force`.
* `runtime-check` works on a disposable copy. The probe only reads the form: it saves and posts
  nothing, and for configuration forms it does not even open the form. The tool stops only the
  `1cv8` process it started and never updates an infobase configuration.
* All subprocesses run without a shell, with time and output limits.

## Limitations

* Ordinary forms only, profile `FormElements27`; managed forms are not supported.
* Cannot add pictures, radio buttons or select fields; a command panel can be added only
  above a table field; existing command panels get buttons and separators, not submenus or button
  pictures. Read-only is set for input fields only.
* A new page goes to the end of a panel; a panel's last page cannot be removed (remove the panel).
* Column visibility is not changed; nested panels do not grow automatically.
* Only the documented events are supported (see `guide`).
* A small share of configuration forms (mostly regulated reports) cannot be rebuilt byte-exactly;
  the tool refuses to edit them (`E_OFORM_ROUNDTRIP`).
* `runtime-check` needs a file infobase and the thick client; a modal window in 1C (module error,
  locked infobase, security warning) ends the check with `timeout`.

## Use with AI agents

Point your agent at the tool's own guide: tell it to run `one-c-form-compiler guide` (or
`one-c-form-compiler.exe guide` with the release build) before editing a form and to follow it.
The guide is the single source of rules for agents: workflow, full plan format, error codes and
what to do about them, limits. No API keys are needed — the agent writes plans itself and checks
them with `--dry-run` and `runtime-check`.

## How editing works

v8unpack decodes the form container into JSON (form header, root panel, data sources and
per-element records). `core/ordinary_form.py` edits these records directly and keeps everything
the platform cross-references consistent: the element id counter, edge bindings and their reverse
lists, the panel anchor index, dense tab and z order per page, data sources, data paths in the
object's `copyinfo`, the default button and the form size. New elements clone the most typical
look of the same type on the form; built-in neutral templates (`core/ordinary_templates.json`)
cover forms that have no element of the needed type.

Format notes: [table fields](docs/table-format.md), [command panels](docs/command-panel-format.md),
[panels](docs/panel-format.md), [input fields](docs/field-format.md).
Design decisions: [ADR-003](docs/adr/ADR-003-json-model-edit-plans.md),
[ADR-004](docs/adr/ADR-004-config-ordinary-forms.md).

## Building the release

```powershell
powershell -ExecutionPolicy Bypass -File packaging\build_release.ps1 [-Python <python 3.14>] [-OutDir <folder>]
```

The script creates a build environment `.venv-build`, installs the package and PyInstaller, and
builds a one-folder distribution from `packaging/one-c-form-compiler.spec` (executable plus
runtime, no `.py` sources; the Anthropic SDK is not bundled). The output folder (default `dist`)
gets the program folder, `LICENSE`, the end-user files from `packaging/release/`
(`README.md`, `README.ru.md`, `THIRD_PARTY_NOTICES.txt`, `set-env.example.cmd`), the archive
`one-c-form-compiler-<version>-win-x64.zip` and `SHA256SUMS.txt`.

## Running tests

```powershell
.venv\Scripts\python -m unittest discover -s tests -t . -p "test_*.py"
```

Tests that need a real ordinary form use the synthetic sample `tests/fixtures/sample_ordinary.epf`
and are skipped when it is absent; tests that need v8unpack are skipped without it.

## Related tools

* **[v8unpack](https://github.com/saby-integration/v8unpack)** (MIT) — unpacks and packs `.epf` /
  `.erf` and form containers; required at runtime when you install from source (`v8unpack==1.2.13`).
* **Windows release builds** of this project — [GitHub Releases](https://github.com/dmg4ebay-sys/1c-ai-form-compiler/releases)
  (`one-c-form-compiler-*-win-x64.zip` plus `SHA256SUMS.txt`).

## Author

Developed and maintained by **mdg_cloder** ([GitHub @dmg4ebay-sys](https://github.com/dmg4ebay-sys)).

## License

MIT — see `LICENSE`. Copyright (c) 2026 mdg_cloder.

The release build bundles third-party components (CPython, v8unpack, tqdm, colorama, the
PyInstaller bootloader); their licenses are in `packaging/release/THIRD_PARTY_NOTICES.txt`, which
ships with the release as `THIRD_PARTY_NOTICES.txt`. 1C:Enterprise is a trademark of its owner;
this project is not affiliated with it.
