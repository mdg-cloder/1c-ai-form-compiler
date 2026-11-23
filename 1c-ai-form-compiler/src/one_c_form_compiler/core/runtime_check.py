"""Runtime verification of an edited ordinary form in the 1C thick client.

The checked container is never modified. A disposable copy gets a read-only
probe injected at the start of the form's ``ПриОткрытии`` handler: it lists
``ЭлементыФормы`` (name, type, geometry, caption) into a UTF-8 file passed via
``/C`` and stops. The harness launches ``1cv8 ENTERPRISE /Execute <copy>``,
waits for the report, terminates only the process it started and compares the
runtime element list with the decoded form.

Safety: the probe does not write data, does not save or post documents and
runs only in the copy. The infobase configuration is not touched.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path

from .ordinary_backend import Backend, BackendError, EditSession
from .ordinary_form import OrdinaryForm

PROBE_MARK = "// one-c-form-compiler runtime probe"
REPORT_DONE = "#DONE"

PROBE_BSL = """\t{mark}
\tПопытка
{getter}\t\tПробаТекст = Новый ТекстовыйДокумент;
\t\tПробаТекст.ДобавитьСтроку("#FORM" + Символы.Таб + Формат({owner}Ширина, "ЧГ=0; ЧН=0") + Символы.Таб + Формат({owner}Высота, "ЧГ=0; ЧН=0"));
{events}{cell_events}
\t\tДля Каждого ПробаЭлемент Из {owner}ЭлементыФормы Цикл
\t\t\tПробаЗаголовок = "";
\t\t\tПопытка
\t\t\t\tПробаЗаголовок = СтрЗаменить(Строка(ПробаЭлемент.Заголовок), Символы.ПС, " ");
\t\t\tИсключение
\t\t\tКонецПопытки;
\t\t\tПробаСобытия = "";
\t\t\tДля Каждого ПробаСобытие Из ПробаИменаСобытий Цикл
\t\t\t\tПопытка
\t\t\t\t\tЕсли ПробаЭлемент.ПолучитьДействие(ПробаСобытие) <> Неопределено Тогда
\t\t\t\t\t\tПробаСобытия = ПробаСобытия + ПробаСобытие + ",";
\t\t\t\t\tКонецЕсли;
\t\t\t\tИсключение
\t\t\t\tКонецПопытки;
\t\t\tКонецЦикла;
\t\t\tПробаДанные = "";
\t\t\tПопытка
\t\t\t\tПробаДанные = Строка(ПробаЭлемент.Данные);
\t\t\tИсключение
\t\t\tКонецПопытки;
\t\t\tПробаТолькоПросмотр = "";
\t\t\tПопытка
\t\t\t\tПробаТолькоПросмотр = ?(ПробаЭлемент.ТолькоПросмотр, "1", "0");
\t\t\tИсключение
\t\t\tКонецПопытки;
\t\t\tПробаТекст.ДобавитьСтроку(ПробаЭлемент.Имя + Символы.Таб + Строка(ТипЗнч(ПробаЭлемент))
\t\t\t\t+ Символы.Таб + Формат(ПробаЭлемент.Лево, "ЧГ=0; ЧН=0") + Символы.Таб + Формат(ПробаЭлемент.Верх, "ЧГ=0; ЧН=0")
\t\t\t\t+ Символы.Таб + Формат(ПробаЭлемент.Ширина, "ЧГ=0; ЧН=0") + Символы.Таб + Формат(ПробаЭлемент.Высота, "ЧГ=0; ЧН=0")
\t\t\t\t+ Символы.Таб + ПробаЗаголовок + Символы.Таб + ПробаДанные + Символы.Таб + ПробаСобытия
\t\t\t\t+ Символы.Таб + ПробаТолькоПросмотр);
\t\t\tЕсли ТипЗнч(ПробаЭлемент) = Тип("КоманднаяПанель") Тогда
\t\t\t\tПопытка
\t\t\t\t\tДля Каждого ПробаКнопка Из ПробаЭлемент.Кнопки Цикл
\t\t\t\t\t\tПробаТекст.ДобавитьСтроку("#BTN" + Символы.Таб + ПробаЭлемент.Имя + Символы.Таб + ПробаКнопка.Имя
\t\t\t\t\t\t\t+ Символы.Таб + СтрЗаменить(Строка(ПробаКнопка.Текст), Символы.ПС, " "));
\t\t\t\t\tКонецЦикла;
\t\t\t\tИсключение
\t\t\t\tКонецПопытки;
\t\t\tКонецЕсли;
\t\t\tЕсли ТипЗнч(ПробаЭлемент) = Тип("ТабличноеПоле") Тогда
\t\t\t\tДля Каждого ПробаКолонка Из ПробаЭлемент.Колонки Цикл
\t\t\t\t\tПробаСобытия = "";
\t\t\t\t\tДля Каждого ПробаСобытие Из ПробаСобытияЯчеек Цикл
\t\t\t\t\t\tПопытка
\t\t\t\t\t\t\tЕсли ПробаКолонка.ЭлементУправления.ПолучитьДействие(ПробаСобытие) <> Неопределено Тогда
\t\t\t\t\t\t\t\tПробаСобытия = ПробаСобытия + ПробаСобытие + ",";
\t\t\t\t\t\t\tКонецЕсли;
\t\t\t\t\t\tИсключение
\t\t\t\t\t\tКонецПопытки;
\t\t\t\t\tКонецЦикла;
\t\t\t\t\tПробаТекст.ДобавитьСтроку("#COL" + Символы.Таб + ПробаЭлемент.Имя + Символы.Таб + ПробаКолонка.Имя
\t\t\t\t\t\t+ Символы.Таб + СтрЗаменить(Строка(ПробаКолонка.ТекстШапки), Символы.ПС, " ")
\t\t\t\t\t\t+ Символы.Таб + Формат(ПробаКолонка.Ширина, "ЧГ=0; ЧН=0; ЧДЦ=0")
\t\t\t\t\t\t+ Символы.Таб + Строка(ПробаКолонка.Данные) + Символы.Таб + ПробаСобытия
						+ Символы.Таб + Строка(ПробаКолонка.ДанныеФлажка));
