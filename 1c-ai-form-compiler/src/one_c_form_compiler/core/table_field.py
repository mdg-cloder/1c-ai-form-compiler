"""Table fields (ТабличноеПоле) of ordinary forms: columns, data binding, events.

Format notes: ``docs/table-format.md``. In short:

* columns live in ``raw[2][2][1][23]`` as ``[N, column...]``;
* a column is ``[COLUMN_KIND, ["1", ["8", props, data_path, ["-1"], ["-1"]], "0", "0"]]``;
* ``props[39]`` holds the cell control (an input field) as base64 of an 18-byte header +
  raw deflate of an 8-byte length + brace text; the cell's events sit in ``cell[4]``.
"""

from __future__ import annotations

import base64
import copy
import os
import re
import struct
import tempfile
import zlib
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from .ordinary_form import OrdinaryForm

COLUMN_KIND = "c2cf1953-2796-4fe2-b78c-ff84140b124e"
# Unbound table field whose value (ТаблицаЗначений) is set by the form module: its columns are another
# record kind, ``[VALUE_TABLE_COLUMN_KIND, ["8", ["8", props, ["-1"], ["-1"], ["-1"]], Данные, ДанныеФлажка,
# ДанныеКартинки, "0"]]`` with the value-table column names as strings (observed on typical report forms).
VALUE_TABLE_TYPE = "acf6192e-81ca-46ef-93a6-5a6968b78663"
VALUE_TABLE_COLUMN_KIND = "737535a4-21e6-4971-8513-3e3173a9fedd"
VT_DATA, VT_CHECK = 2, 3
# raw[2][3] follows the value type: a value table needs this block (all value-table fields seen), a
# tabular section has 51d1e122-... . A mismatch makes 1C fail with "Ошибка формата потока" on reading the form.
VALUE_TABLE_SETTINGS = ["342cf854-134c-42bb-8af9-a2103d5d9723", ["5", "0", "0", "1"]]
VALUE_TABLE_PATTERN = ['"Pattern"', ['"#"', VALUE_TABLE_TYPE]]
VT_COLUMN_PATTERN = ['"Pattern"', ['"S"']]
# Tabular-section tables: the most common block on typical forms (about 3 of 4 section tables in a sample).
SECTION_SETTINGS = ["51d1e122-c0f3-496f-901e-806df8206ba9", ["2", "1", ["0", "1"]]]
CELL_HEADER = bytes.fromhex("0201534b6ff4888dc14ea0d5ebb6bda0a70d")
COLUMNS_PATH = (2, 2, 1, 23)
P_CAPTION, P_WIDTH, P_ORDER_A, P_ORDER_B, P_NAME, P_PATTERN, P_CELL = 1, 4, 5, 6, 30, 35, 39
ROW_NUMBER = "НомерСтроки"

# Table-field events: name -> (id, parameters). Ids and parameters observed on typical forms.
TABLE_EVENTS: dict[str, tuple[str, str]] = {
    "ПриАктивизацииСтроки": ("35", "Элемент"),
    "ПередНачаломДобавления": ("40", "Элемент, Отказ, Копирование"),
    "ПередНачаломИзменения": ("41", "Элемент, Отказ"),
    "ПередУдалением": ("42", "Элемент, Отказ"),
    "ПриНачалеРедактирования": ("43", "Элемент, НоваяСтрока, Копирование"),
    "ПередОкончаниемРедактирования": ("44", "Элемент, НоваяСтрока, ОтменаРедактирования, Отказ"),
    "ПриИзмененииФлажка": ("45", "Элемент, Колонка"),
    "ПриВыводеСтроки": ("47", "Элемент, ОформлениеСтроки, ДанныеСтроки"),
    "ПриОкончанииРедактирования": ("49", "Элемент, НоваяСтрока, ОтменаРедактирования"),
    "ПослеУдаления": ("51", "Элемент"),
    "ОбработкаВыбора": ("52", "Элемент, ВыбранноеЗначение, СтандартнаяОбработка"),
    "ПриПолученииДанных": ("53", "Элемент, ОформленияСтрок"),
}
# Cell (column) events are input-field events.
CELL_EVENTS: dict[str, tuple[str, str]] = {
    "НачалоВыбораИзСписка": ("1", "Элемент, СтандартнаяОбработка"),
    "НачалоВыбора": ("2", "Элемент, СтандартнаяОбработка"),
    "ОбработкаВыбора": ("7", "Элемент, ВыбранноеЗначение, СтандартнаяОбработка"),
    "ПриИзменении": ("2147483647", "Элемент"),
}
CELL_EVENT_SLOT = 4
# Inner column record ["8", props, Данные, ДанныеФлажка, ДанныеКартинки]: data path slots.
PATH_DATA, PATH_CHECK = 2, 3
NO_PATH = ["-1"]
P_CELL_TYPE = 38                   # cell control type (381ed624-... input field), zero uuid without own cell
NO_CELL_TYPE = "00000000-0000-0000-0000-000000000000"
BOOLEAN_PATTERN = ['"Pattern"', ['"B"']]
# Marker of a line generated for a dotted column (see add_column): "// one-c-form-compiler: <Таблица>.<Колонка> = <путь>".
COMPUTED_MARK = "// one-c-form-compiler: "


