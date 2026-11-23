# one-c-form-compiler — user guide

`one-c-form-compiler.exe` edits **ordinary forms of 1C:Enterprise 8.3** (thick client):

* in external data processors and reports (`.epf` / `.erf`) — the result goes to a new file;
* in configuration forms of an EDT project (`Form.oform`) — in place, with a backup in
  `<project>\.one-c-form-backups\` (or next to the file, see below), or into another file with
  `-o`.

Managed forms are not supported. Russian version: `README.ru.md`.

## Requirements

* Windows x64. No Python installation is needed — everything is inside the program folder.
* 1C:Enterprise 8.3 thick client (`1cv8.exe`) and a **test file infobase** — only for checking a
  form in 1C (`runtime-check`). Editing forms does not need 1C.

## Install

1. Unzip `one-c-form-compiler-<version>-win-x64.zip` to any folder, for example `C:\Tools`.
   The archive has one top-level folder, so you get:

   ```
   C:\Tools\one-c-form-compiler-<version>-win-x64\
     LICENSE                  license of the program (MIT)
     README.md, README.ru.md  this guide
     THIRD_PARTY_NOTICES.txt  licenses of the bundled components
     set-env.example.cmd      example environment script
     one-c-form-compiler\
       one-c-form-compiler.exe
       _internal\             runtime of the program
   ```

2. The program is `C:\Tools\one-c-form-compiler-<version>-win-x64\one-c-form-compiler\one-c-form-compiler.exe`
   (keep the `_internal` folder next to it).
3. Optionally add the folder
   `C:\Tools\one-c-form-compiler-<version>-win-x64\one-c-form-compiler` to `PATH`, or call the
   program by its full path.

**Antivirus and SmartScreen.** The executable is not code-signed, so Windows SmartScreen or an
antivirus may warn about an unknown publisher. Compare the archive checksum with
`SHA256SUMS.txt` from the release (`Get-FileHash <archive> -Algorithm SHA256` in PowerShell) and,
if needed, allow the program or add its folder to the exclusions.

## First run

Open `cmd` or PowerShell and run:

```
one-c-form-compiler.exe --version
one-c-form-compiler.exe doctor
one-c-form-compiler.exe guide
```

* `doctor` checks the environment: the v8unpack version, whether 1C and the test infobase are set.
* `guide` prints the full guide: workflow, JSON plan format, error codes, limits.
* Every command has `--help`.

## Typical commands

The EPF examples below run as-is on the sample external data processor `sample_ordinary.epf`
from the project's source repository (`tests/fixtures/sample_ordinary.epf`; it is not part of
this archive). Save it as `Sample.epf`. Its form «Корректировка записей регистров» has, among
others, the label `Надпись1` and the table field `ЗаполнениеДвижений`. The input file is never
changed; each command writes a new file.

```
rem What is on the form
one-c-form-compiler.exe inspect Sample.epf
one-c-form-compiler.exe inspect Sample.epf --json

rem Single edits, each into a new file
one-c-form-compiler.exe modify Sample.epf -o Out1.epf --name Надпись1 --caption "Номер док.:"
one-c-form-compiler.exe add-column Sample.epf -o Out2.epf --table ЗаполнениеДвижений --name Сумма --after Документ --width 80

rem Validate a plan without writing, then write a new file
one-c-form-compiler.exe apply Sample.epf --plan plan.json --dry-run --json
one-c-form-compiler.exe apply Sample.epf --plan plan.json -o Sample_new.epf --json

rem Open the result in the test infobase and compare the form (default wait 180 s)
one-c-form-compiler.exe runtime-check Sample_new.epf --json
one-c-form-compiler.exe runtime-check Sample_new.epf --timeout 300 --json