\t\t\t\tКонецЦикла;
\t\t\tКонецЕсли;
\t\tКонецЦикла;
\t\tПробаТекст.ДобавитьСтроку("{done}");
\t\tПробаТекст.Записать(ПараметрЗапуска, КодировкаТекста.UTF8);
\tИсключение
\t\tПробаОшибка = Новый ТекстовыйДокумент;
\t\tПробаОшибка.ДобавитьСтроку("#ERROR" + Символы.Таб + ОписаниеОшибки());
\t\tПробаОшибка.Записать(ПараметрЗапуска, КодировкаТекста.UTF8);
\tКонецПопытки;
"""

# Manager collections for ПолучитьФорму of a configuration object's form.
MANAGERS = {
    "Catalog": "Справочники", "Document": "Документы", "DataProcessor": "Обработки", "Report": "Отчеты",
    "InformationRegister": "РегистрыСведений", "AccumulationRegister": "РегистрыНакопления",
    "ChartOfCharacteristicTypes": "ПланыВидовХарактеристик", "ChartOfAccounts": "ПланыСчетов",
    "ExchangePlan": "ПланыОбмена", "BusinessProcess": "БизнесПроцессы", "Task": "Задачи",
    "DocumentJournal": "ЖурналыДокументов", "Enum": "Перечисления",
    "AccountingRegister": "РегистрыБухгалтерии", "CalculationRegister": "РегистрыРасчета",
    "ChartOfCalculationTypes": "ПланыВидовРасчета", "FilterCriterion": "КритерииОтбора",
    "SettingsStorage": "ХранилищаНастроек",
}


def _probe_text(*, getter: str = "", owner: str = "") -> str:
    from .ordinary_form import EVENTS
    from .table_field import CELL_EVENTS
    names = sorted({name for events in EVENTS.values() for name in events})

    def array(variable: str, items) -> str:
        # Новый Массив + Добавить: compiles in 8.2 compatibility mode (no СтрРазделить).
        return f"\t\t{variable} = Новый Массив;\n" + "".join(
            f'\t\t{variable}.Добавить("{item}");\n' for item in items)

    return PROBE_BSL.format(mark=PROBE_MARK, done=REPORT_DONE, events=array("ПробаИменаСобытий", names),
                            cell_events=array("ПробаСобытияЯчеек", sorted(CELL_EVENTS)), getter=getter, owner=owner)


def form_getter(fqn: str) -> str:
    """BSL expression that returns the ordinary form ``fqn`` without opening it."""
    from .edt_metadata import COMMON_FORM, OBJECT_KINDS, EdtMetadataError
    parts = [part for part in fqn.split(".") if part]
    if len(parts) == 4 and parts[2].lower() in {"form", "форма"}:
        parts = [parts[0], parts[1], parts[3]]
    if len(parts) == 2 and parts[0].lower() in COMMON_FORM:
        return f'ПолучитьОбщуюФорму("{parts[1]}")'
    if len(parts) != 3 or parts[0].lower() not in OBJECT_KINDS:
        raise EdtMetadataError("E_FORM_FQN", f"form name {fqn!r} must look like Catalog.<Объект>.<Форма>")
    manager = MANAGERS[OBJECT_KINDS[parts[0].lower()][1]]
    return f'{manager}.{parts[1]}.ПолучитьФорму("{parts[2]}")'


def config_probe_module(fqn: str) -> str:
    """Object module of the probe processing: dump the configuration form and quit."""
    body = _probe_text(getter=f"\t\tПробаФорма = {form_getter(fqn)};\n", owner="ПробаФорма.")
    text = body + "\tЗавершитьРаботуСистемы(Ложь);\n"
    return text.replace("\n", "\r\n")

_ON_OPEN = re.compile(r"^(?P<head>[ \t]*Процедура[ \t]+ПриОткрытии[ \t]*\([^)]*\)[^\r\n]*\r?\n)",
                      re.IGNORECASE | re.MULTILINE)


@dataclass(frozen=True, slots=True)
class RuntimeConfig:
    platform_exe: Path
    infobase: Path
    user: str = ""
    password_env: str = ""
    timeout_seconds: float = 180.0

    @classmethod
    def from_env(cls) -> "RuntimeConfig":
        exe = os.environ.get("ONE_C_FORM_1CV8", "")
        base = os.environ.get("ONE_C_FORM_INFOBASE", "")
        if not exe or not base:
            raise BackendError("E_RUNTIME_CONFIG",
                               "set ONE_C_FORM_1CV8 (path to 1cv8.exe) and ONE_C_FORM_INFOBASE (file infobase dir)")
        return cls(Path(exe), Path(base), os.environ.get("ONE_C_FORM_INFOBASE_USER", ""),
                   os.environ.get("ONE_C_FORM_INFOBASE_PASSWORD_ENV", ""))


def inject_probe(module_text: str) -> str:
    if PROBE_MARK in module_text:
        return module_text
    match = _ON_OPEN.search(module_text)
    probe = _probe_text()
    newline = "\r\n" if "\r\n" in module_text else "\n"
    probe = probe.replace("\n", newline)
    if match:
        return module_text[:match.end()] + probe + module_text[match.end():]
    return (module_text.rstrip("\r\n") + newline + newline + "Процедура ПриОткрытии()" + newline + probe
            + "КонецПроцедуры" + newline)


def parse_report(text: str) -> dict:
    lines = [line for line in text.replace("﻿", "").splitlines() if line.strip()]
    if lines and lines[0].startswith("#ERROR"):
        return {"status": "error", "error": lines[0].split("\t", 1)[-1]}
    if not lines or lines[-1] != REPORT_DONE:
        return {"status": "incomplete"}
    form_size = None
    elements = {}
    columns: dict[str, list[dict]] = {}
    buttons: dict[str, list[dict]] = {}
    for line in lines[:-1]:
        parts = line.split("\t")
        if parts[0] == "#FORM":
            form_size = (int(parts[1]), int(parts[2]))
            continue
        if parts[0] == "#BTN":
            parts += [""] * (4 - len(parts))
            buttons.setdefault(parts[1], []).append({"name": parts[2], "caption": parts[3]})
            continue
        if parts[0] == "#COL":
            parts += [""] * (8 - len(parts))
            columns.setdefault(parts[1], []).append({
                "name": parts[2], "caption": parts[3], "width": int(parts[4] or 0), "data": parts[5],
                "events": sorted(filter(None, parts[6].split(","))), "check": parts[7]})
            continue
        if len(parts) < 6:
            continue
        elements[parts[0]] = {"type": parts[1], "left": int(parts[2]), "top": int(parts[3]),
                              "width": int(parts[4]), "height": int(parts[5]),
                              "caption": parts[6] if len(parts) > 6 else "",
                              "data": parts[7] if len(parts) > 7 else "",
                              "events": sorted(filter(None, parts[8].split(","))) if len(parts) > 8 else [],
                              "read_only": parts[9] if len(parts) > 9 else ""}
    return {"status": "ok", "form_size": form_size, "elements": elements, "columns": columns, "buttons": buttons}


def _norm(text: str) -> str:
    return " ".join(text.split())


def compare(form: OrdinaryForm, runtime: dict) -> list[str]:
    """Differences between the decoded form and what the platform reports."""
    problems = []
    actual = runtime["elements"]
    for control in form.controls():
        got = actual.get(control.name)
        if got is None:
            problems.append(f"{control.name}: missing at runtime")
            continue
        # Elements on panel pages report coordinates relative to the page; root ones to the form.
        expected = control.rect.as_dict()
        for axis in ("width", "height"):
            if abs(got[axis] - expected[axis]) > 0:
                problems.append(f"{control.name}: {axis} {got[axis]} != {expected[axis]}")
        if not control.container:
            for axis in ("left", "top"):
                if abs(got[axis] - expected[axis]) > 0:
                    problems.append(f"{control.name}: {axis} {got[axis]} != {expected[axis]}")
        caption = control.caption.get("ru")
        if caption is not None and control.type in ("Label", "Button", "CheckBox", "Group") \
                and got["caption"] and _norm(got["caption"]) != _norm(caption):
            problems.append(f"{control.name}: caption {got['caption']!r} != {caption!r}")
        if control.type == "Table":
            from .table_field import table_events
            expected_events = sorted(name for name in table_events(form, control.key) if not name.startswith("#"))
        else:
            expected_events = sorted(name for name in form.events(control.key) if not name.startswith("#"))
        if expected_events and got.get("events") != expected_events:
            problems.append(f"{control.name}: events {got.get('events')} != {expected_events}")
        if control.data and got.get("data") != control.data:
            problems.append(f"{control.name}: data {got.get('data')!r} != {control.data!r}")
        read_only = form.read_only(control.key)
        if read_only is not None and got.get("read_only") in ("0", "1") and (got["read_only"] == "1") != read_only:
            problems.append(f"{control.name}: ТолькоПросмотр {got['read_only'] == '1'} != {read_only}")
        if control.type == "CommandPanel" and "buttons" in runtime:
            problems += _compare_buttons(form, control, runtime["buttons"].get(control.name, []))
        if control.type == "Table" and "columns" in runtime:
            problems += _compare_columns(form, control, runtime["columns"].get(control.name, []))
    extra = set(actual) - {control.name for control in form.controls()}
    problems += [f"{name}: present at runtime but not in the form" for name in sorted(extra)]
    return problems


def _compare_buttons(form: OrdinaryForm, control, got: list[dict]) -> list[str]:
    """Every top-level button of the panel record must be in ``Кнопки`` at runtime (autofill may add more)."""
    from .command_panel import panel_info
    info = panel_info(form, control.key)
    if not info:
        return []
    present = {button["name"] for button in got}
    return [f"{control.name}: button {button['name']} missing at runtime" for button in info["buttons"]
            if button["name"] not in present]


def _width_scale(expected: list, got: list[dict]) -> float:
    """Characters per formula unit for this table: the runtime width in characters depends on the table's
    font (8.3.27: 110 px -> 18 chars on one table, 16 on another). The median over the table's own columns
    absorbs the font; a single wrong width still stands out. Fewer than 3 columns: no scaling."""
    ratios = sorted(have["width"] / runtime_column_width(want.width) for want, have in zip(expected, got)
                    if runtime_column_width(want.width) > 0 and have.get("width"))
    if len(ratios) < 3:
        return 1.0
    median = ratios[len(ratios) // 2] if len(ratios) % 2 else (ratios[len(ratios) // 2 - 1] + ratios[len(ratios) // 2]) / 2
    return min(max(median, 0.7), 1.3)


def runtime_column_width(width: int) -> int:
    """``Колонка.Ширина`` at runtime is in characters; the file stores pixels.

    Fitted on 8.3.27 (40→7, 120→19, 150→24, 154→24); the platform converts with the average character
    width of the table's font, so the same 154 px also came back as 22 chars (and 163 px as 22). The
    estimate is therefore compared with :func:`width_tolerance`."""
    return int(round((width + 7) / 6.67))


def width_tolerance(chars: int) -> int:
    """Allowed difference in characters: ±2, or ±12 % for wide columns (font-dependent rounding).

    A width that was not applied at all (e.g. 100 px left instead of 154 px: 16 vs 24 chars) still
    stands out."""
    return max(2, int(round(chars * 0.12)))


def _compare_columns(form: OrdinaryForm, control, got: list[dict]) -> list[str]:
    from .table_field import list_columns
    problems = []
    expected = list_columns(form, control.key)
    if [c.name for c in expected] != [c["name"] for c in got]:
        return [f"{control.name}: columns {[c['name'] for c in got]} != {[c.name for c in expected]}"]
    scale = _width_scale(expected, got)
    for want, have in zip(expected, got):
        where = f"{control.name}.{want.name}"
        caption = want.caption.get("ru")
        if caption is not None and _norm(have["caption"]) != _norm(caption):
            problems.append(f"{where}: caption {have['caption']!r} != {caption!r}")
        width = int(round(runtime_column_width(want.width) * scale))
        if abs(have["width"] - width) > width_tolerance(width):
            problems.append(f"{where}: width {have['width']} chars != {width} ({want.width} px in the file)")
        if want.checkbox:
            if have.get("check", "") != want.data:
                problems.append(f"{where}: checkbox data {have.get('check', '')!r} != {want.data!r}")
        elif want.data and have["data"] != want.data:
            problems.append(f"{where}: data {have['data']!r} != {want.data!r}")
        events = sorted(name for name in want.events if not name.startswith("#"))
        if events and have["events"] != events:
            problems.append(f"{where}: events {have['events']} != {events}")
    return problems


def run_runtime_check(container: Path, config: RuntimeConfig, backend: Backend, *,
                      form_name: str | None = None) -> dict:
    container = Path(container).resolve()
    if not config.platform_exe.is_file():
        raise BackendError("E_RUNTIME_CONFIG", f"1cv8.exe not found: {config.platform_exe}")
    work = Path(tempfile.mkdtemp(prefix="1c-form-runtime-"))
    try:
        with EditSession(container, backend) as session:
            form = session.form(form_name)
            if form.module_text is None:
                raise BackendError("E_RUNTIME_NO_MODULE", "form has no module to host the probe")
            form.module_text = inject_probe(form.module_text)
            form._module_changed = True
            probe_epf = work / ("probe" + container.suffix.lower())
            session.build_and_publish(probe_epf)
            expected = OrdinaryForm.load(form.form_dir)
            expected.object_attributes = form.object_attributes
        return _launch(probe_epf, expected, config, work)
    finally:
        shutil.rmtree(work, ignore_errors=True)


def run_config_runtime_check(project: Path, fqn: str, config: RuntimeConfig, backend: Backend) -> dict:
    """Open a configuration form (already loaded into the test infobase) and compare it with the project file.

    The infobase must be updated from the EDT project first (AI-EDT ``infobase_admin update_database``).
    The probe is a form-less external processing whose object module gets the form with
    ``<Менеджер>.ПолучитьФорму`` (the form is not opened, its handlers do not run), dumps it and quits.
    """
    from importlib import resources
    from .edt_metadata import resolve_form
    from .oform_session import OformSession
    if not config.platform_exe.is_file():
        raise BackendError("E_RUNTIME_CONFIG", f"1cv8.exe not found: {config.platform_exe}")
    form_path = resolve_form(Path(project), fqn)
    work = Path(tempfile.mkdtemp(prefix="1c-form-runtime-"))
    try:
        with OformSession(form_path, keep_workdir=True) as session:
            form = session.form()
            expected_dir = work / "expected"
            shutil.copytree(form.form_dir, expected_dir)
            shutil.rmtree(session.workdir, ignore_errors=True)
        expected = OrdinaryForm.load(expected_dir)
        expected.object_attributes = form.object_attributes
        expected.tabular_sections = form.tabular_sections
        source = work / "probe-src"
        with resources.as_file(resources.files(__package__).joinpath("probe_epf")) as packaged:
            shutil.copytree(packaged, source)
        module = source / "ExternalDataProcessor.obj.bsl"
        with open(module, "w", encoding="utf-8-sig", newline="") as stream:
            stream.write(config_probe_module(fqn))
        (work / "tmp").mkdir()
        probe_epf = work / "probe.epf"
        backend.build(source, probe_epf, work / "tmp")
        result = _launch(probe_epf, expected, config, work)
        result["form"] = fqn
        return result
    finally:
        shutil.rmtree(work, ignore_errors=True)


def _launch(probe_epf: Path, expected: OrdinaryForm, config: RuntimeConfig, work: Path) -> dict:
    report_path = work / "report.txt"
    log_path = work / "1cv8-out.log"
    argv = [str(config.platform_exe), "ENTERPRISE", "/F", str(config.infobase),
            "/RunModeOrdinaryApplication", "/DisableStartupDialogs", "/DisableStartupMessages",
            "/Out", str(log_path),
            "/Execute", str(probe_epf), "/C", str(report_path)]
    if config.user:
        argv += ["/N", config.user]
        password = os.environ.get(config.password_env, "") if config.password_env else ""
        if password:
            argv += ["/P", password]
    started = time.monotonic()
    process = subprocess.Popen(argv, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                               stderr=subprocess.DEVNULL)
    result = {"status": "timeout"}
    try:
        while time.monotonic() - started < config.timeout_seconds:
            if report_path.is_file():
                time.sleep(0.5)
                result = parse_report(report_path.read_text(encoding="utf-8-sig", errors="replace"))
                if result["status"] != "incomplete":
                    break
            if process.poll() is not None:
                # The config probe quits right after writing its report: read it once more.
                text = report_path.read_text(encoding="utf-8-sig", errors="replace") if report_path.is_file() else ""
                result = parse_report(text) if text else {"status": "incomplete"}
                if result["status"] == "incomplete":
                    result = {"status": "exited", "returncode": process.returncode}
                break
            time.sleep(0.5)
    finally:
        if process.poll() is None:
            process.kill()
            process.wait(timeout=30)
    result["elapsed_seconds"] = round(time.monotonic() - started, 1)
    if log_path.is_file():
        log_text = log_path.read_bytes().decode("utf-8-sig", "replace").strip()
        if log_text:
            result["platform_log"] = log_text[-2000:]
    if result["status"] == "ok":
        result["problems"] = compare(expected, result)
        result["checked_elements"] = len(expected.controls())
        result["checked_columns"] = sum(len(columns) for columns in result.get("columns", {}).values())
        result["passed"] = not result["problems"]
    else:
        result["passed"] = False
    result.pop("elements", None)
    result.pop("columns", None)
    return result
