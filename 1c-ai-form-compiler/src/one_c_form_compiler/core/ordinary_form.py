"""Editable model of a 1C Ordinary Form (FormElements27) decoded by v8unpack.

v8unpack 1.2.13 (default, non ``auto_include`` mode) decodes ``Form.bin`` of
an ordinary form into three files:

* ``Form.json``      - form header; root panel raw record; data sources;
* ``Form.elem.json`` - ``tree`` (element order per container) and ``data``
  (every element's raw bracket record keyed by ``[panel/page/]name``);
* ``Form.obj.bsl``   - form module.

On build v8unpack recomputes element *counts* from ``tree`` but copies every
raw record verbatim. Everything else that ties elements together therefore has
to be maintained here:

* the form-wide element id counter (``form[0][0][1][1][1]``);
* per-edge bindings in ``raw[3]`` and the matching reverse lists on the target
  element or the container anchor index;
* tab order / z-order per page;
* the data-source table (``form[0][0][2][3]``) on removal;
* form size when the root page has to grow.

Layout record ``raw[3]`` (observed on platform 8.3 profile 27)::

    ["8", left, top, right, bottom, "1",
     edge0..edge5,                      # top, bottom, left, right, +2 extra
     n0, entries.., n1, .., n2, .., n3, ..   # who is bound to my border 0..3
     x, y, page_index, tab_index, z_index, "1", "1"]

    edge  = [kind, primary, secondary]
    bind  = ["2", target_id, target_border, offset]   # target "0" = container
    none  = ["2", "-1", "6", "0"]

Container anchor index (root: ``panel[1][1]``, nested Panel: ``raw[2][1]``)::

    [style, "25", n0, entries.., n1, .., n2, .., n3, ..]   # entry [kind, id, edge]

Every operation is validated by comparing integrity issues before and after
the change: an operation may never introduce a new inconsistency.
"""

from __future__ import annotations

import copy
import json
import re
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable

TYPE_UUIDS: dict[str, str] = {
    "Field": "381ed624-9217-4e63-85db-c4c3cb87daae",
    "CheckBox": "35af3d93-d7c7-4a2e-a8eb-bac87a1a3f26",
    "RadioBtn": "782e569a-79a7-4a4f-a936-b48d013936ec",
    "SelectField": "64483e7f-3833-48e2-8c75-2c31aac49f6e",
    "CommandPanel": "e69bf21d-97b2-4f37-86db-675aea9ec2cb",
    "Button": "6ff79819-710e-4145-97cd-1618da79e3e2",
    "Image": "151ef23e-6bb2-4681-83d0-35bc2217230c",
    "Group": "90db814a-c75f-4b54-bc96-df62e554d67d",
    "Table": "ea83fe3a-ac3c-4cce-8045-3dddf35b28b1",
    "TableField": "236a17b3-7f44-46d9-a907-75f9cdc61ab5",
    "Panel": "09ccdc77-ea1a-4a6d-ab1c-3435eada2433",
    "Label": "0fc7e20d-f241-460c-bdf4-5ad88e5474a5",
    "ListField": "19f8b798-314e-4b4e-8121-905b2a7a03f5",
    "Separator": "36e52348-5d60-4770-8e89-a16ed50a2006",
    "FieldHtml": "d92a805c-98ae-4750-9158-d9ce7cec2f20",
    "Indicator": "b1db1f86-abbb-4cf0-8852-fe6ae21650c2",
    "CalendarBox": "e3c063d8-ef92-41be-9c89-b70290b5368b",
    "TrackBar": "6c06cd5d-8481-4b6f-a90a-7a97a8bb8bef",
    "TextDocumentField": "14c4a229-bfc3-42fe-9ce1-2da049fd0109",
}
TYPE_BY_UUID = {value: key for key, value in TYPE_UUIDS.items()}

# Russian names used by the platform / Designer, for messages and AI context.
TYPE_RU = {
    "Label": "Надпись", "Field": "ПолеВвода", "Button": "Кнопка", "CheckBox": "Флажок",
    "Group": "РамкаГруппы", "Panel": "Панель", "Table": "ТабличноеПоле",
    "CommandPanel": "КоманднаяПанель", "Separator": "Разделитель", "Image": "Картинка",
    "RadioBtn": "Переключатель", "SelectField": "ПолеВыбора", "ListField": "ПолеСписка",
}

ADDABLE_TYPES = ("Label", "Field", "Button", "CheckBox", "Group", "Table")
REMOVABLE_TYPES = ("Label", "Field", "Button", "CheckBox", "Group", "Separator", "Image",
                   "RadioBtn", "SelectField", "Indicator", "Table", "Panel", "CommandPanel")
# Types whose caption lives at a verified path (see ``_caption_path``).
CAPTION_TYPES = ("Label", "Button", "Group", "CheckBox")

EVENT_HANDLER_KIND = "e1692cc2-605b-4535-84dd-28440238746c"
# Event ids observed on profile 27; names confirmed by handler naming in typical configurations.
# event name -> (event id, procedure parameters)
EVENTS: dict[str, dict[str, tuple[str, str]]] = {
    "Button": {"Нажатие": ("0", "Элемент")},
    "Label": {"Нажатие": ("0", "Элемент")},
    "CheckBox": {"ПриИзменении": ("2147483647", "Элемент")},
    "Field": {
        "НачалоВыбораИзСписка": ("1", "Элемент, СтандартнаяОбработка"),
        "НачалоВыбора": ("2", "Элемент, СтандартнаяОбработка"),
        "ОбработкаВыбора": ("7", "Элемент, ВыбранноеЗначение, СтандартнаяОбработка"),
        "ПриИзменении": ("2147483647", "Элемент"),
    },
}
from .table_field import TABLE_EVENTS  # noqa: E402  (table_field imports this module lazily)

EVENTS["Table"] = dict(TABLE_EVENTS)

# Form-level events (ids observed on typical forms): 700xx in the form's own list, 800xx - write events of
# the edited object (list layout known for catalog element forms, CATALOG_OBJECT_EVENTS_KIND).
FORM_EVENTS: dict[str, tuple[str, str]] = {
    "ПередОткрытием": ("70000", "Отказ, СтандартнаяОбработка"),
    "ПриОткрытии": ("70001", ""),
    "ПередЗакрытием": ("70002", "Отказ, СтандартнаяОбработка"),
    "ПриЗакрытии": ("70003", ""),
    "ОбработкаВыбора": ("70004", "ЗначениеВыбора, Источник"),
    "ОбработкаЗаписиНовогоОбъекта": ("70006", "Объект, Источник"),
    "ОбработкаОповещения": ("70007", "ИмяСобытия, Параметр, Источник"),
    "ПриПовторномОткрытии": ("70008", "СтандартнаяОбработка"),
    "ОбновлениеОтображения": ("70009", ""),
    "ВнешнееСобытие": ("70010", "Источник, Событие, Данные"),
    "ПередЗаписью": ("80000", "Отказ"),
    "ПриЗаписи": ("80001", "Отказ"),
    "ПослеЗаписи": ("80002", ""),
}
CATALOG_OBJECT_EVENTS_KIND = "c5251418-7f99-4a2f-ba62-7f3c9e7bf914"
EVENT_SLOT = {"Button": 2, "Label": 2, "CheckBox": 2, "Field": 4, "Table": 4}
DEFAULT_EVENT = {"Button": "Нажатие", "Label": "Нажатие", "CheckBox": "ПриИзменении", "Field": "ПриИзменении"}
DATA_BINDABLE = ("Field", "CheckBox")
# ТолькоПросмотр (confirmed in 1C, see docs/field-format.md): an input field keeps it in raw[2][2][0][13]
# ("1"/"0"); a table field keeps the inverse in bit 10 of the property mask raw[2][2][1][1] (set = editable).
READ_ONLY_SLOT = 13
TABLE_EDITABLE_BIT = 1 << 10
NONE_BIND = ["2", "-1", "6", "0"]
CHILDREN_MARKER = "Дочерние элементы отдельно"     # v8unpack: a panel's elements are kept in the tree
NO_ELEMENT_ID = "4294967295"                       # "none" in id fields (default button, action source)
TOP, BOTTOM, LEFT, RIGHT = 0, 1, 2, 3
BESIDE_GAP = 4                  # px between an element and the one placed beside it
COLUMN_GAP = 12                 # px between the left column and the middle of a two-column page
MIN_FORM_SIZE, MAX_FORM_SIZE = 50, 3000
_IDENT = re.compile(r"^[A-Za-zА-Яа-яЁёІіЇїЄєҐґ_][0-9A-Za-zА-Яа-яЁёІіЇїЄєҐґ_]*$")


class FormModelError(ValueError):
    """Stable, user-facing model error."""

    def __init__(self, code: str, message: str) -> None:
        self.code = code
        super().__init__(message)


# --------------------------------------------------------------------------- strings

def encode_str(text: str) -> str:
    """1C bracket string literal: wrapped in quotes, inner quotes doubled."""
    return '"' + text.replace('"', '""') + '"'


def decode_str(token: str) -> str:
    if len(token) >= 2 and token[0] == '"' and token[-1] == '"':
        return token[1:-1].replace('""', '"')
    return token


def is_loc(node: Any) -> bool:
    """Localized string: ["1", n, ['"lang"', '"text"'] * n]."""
    return (isinstance(node, list) and len(node) >= 2 and node[0] == "1"
            and isinstance(node[1], str) and node[1].isdigit() and len(node) == 2 + int(node[1])
            and all(isinstance(item, list) and len(item) == 2 and all(isinstance(s, str) for s in item)
                    for item in node[2:]))


def loc_to_dict(node: Any) -> dict[str, str]:
    if not is_loc(node):
        return {}
    return {decode_str(lang): decode_str(text) for lang, text in node[2:]}


def make_loc(texts: dict[str, str]) -> list:
    return ["1", str(len(texts))] + [[encode_str(lang), encode_str(text)] for lang, text in texts.items()]


def validate_name(name: str) -> None:
    if not isinstance(name, str) or not _IDENT.match(name) or len(name) > 80:
        raise FormModelError("E_NAME_INVALID", f"invalid 1C identifier: {name!r}")


# --------------------------------------------------------------------------- geometry

@dataclass(frozen=True, slots=True)
class Rect:
    left: int
    top: int
    right: int
    bottom: int

    @property
    def width(self) -> int:
        return self.right - self.left

    @property
    def height(self) -> int:
        return self.bottom - self.top

    def edge(self, index: int) -> int:
        return (self.top, self.bottom, self.left, self.right)[index]

    def overlaps(self, other: "Rect") -> bool:
        return (self.left < other.right and other.left < self.right
                and self.top < other.bottom and other.top < self.bottom)

    @classmethod
    def from_xywh(cls, left: int, top: int, width: int, height: int) -> "Rect":
        return cls(int(left), int(top), int(left) + int(width), int(top) + int(height))

    def as_dict(self) -> dict[str, int]:
        return {"left": self.left, "top": self.top, "width": self.width, "height": self.height}


@dataclass(slots=True)
class Layout:
    """Parsed view of ``raw[3]``; ``write`` re-serializes into the same list."""

    raw: list
    edges: list            # 6 edge records (mutable references into raw)
    reverse: list[list]    # 4 lists of entries
    tail: list             # 7 trailing scalars

    @classmethod
    def parse(cls, raw3: list) -> "Layout":
        if not isinstance(raw3, list) or len(raw3) < 23 or raw3[0] != "8":
            raise FormModelError("E_LAYOUT_UNSUPPORTED", "element layout record has an unknown format")
        edges = raw3[6:12]
        for edge in edges:
            if not (isinstance(edge, list) and len(edge) == 3 and isinstance(edge[1], list) and len(edge[1]) == 4):
                raise FormModelError("E_LAYOUT_UNSUPPORTED", "element edge binding has an unknown format")
        reverse: list[list] = []
        index = 12
        for _ in range(4):
            count = int(raw3[index])
            reverse.append(list(raw3[index + 1:index + 1 + count]))
            index += 1 + count
        tail = raw3[index:]
        if len(tail) != 7:
            raise FormModelError("E_LAYOUT_UNSUPPORTED", "element layout tail has an unknown format")
        return cls(raw3, edges, reverse, tail)

    @property
    def rect(self) -> Rect:
        return Rect(int(self.raw[1]), int(self.raw[2]), int(self.raw[3]), int(self.raw[4]))

    def set_rect(self, rect: Rect) -> None:
        self.raw[1:5] = [str(rect.left), str(rect.top), str(rect.right), str(rect.bottom)]

    @property
    def page_index(self) -> int:
        return int(self.tail[2])

    @property
    def tab_index(self) -> int:
        return int(self.tail[3])

    @property
    def z_index(self) -> int:
        return int(self.tail[4])

    def write(self) -> None:
        body: list = []
        for entries in self.reverse:
            body.append(str(len(entries)))
            body.extend(entries)
        self.raw[12:] = body + self.tail

    def bindings(self) -> Iterable[tuple[int, int, list]]:
        """Yield (edge_index, slot, bind) for every active binding."""
        for edge_index, edge in enumerate(self.edges):
            for slot in (1, 2):
                bind = edge[slot]
                if isinstance(bind, list) and len(bind) == 4 and bind[1] != "-1":
                    yield edge_index, slot, bind


def _anchor_group(border: str | int) -> int:
    value = int(border)
    return 1 if value > 3 else value


def _entry_edge(edge_index: int) -> str:
    return str(1 if edge_index > 3 else edge_index)


# --------------------------------------------------------------------------- model

@dataclass(slots=True)
class Container:
    key: str                 # "" for the form itself, else Panel element key
    name: str
    raw_index: list          # anchor list [style, "25", n0, ...]
    pages: list[str]
    tree: list               # list receiving child tree nodes
    element_id: str          # "0" sentinel for the form

    def page_key(self, page: str) -> str:
        return "/".join(part for part in (self.key, page) if part)