@dataclass
class ColumnInfo:
    name: str
    caption: dict[str, str]
    width: int
    data: str | None
    events: dict[str, str] = field(default_factory=dict)
    editable: bool = True
    checkbox: bool = False
    computed: str | None = None          # dotted path shown by the generated ПриВыводеСтроки code

    def as_dict(self) -> dict[str, Any]:
        result: dict[str, Any] = {"name": self.name, "caption": self.caption, "width": self.width}
        if self.data:
            result["data"] = self.data
        if self.checkbox:
            result["checkbox"] = True
        if self.computed:
            result["computed"] = self.computed
        if self.events:
            result["events"] = self.events
        if not self.editable:
            result["cell"] = False
        return result


# --------------------------------------------------------------------------- numbers & cells

def format_width(value: float) -> str:
    """Width as the platform writes doubles: 100 -> '1e2', 120 -> '1.2e2', 123 -> '123'."""
    if float(value) != int(value):
        return repr(float(value))
    number = int(value)
    if number == 0 or number % 10:
        return str(number)
    digits = str(abs(number))
    mantissa = digits.rstrip("0")
    exponent = len(digits) - 1
    text = mantissa[0] + ("." + mantissa[1:] if len(mantissa) > 1 else "")
    return ("-" if number < 0 else "") + f"{text}e{exponent}"


def parse_width(text: str) -> int:
    return int(round(float(text)))


def _blob_text(node: Any) -> str | None:
    while isinstance(node, list) and node:
        node = node[0]
    return node if isinstance(node, str) and node.startswith("#base64:") else None


def decode_cell(props: list) -> list | None:
    """The cell control record of a column, or None when the column has no own cell."""
    if len(props) <= P_CELL:
        return None
    text = _blob_text(props[P_CELL])
    if text is None:
        return None
    from v8unpack import helper
    try:
        blob = base64.b64decode("".join(text.split(":", 1)[1].split()))
        if not blob.startswith(CELL_HEADER):
            raise ValueError("unknown cell header")
        payload = zlib.decompress(blob[len(CELL_HEADER):], -15)
        size = struct.unpack("<Q", payload[:8])[0]
        body = payload[8:8 + size]
        with tempfile.TemporaryDirectory(prefix="cell-") as tmp:
            with open(os.path.join(tmp, "cell"), "wb") as stream:
                stream.write(body)
            return helper.brace_file_read(tmp, "cell")[0]
    except Exception as error:
        raise _error("E_TABLE_FORMAT", f"column cell record is not in the supported format ({error})") from None


def encode_cell(props: list, cell: list) -> None:
    from v8unpack import helper
    with tempfile.TemporaryDirectory(prefix="cell-") as tmp:
        helper.brace_file_write([cell], tmp, "cell")
        with open(os.path.join(tmp, "cell"), "rb") as stream:
            body = stream.read()
    if not body.startswith(b"\xef\xbb\xbf"):
        body = b"\xef\xbb\xbf" + body
    compressor = zlib.compressobj(9, zlib.DEFLATED, -15)
    packed = compressor.compress(struct.pack("<Q", len(body)) + body) + compressor.flush()
    text = "#base64:" + base64.b64encode(CELL_HEADER + packed).decode("ascii")
    node = props[P_CELL]
    holder = node
    while isinstance(holder, list) and holder and isinstance(holder[0], list):
        holder = holder[0]
    holder[0] = text


# --------------------------------------------------------------------------- reading

def columns_node(form: "OrdinaryForm", key: str) -> list:
    node = form.data[key]["raw"]
    for index in COLUMNS_PATH:
        node = node[index]
    if not (isinstance(node, list) and node and str(node[0]).isdigit()):
        from .ordinary_form import FormModelError
        raise FormModelError("E_TABLE_FORMAT", f"{form._name(key)}: unexpected table record")
    return node


def column_props(column: list) -> list:
    return column[1][1][1]


def column_path(column: list) -> list:
    return column[1][1][2]


