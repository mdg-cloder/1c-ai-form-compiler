# ADR-003: v8unpack JSON model and edit plans instead of AI-generated bracket text

Status: accepted.

## Context

A naive approach to AI-assisted editing of ordinary forms is:

1. The AI generates the bracket (`{...}`) text of an ordinary form.
2. The AI copies existing elements.
3. The AI "increments the counters".
4. A parser checks that the brackets are balanced.

Balanced brackets do not guarantee a consistent form. The platform cross-references:

* the element id counter;
* edge bindings and the reverse lists on the target elements;
* the panel anchor index;
* dense tab order and z-order;
* the form's data sources;
* the data paths in the object's `copyinfo`.

A model that writes bracket text does not keep these links, and the platform rejects such forms
or shows them broken.

## Decision

1. Forms are edited by deterministic operations on the JSON that v8unpack 1.2.13 decodes
   (`core/ordinary_form.py`). After every operation the integrity check is compared with the
   form's original state.
2. The public interface is a JSON edit plan (`core/edit_plan.py`), single-operation CLI commands
   and `inspect --json`.
3. AI agents (Claude Code, Codex and others) call the tool as a regular CLI and learn it from its
   own documentation: `one-c-form-compiler guide`. The built-in `ai` command that calls a model
   with an API key is optional.
4. The result is verified in the platform (`--runtime-check`): a probe in a disposable copy of
   the form compares elements, geometry, captions, `Данные` and attached events with the file.

## Consequences

* No AI self-repair loop over bracket text is needed. Plan errors have stable codes and the
  operation number, so an agent fixes the plan and retries.
* Only verified element types, events and the FormElements27 profile are supported; everything
  else is rejected with an explicit error code.