rem Configuration form in an EDT project (edited in place)
one-c-form-compiler.exe inspect --project C:\path\to\edt-project --form Catalog.Номенклатура.ФормаЭлемента --json
one-c-form-compiler.exe apply   --project C:\path\to\edt-project --form Catalog.Номенклатура.ФормаЭлемента --plan plan.json --json
one-c-form-compiler.exe runtime-check --project C:\path\to\edt-project --form Catalog.Номенклатура.ФормаЭлемента --json
```

Example `plan.json` for the sample:

```json
{"operations": [
  {"op": "add", "type": "Field", "name": "Скидка", "label": "Скидка, %:", "value_type": "Число(5,2)",
   "width": 120},
  {"op": "add", "type": "Button", "name": "Пересчитать", "caption": "Пересчитать", "beside": "Скидка",
   "handler": "ПересчитатьНажатие"},
  {"op": "modify", "name": "Надпись1", "caption": "Номер док.:"},
  {"op": "set_title", "title": "Корректировка записей (пример)"}
]}
```

(`"width": 120` keeps the field narrow enough for the button on its right.)

### Configuration forms

* `Form.oform` is edited in place with a backup. Inside an EDT project the backup goes to
  `<project>\.one-c-form-backups\`; a `Form.oform` given directly, with no EDT project folder
  above it, gets its backup next to the file as `Form.oform.bak-<YYYYMMDD-HHMMSS>`. With
  `-o new.oform` the result goes to another file and the input is not changed.
* After editing, refresh the project in EDT: F5 on the project, or AI-EDT
  `infobase_admin update_database` with `refreshWorkspace=true`.
* Before `runtime-check` of a configuration form, update the **test** infobase from the project
  (in EDT, or AI-EDT `infobase_admin update_database` with `refreshWorkspace=true`).

### Exit codes

* `0` — ok (for `runtime-check`: the form passed the check);
* `2` — usage error (`E_USAGE`), for example `apply` on an EPF without `-o` or `--dry-run`;
* `3` — form or plan error;
* `4` — file, v8unpack or environment error, including a missing input or plan file
  (`E_INPUT_MISSING`), runtime-check settings (`E_RUNTIME_CONFIG`) and unexpected errors
  (`E_INTERNAL`);
* `5` — error of the `ai` command;
* `6` — the runtime check did not pass: differences, `timeout` or an error while running it.

Errors are printed as `Ошибка <code>: <message>`; with `--json` as a JSON object
`{"status": "error", "code": ..., "message": ...}`. `guide` explains every code.

## Environment variables

Needed only for `runtime-check` and the optional `ai` command. The program reads only
environment variables (it does not load `.env` files).

1. Copy `set-env.example.cmd` to `set-env.cmd` and fill in your values.
2. In a `cmd` window run `call set-env.cmd`, then start the program (or your AI agent) from the
   same window.

Main variables: `ONE_C_FORM_1CV8` — path to `1cv8.exe`; `ONE_C_FORM_INFOBASE` — directory of the
test file infobase; `ONE_C_FORM_INFOBASE_USER` and `ONE_C_FORM_INFOBASE_PASSWORD_ENV` — user and
the **name** of the variable that holds the password. The password is passed to `1cv8.exe` on its
command line, so use a dedicated test user, and turn off "Защита от опасных действий" for it.
Never point the tool at a production infobase.

## Using it from an AI agent (Claude Code, Codex and others)

No API keys are needed: the agent writes the plan itself by following the program's guide.

1. Make sure the agent can run `one-c-form-compiler.exe` (folder in `PATH` or a full path).
2. Tell the agent, for example:

   > To edit ordinary 1C forms use
   > `C:\Tools\one-c-form-compiler-<version>-win-x64\one-c-form-compiler\one-c-form-compiler.exe`.
   > First run `one-c-form-compiler.exe guide` and follow it:
   > inspect → plan → apply --dry-run → apply → runtime-check.

3. To make it permanent, put these lines into the agent's instruction file (for example
   `CLAUDE.md` or `AGENTS.md` in your project).

## The `ai` command

The built-in `ai` command (describe a change in words → plan) is optional. This build does not
bundle the Anthropic SDK, so the `anthropic` provider (alias `claude`) is unavailable here — it
works only when the tool is installed from source (`pip install ".[claude]"`). The `openai`
provider (OpenAI Responses API) works: set `ONE_C_FORM_AI_PROVIDER=openai`,
`ONE_C_FORM_AI_MODEL` (required for `openai`) and the key in `OPENAI_API_KEY` (see
`set-env.example.cmd`).

Request timeout `ONE_C_FORM_AI_TIMEOUT_SECONDS`: more than 0 and at most 300 seconds, default 30.
While `ONE_C_FORM_AI_MAX_OUTPUT_TOKENS` is not set, the `ai` command raises the timeout to at least
120 seconds (and the output limit to 16000 tokens); if you set the token limit yourself, set the
timeout as well.

## License

The program is released under the MIT license (`LICENSE`). Licenses of the bundled components
(CPython and the libraries shipped with it, v8unpack, tqdm, colorama, the PyInstaller bootloader)
are in `THIRD_PARTY_NOTICES.txt`.