def column_name(column: list) -> str:
    from .ordinary_form import decode_str
    return decode_str(column_props(column)[P_NAME])


def table_source(form: "OrdinaryForm", key: str) -> tuple[str | None, str | None]:
    """(owner prop id, tabular-section uuid) of the table's data source."""
    entry = form._datasource(form.data[key]["raw"][1])
    if not entry:
        return None, None
    try:
        kind, owner = entry[1][0], entry[1][1][0]
    except (IndexError, TypeError):
        return None, None
    if kind == "2" and len(entry[1]) > 2 and len(entry[1][2]) == 2:
        return owner, entry[1][2][1]
    return owner, None


def table_section(form: "OrdinaryForm", key: str):
    _owner, uuid = table_source(form, key)
    if uuid is None:
        return None
    return next((section for section in form.tabular_sections.values() if section.uuid == uuid), None)


def table_data_name(form: "OrdinaryForm", key: str) -> str | None:
    section = table_section(form, key)
    if section is not None:
        return section.name
    owner, uuid = table_source(form, key)
    if owner is not None and uuid is None:
        prop = next((p for p in form.elem_json.get("props") or [] if p.get("id") == owner), None)
        return prop["name"] if prop else None
    return None


def is_value_table(form: "OrdinaryForm", key: str) -> bool:
    return form.data[key]["raw"][2][1] == VALUE_TABLE_PATTERN


def make_value_table(form: "OrdinaryForm", key: str) -> None:
    """Unbound table field typed ТаблицаЗначений; the module assigns ``ЭлементыФормы.<Имя>.Значение``."""
    element_id = form.data[key]["raw"][1]
    sources = form.datasources
    kept = [entry for entry in sources[1:] if not (isinstance(entry, list) and entry and entry[0] == element_id)]
    sources[:] = [str(len(kept))] + kept
    form.data[key]["raw"][2][1] = copy.deepcopy(VALUE_TABLE_PATTERN)
    form.data[key]["raw"][2][3] = copy.deepcopy(VALUE_TABLE_SETTINGS)
    form.data[key].pop("prop", None)


def _vt_slot(column: list, slot: int) -> str:
    from .ordinary_form import decode_str
    value = column[1][slot] if len(column[1]) > slot else '""'
    return decode_str(value) if isinstance(value, str) and value.startswith('"') else ""


def to_value_table_column(column: list) -> list:
    """Re-shape a tabular-section column into a value-table column (same props, no data path)."""
    if column[0] == VALUE_TABLE_COLUMN_KIND:
        return column
    inner = column[1][1]
    return [VALUE_TABLE_COLUMN_KIND, ["8", [inner[0], inner[1], ["-1"], ["-1"], ["-1"]], '""', '""', '""', "0"]]


def _column_data(section, path: list) -> str | None:
    if path == ["-2"]:
        return ROW_NUMBER
    if section is not None and len(path) == 2:
        return next((name for name, attr in section.attributes.items() if attr.uuid == path[1]), None)
    return None


def _format_guard(function):
    """Unexpected record shapes become E_TABLE_FORMAT instead of a traceback."""
    import functools

    @functools.wraps(function)
    def wrapper(*args, **kwargs):
        try:
            return function(*args, **kwargs)
        except (IndexError, KeyError, TypeError, ValueError, AttributeError) as error:
            raise _error("E_TABLE_FORMAT", f"unexpected table record ({type(error).__name__}: {error})") from None
    return wrapper


@_format_guard
def list_columns(form: "OrdinaryForm", key: str) -> list[ColumnInfo]:
    from .ordinary_form import decode_str, loc_to_dict
    section = table_section(form, key)
    computed = computed_columns(form, form._name(key))
    result = []
    for column in columns_node(form, key)[1:]:
        props = column_props(column)
        events: dict[str, str] = {}
        cell = decode_cell(props)
        if cell is not None:
            names = {event_id: name for name, (event_id, _params) in CELL_EVENTS.items()}
            slot = cell[CELL_EVENT_SLOT] if len(cell) > CELL_EVENT_SLOT else None
            if isinstance(slot, list) and slot and str(slot[0]).isdigit():
                for entry in slot[1:]:
                    if isinstance(entry, list) and len(entry) > 2 and isinstance(entry[2], list):
                        events[names.get(entry[0], "#" + entry[0])] = decode_str(entry[2][1])
        if column[0] == VALUE_TABLE_COLUMN_KIND:
            data, checkbox = _vt_slot(column, VT_DATA), False
            if not data and _vt_slot(column, VT_CHECK):
                data, checkbox = _vt_slot(column, VT_CHECK), True
        else:
            data, checkbox = _column_data(section, column_path(column)), False
            check = column[1][1][PATH_CHECK] if len(column[1][1]) > PATH_CHECK else NO_PATH
            if not data and check != NO_PATH:
                data, checkbox = _column_data(section, check), True
        name = column_name(column)
        result.append(ColumnInfo(name, loc_to_dict(props[P_CAPTION]), parse_width(props[P_WIDTH]),
                                 data or None, events, cell is not None, checkbox,
                                 computed.get(name) if not data else None))
    return result


