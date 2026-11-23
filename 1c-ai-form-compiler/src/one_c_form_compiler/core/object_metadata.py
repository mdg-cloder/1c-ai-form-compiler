"""Object attributes of an external data processor / report decoded by v8unpack.

Ordinary-form fields bind to the object's attributes by attribute UUID; the
attribute type uses the same ``["Pattern", ...]`` record as a field value type.
"""

from __future__ import annotations

import copy
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

# Metadata collection class ids holding object attributes.
ATTRIBUTE_COLLECTIONS = {
    "ec6bb5e5-b7a8-4d75-bec9-658107a699cf",   # ExternalDataProcessor attributes
    "7e7123e0-29e2-11d6-a3c7-0050bae0a776",   # ExternalReport attributes
}
ROOT_FILES = ("ExternalDataProcessor.json", "ExternalReport.json")
ATTRIBUTE_COLLECTIONS_ORDERED = ("ec6bb5e5-b7a8-4d75-bec9-658107a699cf",)


@dataclass(frozen=True, slots=True)
class ObjectAttribute:
    name: str
    uuid: str
    pattern: list
    # Type names as metadata writes them ("CatalogRef.Контрагенты", "String", ...); EDT projects only.
    # Used to follow a dotted data path (Реквизит.Подреквизит) into the referenced object.
    type_names: tuple[str, ...] = ()

    def as_dict(self) -> dict[str, Any]:
        from .ordinary_form import format_value_type
        return {"name": self.name, "type": format_value_type(self.pattern)}

    @property
    def is_boolean(self) -> bool:
        if not self.pattern:
            return False
        return [item[0] for item in self.pattern[1:] if isinstance(item, list) and item] == ['"B"']


def _unquote(token: str) -> str:
    return token[1:-1].replace('""', '"') if len(token) >= 2 and token[0] == token[-1] == '"' else token


def _attribute(item: Any) -> ObjectAttribute | None:
    """Find ["2", ["3", ["1","0",uuid], '"Name"', ...], ["Pattern", ...]] inside one item."""
    stack = [item]
    while stack:
        node = stack.pop()
        if not isinstance(node, list):
            continue
        if (len(node) >= 3 and node[0] == "2" and isinstance(node[1], list) and len(node[1]) > 2
                and node[1][0] == "3" and isinstance(node[1][1], list) and len(node[1][1]) == 3
                and isinstance(node[2], list) and node[2][:1] == ['"Pattern"']):
            return ObjectAttribute(_unquote(node[1][2]), node[1][1][2], copy.deepcopy(node[2]))
        stack.extend(reversed(node))
    return None


def _root_json(source_dir: Path) -> dict | None:
    for name in ROOT_FILES:
        path = Path(source_dir) / name
        if path.is_file():
            return json.loads(path.read_text(encoding="utf-8-sig"))
    return None


def root_file(source_dir: Path) -> Path | None:
    for name in ROOT_FILES:
        path = Path(source_dir) / name
        if path.is_file():
            return path
    return None


def register_data_paths(source_dir: Path, paths: list[tuple["ObjectAttribute", str]]) -> bool:
    """Make sure ``copyinfo`` lists every (attribute, owner type) data path used by forms.

    Designer keeps a table of data paths used by the object's forms in ``copyinfo``::

        ["4", ["N", [attrUuid, attrUuid, "1", [classId, '"Name"']], ...],   # referenced metadata
              ["0"], ["M", [["0", attrUuid], ownerTypeId, ["-1"]], ...], ...]

    The platform resolves an ordinary-form data binding only when it is listed here.
    Returns True when the file was changed.
    """
    path = root_file(source_dir)
    if path is None or not paths:
        return False
    data = json.loads(path.read_text(encoding="utf-8-sig"))
    try:
        info = data["copyinfo"][0]
        refs, table = info[1], info[3]
    except (KeyError, IndexError, TypeError):
        raise ValueError("copyinfo has an unknown format") from None
    if not (isinstance(refs, list) and refs and refs[0].isdigit() and isinstance(table, list) and table
            and table[0].isdigit()):
        raise ValueError("copyinfo has an unknown format")
    changed = False
    known_refs = {entry[0] for entry in refs[1:] if isinstance(entry, list) and entry}
    known_paths = {(json.dumps(entry[0]), entry[1]) for entry in table[1:] if isinstance(entry, list) and len(entry) > 1}
    for attribute, owner_type in paths:
        if attribute.uuid not in known_refs:
            refs.append([attribute.uuid, attribute.uuid, "1",
                         [next(iter(ATTRIBUTE_COLLECTIONS_ORDERED)), '"' + attribute.name.replace('"', '""') + '"']])
            known_refs.add(attribute.uuid)
            changed = True
        key = (json.dumps(["0", attribute.uuid]), owner_type)
        if key not in known_paths:
            table.append([["0", attribute.uuid], owner_type, ["-1"]])
            known_paths.add(key)
            changed = True
    if changed:
        refs[0] = str(len(refs) - 1)
        table[0] = str(len(table) - 1)
        with open(path, "w", encoding="utf-8") as stream:
            json.dump(data, stream, ensure_ascii=False, indent=2)
    return changed


def read_object_type_id(source_dir: Path) -> str | None:
    """TypeId of ExternalDataProcessorObject.<Name> / ExternalReportObject.<Name>.

    Stored in the object header as ["4", TypeId, ValueId, ...] (GeneratedType "Object").
    """
    data = _root_json(source_dir)
    try:
        record = data["header"][0][3][1][1]
        if record[0] == "4" and len(record[1]) == 36:
            return record[1]
    except (KeyError, IndexError, TypeError):
        pass
    return None


def read_object_attributes(source_dir: Path) -> dict[str, ObjectAttribute]:
    data = _root_json(source_dir)
    if data is None:
        return {}
    result: dict[str, ObjectAttribute] = {}
    stack = [data.get("header")]
    while stack:
        node = stack.pop()
        if isinstance(node, list):
            if (node and isinstance(node[0], str) and node[0] in ATTRIBUTE_COLLECTIONS and len(node) >= 2
                    and isinstance(node[1], str) and node[1].isdigit()):
                for item in node[2:2 + int(node[1])]:
                    attribute = _attribute(item)
                    if attribute:
                        result[attribute.name] = attribute
                continue
            stack.extend(node)
        elif isinstance(node, dict):
            stack.extend(node.values())
    return result