@dataclass(slots=True)
class ControlInfo:
    key: str
    name: str
    type: str
    id: int
    container: str
    page: str
    rect: Rect
    tab_index: int
    z_index: int
    caption: dict[str, str] = field(default_factory=dict)
    tooltip: dict[str, str] = field(default_factory=dict)
    data_bound: bool = False
    value_type: list | None = None
    handlers: list[str] = field(default_factory=list)
    data: str | None = None
    read_only: bool = False

    def as_dict(self) -> dict[str, Any]:
        result: dict[str, Any] = {
            "name": self.name, "type": self.type, "type_ru": TYPE_RU.get(self.type, self.type),
            "id": self.id, "container": self.container or "<form>", "page": self.page,
            **self.rect.as_dict(), "tab_index": self.tab_index,
        }
        if self.caption:
            result["caption"] = self.caption
        if self.tooltip:
            result["tooltip"] = self.tooltip
        if self.data:
            result["data"] = self.data
        elif self.data_bound:
            result["data_bound"] = True
        if self.value_type is not None:
            result["value_type"] = format_value_type(self.value_type)
        if self.handlers:
            result["handlers"] = self.handlers
        if self.read_only:
            result["read_only"] = True
        return result


def format_value_type(pattern: list | None) -> str | None:
    if not pattern:
        return None
    parts = []
    for item in pattern[1:]:
        if not isinstance(item, list) or not item:
            continue
        kind = decode_str(item[0])
        if kind == "S":
            parts.append("Строка" if len(item) == 1 or item[1] == "0" else f"Строка({item[1]})")
        elif kind == "N":
            parts.append(f"Число({item[1]},{item[2]})" if len(item) >= 3 else "Число")
        elif kind == "D":
            parts.append("Дата" if len(item) > 1 else "ДатаВремя")
        elif kind == "B":
            parts.append("Булево")
        elif kind == "#":
            parts.append("Ссылка")
        else:
            parts.append(kind)
    return ", ".join(parts) or None


_TYPE_SPEC = re.compile(
    r"^\s*(?:(?P<s>строка|string)(?:\((?P<slen>\d+)\))?"
    r"|(?P<n>число|number)(?:\((?P<nd>\d+)\s*,\s*(?P<nf>\d+)\))?"
    r"|(?P<d>дата|date)|(?P<dt>датавремя|datetime)|(?P<b>булево|boolean|bool))\s*$",
    re.IGNORECASE)


def parse_value_type(spec: str) -> list:
    """'Строка(50)' | 'Число(15,2)' | 'Дата' | 'ДатаВремя' | 'Булево' -> Pattern record."""
    match = _TYPE_SPEC.match(spec or "")
    if not match:
        raise FormModelError("E_VALUE_TYPE_UNSUPPORTED",
                             f"unsupported value type {spec!r}; use Строка(N), Число(D,F), Дата, ДатаВремя, Булево "
                             "or like:<ИмяПоля>")
    if match.group("s"):
        length = match.group("slen")
        return ['"Pattern"', ['"S"', length, "0"] if length and length != "0" else ['"S"']]
    if match.group("n"):
        digits, frac = match.group("nd") or "10", match.group("nf") or "0"
        if int(frac) > int(digits):
            raise FormModelError("E_VALUE_TYPE_UNSUPPORTED", "number fraction exceeds digits")
        return ['"Pattern"', ['"N"', digits, frac, "0"]]
    if match.group("d"):
        return ['"Pattern"', ['"D"', '"D"']]
    if match.group("dt"):
        return ['"Pattern"', ['"D"']]
    return ['"Pattern"', ['"B"']]


def _attribute_path(attribute) -> list:
    """Data path of an object attribute: ``["0", uuid]``, or ``["-3"]`` for a standard attribute."""
    return [attribute.uuid] if attribute.uuid.startswith("-") else ["0", attribute.uuid]