def computed_columns(form: "OrdinaryForm", table: str) -> dict[str, str]:
    """Column -> dotted path of the lines generated for dotted columns of ``table``."""
    if not form.module_text:
        return {}
    pattern = re.compile(re.escape(COMPUTED_MARK) + re.escape(table) + r"\.(\w+) = ([\w.]+)")
    return {match.group(1): match.group(2) for match in pattern.finditer(form.module_text)}


@_format_guard
def table_events(form: "OrdinaryForm", key: str) -> dict[str, str]:
    from .ordinary_form import decode_str
    names = {event_id: name for name, (event_id, _params) in TABLE_EVENTS.items()}
    slot = form.data[key]["raw"][2][4]
    result = {}
    if isinstance(slot, list) and slot and str(slot[0]).isdigit():
        for entry in slot[1:]:
            result[names.get(entry[0], "#" + entry[0])] = decode_str(entry[2][1])
    return result


# --------------------------------------------------------------------------- editing

def _error(code: str, message: str):
    from .ordinary_form import FormModelError
    return FormModelError(code, message)


def find_column(form: "OrdinaryForm", key: str, name: str) -> tuple[int, list]:
    columns = columns_node(form, key)
    for index, column in enumerate(columns[1:], start=1):
        if column_name(column) == name:
            return index, column
    known = ", ".join(column_name(c) for c in columns[1:]) or "none"
    raise _error("E_COLUMN_NOT_FOUND", f"column {name!r} not found in {form._name(key)}; columns: {known}")


def _column_template(form: "OrdinaryForm", key: str) -> list:
    """A column to clone: own table first (editable cell, data-bound), then other tables, then built-in."""
    value_table = is_value_table(form, key)

    def candidates(table_key: str) -> list[list]:
        try:
            columns = columns_node(form, table_key)[1:]
        except Exception:
            return []
        columns = [c for c in columns if (c[0] == VALUE_TABLE_COLUMN_KIND) == value_table]
        return sorted(columns, key=lambda c: (len(column_props(c)) <= P_CELL, len(column_path(c)) != 2))
    own = candidates(key)
    if own and len(column_props(own[0])) > P_CELL:
        return own[0]
    for other in form.element_keys():
        if other != key and form._type(other) == "Table":
            found = candidates(other)
            if found and len(column_props(found[0])) > P_CELL:
                return found[0]
    from .standards import standard_template
    template = standard_template("TableColumn")
    return to_value_table_column(template) if value_table else template


def _clear_cell_events(props: list) -> None:
    cell = decode_cell(props)
    if cell is None:
        return
    from .ordinary_form import _clear_events
    if len(cell) > CELL_EVENT_SLOT and isinstance(cell[CELL_EVENT_SLOT], list):
        cell[CELL_EVENT_SLOT] = ["0"]
    _clear_events(cell[2] if len(cell) > 2 else cell)
    encode_cell(props, cell)


def _resolve_column_data(form: "OrdinaryForm", key: str, data: str) -> tuple[list, list | None]:
    """(data path, value pattern) for a column bound to ``data``."""
    if data == ROW_NUMBER:
        return ["-2"], ['"Pattern"', ['"N"', "10", "0", "1"]]
    section = table_section(form, key)
    if section is None:
        raise _error("E_DATA_UNSUPPORTED",
                     f"{form._name(key)} is not bound to a tabular section of the object; column data is not supported")
    attribute = section.attributes.get(data)
    if attribute is None:
        known = ", ".join([ROW_NUMBER, *section.attributes]) or "none"
        raise _error("E_DATA_UNKNOWN", f"tabular section {section.name} has no attribute {data!r}; available: {known}")
    return ["0", attribute.uuid], copy.deepcopy(attribute.pattern) if attribute.pattern else None


