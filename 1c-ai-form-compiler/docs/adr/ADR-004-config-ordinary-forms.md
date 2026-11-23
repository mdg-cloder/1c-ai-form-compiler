# ADR-004: editing ordinary forms of a configuration directly in an EDT project

Status: accepted.

## Context

Most changes to ordinary forms concern forms of the configuration itself (for example, the item
form of a catalog), not external data processors. EDT-oriented tools such as AI-EDT edit managed
forms, but not ordinary ones.

EDT stores an ordinary form as `Forms/<Form>/Form.oform`: an uncompressed 1C container with two
records, `form` (the same FormElements27 bracket text as inside an EPF) and `module`.

## Decision

1. The tool reads and writes `Form.oform` in an EDT project with the same operations and checks
   as for EPF/ERF (`core/oform.py`, `core/oform_session.py`).
2. Object attributes, tabular sections and standard attributes come from the object's `.mdo`
   (`core/edt_metadata.py`).
3. The form is written in place, and only when the rebuilt container decodes back to exactly the
   edited form and the file has not changed during the edit. The backup goes outside `src/`
   (`<project>/.one-c-form-backups/`), so EDT does not treat it as a source; a `Form.oform` with no
   EDT project root above it gets its backup next to the file (`Form.oform.bak-<timestamp>`).
   `-o <file>.oform` writes the result elsewhere and leaves the input unchanged.
4. After a write EDT has to re-read the project: F5 on the project, or an equivalent refresh
   (for example AI-EDT `infobase_admin update_database` with `refreshWorkspace=true`).
5. Runtime check of a configuration form: the TEST infobase is updated from the project first;
   the probe is a separate form-less external data processor (`core/probe_epf`) that gets the form
   via `<Manager>.ПолучитьФорму` (the form is not opened and its handlers do not run), dumps its
   elements and columns and exits.

## Consequences

* An AI agent edits configuration forms with the same edit plan as EPF/ERF forms.
* Table fields and columns are supported the same way for both sources (`core/table_field.py`).
* The tool never updates an infobase itself; updating the test infobase is a separate explicit step.