class OrdinaryForm:
    """Mutable decoded ordinary form. Call :meth:`save` to persist."""

    FORM_JSON = "Form.json"
    ELEM_JSON = "Form.elem.json"
    MODULE = "Form.obj.bsl"

    def __init__(self, form_dir: Path, form_json: dict, elem_json: dict, module_text: str | None) -> None:
        self.form_dir = Path(form_dir)
        self.form_json = form_json
        self.elem_json = elem_json
        self.module_text = module_text
        self._module_changed = False
        # Object attributes (name -> ObjectAttribute) for data binding; set by the edit session.
        self.object_attributes: dict = {}
        self.object_type_id: str | None = None
        # Tabular sections of a configuration object (name -> TabularSection); set by OformSession.
        self.tabular_sections: dict = {}
        self.warnings: list[str] = []
        # Bottom of the last row placed in this session, per (container, page).
        self._row_cursor: dict[tuple[str, str], int] = {}
        try:
            self._root = form_json["form"][0][0]
            header = self._root[1]
            if header[2][0] != TYPE_UUIDS["Panel"]:
                raise KeyError("root panel")
            self._header = header
            self._root_panel = header[2]
            int(header[1][1])
            int(header[3]), int(header[4])
        except (KeyError, IndexError, TypeError, ValueError):
            raise FormModelError("E_FORM_PROFILE_UNSUPPORTED",
                                 "Form.json is not an ordinary form of the supported profile (FormElements27)") from None
        if not isinstance(elem_json, dict) or not {"tree", "data"} <= set(elem_json):
            raise FormModelError("E_FORM_PROFILE_UNSUPPORTED", "Form.elem.json lacks tree/data sections")
        # Repairs of the id bookkeeping made on load: written only with an edit and reported by the edit
        # sessions in their publish result ("repairs").
        self.repairs: list[str] = self._repair_ids()
        self._baseline_issues = set(self.integrity_issues())

    # ------------------------------------------------------------------ io
    @classmethod
    def load(cls, form_dir: Path) -> "OrdinaryForm":
        form_dir = Path(form_dir)
        try:
            form_json = json.loads((form_dir / cls.FORM_JSON).read_text(encoding="utf-8-sig"))
            elem_json = json.loads((form_dir / cls.ELEM_JSON).read_text(encoding="utf-8-sig"))
        except FileNotFoundError:
            raise FormModelError("E_FORM_NOT_ORDINARY",
                                 f"{form_dir.name}: no Form.elem.json - not an ordinary (FormElements27) form") from None
        except ValueError:
            raise FormModelError("E_FORM_JSON_INVALID", f"{form_dir.name}: decoded form JSON is invalid") from None
        module_path = form_dir / cls.MODULE
        module_text = module_path.read_text(encoding="utf-8-sig") if module_path.is_file() else None
        return cls(form_dir, form_json, elem_json, module_text)

    def save(self) -> None:
        issues = self.new_issues()
        if issues:
            raise FormModelError("E_INTEGRITY", "refusing to save inconsistent form: " + "; ".join(issues[:5]))
        self._normalize_children_markers()
        for name, payload in ((self.FORM_JSON, self.form_json), (self.ELEM_JSON, self.elem_json)):
            with open(self.form_dir / name, "w", encoding="utf-8") as stream:
                json.dump(payload, stream, ensure_ascii=False, indent=2)
        if self._module_changed and self.module_text is not None:
            # v8unpack reads modules as utf-8-sig and writes them with a BOM.
            with open(self.form_dir / self.MODULE, "w", encoding="utf-8-sig", newline="") as stream:
                stream.write(self.module_text)

    def _normalize_children_markers(self) -> None:
        """A nested panel's child list as v8unpack decodes it: the "children elsewhere" marker when the panel
        has elements, ``["0"]`` when it has none (decoding an empty panel leaves the count as is)."""
        for key, container in self.containers().items():
            if not key:
                continue
            raw = self.data[key]["raw"]
            if raw[-1] in (["0"], [CHILDREN_MARKER]):
                raw[-1] = [CHILDREN_MARKER] if container.tree else ["0"]

    # ------------------------------------------------------------------ basics
    @property
    def data(self) -> dict:
        return self.elem_json["data"]

    @property
    def title(self) -> dict[str, str]:
        return loc_to_dict(self._header[1][0])

    def set_title(self, title: str | dict[str, str]) -> None:
        current = loc_to_dict(self._header[1][0])
        texts = self._texts(title, current)
        self._header[1][0] = make_loc({"#": current["#"], **texts} if "#" in current else texts)

    @property
    def size(self) -> tuple[int, int]:
        return int(self._header[3]), int(self._header[4])

    @property
    def id_counter(self) -> int:
        return int(self._header[1][1])

    def _referenced_ids(self) -> list[int]:
        """Every element id the form mentions: elements, edge bindings, reverse lists, panel indexes, data
        sources, command panel action sources and the default button ("none" excluded)."""
        values: list = []
        for key in self.element_keys():
            raw = self.data[key]["raw"]
            values.append(raw[1])
            if self._type(key) == "CommandPanel":
                values.append(_action_source(raw))
            try:
                layout = self._layout(key)
            except (FormModelError, ValueError, IndexError):
                continue
            values += [bind[1] for _edge, _slot, bind in layout.bindings()]
            values += [entry[1] for entries in layout.reverse for entry in entries
                       if isinstance(entry, list) and len(entry) > 1]
        for container in self.containers().values():
            values += [entry[1] for entries in _panel_index_groups(container.raw_index) for entry in entries
                       if isinstance(entry, list) and len(entry) > 1]
        values += [entry[0] for entry in self.datasources[1:] if isinstance(entry, list) and entry]
        values.append(self.default_button_id)
        return [int(value) for value in values
                if isinstance(value, str) and value.isdigit() and value != NO_ELEMENT_ID]

    def _allocate_id(self) -> str:
        """The id of a new element: above the counter and above every id the form mentions (a stale anchor
        of a deleted element must not attach to the new one); the counter (header) is moved to it."""
        new_id = str(max([self.id_counter, *self._referenced_ids()]) + 1)
        self._header[1][1] = new_id
        return new_id

    def _repair_ids(self) -> list[str]:
        """Bring the id bookkeeping in order before editing: the counter not below the highest element id;
        reverse-list and panel-index entries of elements that do not exist are dropped (self-bindings and
        entries of existing elements are kept as they are)."""
        repairs: list[str] = []
        keys = self.element_keys()
        ids = {self.data[key]["raw"][1] for key in keys}
        numeric = [int(value) for value in ids if isinstance(value, str) and value.isdigit()]
        if numeric and max(numeric) > self.id_counter:
            repairs.append(f"id counter {self.id_counter} raised to {max(numeric)} (the highest element id)")
            self._header[1][1] = str(max(numeric))
        for key in keys:
            try:
                layout = self._layout(key)
            except (FormModelError, ValueError, IndexError):
                continue
            dropped = sorted({entry[1] for entries in layout.reverse for entry in entries if _dangling(entry, ids)},
                             key=int)
            if dropped:
                layout.reverse = [[entry for entry in entries if not _dangling(entry, ids)]
                                  for entries in layout.reverse]
                layout.write()
                repairs.append(f"{self._name(key)}: dropped anchors of missing elements {', '.join(dropped)}")
        try:
            containers = self.containers()
        except (KeyError, IndexError, TypeError):
            return repairs
        for container in containers.values():
            groups = _panel_index_groups(container.raw_index)
            dropped = sorted({entry[1] for entries in groups for entry in entries if _dangling(entry, ids)}, key=int)
            if dropped:
                _panel_index_write(container.raw_index,
                                   [[entry for entry in entries if not _dangling(entry, ids)] for entries in groups])
                repairs.append(f"{container.name}: dropped panel anchors of missing elements {', '.join(dropped)}")
        return repairs

    @property
    def default_button_id(self) -> str:
        return self._header[1][2]

    @property
    def datasources(self) -> list:
        return self._root[2][3]

    def languages(self) -> list[str]:
        langs = [lang for lang in self.title if lang != "#"]
        return langs or ["ru"]

    def element_keys(self) -> list[str]:
        return [key for key, value in self.data.items() if isinstance(value, dict) and "id" in value]

    def _layout(self, key: str) -> Layout:
        return Layout.parse(self.data[key]["raw"][3])

    def _type(self, key: str) -> str:
        return TYPE_BY_UUID.get(self.data[key]["raw"][0], "Unknown")

    def _name(self, key: str) -> str:
        return decode_str(self.data[key]["raw"][-2][1])

    # ------------------------------------------------------------------ containers
    def containers(self) -> dict[str, Container]:
        result = {"": Container("", "<form>", self._root_panel[1][1], list(self.data.get("-pages-", [""])),
                                self.elem_json["tree"], "0")}

        def walk(nodes: list, path: str) -> None:
            for node in nodes:
                key = "/".join(part for part in (path, node.get("page", ""), node["name"]) if part)
                if node.get("type") == "Panel" and "child" in node:
                    raw = self.data[key]["raw"]
                    result[key] = Container(key, node["name"], raw[2][1],
                                            list(self.data.get(f"{key}/-pages-", [])), node["child"], raw[1])
                    walk(node["child"], key)

        walk(self.elem_json["tree"], "")
        return result

    def container_of(self, key: str) -> tuple[Container, str]:
        page_key = self.data[key]["page"]
        for container in self.containers().values():
            for page in container.pages:
                if container.page_key(page) == page_key:
                    return container, page
        raise FormModelError("E_CONTAINER_UNKNOWN", f"cannot resolve container of {key}")

    def resolve_container(self, container: str | None, page: str | None) -> tuple[Container, str]:
        containers = self.containers()
        name = (container or "").strip()
        if name in {"", "<form>", "form", "Форма"}:
            target = containers[""]
        else:
            matches = [c for c in containers.values() if c.key and (c.name == name or c.key == name)]
            if len(matches) != 1:
                raise FormModelError("E_CONTAINER_UNKNOWN", f"panel {name!r} not found")
            target = matches[0]
        if page is None or page == "":
            if len(target.pages) != 1:
                raise FormModelError("E_PAGE_REQUIRED",
                                     f"panel {target.name} has pages {target.pages}; specify page")
            return target, target.pages[0]
        if page not in target.pages:
            raise FormModelError("E_PAGE_UNKNOWN", f"page {page!r} not found in {target.name}: {target.pages}")
        return target, page

    def page_elements(self, container: Container, page: str) -> list[str]:
        page_key = container.page_key(page)
        return [key for key in self.element_keys() if self.data[key]["page"] == page_key]

    def container_size(self, container: Container, page: str) -> tuple[int, int] | None:
        """Client size of a container page, derived from right/bottom bindings."""
        if not container.key:
            return self.size
        width, height = self._bound_size(container, page)
        if width is None or height is None:
            # The client area is the same on every page of a panel: take it from the bindings of the other
            # pages, then from the page guides (right/bottom guide coordinate + its margin), then from the
            # panel rectangle minus the tab frame. A new page (add_page / add_panel) has no elements yet.
            for other in container.pages:
                if width is not None and height is not None:
                    break
                if other == page:
                    continue
                other_width, other_height = self._bound_size(container, other)
                width = width if width is not None else other_width
                height = height if height is not None else other_height
        if width is None or height is None:
            from .panel import border_guides, frame_size, guide_size
            panel_rect = self._layout(container.key).rect
            guides = border_guides(self, container)
            own = str(container.pages.index(page))
            for page_id in [own, *guides]:
                found = guide_size(guides.get(page_id, {}))
                if found and found[0] <= panel_rect.width and found[1] <= panel_rect.height:
                    width = width if width is not None else found[0]
                    height = height if height is not None else found[1]
                    break
            frame = frame_size(self.data[container.key]["raw"])
            width = width if width is not None else panel_rect.width - frame[0]
            height = height if height is not None else panel_rect.height - frame[1]
        return max(width, 0), max(height, 0)

    def _bound_size(self, container: Container, page: str) -> tuple[int | None, int | None]:
        """Client size of a page derived from right/bottom bindings of its elements to the container."""
        width = height = None
        for key in self.page_elements(container, page):
            layout = self._layout(key)
            rect = layout.rect
            for edge_index, _slot, bind in layout.bindings():
                if bind[1] != "0" or edge_index > 3:
                    continue
                coord, border, offset = rect.edge(edge_index), int(bind[2]), int(bind[3])
                if border == RIGHT and width is None:
                    width = coord - offset
                elif border == BOTTOM and height is None:
                    height = coord - offset
        return width, height

    # ------------------------------------------------------------------ lookup
    def find(self, name: str) -> str:
        matches = [key for key in self.element_keys() if self._name(key) == name]
        if not matches:
            raise FormModelError("E_ELEMENT_NOT_FOUND", f"element {name!r} not found")
        if len(matches) > 1:
            raise FormModelError("E_ELEMENT_AMBIGUOUS", f"element name {name!r} is not unique")
        return matches[0]

    def has_element(self, name: str) -> bool:
        return any(self._name(key) == name for key in self.element_keys())

    def info(self, key: str) -> ControlInfo:
        raw = self.data[key]["raw"]
        element_type = self._type(key)
        layout = self._layout(key)
        container, page = self.container_of(key)
        caption_path = _caption_path(raw, element_type)
        caption = loc_to_dict(_get(raw, caption_path)) if caption_path else {}
        caption.pop("#", None)
        tooltip_node = _tooltip_node(raw)
        tooltip = loc_to_dict(tooltip_node) if tooltip_node is not None else {}
        value_type = None
        if element_type == "Field" and isinstance(raw[2], list) and len(raw[2]) > 1 \
                and isinstance(raw[2][1], list) and raw[2][1][:1] == ['"Pattern"']:
            value_type = raw[2][1]
        binding = self._datasource(raw[1])
        handlers = [decode_str(entry[2][1]) for entries in _event_lists(raw[2]) for entry in entries[1:]
                    if isinstance(entry[2], list) and len(entry[2]) > 1]
        if element_type == "Table":
            from .table_field import table_data_name
            data_name = table_data_name(self, key)
        else:
            data_name = self._data_name(binding)
        return ControlInfo(key=key, name=self._name(key), type=element_type, id=int(raw[1]),
                           container=container.key, page=page, rect=layout.rect,
                           tab_index=layout.tab_index, z_index=layout.z_index, caption=caption,
                           tooltip=tooltip, data_bound=binding is not None, value_type=value_type,
                           handlers=handlers, data=data_name, read_only=self.read_only(key) is True)

    # ------------------------------------------------------------------ data binding
    def bound_attributes(self) -> list[tuple]:
        """(ObjectAttribute, owner type id) for every object-attribute binding on the form."""
        main_prop = self._main_prop()
        owner = None
        if main_prop and len(main_prop.get("raw", [])) > 5:
            pattern = main_prop["raw"][5]
            if isinstance(pattern, list) and len(pattern) > 1 and pattern[1][:1] == ['"#"']:
                owner = pattern[1][1]
        by_uuid = {attribute.uuid: attribute for attribute in self.object_attributes.values()}
        result = []
        for entry in self.datasources[1:]:
            try:
                path = entry[1][2]
            except (IndexError, TypeError):
                continue
            if owner and isinstance(path, list) and len(path) == 2 and path[0] == "0" and path[1] in by_uuid:
                result.append((by_uuid[path[1]], owner))
        return result

    def _main_prop(self) -> dict | None:
        """The form's main attribute (ОбработкаОбъект / ОтчетОбъект ...): id is stored in root[2][0]."""
        try:
            main_id = self._root[2][0][0]
        except (IndexError, TypeError):
            return None
        return next((prop for prop in self.elem_json.get("props") or [] if prop.get("id") == main_id), None)

    def events(self, key: str) -> dict[str, str]:
        """Assigned handlers: event name (or #id when unknown) -> procedure."""
        element_type = self._type(key)
        names = {event_id: name for name, (event_id, _params) in EVENTS.get(element_type, {}).items()}
        result = {}
        for entries in _event_lists(self.data[key]["raw"][2]):
            for entry in entries[1:]:
                if isinstance(entry[2], list) and len(entry[2]) > 1:
                    result[names.get(entry[0], "#" + entry[0])] = decode_str(entry[2][1])
        return result

    def _datasource(self, element_id: str) -> list | None:
        for entry in self.datasources[1:]:
            if isinstance(entry, list) and entry and entry[0] == element_id:
                return entry
        return None

    def _data_name(self, entry: list | None) -> str | None:
        if not entry:
            return None
        try:
            path = entry[1][2]
        except (IndexError, TypeError):
            return None
        if isinstance(path, list) and path:
            for attribute in self.object_attributes.values():
                if _attribute_path(attribute) == path:
                    return attribute.name
        return None

    def _bind_data(self, key: str, attribute_name: str) -> None:
        element_type = self._type(key)
        if element_type not in DATA_BINDABLE:
            raise FormModelError("E_DATA_UNSUPPORTED", f"only {', '.join(DATA_BINDABLE)} can be bound to data")
        attribute = self.object_attributes.get(attribute_name)
        if attribute is None:
            known = ", ".join(sorted(self.object_attributes)) or "none"
            raise FormModelError("E_DATA_UNKNOWN",
                                 f"object attribute {attribute_name!r} not found; available: {known}")
        if element_type == "CheckBox" and not attribute.is_boolean:
            raise FormModelError("E_DATA_TYPE", f"CheckBox needs a Булево attribute; {attribute_name} is not")
        main_prop = self._main_prop()
        prop_type = main_prop["raw"][5] if main_prop and len(main_prop.get("raw", [])) > 5 else None
        if self.object_type_id and prop_type != ['"Pattern"', ['"#"', self.object_type_id]]:
            warning = ("the form's main attribute has another object's type (form copied from a configuration "
                       "object?): the binding resolves only where that type has the same attribute; "
                       "check with --runtime-check")
            if warning not in self.warnings:
                self.warnings.append(warning)
        raw = self.data[key]["raw"]
        element_id = raw[1]
        if main_prop is None:
            raise FormModelError("E_DATA_CONTEXT", "the form has no main attribute")
        # Same record Designer writes for a field bound to an object attribute.
        record = [element_id, ["2", [main_prop["id"]], _attribute_path(attribute)]]
        sources = self.datasources
        kept = [entry for entry in sources[1:] if not (isinstance(entry, list) and entry and entry[0] == element_id)]
        kept.append(record)
        kept.sort(key=lambda entry: int(entry[0]))
        sources[:] = [str(len(kept))] + kept
        # v8unpack derives "prop" (form attribute name) from the data source on decode.
        if main_prop is not None:
            self.data[key]["prop"] = main_prop["name"]
        if element_type == "Field" and attribute.pattern:
            raw[2][1] = copy.deepcopy(attribute.pattern)

    def controls(self) -> list[ControlInfo]:
        return [self.info(key) for key in self.element_keys()]

    # ------------------------------------------------------------------ integrity
    def integrity_issues(self) -> list[str]:
        issues: list[str] = []
        keys = self.element_keys()
        names = Counter(self._name(key) for key in keys)
        issues += [f"duplicate name {name}" for name, count in names.items() if count > 1]
        ids = Counter(self.data[key]["raw"][1] for key in keys)
        issues += [f"duplicate id {value}" for value, count in ids.items() if count > 1]
        if ids and max(int(value) for value in ids) > self.id_counter:
            issues.append("id counter below max element id")
        by_id = {self.data[key]["raw"][1]: key for key in keys}
        containers = self.containers()
        container_by_page = {c.page_key(p): c for c in containers.values() for p in c.pages}
        # Anchors are compared as multisets: a repeated entry (e.g. a stale one that a new id brought back
        # to life) is reported as "duplicate ... anchor".
        expected_reverse: Counter = Counter()
        expected_panel: Counter = Counter()
        pages: dict[str, list[Layout]] = {}
        for key in keys:
            raw = self.data[key]["raw"]
            try:
                layout = self._layout(key)
            except (FormModelError, ValueError, IndexError):
                issues.append(f"bad layout {key}")
                continue
            pages.setdefault(self.data[key]["page"], []).append(layout)
            container = container_by_page.get(self.data[key]["page"])
            if container is None:
                issues.append(f"element {key} on unknown page")
                continue
            for edge_index, _slot, bind in layout.bindings():
                target = bind[1]
                entry = (raw[1], _entry_edge(edge_index))
                if target == "0":
                    expected_panel[(container.key, _anchor_group(bind[2]), *entry)] += 1
                elif target in by_id:
                    expected_reverse[(target, _anchor_group(bind[2]), *entry)] += 1
                elif int(target) > 0:
                    issues.append(f"{key} bound to missing element {target}")
        actual_reverse: Counter = Counter()
        for key in keys:
            try:
                layout = self._layout(key)
            except (FormModelError, ValueError, IndexError):
                continue
            for group, entries in enumerate(layout.reverse):
                for entry in entries:
                    actual_reverse[(self.data[key]["raw"][1], group, entry[1], entry[2])] += 1
        actual_panel: Counter = Counter()
        for container in containers.values():
            for group, entries in enumerate(_panel_index_groups(container.raw_index)):
                for entry in entries:
                    actual_panel[(container.key, group, entry[1], entry[2])] += 1
        # An element's entry about itself (its own edge bound to its other edge) is how 1C writes such a
        # binding, often twice: accepted as long as it is there.
        issues += _anchor_issues("reverse", expected_reverse, actual_reverse, lambda item: item[0] == item[2])
        issues += _anchor_issues("panel", expected_panel, actual_panel, lambda item: False)
        for page_key, layouts in pages.items():
            if sorted(item.tab_index for item in layouts) != list(range(len(layouts))):
                issues.append(f"tab order not dense on page {page_key or '<form>'}")
            if sorted(item.z_index for item in layouts) != list(range(1, len(layouts) + 1)):
                issues.append(f"z-order not dense on page {page_key or '<form>'}")
        for entry in self.datasources[1:]:
            if isinstance(entry, list) and entry and entry[0] not in by_id:
                issues.append(f"datasource for missing element {entry[0]}")
        if self.default_button_id not in {"0", NO_ELEMENT_ID, *by_id}:
            issues.append("default button refers to missing element")
        return issues

    def new_issues(self) -> list[str]:
        return [issue for issue in self.integrity_issues() if issue not in self._baseline_issues]

    # ------------------------------------------------------------------ anchors helpers
    def _panel_index_add(self, container: Container, group: int, entry: list) -> None:
        groups = _panel_index_groups(container.raw_index)
        groups[group].append(entry)
        _panel_index_write(container.raw_index, groups)

    def _panel_index_remove(self, container: Container, element_id: str) -> None:
        groups = _panel_index_groups(container.raw_index)
        groups = [[entry for entry in entries if entry[1] != element_id] for entries in groups]
        _panel_index_write(container.raw_index, groups)

    def _key_by_id(self, element_id: str) -> str | None:
        for key in self.element_keys():
            if self.data[key]["raw"][1] == element_id:
                return key
        return None

    def _bind_to_container(self, layout: Layout, element_id: str, container: Container,
                           edge_index: int, rect: Rect, border: int | None = None,
                           size: tuple[int, int] | None = None) -> None:
        """Bind one edge to a container border; offset = edge coordinate - border coordinate."""
        if border is None:
            border = TOP if edge_index in (TOP, BOTTOM) else LEFT
        width, height = size or (0, 0)
        border_coord = {TOP: 0, LEFT: 0, BOTTOM: height, RIGHT: width}[border]
        layout.edges[edge_index][1] = ["2", "0", str(border), str(rect.edge(edge_index) - border_coord)]
        self._panel_index_add(container, border, [layout.edges[edge_index][0], element_id, _entry_edge(edge_index)])

    # ------------------------------------------------------------------ operations
    def add_page(self, container: str, name: str, caption: str | dict[str, str] | None = None) -> dict[str, Any]:
        """Append a page (tab) to a panel: a clone of its last page record, renamed, visible, empty."""
        validate_name(name)
        matches = [c for c in self.containers().values()
                   if c.key and (c.name == (container or "").strip() or c.key == container)]
        if len(matches) != 1:
            raise FormModelError("E_CONTAINER_UNKNOWN", f"panel {container!r} not found")
        target = matches[0]
        if not target.pages:
            raise FormModelError("E_CONTAINER_UNKNOWN", f"{target.name} has no pages")
        if name.casefold() in {page.casefold() for page in target.pages}:
            raise FormModelError("E_NAME_EXISTS", f"page {name!r} already exists in {target.name}")
        source = self.data[target.page_key(target.pages[-1])]
        # Page guides: v8unpack slices the panel's flat guide list positionally (first page 4 + extra,
        # others 4), so a page's slice need not be its own. Each entry carries its page id in [5] and its
        # kind in [4] (1..4 - page borders, 0 - user guide): take the four borders of one page id.
        entries = [entry for page in target.pages for entry in self.data[target.page_key(page)].get("info") or []
                   if isinstance(entry, list) and len(entry) > 5]
        borders: dict[str, dict[str, list]] = {}
        for entry in entries:
            if entry[4] in ("1", "2", "3", "4"):
                borders.setdefault(entry[5], {}).setdefault(entry[4], entry)
        complete = [page_id for page_id, kinds in borders.items() if len(kinds) == 4]
        if not complete:
            raise FormModelError("E_PANEL_FORMAT", f"{target.name}: no page with four border guides")
        last_ids = [entry[5] for entry in source.get("info") or [] if isinstance(entry, list) and len(entry) > 5]
        template_id = max(complete, key=lambda page_id: (last_ids.count(page_id), page_id == complete[-1]))
        guides = [copy.deepcopy(borders[template_id][kind]) for kind in ("1", "2", "3", "4")]
        # After the page guides the panel holds a flag, one value per page (4294967295 on every panel of a
        # typical configuration) and then "5". Without the new page's value 1C fails with "Ошибка формата потока".
        panel_record = self.data[target.key]["raw"][2][1]
        count = len(target.pages)
        try:
            marker = panel_record.index("в отдельном файле")
            per_page = panel_record[marker + 2:marker + 2 + count]
            shape_ok = (len(per_page) == count and panel_record[marker + 2 + count] == "5"
                        and all(isinstance(value, str) and value.lstrip("-").isdigit() for value in per_page))
        except (ValueError, IndexError):
            shape_ok = False
        if not shape_ok:
            raise FormModelError("E_PANEL_FORMAT", f"{target.name}: unexpected panel record; add the page in Designer")
        from .panel import fit_guides
        # The new page gets the panel's client area (as its sibling pages have), not the template's guides:
        # with stale or degenerate guides an empty page would otherwise get a wrong (even 0x0) size.
        client = self.container_size(target, target.pages[-1])
        record = copy.deepcopy(source)
        raw = record["raw"]
        raw[6] = encode_str(name)
        raw[4] = "1"
        raw[1] = make_loc(self._texts(caption if caption is not None else name, {}))
        record["info"] = fit_guides(guides, len(target.pages), client)
        self.data[target.page_key(name)] = record
        self.data[f"{target.key}/-pages-"].append(name)
        panel_record.insert(marker + 2 + count, "4294967295")
        return {"container": target.name, "name": name, "caption": loc_to_dict(raw[1])}

    def add_element(self, *, element_type: str, name: str, rect: Rect, container: str | None = None,
                    page: str | None = None, caption: str | dict[str, str] | None = None,
                    tooltip: str | dict[str, str] | None = None, template: str | None = None,
                    value_type: str | None = None, handler: str | None = None,
                    anchor_bottom: bool | None = None, data: str | None = None,
                    events: dict[str, str] | None = None, columns: list[str] | None = None,
                    stretch: str | None = None, read_only: bool | None = None) -> ControlInfo:
        if stretch not in (None, "width", "height", "both"):
            raise FormModelError("E_PLAN_FIELD", "stretch must be width, height or both")
        if element_type not in ADDABLE_TYPES:
            raise FormModelError("E_TYPE_UNSUPPORTED",
                                 f"adding {element_type} is not supported; supported: {', '.join(ADDABLE_TYPES)}")
        if element_type == "Table" and not data and not columns:
            raise FormModelError("E_DATA_REQUIRED",
                                 "a table needs data (a tabular section of the object or a form attribute), "
                                 "or columns for an unbound value table filled by the form module")
        if columns is not None and element_type != "Table":
            raise FormModelError("E_PLAN_FIELD", "columns apply only to Table")
        validate_name(name)
        if self.has_element(name):
            raise FormModelError("E_NAME_EXISTS", f"element {name!r} already exists")
        _check_rect(rect)
        target, page_name = self.resolve_container(container, page)
        size = self.container_size(target, page_name)
        if size and (rect.right > size[0] or rect.bottom > size[1]):
            raise FormModelError("E_GEOMETRY_OUTSIDE",
                                 f"{name}: rectangle {rect.as_dict()} exceeds container {target.name}/{page_name} "
                                 f"client size {size[0]}x{size[1]}")
        template_raw = self._template_raw(element_type, template)
        raw = copy.deepcopy(template_raw)
        new_id = self._allocate_id()
        raw[1] = new_id
        raw[-2][1] = encode_str(name)
        _clear_events(raw[2])
        style = _style_block(raw[2])
        if style is not None:
            style[12] = ["1", "0"]          # never inherit the template's tooltip
        if caption is None and element_type in CAPTION_TYPES:
            caption = name

        self._init_layout(raw, new_id, target, page_name, rect, stretch=stretch, anchor_bottom=anchor_bottom)

        key = "/".join(part for part in (target.page_key(page_name), name) if part)
        self.data[key] = {"id": new_id, "ver": 27, "page": target.page_key(page_name), "raw": raw}
        node = {"name": name, "type": element_type, "page": page_name}
        target.tree.append(node)
        self._header[1][1] = new_id

        if caption is not None:
            self._set_caption(key, caption)
        if tooltip is not None:
            self._set_tooltip(key, tooltip)
        if element_type == "Table":
            if value_type:
                raise FormModelError("E_VALUE_TYPE_UNSUPPORTED", "value_type applies only to Field")
            from .table_field import bind_table, make_value_table, reset_columns
            if data:
                bind_table(self, key, data)
            else:
                make_value_table(self, key)
            reset_columns(self, key, columns)
        elif data:
            if value_type:
                raise FormModelError("E_VALUE_TYPE_BOUND", "a data-bound field takes its type from the attribute")
            self._bind_data(key, data)
        elif element_type == "Field":
            self._set_value_type(key, value_type)
        elif value_type:
            raise FormModelError("E_VALUE_TYPE_UNSUPPORTED", "value_type applies only to Field")
        for event, procedure in _merge_events(element_type, handler, events).items():
            self.set_handler(key, event, procedure)
        if read_only is None and not template and self.read_only(key):
            read_only = False          # a new field/table is editable unless asked (the cloned look may be read-only)
        if read_only is not None:
            self.set_read_only(key, read_only)
        return self.info(key)

    def _init_layout(self, raw: list, new_id: str, target: Container, page_name: str, rect: Rect, *,
                     stretch: str | None = None, anchor_bottom: bool | None = None,
                     flags: list[str] | None = None) -> None:
        """Layout of a new element: its rectangle, last in tab/z order, every edge bound to the container."""
        size = self.container_size(target, page_name)
        layouts = [self._layout(key) for key in self.page_elements(target, page_name)]
        layout = Layout.parse(raw[3])
        layout.set_rect(rect)
        for edge in layout.edges:
            edge[1] = list(NONE_BIND)
            edge[2] = list(NONE_BIND)
        layout.reverse = [[], [], [], []]
        layout.tail[2] = str(target.pages.index(page_name))
        layout.tail[3] = str(len(layouts))
        layout.tail[4] = str(len(layouts) + 1)
        layout.tail[5:7] = list(flags or ["1", "1"])
        if anchor_bottom is None:
            anchor_bottom = self._below_stretching(target, page_name, rect)
        vertical = BOTTOM if anchor_bottom else TOP
        for edge_index in (TOP, BOTTOM, LEFT, RIGHT):
            border = vertical if edge_index in (TOP, BOTTOM) else LEFT
            # Stretching: the far edge follows the container's far border (resizes with the form).
            if edge_index == BOTTOM and stretch in ("height", "both"):
                border = BOTTOM
            if edge_index == TOP and stretch in ("height", "both"):
                border = TOP
            if edge_index == RIGHT and stretch in ("width", "both"):
                border = RIGHT
            self._bind_to_container(layout, new_id, target, edge_index, rect, border, size)
        layout.write()

    def modify_element(self, name: str, *, caption: str | dict[str, str] | None = None,
                       tooltip: str | dict[str, str] | None = None, rect: Rect | None = None,
                       value_type: str | None = None, handler: str | None = None,
                       data: str | None = None, events: dict[str, str] | None = None,
                       read_only: bool | None = None) -> ControlInfo:
        key = self.find(name)
        if read_only is not None:
            self.set_read_only(key, read_only)
        if caption is not None:
            self._set_caption(key, caption)
        if tooltip is not None:
            self._set_tooltip(key, tooltip)
        if value_type is not None:
            if self._type(key) != "Field":
                raise FormModelError("E_VALUE_TYPE_UNSUPPORTED", "value_type applies only to Field")
            if self.info(key).data_bound:
                raise FormModelError("E_VALUE_TYPE_BOUND", f"{name} is bound to data; its type comes from metadata")
            self._set_value_type(key, value_type)
        if data is not None:
            if value_type is not None:
                raise FormModelError("E_VALUE_TYPE_BOUND", "a data-bound field takes its type from the attribute")
            self._bind_data(key, data)
        for event, procedure in _merge_events(self._type(key), handler, events).items():
            self.set_handler(key, event, procedure)
        if rect is not None:
            self._move(key, rect)
        return self.info(key)

    def module_references(self, name: str) -> int:
        """Count ``ЭлементыФормы.<name>`` references in the form module."""
        if not self.module_text:
            return 0
        pattern = re.compile(r"(?:ЭлементыФормы|Controls)\s*\.\s*" + re.escape(name) + r"(?![0-9A-Za-zА-Яа-яЁёІіЇїЄєҐґ_])",
                             re.IGNORECASE)
        return len(pattern.findall(self.module_text))

    def remove_element(self, name: str, *, force: bool = False) -> list[str]:
        """Remove an element; a table goes with its command panels, a panel with its pages and contents.

        Returns the names of every removed element (the element itself first)."""
        key = self.find(name)
        element_type = self._type(key)
        if element_type not in REMOVABLE_TYPES:
            raise FormModelError("E_TYPE_UNSUPPORTED", f"removing {element_type} is not supported")
        # Everything that goes: for a panel all elements inside it; for every table removed, the command
        # panels whose action source it is (wherever they are).
        inner = [other for other in self.element_keys() if other.startswith(key + "/")] if element_type == "Panel" else []
        doomed = [key, *inner]
        table_ids = ({self.data[k]["raw"][1] for k in doomed} if element_type in ("Table", "Panel") else set())
        tied = [other for other in self.element_keys() if other not in doomed and self._type(other) == "CommandPanel"
                and _action_source(self.data[other]["raw"]) in table_ids]
        doomed += tied
        names = [self._name(k) for k in doomed]
        for doomed_key, doomed_name in zip(doomed, names):
            references = self.module_references(doomed_name)
            if references and not force:
                which = "" if doomed_key == key else f" (removed with {name})"
                raise FormModelError("E_ELEMENT_REFERENCED",
                                     f"{doomed_name}{which} is used {references} time(s) in the form module "
                                     f"(ЭлементыФормы.{doomed_name}); fix the code first or remove with force")
            if self.data[doomed_key]["raw"][1] == self.default_button_id:
                raise FormModelError("E_ELEMENT_REFERENCED", f"{doomed_name} is the form's default button")
        for tied_key in tied:
            self._detach(tied_key)
        if inner:
            inner_ids = {self.data[k]["raw"][1] for k in inner}
            sources = self.datasources
            kept = [entry for entry in sources[1:] if not (isinstance(entry, list) and entry[0] in inner_ids)]
            sources[:] = [str(len(kept))] + kept
        self._detach(key)
        for other in [k for k in self.data if k.startswith(key + "/")]:
            del self.data[other]
        return names

    def _detach(self, key: str) -> None:
        """Remove one element record: its anchors, dependents' bindings, tab/z order, data source, tree node."""
        raw = self.data[key]["raw"]
        element_id = raw[1]
        container, page = self.container_of(key)
        layout = self._layout(key)

        # 1. Own bindings: drop reverse entries on targets and panel-index entries.
        for _edge, _slot, bind in layout.bindings():
            target = bind[1]
            if target not in {"0", "-1"}:
                target_key = self._key_by_id(target)
                if target_key:
                    target_layout = self._layout(target_key)
                    target_layout.reverse = [[e for e in entries if e[1] != element_id]
                                             for entries in target_layout.reverse]
                    target_layout.write()
        self._panel_index_remove(container, element_id)

        # 2. Dependents bound to this element: rebind each such edge to the container.
        for other in self.page_elements(container, page):
            if other == key:
                continue
            other_layout = self._layout(other)
            changed = False
            for edge_index, slot, bind in list(other_layout.bindings()):
                if bind[1] != element_id:
                    continue
                if slot == 2:
                    other_layout.edges[edge_index][2] = list(NONE_BIND)
                elif edge_index > 3:
                    other_layout.edges[edge_index][1] = list(NONE_BIND)
                else:
                    self._bind_to_container(other_layout, self.data[other]["raw"][1], container,
                                            edge_index, other_layout.rect)
                changed = True
            if changed:
                other_layout.write()

        # 3. Tab/z order compaction on the page.
        for other in self.page_elements(container, page):
            if other == key:
                continue
            other_layout = self._layout(other)
            if other_layout.tab_index > layout.tab_index:
                other_layout.tail[3] = str(other_layout.tab_index - 1)
            if other_layout.z_index > layout.z_index:
                other_layout.tail[4] = str(other_layout.z_index - 1)
            other_layout.write()

        # 4. Data source, tree node, data record.
        sources = self.datasources
        kept = [entry for entry in sources[1:] if not (isinstance(entry, list) and entry[0] == element_id)]
        sources[:] = [str(len(kept))] + kept
        node_name = self._name(key)
        container.tree[:] = [node for node in container.tree
                             if not (node["name"] == node_name and node.get("page", "") == page)]
        del self.data[key]

    # ------------------------------------------------------------------ geometry edits
    def _move(self, key: str, rect: Rect) -> None:
        _check_rect(rect)
        container, page = self.container_of(key)
        size = self.container_size(container, page)
        if size and (rect.right > size[0] or rect.bottom > size[1]):
            raise FormModelError("E_GEOMETRY_OUTSIDE",
                                 f"{self._name(key)}: rectangle exceeds container client size {size[0]}x{size[1]}")
        layout = self._layout(key)
        old = layout.rect
        delta = [rect.edge(i) - old.edge(i) for i in range(4)]
        element_id = self.data[key]["raw"][1]
        for edge_index, _slot, bind in layout.bindings():
            if edge_index <= 3:
                if bind[1] == element_id and int(bind[2]) <= 3:
                    # Bound to one of its own edges (a command panel's height): the offset follows both edges.
                    bind[3] = str(int(bind[3]) + delta[edge_index] - delta[int(bind[2])])
                else:
                    bind[3] = str(int(bind[3]) + delta[edge_index])
        layout.set_rect(rect)
        layout.write()
        # Keep dependents where they are: their offsets are relative to our borders.
        for other in self.element_keys():
            if other == key:
                continue
            other_layout = self._layout(other)
            changed = False
            for _edge, _slot, bind in other_layout.bindings():
                if bind[1] == element_id and int(bind[2]) <= 3:
                    bind[3] = str(int(bind[3]) - delta[int(bind[2])])
                    changed = True
            if changed:
                other_layout.write()

    def _below_stretching(self, container: Container, page: str, rect: Rect) -> bool:
        """True when an element above ``rect`` stretches with the container bottom.

        Such an element grows when the form is resized, so a new element below
        it must follow the container bottom instead of the top.
        """
        for key in self.page_elements(container, page):
            layout = self._layout(key)
            other = layout.rect
            if other.bottom > rect.top or other.right <= rect.left or other.left >= rect.right:
                continue
            primary = [(edge_index, bind) for edge_index, slot, bind in layout.bindings() if slot == 1]
            bottom_follows = any(e == BOTTOM and b[1] == "0" and int(b[2]) == BOTTOM for e, b in primary)
            top_follows = any(e == TOP and b[1] == "0" and int(b[2]) == BOTTOM for e, b in primary)
            if bottom_follows and not top_follows:
                return True
        return False

    def _rederive_offsets(self, container: Container, page: str, size: tuple[int, int],
                          rects: dict[str, Rect]) -> None:
        """Write new rectangles and recompute every binding offset from geometry.

        Verified on real forms: offset == own edge coordinate - target border coordinate,
        for primary and secondary bindings, to the container and to other elements.
        """
        width, height = size
        by_id = {self.data[key]["raw"][1]: key for key in self.element_keys()}
        layouts = {key: self._layout(key) for key in self.page_elements(container, page)}
        for key, rect in rects.items():
            layouts[key].set_rect(rect)
        for key, layout in layouts.items():
            rect = layout.rect
            for edge_index, _slot, bind in layout.bindings():
                if edge_index > 3:
                    continue
                border = int(bind[2])
                border_coord = None
                if bind[1] == "0":
                    border_coord = {TOP: 0, LEFT: 0, BOTTOM: height, RIGHT: width}.get(border)
                else:
                    target = by_id.get(bind[1])
                    if target is not None and border <= 3:
                        target_layout = layouts.get(target) or self._layout(target)
                        border_coord = target_layout.rect.edge(border)
                if border_coord is not None:
                    bind[3] = str(rect.edge(edge_index) - border_coord)
            layout.write()

    def insert_band(self, y: int, delta: int, *, stretch: str | None = None) -> None:
        """Open a horizontal band of ``delta`` px at ``y`` on the form's root page.

        The form grows by ``delta``; elements at or below ``y`` move down,
        elements crossing ``y`` stretch. Panels may not cross ``y``.
        """
        if delta <= 0:
            return
        width, height = self.size
        if height + delta > 3000:
            raise FormModelError("E_GEOMETRY_OUTSIDE", "form height would exceed 3000")
        root = self.containers()[""]
        page = root.pages[0]
        rects: dict[str, Rect] = {}
        for key in self.page_elements(root, page):
            rect = self._layout(key).rect
            top = rect.top + delta if rect.top >= y else rect.top
            bottom = rect.bottom + delta if rect.bottom > y or rect.top >= y or key == stretch else rect.bottom
            if (top, bottom) != (rect.top, rect.bottom):
                if self._type(key) == "Panel" and top == rect.top and key != stretch:
                    raise FormModelError("E_LAYOUT_STRADDLE",
                                         f"cannot insert a row inside panel {self._name(key)}; "
                                         "place the element on a panel page or below the panel")
                rects[key] = Rect(rect.left, top, rect.right, bottom)
        self._rederive_offsets(root, page, (width, height + delta), rects)
        self._header[4] = str(height + delta)
        if len(self._header) > 12 and self._header[12] == str(height):
            self._header[12] = str(height + delta)

    def grow_panel(self, container: Container, delta: int) -> None:
        """Make a root-level panel ``delta`` px taller; the form grows with it.

        Elements below the panel move down. On every page of the panel, edges bound to
        the panel bottom move with it (offsets keep their meaning), so free room appears
        above bottom-anchored blocks and stretching elements (tables) grow.
        """
        if delta <= 0:
            return
        if not container.key:
            raise FormModelError("E_PLACEMENT", "grow_panel needs a panel")
        owner, _owner_page = self.container_of(container.key)
        if owner.key:
            raise FormModelError("E_NO_SPACE", f"panel {container.name} is nested; resize it manually")
        sizes = {page: self.container_size(container, page) for page in container.pages}
        panel_rect = self._layout(container.key).rect
        self.insert_band(panel_rect.bottom, delta, stretch=container.key)
        for page in container.pages:
            width, height = sizes[page]
            rects: dict[str, Rect] = {}
            for key in self.page_elements(container, page):
                layout = self._layout(key)
                rect = layout.rect
                follows = {edge_index for edge_index, slot, bind in layout.bindings()
                           if slot == 1 and edge_index in (TOP, BOTTOM) and bind[1] == "0" and int(bind[2]) == BOTTOM}
                top = rect.top + delta if TOP in follows else rect.top
                bottom = rect.bottom + delta if BOTTOM in follows else rect.bottom
                if (top, bottom) != (rect.top, rect.bottom):
                    rects[key] = Rect(rect.left, top, rect.right, bottom)
            self._rederive_offsets(container, page, (width, height + delta), rects)
        # The bottom border guide of every page follows the panel bottom (keeps empty pages' size right).
        for page in container.pages:
            for entry in self.data.get(container.page_key(page), {}).get("info") or []:
                if isinstance(entry, list) and len(entry) > 7 and entry[4] == "4" and str(entry[1]).isdigit():
                    entry[1] = str(int(entry[1]) + delta)

    # ------------------------------------------------------------------ style & placement
    def style_passport(self, container: str | None = None, page: str | None = None) -> dict[str, Any]:
        """Typical geometry of a page, derived from its existing elements."""
        target, page_name = self.resolve_container(container, page)
        infos = [self.info(key) for key in self.page_elements(target, page_name)]

        def mode(values: list[int], default: int) -> int:
            values = [value for value in values if value > 0]
            return Counter(values).most_common(1)[0][0] if values else default

        fields = [i for i in infos if i.type == "Field"]
        labels = [i for i in infos if i.type == "Label"]
        tops = sorted({i.rect.top for i in fields + labels})
        pitches = [b - a for a, b in zip(tops, tops[1:]) if 0 < b - a <= 40]
        row_height = mode([i.rect.height for i in fields], 19)
        pitch = mode(pitches, row_height + 5)
        width, height = self.container_size(target, page_name)
        return {
            "container": target.name, "page": page_name, "client_width": width, "client_height": height,
            "margin_left": min((i.rect.left for i in infos), default=8),
            "label_left": mode([i.rect.left for i in labels], 8),
            "label_width": mode([i.rect.width for i in labels], 84),
            "field_left": mode([i.rect.left for i in fields], 96),
            "field_width": mode([i.rect.width for i in fields], 120),
            "row_height": row_height,
            "label_height": mode([i.rect.height for i in labels], row_height),
            "button_height": mode([i.rect.height for i in infos if i.type == "Button"], 22),
            "checkbox_height": mode([i.rect.height for i in infos if i.type == "CheckBox"], row_height),
            "row_pitch": pitch,
            "row_gap": max(pitch - row_height, 2),
        }

    def _footer_top(self, container: Container, page: str) -> int:
        """Top of the block that follows the container bottom (e.g. a bottom command bar)."""
        _width, height = self.container_size(container, page)
        top = height
        for key in self.page_elements(container, page):
            layout = self._layout(key)
            if any(edge_index == TOP and slot == 1 and bind[1] == "0" and int(bind[2]) == BOTTOM
                   for edge_index, slot, bind in layout.bindings()):
                top = min(top, layout.rect.top)
            elif self._type(key) == "CommandPanel" and layout.rect.bottom >= height - 8 and layout.rect.top > 0:
                # A command bar docked to the bottom (auto position) keeps no edge binding.
                top = min(top, layout.rect.top)
        return top

    def free_row_top(self, container: Container, page: str) -> int:
        """Bottom of the content above the footer block."""
        footer = self._footer_top(container, page)
        rects = [self._layout(key).rect for key in self.page_elements(container, page)]
        return max((rect.bottom for rect in rects if rect.top < footer), default=0)

    def place(self, element_type: str, *, container: str | None = None, page: str | None = None,
              width: int | None = None, height: int | None = None, left: int | None = None,
              top: int | None = None, beside: str | None = None, grow: bool = True,
              text: str | None = None, column: int | None = None) -> Rect:
        """Choose a rectangle for a new element in the page's own style.

        * explicit ``left``/``top`` win; a rectangle below/right of the client area grows the form
          (or a top-level panel: its height), see :meth:`_fit_explicit`;
        * ``column`` (1 or 2) puts the element into the left/right half of a two-column layout, on the
          row of ``beside`` (or at ``top``, or in a new row);
        * ``beside=<name>`` puts the element to the right of that element, same row;
        * otherwise a new row is opened below the content (above a bottom command bar);
          on the form's root page the form grows when there is no room.
        """
        target, page_name = self.resolve_container(container, page)
        passport = self.style_passport(target.key, page_name)
        h = int(height or self._default_height(element_type, passport))
        if column is not None:
            return self._place_in_column(element_type, target, page_name, passport, int(column), h,
                                         width=width, top=top, beside=beside, text=text, grow=grow)
        w = int(width or self._default_width(element_type, passport, text))
        if not (width and left is not None):
            w = min(w, max(passport["client_width"] - 2 * passport["margin_left"], 4))
        if beside:
            anchor = self._row_anchor(beside, target, page_name)
            x = anchor.right + BESIDE_GAP if left is None else int(left)
            y = anchor.top if top is None else int(top)
            rect = Rect.from_xywh(x, y, w, h)
            if rect.right > passport["client_width"]:
                raise FormModelError("E_NO_SPACE", f"no room to the right of {beside}")
            return rect
        default_left = passport["field_left"] if element_type == "Field" else passport["label_left"]
        if element_type in ("Group", "Table", "Panel"):
            default_left = passport["margin_left"]
        x = default_left if left is None else int(left)
        y = int(top) if top is not None else self._new_row(target, page_name, h, passport, grow)
        return self._fit_explicit(target, page_name, Rect.from_xywh(x, y, w, h), passport, grow)

    @staticmethod
    def _default_height(element_type: str, passport: dict[str, Any]) -> int:
        return {"Button": passport["button_height"], "Label": passport["label_height"],
                "CheckBox": passport["checkbox_height"], "Group": 16, "Table": 150, "Panel": 150}.get(
            element_type, passport["row_height"])

    @staticmethod
    def _default_width(element_type: str, passport: dict[str, Any], text: str | None) -> int:
        if element_type in ("Group", "Table", "Panel"):
            return passport["client_width"] - 2 * passport["margin_left"]
        default_width = {"Label": passport["label_width"], "Field": passport["field_width"],
                         "Button": 100, "CheckBox": max(passport["label_width"], 100)}.get(element_type, 100)
        if text and element_type in ("Label", "CheckBox", "Button"):
            default_width = max(default_width, _text_width(element_type, text))
        return default_width

    def _row_anchor(self, name: str, target: Container, page_name: str) -> Rect:
        """Rectangle of the element whose row a new element joins (``beside``)."""
        anchor = self.info(self.find(name))
        if anchor.container != target.key or anchor.page != page_name:
            raise FormModelError("E_PLACEMENT", f"{name} is on another container/page")
        return anchor.rect

    def _new_row(self, target: Container, page_name: str, h: int, passport: dict[str, Any], grow: bool) -> int:
        """Top of a new row of height ``h`` below the content; grows the form or a top-level panel."""
        cursor_key = (target.key, page_name)
        content_bottom = max(self.free_row_top(target, page_name), self._row_cursor.get(cursor_key, 0))
        y = content_bottom + passport["row_gap"]
        below = [self._layout(key).rect.top for key in self.page_elements(target, page_name)
                 if self._layout(key).rect.top >= y]
        _width, height = self.container_size(target, page_name)
        next_top = min(below, default=height)
        need = y + h + passport["row_gap"] - next_top
        if need > 0:
            if not grow:
                raise FormModelError("E_NO_SPACE",
                                     f"no free room on {target.name}/{page_name}: need {need}px more height")
            if target.key:
                self.grow_panel(target, need)
                next_top_after = min((self._layout(key).rect.top for key in self.page_elements(target, page_name)
                                      if self._layout(key).rect.top >= y),
                                     default=self.container_size(target, page_name)[1])
                if y + h + passport["row_gap"] > next_top_after:
                    raise FormModelError("E_NO_SPACE", f"no free room on {target.name}/{page_name}: an element "
                                         "anchored to the top sits below the content; use left/top or beside")
            else:
                self.insert_band(y, need)
        self._row_cursor[cursor_key] = y + h
        return y

    def _fit_explicit(self, target: Container, page_name: str, rect: Rect, passport: dict[str, Any],
                      grow: bool) -> Rect:
        """Grow the form (or a top-level panel) so that an explicitly placed rectangle fits.

        Height: a band opens above the bottom block (a bottom command bar, elements anchored to the bottom),
        as for a new row, so the bottom block stays below the element. Width (the form only): the form gets
        wider by the anchor model (right-anchored elements move, stretching ones grow). A nested panel does
        not grow: :meth:`add_element` then refuses the rectangle (``E_GEOMETRY_OUTSIDE``)."""
        width, height = self.container_size(target, page_name)
        if not grow or (rect.right <= width and rect.bottom <= height):
            return rect
        if target.key and self.container_of(target.key)[0].key:
            return rect
        if rect.bottom > height:
            band = self._bottom_strip_top(target, page_name, rect.top)
            self._grow_below(target, page_name, band, rect.bottom + passport["row_gap"] - band)
        if rect.right > width and not target.key and rect.right + max(passport["label_left"], 0) <= MAX_FORM_SIZE:
            self.resize_form(width=rect.right + max(passport["label_left"], 0))
        return rect

    def _bottom_strip_top(self, target: Container, page_name: str, limit: int) -> int:
        """The line where a band opens for an element placed at ``limit`` or below: the top of the strip of
        elements touching the bottom border (a bottom command bar), else the client bottom; never below
        ``limit`` and never through an element."""
        height = self.container_size(target, page_name)[1]
        rects = [self._layout(key).rect for key in self.page_elements(target, page_name)]
        candidates = {height, limit, *(rect.top for rect in rects if rect.bottom >= height - 8 and rect.top > 0),
                      *(rect.bottom for rect in rects)}
        free = [y for y in candidates if 0 <= y <= min(limit, height)
                and not any(rect.top < y < rect.bottom for rect in rects)]
        strip = [y for y in free if y < height and any(rect.top == y and rect.bottom >= height - 8 for rect in rects)]
        return max(strip, default=max(free, default=0))

    def _grow_below(self, target: Container, page_name: str, y: int, delta: int) -> None:
        """Grow the form (or a top-level panel) by ``delta`` px with a band at ``y`` no element crosses: the
        elements below ``y`` move down, the ones above keep their place (a stretching table above does not
        grow into the band)."""
        if delta <= 0:
            return
        if not target.key:
            self.insert_band(y, delta)
            return
        rects = {key: self._layout(key).rect for key in self.page_elements(target, page_name)}
        self.grow_panel(target, delta)
        final = {key: (Rect(rect.left, rect.top + delta, rect.right, rect.bottom + delta) if rect.top >= y else rect)
                 for key, rect in rects.items()}
        self._rederive_offsets(target, page_name, self.container_size(target, page_name), final)

    def _column_span(self, passport: dict[str, Any], column: int) -> tuple[int, int]:
        """Horizontal span [start, end) of a column of a two-column layout on a page."""
        if column not in (1, 2):
            raise FormModelError("E_PLAN_FIELD", "column must be 1 or 2")
        width = passport["client_width"]
        middle = width // 2
        start, end = ((passport["label_left"], middle - COLUMN_GAP) if column == 1
                      else (middle, width - max(passport["label_left"], 0)))
        if end - start < 40:
            raise FormModelError("E_NO_SPACE", f"the page is too narrow ({width}px) for two columns")
        return start, end

    def _place_in_column(self, element_type: str, target: Container, page_name: str, passport: dict[str, Any],
                         column: int, h: int, *, width: int | None, top: int | None, beside: str | None,
                         text: str | None, grow: bool) -> Rect:
        start, end = self._column_span(passport, column)
        x = start + (passport["field_left"] - passport["label_left"]) if element_type == "Field" else start
        if element_type in ("Group", "Table", "Panel"):
            default_width = end - start
        else:
            default_width = self._default_width(element_type, passport, text)
        w = int(width) if width else min(default_width, end - x)
        if x >= end or w < 4:
            raise FormModelError("E_NO_SPACE", f"no room in column {column} of {target.name}/{page_name}")
        if beside:
            y = self._row_anchor(beside, target, page_name).top if top is None else int(top)
        else:
            y = int(top) if top is not None else self._new_row(target, page_name, h, passport, grow)
        rect = Rect.from_xywh(x, y, w, h)
        self._check_free(target, page_name, [rect], f"column {column}")
        return self._fit_explicit(target, page_name, rect, passport, grow)

    def place_pair(self, element_type: str, label: str, *, container: str | None = None, page: str | None = None,
                   width: int | None = None, height: int | None = None, left: int | None = None,
                   top: int | None = None, beside: str | None = None, column: int | None = None,
                   grow: bool = True) -> tuple[Rect, Rect]:
        """Rectangles of a caption label and its Field/Button in one row (``add`` with ``label``).

        * default: a new row, the label in the label column and the field in the field column;
        * ``beside``: the row of that element, the label right after it (by its text) and the field after
          the label;
        * ``column`` (1/2): the left/right half of a two-column layout, label and field columns kept as in
          the page style; the row is the row of ``beside``, ``top`` or a new row;
        * ``left``/``top``: the label at that point (missing coordinate - as by default), the field after it.
        """
        target, page_name = self.resolve_container(container, page)
        passport = self.style_passport(target.key, page_name)
        label_h = passport["label_height"]
        h = int(height or (passport["button_height"] if element_type == "Button" else passport["row_height"]))
        offset = passport["field_left"] - passport["label_left"]
        text_width = _text_width("Label", label)
        default_width = passport["field_width"] if element_type == "Field" else 100
        explicit = beside is not None or column is not None or left is not None or top is not None
        if column is not None:
            start, end = self._column_span(passport, int(column))
            label_x, label_w, right_limit = start, min(max(passport["label_width"], text_width), end - start), end
        elif beside is not None:
            anchor = self._row_anchor(beside, target, page_name)
            label_x, label_w = anchor.right + BESIDE_GAP, text_width
            right_limit = passport["client_width"] - max(passport["label_left"], 0)
        else:
            label_x = passport["label_left"] if left is None else int(left)
            label_w = min(max(passport["label_width"], text_width),
                          max(passport["client_width"] - 2 * passport["margin_left"], 4))
            right_limit = passport["client_width"] - max(passport["label_left"], 0)
        if beside is not None and top is None:
            y = self._row_anchor(beside, target, page_name).top
        elif top is not None:
            y = int(top)
        else:
            y = self._new_row(target, page_name, label_h, passport, grow)
        label_rect = Rect.from_xywh(label_x, y, label_w, label_h)
        x = label_x + offset if element_type == "Field" and beside is None else label_rect.right + BESIDE_GAP
        if column is not None and element_type == "Field":
            x = max(x, self._column_field_left(target, page_name, label_x, right_limit))
        if x <= label_rect.right:
            x = label_rect.right + BESIDE_GAP
        if not explicit:
            # The original new-row pair: the field in the page's field column, clipped to the client width.
            x = passport["field_left"] if element_type == "Field" else label_rect.right + BESIDE_GAP
            if x <= label_rect.right:
                x = label_rect.right + BESIDE_GAP
            w = min(int(width or default_width), passport["client_width"] - x - passport["margin_left"])
        elif width and left is not None and column is None:
            w = int(width)           # explicit geometry: the form grows if needed
        else:
            w = min(int(width or default_width), right_limit - x)
        if w < 20:
            raise FormModelError("E_NO_SPACE", f"no room for the {element_type} after its label on "
                                               f"{target.name}/{page_name}")
        rect = Rect.from_xywh(x, y, w, h)
        if explicit:
            self._check_free(target, page_name, [label_rect, rect], "the label and its element")
        for each in (label_rect, rect):
            self._fit_explicit(target, page_name, each, passport, grow)
        return label_rect, rect

    def _column_field_left(self, target: Container, page_name: str, start: int, end: int) -> int:
        """Left of the fields already paired with a label at ``start`` in a column (aligns the column's fields)."""
        rects = {key: self._layout(key).rect for key in self.page_elements(target, page_name)}
        labels = {rect.top for key, rect in rects.items() if self._type(key) == "Label" and rect.left == start}
        lefts = [rect.left for key, rect in rects.items()
                 if self._type(key) == "Field" and rect.top in labels and start < rect.left < end]
        return Counter(lefts).most_common(1)[0][0] if lefts else 0

    def _check_free(self, target: Container, page_name: str, rects: list[Rect], what: str) -> None:
        for key in self.page_elements(target, page_name):
            other = self._layout(key).rect
            if any(rect.overlaps(other) for rect in rects):
                raise FormModelError("E_NO_SPACE", f"{what} would overlap {self._name(key)} on "
                                                   f"{target.name}/{page_name}; use another row or column")

    def tab_after_row(self, key: str) -> None:
        """Put a new element right after the last element to its left in the same row (row-major tab order,
        as in typical two-column headers); without such an element its tab position is kept."""
        container, page = self.container_of(key)
        layout = self._layout(key)
        rect = layout.rect
        left = [self._layout(other) for other in self.page_elements(container, page) if other != key]
        left = [other for other in left if other.rect.right <= rect.left
                and other.rect.top < rect.bottom and rect.top < other.rect.bottom]
        if not left:
            return
        position = max(other.tab_index for other in left) + 1
        current = layout.tab_index
        if position >= current:
            return
        for other in self.page_elements(container, page):
            if other == key:
                continue
            other_layout = self._layout(other)
            if position <= other_layout.tab_index < current:
                other_layout.tail[3] = str(other_layout.tab_index + 1)
                other_layout.write()
        layout.tail[3] = str(position)
        layout.write()

    # ------------------------------------------------------------------ resizing (anchor model)
    def resize_form(self, width: int | None = None, height: int | None = None) -> list[str]:
        """Set the form size; elements follow their anchors as in 1C (``set_form_size``).

        Every edge bound to the right/bottom border of the form moves with it, edges bound to other
        elements follow those elements, an unbound edge stays; a panel that changes size resizes its pages the same way. A command bar
        docked to the bottom (no bindings) moves with the bottom. Refuses (``E_GEOMETRY_INVALID``) a size
        at which an element would leave its container, collapse or newly overlap another one.
        Returns the names of elements whose rectangle changed."""
        old = self.size
        new = (old[0] if width is None else int(width), old[1] if height is None else int(height))
        if not (MIN_FORM_SIZE <= new[0] <= MAX_FORM_SIZE and MIN_FORM_SIZE <= new[1] <= MAX_FORM_SIZE):
            raise FormModelError("E_GEOMETRY_INVALID",
                                 f"form size must be {MIN_FORM_SIZE}..{MAX_FORM_SIZE} px, got {new[0]}x{new[1]}")
        if new == old:
            return []
        root = self.containers()[""]
        plan: dict[tuple[str, str], tuple[Container, tuple[int, int], tuple[int, int], dict[str, Rect]]] = {}
        problems: list[str] = []
        for page in root.pages:
            self._solve_resize(root, page, old, new, plan, problems)
        if problems:
            raise FormModelError("E_GEOMETRY_INVALID", f"the form cannot be {new[0]}x{new[1]}: "
                                 + "; ".join(problems[:5]))
        moved: list[str] = []
        for (_container_key, page), (container, old_size, size, rects) in plan.items():
            changed = {key: rect for key, rect in rects.items() if rect != self._layout(key).rect}
            moved += [self._name(key) for key in changed]
            self._rederive_offsets(container, page, size, changed)
            if container.key:
                for entry in self.data.get(container.page_key(page), {}).get("info") or []:
                    if isinstance(entry, list) and len(entry) > 7 and str(entry[1]).lstrip("-").isdigit():
                        if entry[4] == "3":
                            entry[1] = str(int(entry[1]) + size[0] - old_size[0])
                        elif entry[4] == "4":
                            entry[1] = str(int(entry[1]) + size[1] - old_size[1])
        for index, value in ((3, new[0]), (4, new[1])):
            previous = self._header[index]
            self._header[index] = str(value)
            if len(self._header) > index + 8 and self._header[index + 8] == previous:
                self._header[index + 8] = str(value)
        return moved

    def _solve_resize(self, container: Container, page: str, old_size: tuple[int, int], new_size: tuple[int, int],
                      plan: dict, problems: list[str]) -> None:
        """New rectangles of a page's elements when its client area changes from ``old_size`` to ``new_size``."""
        keys = self.page_elements(container, page)
        by_id = {self.data[key]["raw"][1]: key for key in keys}
        rects = {key: self._layout(key).rect for key in keys}
        binds: dict[str, dict[int, tuple[str | None, int]]] = {}
        for key in keys:
            own: dict[int, tuple[str | None, int]] = {}
            for edge_index, slot, bind in self._layout(key).bindings():
                if slot != 1 or edge_index > 3 or int(bind[2]) > 3:
                    continue
                if bind[1] == "0":
                    own[edge_index] = (None, int(bind[2]))
                elif bind[1] in by_id:
                    own[edge_index] = (by_id[bind[1]], int(bind[2]))
            binds[key] = own
        dw, dh = new_size[0] - old_size[0], new_size[1] - old_size[1]
        border_delta = {TOP: 0, LEFT: 0, BOTTOM: dh, RIGHT: dw}
        delta: dict[str, list[int | None]] = {key: [None, None, None, None] for key in keys}
        for key in keys:
            rect = rects[key]
            if self._type(key) == "CommandPanel" and not binds[key]:
                # A docked command bar keeps no bindings: the bottom one follows the bottom, full-width bars
                # follow the right border.
                if rect.bottom >= old_size[1] and rect.top > 0:
                    delta[key][TOP] = delta[key][BOTTOM] = dh
                if rect.left <= 0 and rect.right >= old_size[0]:
                    delta[key][RIGHT] = dw
        for last_pass in (False, True):
            progress = True
            while progress:
                progress = False
                for key in keys:
                    for edge in range(4):
                        if delta[key][edge] is not None:
                            continue
                        value: int | None = 0                      # an unbound edge stays where it is
                        if edge in binds[key]:
                            target, border = binds[key][edge]
                            value = border_delta[border] if target is None else delta[target][border]
                        if value is None and last_pass:
                            value = 0                              # cyclic bindings: the edge stays
                        if value is not None:
                            delta[key][edge] = value
                            progress = True
        result: dict[str, Rect] = {}
        width, height = new_size
        for key in keys:
            rect, (d_top, d_bottom, d_left, d_right) = rects[key], delta[key]
            new = Rect(rect.left + d_left, rect.top + d_top, rect.right + d_right, rect.bottom + d_bottom)
            result[key] = new
            was_inside = rect.left >= 0 and rect.top >= 0 and rect.right <= old_size[0] and rect.bottom <= old_size[1]
            inside = new.left >= 0 and new.top >= 0 and new.right <= width and new.bottom <= height
            if (was_inside and not inside) or (new.width < min(rect.width, 4) or new.height < min(rect.height, 4)):
                problems.append(f"{self._name(key)} would be {new.as_dict()}")
        for index, first in enumerate(keys):
            for second in keys[index + 1:]:
                if result[first].overlaps(result[second]) and not rects[first].overlaps(rects[second]):
                    problems.append(f"{self._name(first)} would overlap {self._name(second)}")
        plan[(container.key, page)] = (container, old_size, new_size, result)
        containers = self.containers()
        for key in keys:
            if self._type(key) != "Panel" or key not in containers:
                continue
            grow = (result[key].width - rects[key].width, result[key].height - rects[key].height)
            if grow == (0, 0):
                continue
            panel = containers[key]
            for panel_page in panel.pages:
                size = self.container_size(panel, panel_page)
                self._solve_resize(panel, panel_page, size, (size[0] + grow[0], size[1] + grow[1]), plan, problems)

    def open_band(self, container: Container, page: str, y: int, delta: int) -> None:
        """Move the elements of a page whose top is at or below ``y`` down by ``delta`` px.

        Free room below is used first; otherwise the form (root page) or a top-level panel grows by
        ``delta`` - the elements below ``y`` move with the band, elements anchored to the bottom follow the
        new bottom. A nested panel without room: ``E_NO_SPACE``."""
        if delta <= 0:
            return
        keys = self.page_elements(container, page)
        rects = {key: self._layout(key).rect for key in keys}
        moved = {key: Rect(rect.left, rect.top + delta, rect.right, rect.bottom + delta)
                 for key, rect in rects.items() if rect.top >= y}
        size = self.container_size(container, page)
        fixed = [rect for key, rect in rects.items() if key not in moved]
        if all(rect.bottom <= size[1] for rect in moved.values()) and not any(
                rect.overlaps(other) and not rects[key].overlaps(other)
                for key, rect in moved.items() for other in fixed):
            self._rederive_offsets(container, page, size, moved)
            return
        if not container.key:
            self.insert_band(y, delta)
            return
        if self.container_of(container.key)[0].key:
            raise FormModelError("E_NO_SPACE", f"no room on {container.name}/{page} (a nested panel does not "
                                               f"grow): need {delta}px; free room first or use left/top")
        self.grow_panel(container, delta)
        self._rederive_offsets(container, page, self.container_size(container, page), moved)

    # ------------------------------------------------------------------ property setters
    def _set_caption(self, key: str, caption: str | dict[str, str]) -> None:
        raw = self.data[key]["raw"]
        element_type = self._type(key)
        path = _caption_path(raw, element_type)
        if path is None:
            raise FormModelError("E_CAPTION_UNSUPPORTED",
                                 f"{self._name(key)} ({element_type}) has no caption; add a Label next to it")
        current = loc_to_dict(_get(raw, path))
        texts = self._texts(caption, current)
        node = make_loc(texts)
        if "#" in current:
            node = make_loc({"#": current["#"], **texts})
        _set(raw, path, node)

    def _set_tooltip(self, key: str, tooltip: str | dict[str, str]) -> None:
        raw = self.data[key]["raw"]
        style = _style_block(raw[2])
        if style is None:
            raise FormModelError("E_TOOLTIP_UNSUPPORTED", f"{self._name(key)} has no tooltip slot")
        style[12] = make_loc(self._texts(tooltip, loc_to_dict(style[12]))) if tooltip else ["1", "0"]

    def _read_only_block(self, key: str) -> list | None:
        """The Field property block ``raw[2][2][0]`` when it has the known shape (else None)."""
        if self._type(key) != "Field":
            return None
        try:
            block = self.data[key]["raw"][2][2][0]
        except (IndexError, KeyError, TypeError):
            return None
        if (isinstance(block, list) and len(block) > READ_ONLY_SLOT and isinstance(block[0], list)
                and block[0][:1] == ["13"] and block[READ_ONLY_SLOT] in ("0", "1")):
            return block
        return None

    def _table_mask_record(self, key: str) -> list | None:
        """The Table record ``raw[2][2][1]`` when its property mask ``[1]`` has the known shape (else None)."""
        if self._type(key) != "Table":
            return None
        try:
            record = self.data[key]["raw"][2][2][1]
            mask = int(record[1])
        except (IndexError, KeyError, TypeError, ValueError):
            return None
        # Bits 11 and 12 are set in every table field of the surveyed corpus: a sanity check of the slot.
        if record[0] in ("15", "17") and mask >= 0 and mask & 0x1800 == 0x1800:
            return record
        return None

    def read_only(self, key: str) -> bool | None:
        """ТолькоПросмотр of a Field or Table (None for other types or an unknown record)."""
        block = self._read_only_block(key)
        if block is not None:
            return block[READ_ONLY_SLOT] == "1"
        record = self._table_mask_record(key)
        return None if record is None else not int(record[1]) & TABLE_EDITABLE_BIT

    def set_read_only(self, key: str, value: bool) -> None:
        """Set ТолькоПросмотр of a Field (``raw[2][2][0][13]``) or a Table (bit 10 of ``raw[2][2][1][1]``
        cleared), see docs/field-format.md."""
        if not isinstance(value, bool):
            raise FormModelError("E_PLAN_FIELD", "read_only must be true or false")
        element_type = self._type(key)
        if element_type not in ("Field", "Table"):
            raise FormModelError("E_TYPE_UNSUPPORTED", f"read_only applies to Field (ПолеВвода) and Table "
                                                       f"(ТабличноеПоле) only; {self._name(key)} is {element_type}")
        block = self._read_only_block(key)
        record = self._table_mask_record(key)
        if block is None and record is None:
            raise FormModelError("E_PROPERTY_FORMAT", f"{self._name(key)}: the record is not in the "
                                                      "supported format; set ТолькоПросмотр in Designer")
        if block is not None:
            block[READ_ONLY_SLOT] = "1" if value else "0"
        else:
            mask = int(record[1])
            record[1] = str(mask & ~TABLE_EDITABLE_BIT if value else mask | TABLE_EDITABLE_BIT)

    def _texts(self, value: str | dict[str, str], current: dict[str, str]) -> dict[str, str]:
        if isinstance(value, dict):
            texts = {str(lang): str(text) for lang, text in value.items() if lang != "#"}
            if not texts:
                raise FormModelError("E_CAPTION_INVALID", "caption dictionary is empty")
            return texts
        langs = [lang for lang in current if lang != "#"] or self.languages()
        return {lang: str(value) for lang in langs}

    def _set_value_type(self, key: str, spec: str | None) -> None:
        raw = self.data[key]["raw"]
        if not (isinstance(raw[2], list) and len(raw[2]) > 1 and isinstance(raw[2][1], list)
                and raw[2][1][:1] == ['"Pattern"']):
            raise FormModelError("E_VALUE_TYPE_UNSUPPORTED", "field has no value type slot")
        if spec is None:
            spec = "Строка"
        if spec.lower().startswith("like:"):
            source = self.data[self.find(spec[5:].strip())]["raw"]
            if TYPE_BY_UUID.get(source[0]) != "Field":
                raise FormModelError("E_VALUE_TYPE_UNSUPPORTED", "like: must reference a Field")
            raw[2][1] = copy.deepcopy(source[2][1])
        else:
            raw[2][1] = parse_value_type(spec)

    def set_handler(self, key: str, event: str, procedure: str) -> None:
        """Attach ``procedure`` to ``event`` of the element and add a stub to the module."""
        element_type = self._type(key)
        known = EVENTS.get(element_type, {})
        if event not in known:
            supported = ", ".join(known) or "none"
            raise FormModelError("E_HANDLER_UNSUPPORTED",
                                 f"event {event!r} is not supported for {element_type}; supported: {supported}")
        validate_name(procedure)
        event_id, params = known[event]
        raw = self.data[key]["raw"]
        slot = EVENT_SLOT[element_type]
        current = raw[2][slot] if isinstance(raw[2], list) and len(raw[2]) > slot else None
        if not (isinstance(current, list) and current and str(current[0]).isdigit()):
            raise FormModelError("E_HANDLER_UNSUPPORTED", f"{self._name(key)} has no event slot")
        entries = [entry for entry in current[1:] if entry[0] != event_id]
        synonym = ["1", "1", ['"ru"', encode_str(procedure)]]
        entries.append([event_id, EVENT_HANDLER_KIND,
                        ["3", encode_str(procedure),
                         ["1", encode_str(procedure), synonym, copy.deepcopy(synonym), copy.deepcopy(synonym),
                          ["4", "0", ["0"], '""', "-1", "-1", "1", "0", '""'], ["0", "0", "0"]]]])
        entries.sort(key=lambda entry: int(entry[0]))
        raw[2][slot] = [str(len(entries))] + entries
        self.ensure_procedure(procedure, params)

    # ------------------------------------------------------------------ form events
    def _form_event_slot(self, event_id: str, create: bool) -> tuple[list, int] | None:
        """(holder, index) of the event list that carries ``event_id``: form events live in root[4],
        write events of the edited object in root[3] (layout known for catalog element forms only)."""
        if event_id.startswith("7"):
            holder, index = self._root, 4
        else:
            object_events = self._root[3] if len(self._root) > 3 else None
            if not (isinstance(object_events, list) and object_events[:1] == [CATALOG_OBJECT_EVENTS_KIND]
                    and isinstance(object_events[2], list) and len(object_events[2]) > 2):
                if not create:
                    return None
                raise FormModelError("E_HANDLER_UNSUPPORTED",
                                     "object write events are supported on catalog element forms only; "
                                     "assign them in Designer")
            holder, index = object_events[2], 2
        current = holder[index] if len(holder) > index else None
        if not (isinstance(current, list) and current and str(current[0]).isdigit()):
            if not create:
                return None
            raise FormModelError("E_HANDLER_UNSUPPORTED", "the form has no event list")
        return holder, index

    def form_events(self) -> dict[str, str]:
        """Assigned form-level handlers: event name -> procedure."""
        names = {event_id: name for name, (event_id, _params) in FORM_EVENTS.items()}
        result = {}
        for event_id in ("70000", "80000"):
            found = self._form_event_slot(event_id, create=False)
            if found is None:
                continue
            holder, index = found
            for entry in holder[index][1:]:
                if not isinstance(entry, list) or len(entry) < 3:
                    continue
                procedure = entry[2][1] if isinstance(entry[2], list) else entry[2]
                result[names.get(entry[0], "#" + entry[0])] = decode_str(procedure)
        return result

    def set_form_handler(self, event: str, procedure: str) -> None:
        if event not in FORM_EVENTS:
            raise FormModelError("E_HANDLER_UNSUPPORTED",
                                 f"form event {event!r} is not supported; supported: {', '.join(FORM_EVENTS)}")
        validate_name(procedure)
        event_id, params = FORM_EVENTS[event]
        holder, index = self._form_event_slot(event_id, create=True)
        entries = [entry for entry in holder[index][1:] if entry[0] != event_id]
        synonym = ["1", "1", ['"ru"', encode_str(procedure)]]
        entries.append([event_id, EVENT_HANDLER_KIND,
                        ["3", encode_str(procedure),
                         ["1", encode_str(procedure), synonym, copy.deepcopy(synonym), copy.deepcopy(synonym),
                          ["4", "0", ["0"], '""', "-1", "-1", "1", "0", '""'], ["0", "0", "0"]]]])
        entries.sort(key=lambda entry: int(entry[0]))
        holder[index] = [str(len(entries))] + entries
        self.ensure_procedure(procedure, params)

    def set_procedure(self, name: str, text: str) -> bool:
        """Replace procedure/function ``name`` in the module with ``text`` (one whole method), or append it.

        Returns True when an existing method was replaced."""
        if self.module_text is None:
            raise FormModelError("E_MODULE_MISSING", "form module is missing")
        validate_name(name)
        if not isinstance(text, str):
            raise FormModelError("E_PROCEDURE_TEXT", "text must be a string")
        lines = text.strip().splitlines()
        header_re = re.compile(r"^[ \t]*(Процедура|Функция|Procedure|Function)[ \t]+([^\s(]+)[ \t]*\(", re.IGNORECASE)
        end_re = re.compile(r"^[ \t]*(КонецПроцедуры|КонецФункции|EndProcedure|EndFunction)\b", re.IGNORECASE)
        headers = [(i, header_re.match(line)) for i, line in enumerate(lines) if header_re.match(line)]
        ends = [(i, end_re.match(line)) for i, line in enumerate(lines) if end_re.match(line)]
        if len(headers) != 1 or len(ends) != 1:
            raise FormModelError("E_PROCEDURE_TEXT", "text must hold exactly one Процедура/Функция ... КонецПроцедуры/"
                                                     "КонецФункции")
        (start, header), (finish, end) = headers[0], ends[0]
        if header.group(2).casefold() != name.casefold():
            raise FormModelError("E_PROCEDURE_TEXT", f"text defines {header.group(2)}, not {name}")
        is_function = header.group(1).casefold() in ("функция", "function")
        if (end.group(1).casefold() in ("конецфункции", "endfunction")) != is_function:
            raise FormModelError("E_PROCEDURE_TEXT", f"{name}: end keyword does not match {header.group(1)}")
        if finish != len(lines) - 1:
            raise FormModelError("E_PROCEDURE_TEXT", f"{name}: nothing may follow the method (code found after "
                                                     "its end keyword)")
        if any(line.strip() and not line.strip().startswith(("//", "&")) for line in lines[:start]):
            raise FormModelError("E_PROCEDURE_TEXT", f"{name}: only comments may precede the method")
        module = self.module_text
        newline = "\r\n" if "\r\n" in module else "\n"
        block = newline.join(lines) + newline
        existing = re.compile(r"^[ \t]*(?:Процедура|Функция|Procedure|Function)[ \t]+" + re.escape(name) + r"[ \t]*\(",
                              re.IGNORECASE | re.MULTILINE).search(module)
        if existing:
            module_end = re.compile(r"^[ \t]*(?:КонецПроцедуры|КонецФункции|EndProcedure|EndFunction)\b[^\r\n]*(?:\r?\n|$)",
                                    re.IGNORECASE | re.MULTILINE).search(module, existing.end())
            if module_end is None:
                raise FormModelError("E_PROCEDURE_TEXT", f"{name}: the module's method has no end")
            begin = existing.start()
            if start > 0:
                # The text brings its own leading comments/directives: replace the ones above the method.
                while begin > 0:
                    previous_start = module.rfind(chr(10), 0, begin - 1) + 1
                    previous = module[previous_start:begin].strip()
                    if previous.startswith(("//", "&")):
                        begin = previous_start
                    else:
                        break
            self.module_text = module[:begin] + block + module[module_end.end():]
            self._module_changed = True
            return True
        ends_in_module = list(re.finditer(
            r"^[ \t]*(?:КонецПроцедуры|КонецФункции|EndProcedure|EndFunction)\b[^\r\n]*(?:\r?\n|$)",
            module, re.IGNORECASE | re.MULTILINE))
        if ends_in_module:
            position = ends_in_module[-1].end()
        else:
            declarations = list(re.finditer(r"^[ \t]*(?:Перем|Var)\b[^\r\n]*(?:\r?\n|$)", module,
                                            re.IGNORECASE | re.MULTILINE))
            position = declarations[-1].end() if declarations else 0
        prefix = module[:position]
        if prefix and not prefix.endswith(("\n", "\r")):
            prefix += newline
        self.module_text = prefix + newline + block + module[position:]
        self._module_changed = True
        return False

    def add_variable(self, name: str) -> bool:
        """Declare a module variable (``Перем <name>;``) before the first method. False if it exists."""
        if self.module_text is None:
            raise FormModelError("E_MODULE_MISSING", "form module is missing")
        validate_name(name)
        module = self.module_text
        if re.search(r"^[ \t]*(?:Перем|Var)\b[^\r\n]*\b" + re.escape(name) + r"\b", module, re.IGNORECASE | re.MULTILINE):
            return False
        newline = "\r\n" if "\r\n" in module else "\n"
        declarations = list(re.finditer(r"^[ \t]*(?:Перем|Var)\b[^\r\n]*(?:\r?\n|$)", module, re.IGNORECASE | re.MULTILINE))
        first_method = re.search(r"^[ \t]*(?:Процедура|Функция|Procedure|Function)\b", module, re.IGNORECASE | re.MULTILINE)
        if declarations and (first_method is None or declarations[-1].start() < first_method.start()):
            position = declarations[-1].end()
        else:
            position = 0
        line = f"Перем {name};{newline}"
        prefix = module[:position]
        if prefix and not prefix.endswith(("\n", "\r")):
            prefix += newline
        self.module_text = prefix + line + module[position:]
        self._module_changed = True
        return True

    def ensure_procedure(self, name: str, params: str) -> bool:
        """Append a stub procedure to the form module unless it already exists."""
        if self.module_text is None:
            raise FormModelError("E_MODULE_MISSING", "form module is missing")
        pattern = re.compile(r"^\s*(?:Процедура|Функция|Procedure|Function)\s+" + re.escape(name) + r"\s*\(",
                             re.IGNORECASE | re.MULTILINE)
        if pattern.search(self.module_text):
            return False
        text = self.module_text
        newline = "\r\n" if "\r\n" in text else "\n"
        stub = (f"{newline}Процедура {name}({params}){newline}"
                f"\t// Сгенерировано one-c-form-compiler: добавьте обработку события.{newline}"
                f"КонецПроцедуры{newline}")
        # Procedures must precede the module body ("основная программа"): insert the
        # stub right after the last procedure/function, else after the variable block.
        ends = list(re.finditer(r"^[ \t]*(?:КонецПроцедуры|КонецФункции|EndProcedure|EndFunction)\b[^\r\n]*(?:\r?\n|$)",
                                text, re.IGNORECASE | re.MULTILINE))
        if ends:
            position = ends[-1].end()
        else:
            declarations = list(re.finditer(r"^[ \t]*(?:Перем|Var)\b[^\r\n]*(?:\r?\n|$)", text,
                                            re.IGNORECASE | re.MULTILINE))
            position = declarations[-1].end() if declarations else 0
        prefix = text[:position]
        if prefix and not prefix.endswith(("\n", "\r")):
            prefix += newline
        self.module_text = prefix + stub + text[position:]
        self._module_changed = True
        return True

    # ------------------------------------------------------------------ templates
    def _style_signature(self, key: str) -> str:
        """Coarse visual signature: scalar flags of the property block plus the font.

        Captions, tooltips, handlers and value types are lists and therefore ignored.
        """
        parent = _style_parent(self.data[key]["raw"][2])
        if parent is None:
            return ""
        style = parent[0]
        if self._type(key) == "Field" and len(parent) > 17:
            # Observed on 8.3 profile 27: [4] border, [12] read-only, [13] skip on input,
            # [15]..[17] alignment/choice flags; other scalars vary with the data type.
            flags = [parent[index] for index in (4, 12, 13, 15, 16, 17)]
        else:
            flags = [item for item in parent if isinstance(item, str)]
        return json.dumps(flags + [style[4]], ensure_ascii=False)

    def _template_raw(self, element_type: str, template: str | None) -> list:
        if template:
            key = self.find(template)
            if self._type(key) != element_type:
                raise FormModelError("E_TEMPLATE_TYPE", f"template {template} is not a {element_type}")
            return self.data[key]["raw"]
        candidates = [key for key in self.element_keys() if self._type(key) == element_type]
        if candidates:
            # Clone the form's dominant look: group candidates by their visual
            # properties (captions, tooltips, handlers and value type ignored) and
            # take the largest group; inside it prefer elements without handlers.
            groups = Counter(self._style_signature(key) for key in candidates)

            def score(key: str) -> tuple:
                info = self.info(key)
                signature = self._style_signature(key)
                # New fields hold primitive values: avoid templates with choice buttons.
                plain = element_type != "Field" or json.loads(signature or "[0,0,0,0,0,\"0\"]")[5] == "0"
                return (not plain, -groups[signature], len(info.handlers), info.id)
            return self.data[min(candidates, key=score)]["raw"]
        from .standards import standard_template
        return standard_template(element_type)