def add_column(form: "OrdinaryForm", table: str, name: str, *, data: str | None = None,
               caption: str | dict[str, str] | None = None, width: int | None = None, after: str | None = None,
               events: dict[str, str] | None = None, checkbox: bool = False) -> ColumnInfo:
    from .ordinary_form import encode_str, make_loc, validate_name
    key = _table_key(form, table)
    validate_name(name)
    value_table = is_value_table(form, key)
    dotted = bool(data) and "." in data
    chain = None
    if dotted:
        if value_table or checkbox:
            raise _error("E_DATA_UNSUPPORTED", "a dotted data path (Реквизит.Подреквизит) is supported for tables on a "
                                               "tabular section of a configuration object, not for value tables or "
                                               "checkbox columns")
        if events:
            raise _error("E_HANDLER_UNSUPPORTED", "a dotted column has no own cell control; events are not available")
        chain = _resolve_dotted(form, key, data)
    if checkbox and not value_table:
        if not data:
            raise _error("E_DATA_REQUIRED", "a checkbox column of a tabular-section table needs data: a Булево "
                                            "attribute of the tabular section")
        if events:
            raise _error("E_HANDLER_UNSUPPORTED", "a checkbox column has no own cell control; use the table event "
                                                  "ПриИзмененииФлажка")
        section_attribute = _section_attribute(form, key, data)
        if not section_attribute.is_boolean:
            raise _error("E_DATA_TYPE", f"a checkbox column needs a Булево attribute; {data} is "
                                        f"{_type_text(section_attribute)}")
    if after:
        find_column(form, key, after)
    columns = columns_node(form, key)
    if any(column_name(c) == name for c in columns[1:]):
        raise _error("E_NAME_EXISTS", f"column {name!r} already exists in {table}")
    column = copy.deepcopy(_column_template(form, key))
    props = column_props(column)
    numbers = [column_props(c) for c in columns[1:]]
    props[P_ORDER_A] = str(max([int(p[P_ORDER_A]) for p in numbers], default=0) + 1)
    props[P_ORDER_B] = str(max([int(p[P_ORDER_B]) for p in numbers], default=-1) + 1)
    props[P_NAME] = encode_str(name)
    texts = form._texts(caption if caption is not None else ((data or name).rsplit(".", 1)[-1]), {})
    props[P_CAPTION] = make_loc(texts)
    if width is not None:
        if not 4 <= int(width) <= 2000:
            raise _error("E_GEOMETRY_INVALID", f"column width {width} is out of range 4..2000")
        props[P_WIDTH] = format_width(int(width))
    if value_table:
        if data is not None:
            validate_name(data)
        _set_vt_data(column, data or name, checkbox)
        props[P_PATTERN] = copy.deepcopy(VT_COLUMN_PATTERN)
        cell = decode_cell(props)
        if cell is not None:
            cell[1] = copy.deepcopy(VT_COLUMN_PATTERN)
            encode_cell(props, cell)
    elif checkbox:
        # As on typical forms (247 of 383 checkbox columns of tabular-section tables): no Данные, the attribute in
        # ДанныеФлажка, no own cell control - the platform draws and toggles the check box.
        column[1][1][PATH_DATA] = list(NO_PATH)
        column[1][1][PATH_CHECK] = ["0", section_attribute.uuid]
        props[P_PATTERN] = copy.deepcopy(BOOLEAN_PATTERN)
        _drop_cell(props)
    elif dotted:
        # Typical forms have no multi-step column paths; such a column has no data and its text is set in
        # ПриВыводеСтроки (see _add_row_output_line).
        column[1][1][PATH_DATA] = list(NO_PATH)
        column[1][1][PATH_CHECK] = list(NO_PATH)
        if chain[-1].pattern:
            props[P_PATTERN] = copy.deepcopy(chain[-1].pattern)
        _drop_cell(props)
    elif data:
        path, pattern = _resolve_column_data(form, key, data)
        column[1][1][PATH_DATA] = path
        column[1][1][PATH_CHECK] = list(NO_PATH)
        if pattern:
            props[P_PATTERN] = pattern
    else:
        column[1][1][PATH_DATA] = list(NO_PATH)
        column[1][1][PATH_CHECK] = list(NO_PATH)
    _clear_cell_events(props)
    if data and not value_table and not checkbox and not dotted:
        cell = decode_cell(props)
        if cell is not None and props[P_PATTERN]:
            cell[1] = copy.deepcopy(props[P_PATTERN])
            encode_cell(props, cell)
    position = len(columns)
    if after:
        index, _ = find_column(form, key, after)
        position = index + 1
    columns.insert(position, column)
    columns[0] = str(len(columns) - 1)
    if dotted:
        _add_row_output_line(form, key, name, data)
    for event, procedure in (events or {}).items():
        set_column_handler(form, key, name, event, procedure)
    return next(c for c in list_columns(form, key) if c.name == name)


