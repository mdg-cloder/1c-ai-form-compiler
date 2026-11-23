"""Command line interface: edit 1C ordinary forms inside EPF/ERF containers and EDT Form.oform files.

Exit codes: 0 ok · 2 usage · 3 form/plan error · 4 container/backend error ·
5 AI error · 6 runtime check failed.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any

from . import __version__
from .core.ai import AIError
from .core.edit_plan import apply_plan, describe_form
from .core.ordinary_backend import FROZEN_V8UNPACK_FLAG, Backend, BackendError, EditSession, is_frozen
from .core.edt_metadata import EdtMetadataError
from .core.ordinary_form import FormModelError

EXIT_OK, EXIT_USAGE, EXIT_FORM, EXIT_BACKEND, EXIT_AI, EXIT_RUNTIME = 0, 2, 3, 4, 5, 6
PROJECT_COMMANDS = {"inspect", "add", "modify", "remove", "title", "size", "apply", "ai", "runtime-check",
                    "add-column", "modify-column", "remove-column"}
PROJECT_PLACEHOLDER = "@project"


class UsageError(Exception):
    """Command line could not be parsed (argparse error); reported as E_USAGE, exit code 2."""

    def __init__(self, parser: argparse.ArgumentParser, message: str) -> None:
        super().__init__(message)
        self.parser = parser


class _Parser(argparse.ArgumentParser):
    """ArgumentParser that raises instead of exiting, so ``main`` can honour ``--json`` for usage errors."""

    def error(self, message: str):  # type: ignore[override]
        raise UsageError(self, message)


def _emit(args: argparse.Namespace, payload: dict[str, Any], human: str) -> None:
    if getattr(args, "json", False):
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    else:
        print(human)


def _geometry_args(parser: argparse.ArgumentParser) -> None:
    for name in ("left", "top", "width", "height"):
        parser.add_argument(f"--{name}", type=int)


def _common(parser: argparse.ArgumentParser, *, output: bool) -> None:
    parser.add_argument("input", type=Path,
                        help="source .epf/.erf (never modified) or a configuration form Form.oform (edited in place); "
                             "omit it when --project is given")
    parser.add_argument("--project", type=Path, help="EDT project directory; the form is chosen with --form")
    parser.add_argument("--form", help="form name: in an EPF/ERF when it has several; with --project the full "
                                       "name, e.g. Catalog.Номенклатура.ФормаЭлемента")
    parser.add_argument("--json", action="store_true", help="machine-readable output")
    if output:
        parser.add_argument("-o", "--output", type=Path,
                            help="result file (must differ from input); optional for Form.oform (in place + backup)")
        parser.add_argument("--overwrite", action="store_true", help="replace an existing output file")
        parser.add_argument("--dry-run", action="store_true", help="validate and show the result, write nothing")
        parser.add_argument("--runtime-check", action="store_true",
                            help="open the result in 1C (ONE_C_FORM_1CV8 / ONE_C_FORM_INFOBASE) and compare")


def build_parser() -> argparse.ArgumentParser:
    parser = _Parser(
        prog="one-c-form-compiler",
        description="Edit 1C ordinary forms (platform 8.3, FormElements27) inside EPF/ERF files "
                    "and in configuration forms of an EDT project (Form.oform).")
    parser.add_argument("--version", action="version", version=__version__)
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("inspect", help="list forms, elements and style of a container")
    _common(p, output=False)

    p = sub.add_parser("add", help="add an element")
    _common(p, output=True)
    p.add_argument("--type", required=True,
                   help="Label|Field|Button|CheckBox|Group|Table|Panel (or Russian names)")
    p.add_argument("--name", required=True)
    p.add_argument("--caption")
    p.add_argument("--label", help="Field/Button: also add a caption label in the same row")
    p.add_argument("--tooltip")
    p.add_argument("--value-type", help="Строка(50) | Число(15,2) | Дата | ДатаВремя | Булево | like:<Field>")
    p.add_argument("--handler", help="procedure for the main event (Нажатие / ПриИзменении); stub is added")
    p.add_argument("--event", action="append", metavar="СОБЫТИЕ=ПРОЦЕДУРА", help="extra event handler (repeatable)")
    p.add_argument("--data", help="bind Field/CheckBox to an object attribute; Table to a tabular section "
                                  "or a form attribute")
    p.add_argument("--column", action="append", metavar="ИМЯ",
                   help="Table: column (repeatable); default: НомерСтроки and every attribute of the section")
    p.add_argument("--container", help="panel name (default: the form)")
    p.add_argument("--page", action="append",
                   help="panel page to place the element on; with --type Panel: a page of the new panel "
                        "(repeatable, caption = name)")
    p.add_argument("--stretch", choices=("width", "height", "both"), help="Table/Group/Panel: grow with the form")
    p.add_argument("--beside", help="place to the right of this element (with --label: the label and the "
                                    "field go to the right of it; with --layout-column: only the row is taken "
                                    "from it)")
    p.add_argument("--layout-column", type=int, choices=(1, 2),
                   help="two-column layout: 1 - the left half, 2 - the right half of the page "
                        "(plan field column)")
    p.add_argument("--read-only", action=argparse.BooleanOptionalAction, default=None,
                   help="Field/Table: ТолькоПросмотр (a new element is editable by default)")
    _geometry_args(p)

    p = sub.add_parser("modify", help="change caption/tooltip/geometry/type/handler of an element")
    _common(p, output=True)
    p.add_argument("--name", required=True)
    p.add_argument("--caption")
    p.add_argument("--tooltip")
    p.add_argument("--value-type")
    p.add_argument("--handler")
    p.add_argument("--event", action="append", metavar="СОБЫТИЕ=ПРОЦЕДУРА")
    p.add_argument("--data")
    p.add_argument("--read-only", action=argparse.BooleanOptionalAction, default=None,
                   help="Field/Table: set (--read-only) or clear (--no-read-only) ТолькоПросмотр")
    _geometry_args(p)

    p = sub.add_parser("remove", help="remove an element")
    _common(p, output=True)
    p.add_argument("--name", required=True)
    p.add_argument("--force", action="store_true", help="remove even if the module references the element")

    for command, text in (("add-column", "add a column to a table field"),
                          ("modify-column", "change caption/width/data/events of a table column"),
                          ("remove-column", "remove a table column")):
        p = sub.add_parser(command, help=text)
        _common(p, output=True)
        p.add_argument("--table", required=True, help="table field name")
        p.add_argument("--name", required=True, help="column name")
        if command == "remove-column":
            p.add_argument("--force", action="store_true", help="remove even if the module references the column")
            continue
        p.add_argument("--data", help="attribute of the table's tabular section (or НомерСтроки)")
        p.add_argument("--caption")
        p.add_argument("--width", type=int)
        p.add_argument("--event", action="append", metavar="СОБЫТИЕ=ПРОЦЕДУРА", help="cell event (repeatable)")
        if command == "add-column":
            p.add_argument("--after", help="insert after this column (default: last)")

    p = sub.add_parser("title", help="set the form title")
    _common(p, output=True)
    p.add_argument("text")

    p = sub.add_parser("size", help="set the form size; elements anchored to the right/bottom follow "
                                    "(plan operation set_form_size)")
    _common(p, output=True)
    p.add_argument("--width", type=int)
    p.add_argument("--height", type=int)

    p = sub.add_parser("apply", help="apply a JSON edit plan")
    _common(p, output=True)
    p.add_argument("--plan", type=Path, required=True)

    p = sub.add_parser("ai", help="describe the change in words; the AI builds a plan, it is validated and applied")
    _common(p, output=True)
    p.add_argument("request", help="what to change, in natural language")
    p.add_argument("--recorded", type=Path, help="offline: JSON file with prepared model responses")
    p.add_argument("--show-plan", action="store_true", help="print the generated plan")

    p = sub.add_parser("runtime-check", help="open a form in 1C and compare the live form with the file "
                                             "(EPF/ERF, or --project/--form for a configuration form)")
    p.add_argument("input", type=Path, help="EPF/ERF; omit it with --project")
    p.add_argument("--project", type=Path, help="EDT project; the test infobase must be updated from it first")
    p.add_argument("--form")
    p.add_argument("--json", action="store_true")
    p.add_argument("--timeout", type=float, default=180.0)

    sub.add_parser("doctor", help="check the environment").add_argument("--json", action="store_true")
    sub.add_parser("guide", help="print the guide for AI agents and scripts (workflow, plan format, errors)")
    for command in sub.choices.values():
        command.allow_abbrev = False          # "--proj" would bypass the --project placeholder
    return parser


def _operation_from_args(args: argparse.Namespace) -> dict[str, Any]:
    if args.command == "title":
        return {"op": "set_title", "title": args.text}
    if args.command == "size":
        if args.width is None and args.height is None:
            raise FormModelError("E_PLAN_FIELD", "size needs --width and/or --height")
        return {"op": "set_form_size", **{name: getattr(args, name) for name in ("width", "height")
                                          if getattr(args, name) is not None}}
    if args.command == "add" and _is_panel_type(args.type):
        extra = [name for name in ("caption", "label", "tooltip", "value_type", "handler", "beside", "data", "read_only",
                                   "layout_column")
                 if getattr(args, name, None) is not None] + (["event"] if args.event else []) + (
                     ["column"] if args.column else [])
        if extra:
            raise FormModelError("E_PLAN_FIELD", f"--type Panel does not take {', '.join('--' + e.replace('_', '-') for e in extra)}")
        op = {"op": "add_panel", "name": args.name, "pages": list(args.page or [])}
        for name in ("container", "left", "top", "width", "height", "stretch"):
            if getattr(args, name, None) is not None:
                op[name] = getattr(args, name)
        return op
    if args.command == "add" and args.page and len(args.page) > 1:
        raise BackendError("E_USAGE", "--page is given once (the panel page to place the element on)")
    op: dict[str, Any] = {"op": args.command.replace("-", "_")}
    fields = {"add": ("type", "name", "caption", "label", "tooltip", "value_type", "handler", "container",
                      "page", "beside", "left", "top", "width", "height", "data", "stretch"),
              "modify": ("name", "caption", "tooltip", "value_type", "handler", "left", "top", "width", "height",
                         "data"),
              "remove": ("name", "force"),
              "add-column": ("table", "name", "data", "caption", "width", "after"),
              "modify-column": ("table", "name", "data", "caption", "width"),
              "remove-column": ("table", "name", "force")}[args.command]
    for name in fields:
        value = getattr(args, name, None)
        if name == "page" and isinstance(value, list):
            value = value[0]
        if value is not None and value is not False:
            op[name] = value
    if getattr(args, "read_only", None) is not None:
        op["read_only"] = args.read_only
    if getattr(args, "layout_column", None) is not None:
        op["column"] = args.layout_column
    if getattr(args, "column", None):
        op["columns"] = list(args.column)
    for item in getattr(args, "event", None) or []:
        event, _, procedure = item.partition("=")
        if not procedure:
            raise FormModelError("E_PLAN_FIELD", f"--event expects СОБЫТИЕ=ПРОЦЕДУРА, got {item!r}")
        op.setdefault("events", {})[event.strip()] = procedure.strip()
    return op


def _is_panel_type(value: str | None) -> bool:
    return (value or "").strip().casefold() in {"panel", "панель"}


def _open_session(args: argparse.Namespace, backend: Backend):
    """EditSession for EPF/ERF, OformSession for a configuration form; sets args.oform."""
    from .core.edt_metadata import resolve_form
    from .core.oform_session import OformSession
    if args.input is not None and str(args.input) == PROJECT_PLACEHOLDER:
        args.input = None
    if args.project is not None:
        if args.input is not None:
            raise BackendError("E_USAGE", "give either an input file or --project with --form, not both")
        if not args.form:
            raise BackendError("E_USAGE", "--project needs --form <Тип>.<Объект>.<Форма>")
        args.input = resolve_form(args.project, args.form)
    if args.input is None:
        raise BackendError("E_USAGE", "input file (or --project with --form) is required")
    args.oform = Path(args.input).suffix.lower() == ".oform"
    if args.oform:
        return OformSession(args.input), None
    return EditSession(args.input, backend), args.form


def _edit(args: argparse.Namespace, plan_source) -> int:
    backend = Backend.discover()
    session, form_name = _open_session(args, backend)
    if not args.dry_run and args.output is None and not args.oform:
        raise BackendError("E_USAGE", "--output is required (or use --dry-run)")
    with session:
        form = session.form(form_name)
        plan_info: dict[str, Any] = {}
        if callable(plan_source):
            plan_source, plan_info = plan_source(form)
        results = apply_plan(form, plan_source)
        payload: dict[str, Any] = {"status": "ok", "form": form.form_dir.name, "changes": results, **plan_info}
        if form.warnings:
            payload["warnings"] = list(form.warnings)
        if getattr(form, "repairs", None):
            payload["repairs"] = list(form.repairs)     # id bookkeeping repaired on load, written with the edit
        if args.dry_run:
            payload["status"] = "dry-run"
        else:
            payload["publish"] = session.build_and_publish(args.output, overwrite=args.overwrite)
    lines = [f"{'Проверено (без записи)' if args.dry_run else 'Готово'}: форма {payload['form']}"]
    if plan_info.get("plan") and getattr(args, "show_plan", False):
        lines.append(json.dumps(plan_info["plan"], ensure_ascii=False, indent=1))
    if plan_info.get("ai_repairs"):
        lines.append(f"  ИИ: попыток {plan_info['ai_attempts']}, исправлено ошибок: {len(plan_info['ai_repairs'])}")
    for change in results:
        geometry = f" [{change['left']},{change['top']} {change['width']}x{change['height']}]" if "left" in change else ""
        kind = f" ({change['type_ru']})" if "type_ru" in change else ""
        owner = f"{change['table']}." if change.get("table") else ""
        subject = change.get("name") or change.get("title")
        if change["op"] == "set_form_size":
            subject = f"{change['width']}x{change['height']}"
        lines.append(f"  {change['op']}: {owner}{subject}{kind}{geometry}")
        if change.get("columns"):
            lines.append("    колонки: " + ", ".join(c["name"] for c in change["columns"]))
    if "publish" in payload:
        lines.append(f"  → {payload['publish']['output']}")
        if payload["publish"].get("backup"):
            lines.append(f"  резервная копия: {payload['publish']['backup']}")
        if payload["publish"].get("refresh_hint"):
            lines.append(f"  ! {payload['publish']['refresh_hint']}")
    for repair in payload.get("repairs", []):
        lines.append(f"  ! исправлено при загрузке: {repair}")
    for warning in payload.get("warnings", []):
        lines.append(f"  ! {warning}")
    exit_code = EXIT_OK
    if getattr(args, "runtime_check", False) and not args.dry_run and args.oform:
        lines.append("  1С: для формы конфигурации обновите тестовую базу из проекта и запустите "
                     "runtime-check --project <проект> --form <форма>")
    elif getattr(args, "runtime_check", False) and not args.dry_run:
        from .core.runtime_check import RuntimeConfig, run_runtime_check
        check = run_runtime_check(args.output, RuntimeConfig.from_env(), backend, form_name=args.form)
        payload["runtime_check"] = check
        lines.append(_runtime_line(check))
        if not check.get("passed"):
            exit_code = EXIT_RUNTIME
    _emit(args, payload, "\n".join(lines))
    return exit_code


def _runtime_line(check: dict[str, Any]) -> str:
    if check.get("passed"):
        columns = f", колонок: {check['checked_columns']}" if check.get("checked_columns") else ""
        return (f"  1С: форма открыта, {check['checked_elements']} элементов совпали{columns} "
                f"({check['elapsed_seconds']} с)")
    if check["status"] == "ok":
        return "  1С: расхождения: " + "; ".join(check["problems"][:10])
    if check["status"] == "timeout":
        return ("  1С: отчёт не получен за отведённое время (вероятно, модальное окно: ошибка модуля, "
                "блокировка базы или диалог запуска)")
    return f"  1С: {check['status']} {check.get('error', '')}".rstrip()


def _ai_plan_source(args: argparse.Namespace):
    from .core import ai_plan
    from .core.ai import AIConfig

    def build(form):
        if args.recorded:
            adapter = ai_plan.load_recorded(args.recorded)
            config = AIConfig(provider="recorded", model="recorded", max_output_tokens=16_000)
        else:
            config = ai_plan.config_from_env()
            adapter = ai_plan.adapter_for(config)
        result = ai_plan.generate_plan(form, args.request, config=config, adapter=adapter)
        info = {"plan": {"operations": result.operations}, "ai_attempts": len(result.attempts),
                "ai_repairs": [a.error for a in result.attempts if a.error]}
        return {"operations": result.operations}, info

    return build


def _inspect(args: argparse.Namespace) -> int:
    session, form_name = _open_session(args, Backend.discover())
    with session:
        refs = session.form_refs()
        form = session.form(form_name)
        description = describe_form(form)
        payload = {"forms": [ref.relative for ref in refs], "form": form.form_dir.name, **description,
                   "known_issues": sorted(form.integrity_issues())}
    if args.json:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return EXIT_OK
    title = description["title"].get("ru") or next(iter(description["title"].values()), "")
    print(f"Форма {payload['form']} «{title}» {description['size']['width']}x{description['size']['height']}, "
          f"элементов: {len(description['elements'])}")
    for element in description["elements"]:
        where = "" if element["container"] == "<form>" else f" @{element['container']}/{element['page']}"
        caption = element.get("caption", {}).get("ru", "")
        data = f"  ← {element['data']}" if element.get("data") else ""
        print(f"  {element['name']:<40} {element['type_ru']:<15} {element['left']:>4},{element['top']:<4} "
              f"{element['width']}x{element['height']}{where}{'  «' + caption + '»' if caption else ''}{data}")
        for column in element.get("columns") or []:
            column_data = f"  ← {column['data']}" if column.get("data") else ""
            print(f"      ├ {column['name']:<34} {column['width']:>4}  «{column['caption'].get('ru', '')}»{column_data}")
    if description["object_attributes"]:
        print("Реквизиты объекта: " + ", ".join(f"{a['name']} ({a['type'] or '?'})"
                                                for a in description["object_attributes"]))
    for name, attributes in (description.get("tabular_sections") or {}).items():
        print(f"Табличная часть {name}: " + ", ".join(attributes))
    return EXIT_OK


def _doctor(args: argparse.Namespace) -> int:
    checks: dict[str, Any] = {"python": sys.version.split()[0], "build": "frozen" if is_frozen() else "source"}
    try:
        backend = Backend.discover()
        checks["v8unpack"] = backend.version
    except BackendError as error:
        backend = None
        checks["v8unpack"] = f"ERROR {error.code}: {error}"
    if backend is not None:
        # Actually start the backend: catches a broken frozen dispatch or a bad ONE_C_FORM_V8UNPACK.
        try:
            checks["v8unpack_run"] = backend.smoke()
        except BackendError as error:
            checks["v8unpack_run"] = f"ERROR {error.code}: {error}"
    exe = os.environ.get("ONE_C_FORM_1CV8")
    checks["1cv8"] = exe if exe and Path(exe).is_file() else "not configured (runtime-check unavailable)"
    checks["infobase"] = os.environ.get("ONE_C_FORM_INFOBASE") or "not configured"
    provider = os.environ.get("ONE_C_FORM_AI_PROVIDER") or "not configured (ai unavailable)"
    checks["ai_provider"] = provider
    if provider.casefold() in {"anthropic", "claude"}:
        try:
            import anthropic  # noqa: F401
            checks["anthropic_sdk"] = "installed"
        except ImportError:
            from .core.ai_plan import anthropic_missing_message
            checks["anthropic_sdk"] = "missing: " + anthropic_missing_message()
    ok = not any(str(checks.get(key, "")).startswith("ERROR") for key in ("v8unpack", "v8unpack_run"))
    if getattr(args, "json", False):
        print(json.dumps({"ok": ok, **checks}, ensure_ascii=False, indent=2))
    else:
        for key, value in checks.items():
            print(f"{key:<14} {value}")
    return EXIT_OK if ok else EXIT_BACKEND


def _run_bundled_v8unpack(args: list[str]) -> int:
    """Frozen build: run the bundled v8unpack CLI in-process (it parses ``sys.argv`` itself)."""
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8")
        except (AttributeError, ValueError):
            pass
    try:
        from v8unpack.v8unpack import main as v8unpack_main
    except ImportError as error:
        print(f"v8unpack is not available in this build: {error}", file=sys.stderr)
        return EXIT_BACKEND
    saved_argv = sys.argv
    sys.argv = ["v8unpack", *args]
    try:
        result = v8unpack_main()
    except SystemExit as stop:          # argparse --help and usage errors
        code = stop.code
        return code if isinstance(code, int) else (0 if code is None else 1)
    finally:
        sys.argv = saved_argv
    return result if isinstance(result, int) else 0


def main(argv: list[str] | None = None) -> int:
    if is_frozen():
        import multiprocessing
        multiprocessing.freeze_support()
    argv = list(sys.argv[1:] if argv is None else argv)
    if argv and argv[0] == FROZEN_V8UNPACK_FLAG:
        return _run_bundled_v8unpack(argv[1:])
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8")
        except (AttributeError, ValueError):
            pass
    # With --project the input file is absent; title/ai have their own positionals, so the input
    # positional stays required and gets a placeholder that _open_session resolves from --project.
    if argv and argv[0] in PROJECT_COMMANDS and any(a == "--project" or a.startswith("--project=") for a in argv):
        argv.insert(1, PROJECT_PLACEHOLDER)
    parser = build_parser()
    try:
        args = parser.parse_args(argv)
    except UsageError as error:
        if "--json" in argv:
            return _fail(argparse.Namespace(json=True), EXIT_USAGE, "E_USAGE", str(error))
        error.parser.print_usage(sys.stderr)
        print(f"{error.parser.prog}: error: {error}", file=sys.stderr)
        return EXIT_USAGE
    try:
        if args.command == "inspect":
            return _inspect(args)
        if args.command == "doctor":
            return _doctor(args)
        if args.command == "guide":
            from importlib import resources
            print(resources.files(__package__).joinpath("AGENT_GUIDE.md").read_text(encoding="utf-8"))
            return EXIT_OK
        if args.command == "runtime-check":
            from .core.runtime_check import RuntimeConfig, run_runtime_check
            config = RuntimeConfig.from_env()
            config = RuntimeConfig(config.platform_exe, config.infobase, config.user, config.password_env,
                                   args.timeout)
            if args.project is not None:
                from .core.runtime_check import run_config_runtime_check
                if not args.form:
                    raise BackendError("E_USAGE", "--project needs --form <Тип>.<Объект>.<Форма>")
                check = run_config_runtime_check(args.project, args.form, config, Backend.discover())
            else:
                check = run_runtime_check(args.input, config, Backend.discover(), form_name=args.form)
            _emit(args, check, _runtime_line(check).strip())
            return EXIT_OK if check.get("passed") else EXIT_RUNTIME
        if args.command == "apply":
            if not args.plan.is_file():
                raise BackendError("E_INPUT_MISSING", f"plan file not found: {args.plan}")
            return _edit(args, args.plan.read_text(encoding="utf-8-sig"))
        if args.command == "ai":
            if args.recorded is not None and not args.recorded.is_file():
                raise BackendError("E_INPUT_MISSING", f"recorded responses file not found: {args.recorded}")
            return _edit(args, _ai_plan_source(args))
        return _edit(args, {"operations": [_operation_from_args(args)]})
    except AIError as error:
        return _fail(args, EXIT_AI, error.code, str(error))
    except FormModelError as error:
        return _fail(args, EXIT_FORM, error.code, str(error))
    except EdtMetadataError as error:
        return _fail(args, EXIT_FORM, error.code, str(error))
    except BackendError as error:
        return _fail(args, EXIT_USAGE if error.code == "E_USAGE" else EXIT_BACKEND, error.code, str(error))
    except OSError as error:
        return _fail(args, EXIT_BACKEND, "E_IO", str(error))
    except Exception as error:  # last resort: an error code instead of a traceback; nothing was written
        return _fail(args, EXIT_BACKEND, "E_INTERNAL", f"{type(error).__name__}: {error}")


def _fail(args: argparse.Namespace, code: int, error_code: str, message: str) -> int:
    if getattr(args, "json", False):
        print(json.dumps({"status": "error", "code": error_code, "message": message}, ensure_ascii=False, indent=2))
    else:
        print(f"Ошибка {error_code}: {message}", file=sys.stderr)
    return code