# --------------------------------------------------------------------------- raw helpers

def _action_source(raw: list) -> str | None:
    """Action source id of a command panel (``raw[-2][2]``)."""
    try:
        return raw[-2][2]
    except (IndexError, TypeError):
        return None


def _merge_events(element_type: str, handler: str | None, events: dict[str, str] | None) -> dict[str, str]:
    """``handler`` is a shortcut for the type's main event (click / on change)."""
    merged = dict(events or {})
    if handler:
        default = DEFAULT_EVENT.get(element_type)
        if default is None:
            raise FormModelError("E_HANDLER_UNSUPPORTED", f"{element_type} has no supported events")
        merged.setdefault(default, handler)
    return merged


def _text_width(element_type: str, text: str) -> int:
    """Approximate width of a caption in the default 8pt font (so captions are not cut off)."""
    return {"Label": 6, "CheckBox": 24, "Button": 20}.get(element_type, 6) + round(len(text) * 6.5)


def _check_rect(rect: Rect) -> None:
    if rect.left < 0 or rect.top < 0 or rect.width < 4 or rect.height < 4 or rect.width > 4000 or rect.height > 3000:
        raise FormModelError("E_GEOMETRY_INVALID", f"invalid rectangle {rect.as_dict()}")


def _get(node: Any, path: tuple[int, ...]) -> Any:
    for index in path:
        node = node[index]
    return node