def _section_attribute(form: "OrdinaryForm", key: str, data: str):
    section = table_section(form, key)
    if section is None:
        raise _error("E_DATA_UNSUPPORTED",
                     f"{form._name(key)} is not bound to a tabular section of the object; column data is not supported")
    attribute = section.attributes.get(data)
    if attribute is None:
        known = ", ".join([ROW_NUMBER, *section.attributes]) or "none"
        raise _error("E_DATA_UNKNOWN", f"tabular section {section.name} has no attribute {data!r}; available: {known}")
    return attribute


def _type_text(attribute) -> str:
    from .ordinary_form import format_value_type
    return " | ".join(attribute.type_names) or format_value_type(attribute.pattern) or "of an unknown type"


def _resolve_dotted(form: "OrdinaryForm", key: str, data: str) -> list:
    """Check ``data`` = ``Реквизит.Подреквизит[...]`` against the metadata; the chain of attributes."""
    from .edt_metadata import EdtMetadataError, resolve_path
    from .ordinary_form import validate_name
    parts = data.split(".")
    for part in parts:
        validate_name(part)
    first = _section_attribute(form, key, parts[0])
    section = table_section(form, key)
    try:
        return resolve_path(getattr(section, "project_dir", None), first, parts[1:])
    except EdtMetadataError as error:
        raise _error(error.code, f"{data}: {error}") from None


def _drop_cell(props: list) -> None:
    """Remove the column's own cell control (a column without one is drawn by the table)."""
    if len(props) > P_CELL:
        del props[P_CELL:]
    if len(props) > P_CELL_TYPE:
        props[P_CELL_TYPE] = NO_CELL_TYPE


def _row_output_procedure(form: "OrdinaryForm", key: str) -> str:
    """The table's ПриВыводеСтроки handler; assigned (``<Таблица>ПриВыводеСтроки``) when there is none."""
    handler = table_events(form, key).get("ПриВыводеСтроки")
    if handler:
        return handler
    procedure = form._name(key) + "ПриВыводеСтроки"
    form.set_handler(key, "ПриВыводеСтроки", procedure)
    return procedure


def _add_row_output_line(form: "OrdinaryForm", key: str, column: str, data: str) -> None:
    """``ОформлениеСтроки.Ячейки.<column>.УстановитьТекст(Строка(ДанныеСтроки.<data>))`` at the start of the
    table's ПриВыводеСтроки handler (the way typical forms fill columns without data)."""
    procedure = _row_output_procedure(form, key)
    form.ensure_procedure(procedure, TABLE_EVENTS["ПриВыводеСтроки"][1])
    text = form.module_text
    match = re.compile(r"^[ \t]*(?:Процедура|Procedure)[ \t]+" + re.escape(procedure) + r"[ \t]*\(([^)]*)\)[^\r\n]*\r?\n",
                       re.IGNORECASE | re.MULTILINE).search(text)
    if match is None:
        raise _error("E_PROCEDURE_TEXT", f"procedure {procedure} (ПриВыводеСтроки of {form._name(key)}) not found")
    params = [part.strip().split()[-1] for part in match.group(1).split(",") if part.strip()]
    if len(params) < 3:
        raise _error("E_PROCEDURE_TEXT", f"{procedure} must take (Элемент, ОформлениеСтроки, ДанныеСтроки)")
    newline = "\r\n" if "\r\n" in text else "\n"
    line = (f"\t{params[1]}.Ячейки.{column}.УстановитьТекст(Строка({params[2]}.{data})); "
            f"{COMPUTED_MARK}{form._name(key)}.{column} = {data}{newline}")
    form.module_text = text[:match.end()] + line + text[match.end():]
    form._module_changed = True


def _remove_row_output_lines(form: "OrdinaryForm", table: str, column: str) -> None:
    if not form.module_text:
        return
    pattern = re.compile(r"^[^\r\n]*" + re.escape(f"{COMPUTED_MARK}{table}.{column} = ") + r"[^\r\n]*(?:\r?\n|$)",
                         re.MULTILINE)
    text, count = pattern.subn("", form.module_text)
    if count:
        form.module_text = text
        form._module_changed = True


def modify_column(form: "OrdinaryForm", table: str, name: str, *, caption: str | dict[str, str] | None = None,
                  width: int | None = None, data: str | None = None,
                  events: dict[str, str] | None = None) -> ColumnInfo:
    from .ordinary_form import loc_to_dict, make_loc
    key = _table_key(form, table)
    _index, column = find_column(form, key, name)
    props = column_props(column)
    if caption is not None:
        props[P_CAPTION] = make_loc(form._texts(caption, loc_to_dict(props[P_CAPTION])))
    if width is not None:
        if not 4 <= int(width) <= 2000:
            raise _error("E_GEOMETRY_INVALID", f"column width {width} is out of range 4..2000")
        props[P_WIDTH] = format_width(int(width))
    if data is not None and is_value_table(form, key):
        from .ordinary_form import validate_name
        validate_name(data)
        from .ordinary_form import encode_str
        # Update the slot in use: Данные if set (also when ДанныеФлажка is set too), else ДанныеФлажка.
        if _vt_slot(column, VT_DATA) or not _vt_slot(column, VT_CHECK):
            column[1][VT_DATA] = encode_str(data)
        else:
            column[1][VT_CHECK] = encode_str(data)
    elif data is not None and "." in data:
        raise _error("E_DATA_UNSUPPORTED", "a dotted data path is set by add_column; remove the column and add it again")
    elif data is not None and column[1][1][PATH_DATA] == NO_PATH and column[1][1][PATH_CHECK] != NO_PATH:
        attribute = _section_attribute(form, key, data)        # a checkbox column: ДанныеФлажка
        if not attribute.is_boolean:
            raise _error("E_DATA_TYPE", f"a checkbox column needs a Булево attribute; {data} is {_type_text(attribute)}")
        column[1][1][PATH_CHECK] = ["0", attribute.uuid]
    elif data is not None:
        path, pattern = _resolve_column_data(form, key, data)
        column[1][1][2] = path
        if pattern:
            props[P_PATTERN] = pattern
            cell = decode_cell(props)
            if cell is not None:
                cell[1] = copy.deepcopy(pattern)
                encode_cell(props, cell)
    for event, procedure in (events or {}).items():
        set_column_handler(form, key, name, event, procedure)
    return next(c for c in list_columns(form, key) if c.name == name)


def _set_vt_data(column: list, data: str, checkbox: bool) -> None:
    from .ordinary_form import encode_str
    column[1][VT_DATA] = '""' if checkbox else encode_str(data)
    column[1][VT_CHECK] = encode_str(data) if checkbox else '""'


def column_references(form: "OrdinaryForm", table: str, name: str) -> int:
    if not form.module_text:
        return 0
    pattern = re.compile(r"\.\s*Колонки\s*\.\s*" + re.escape(name) + r"(?![0-9A-Za-zА-Яа-яЁёІіЇїЄєҐґ_])"
                         r"|Колонки\s*\.\s*Найти\s*\(\s*\"" + re.escape(name) + r"\""
                         r"|\.\s*Ячейки\s*\.\s*" + re.escape(name) + r"(?![0-9A-Za-zА-Яа-яЁёІіЇїЄєҐґ_])", re.IGNORECASE)
    # Lines generated for a dotted column are removed together with it: they do not count.
    generated = re.compile(r"^[^\r\n]*" + re.escape(f"{COMPUTED_MARK}{table}.{name} = ") + r"[^\r\n]*$", re.MULTILINE)
    return len(pattern.findall(generated.sub("", form.module_text)))


def remove_column(form: "OrdinaryForm", table: str, name: str, *, force: bool = False) -> None:
    key = _table_key(form, table)
    index, _column = find_column(form, key, name)
    references = column_references(form, table, name)
    if references and not force:
        raise _error("E_ELEMENT_REFERENCED",
                     f"column {name} is used {references} time(s) in the form module (…Колонки.{name}); "
                     "fix the code first or remove with force")
    columns = columns_node(form, key)
    del columns[index]
    columns[0] = str(len(columns) - 1)
    _remove_row_output_lines(form, form._name(key), name)


def set_column_handler(form: "OrdinaryForm", key: str, name: str, event: str, procedure: str) -> None:
    from .ordinary_form import validate_name
    if event not in CELL_EVENTS:
        raise _error("E_HANDLER_UNSUPPORTED",
                     f"column event {event!r} is not supported; supported: {', '.join(CELL_EVENTS)}")
    validate_name(procedure)
    _index, column = find_column(form, key, name)
    props = column_props(column)
    cell = decode_cell(props)
    if cell is None:
        raise _error("E_HANDLER_UNSUPPORTED", f"column {name} has no own cell control; events are not available")
    event_id, params = CELL_EVENTS[event]
    slot = cell[CELL_EVENT_SLOT] if len(cell) > CELL_EVENT_SLOT else None
    if not (isinstance(slot, list) and slot and str(slot[0]).isdigit()):
        raise _error("E_HANDLER_UNSUPPORTED", f"column {name} has no event slot")
    cell[CELL_EVENT_SLOT] = _with_handler(slot, event_id, procedure)
    encode_cell(props, cell)
    form.ensure_procedure(procedure, params)