def _set(node: Any, path: tuple[int, ...], value: Any) -> None:
    for index in path[:-1]:
        node = node[index]
    node[path[-1]] = value


def _caption_path(raw: list, element_type: str) -> tuple[int, ...] | None:
    candidates = {
        "Label": (2, 1, 2),
        "Button": (2, 1, 2),
        "Group": (2, 1, 2),
        "CheckBox": (2, 1, 0, 2),
    }
    path = candidates.get(element_type)
    if path is None:
        return None
    try:
        return path if is_loc(_get(raw, path)) else None
    except (IndexError, TypeError):
        return None


def _style_block(node: Any, depth: int = 0) -> list | None:
    """First style record ["13", ...] whose slot 12 is a localized tooltip."""
    if depth > 6 or not isinstance(node, list):
        return None
    if len(node) > 12 and node[0] == "13" and is_loc(node[12]):
        return node
    for item in node:
        found = _style_block(item, depth + 1)
        if found is not None:
            return found
    return None


def _style_parent(node: Any, depth: int = 0) -> list | None:
    """The property block whose first item is the element's style record."""
    if depth > 6 or not isinstance(node, list) or not node:
        return None
    first = node[0]
    if isinstance(first, list) and len(first) > 12 and first[0] == "13":
        return node
    for item in node:
        found = _style_parent(item, depth + 1)
        if found is not None:
            return found
    return None