def _with_handler(slot: list, event_id: str, procedure: str) -> list:
    from .ordinary_form import EVENT_HANDLER_KIND, encode_str
    entries = [entry for entry in slot[1:] if entry[0] != event_id]
    synonym = ["1", "1", ['"ru"', encode_str(procedure)]]
    entries.append([event_id, EVENT_HANDLER_KIND,
                    ["3", encode_str(procedure),
                     ["1", encode_str(procedure), synonym, copy.deepcopy(synonym), copy.deepcopy(synonym),
                      ["4", "0", ["0"], '""', "-1", "-1", "1", "0", '""'], ["0", "0", "0"]]]])
    entries.sort(key=lambda entry: int(entry[0]))
    return [str(len(entries))] + entries


def _table_key(form: "OrdinaryForm", table: str) -> str:
    key = form.find(table)
    if form._type(key) != "Table":
        raise _error("E_TYPE_UNSUPPORTED", f"{table} is not a table field")
    return key


def bind_table(form: "OrdinaryForm", key: str, data: str) -> None:
    """Bind a table to a tabular section of the main attribute, or to a form attribute (value table)."""
    main = form._main_prop()
    element_id = form.data[key]["raw"][1]
    section = form.tabular_sections.get(data)
    if section is not None:
        if main is None:
            raise _error("E_DATA_CONTEXT", "the form has no main attribute")
        record = [element_id, ["2", [main["id"]], ["0", section.uuid]]]
        prop_name = main["name"]
        if section.object_type_id:
            form.data[key]["raw"][2][1] = ['"Pattern"', ['"#"', section.object_type_id]]
        elif form.data[key]["raw"][2][1] == VALUE_TABLE_PATTERN:
            form.data[key]["raw"][2][1] = ['"Pattern"']          # no type known (EPF): never a value table
        # The settings block follows the value type; a template cloned from another kind of table (value
        # table, dynamic list) would otherwise keep its own block -> "Ошибка формата потока".
        if form.data[key]["raw"][2][3][:1] != SECTION_SETTINGS[:1]:
            form.data[key]["raw"][2][3] = copy.deepcopy(SECTION_SETTINGS)
    else:
        prop = next((p for p in form.elem_json.get("props") or [] if p.get("name") == data), None)
        if prop is None:
            known = ", ".join([*form.tabular_sections, *(p["name"] for p in form.elem_json.get("props") or [])])
            raise _error("E_DATA_UNKNOWN", f"no tabular section or form attribute {data!r}; available: {known or 'none'}")
        pattern = prop["raw"][5] if len(prop.get("raw", [])) > 5 else None
        if pattern != VALUE_TABLE_PATTERN:
            raise _error("E_DATA_UNSUPPORTED", f"form attribute {data!r} is not a ТаблицаЗначений; "
                                               "only value-table form attributes can feed a table field")
        record = [element_id, ["1", [prop["id"]]]]
        prop_name = prop["name"]
        form.data[key]["raw"][2][1] = copy.deepcopy(VALUE_TABLE_PATTERN)
        form.data[key]["raw"][2][3] = copy.deepcopy(VALUE_TABLE_SETTINGS)
    sources = form.datasources
    kept = [entry for entry in sources[1:] if not (isinstance(entry, list) and entry and entry[0] == element_id)]
    kept.append(record)
    kept.sort(key=lambda entry: int(entry[0]))
    sources[:] = [str(len(kept))] + kept
    form.data[key]["prop"] = prop_name


def reset_columns(form: "OrdinaryForm", key: str, names: list[str] | None) -> None:
    """Replace the cloned template's columns with ``names`` (default: row number + all section attributes)."""
    template = copy.deepcopy(_column_template(form, key))
    columns = columns_node(form, key)
    del columns[1:]
    from .ordinary_form import encode_str
    column_props(template)[P_NAME] = encode_str(" template ")   # never clashes with a real name
    columns.append(template)          # keeps a clone source while the new columns are added
    columns[0] = "1"
    section = table_section(form, key)
    value_table = is_value_table(form, key)
    if names is None:
        names = [ROW_NUMBER, *section.attributes] if section is not None else []
    for name in names:
        if value_table:
            add_column(form, form._name(key), name, data=name)
            continue
        data = name if (name == ROW_NUMBER or (section is not None and name in section.attributes)) else None
        add_column(form, form._name(key), name, data=data, width=40 if name == ROW_NUMBER else None)
    index = next(i for i, c in enumerate(columns[1:], start=1) if c is template)
    del columns[index]
    columns[0] = str(len(columns) - 1)