def _tooltip_node(raw: list) -> list | None:
    style = _style_block(raw[2])
    return style[12] if style is not None else None


def _is_event_list(node: Any) -> bool:
    return (isinstance(node, list) and node and isinstance(node[0], str) and node[0].isdigit()
            and int(node[0]) == len(node) - 1 and len(node) > 1
            and all(isinstance(entry, list) and len(entry) >= 3 and entry[1] == EVENT_HANDLER_KIND
                    for entry in node[1:]))


def _event_lists(node: Any, depth: int = 0) -> list[list]:
    if depth > 4 or not isinstance(node, list):
        return []
    if _is_event_list(node):
        return [node]
    result: list[list] = []
    for item in node:
        result += _event_lists(item, depth + 1)
    return result


def _clear_events(node: Any, depth: int = 0) -> None:
    if depth > 4 or not isinstance(node, list):
        return
    for index, item in enumerate(node):
        if _is_event_list(item):
            node[index] = ["0"]
        else:
            _clear_events(item, depth + 1)


def _anchor_issues(kind: str, expected: Counter, actual: Counter, lenient) -> list[str]:
    """Missing / stale / duplicate anchor entries; ``lenient`` items only need to be present."""
    missing, stale, duplicate = [], [], []
    for item in sorted(set(expected) | set(actual)):
        want, have = expected[item], actual[item]
        if lenient(item):
            if want and not have:
                missing.append(item)
        elif want > have:
            missing.append(item)
        elif have > want:
            (duplicate if want else stale).append(item)
    return ([f"missing {kind} anchor {item}" for item in missing]
            + [f"stale {kind} anchor {item}" for item in stale]
            + [f"duplicate {kind} anchor {item}" for item in duplicate])


def _dangling(entry: Any, ids: set) -> bool:
    """An anchor entry ``[kind, element id, edge]`` whose element does not exist."""
    return (isinstance(entry, list) and len(entry) > 1 and str(entry[1]).isdigit() and int(entry[1]) > 0
            and entry[1] not in ids)


def _panel_index_groups(raw_index: list) -> list[list]:
    groups: list[list] = []
    index = 2
    for _ in range(4):
        count = int(raw_index[index])
        groups.append(list(raw_index[index + 1:index + 1 + count]))
        index += 1 + count
    return groups


def _panel_index_write(raw_index: list, groups: list[list]) -> None:
    index = 2
    for _ in range(4):
        count = int(raw_index[index])
        index += 1 + count
    rest = raw_index[index:]
    body: list = []
    for entries in groups:
        body.append(str(len(entries)))
        body.extend(entries)
    raw_index[2:] = body + rest


def find_form_dirs(unpacked_root: Path) -> list[Path]:
    """All decoded ordinary form directories below an extracted EPF/ERF tree."""
    return sorted(path.parent for path in Path(unpacked_root).rglob("Form.elem.json"))
