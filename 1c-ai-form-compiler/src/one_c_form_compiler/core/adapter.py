"""Evidence-bounded structural adapter for ordinary FormElements27 payloads.

Only offsets used by v8unpack 1.2.13's FormElements27 decoder are interpreted.
Every selected element retains its original syntax node for unknown-field
preservation. This module performs no file I/O and never mutates a parsed tree.
"""

from __future__ import annotations

from dataclasses import dataclass
from types import MappingProxyType
from typing import Mapping

from .contracts import Node, Token
from .parser import ParsedForm


ROOT_PANEL_UUID = "09ccdc77-ea1a-4a6d-ab1c-3435eada2433"

# UUID-to-name table copied from the FormItemTypes enum in the pinned
# v8unpack 1.2.13 FormElements27/FormElement.py. Unknown UUIDs remain opaque.
ELEMENT_TYPES: Mapping[str, str] = MappingProxyType({
    "381ed624-9217-4e63-85db-c4c3cb87daae": "Field",
    "35af3d93-d7c7-4a2e-a8eb-bac87a1a3f26": "CheckBox",
    "782e569a-79a7-4a4f-a936-b48d013936ec": "RadioBtn",
    "64483e7f-3833-48e2-8c75-2c31aac49f6e": "SelectField",
    "e69bf21d-97b2-4f37-86db-675aea9ec2cb": "CommandPanel",
    "6ff79819-710e-4145-97cd-1618da79e3e2": "Button",
    "151ef23e-6bb2-4681-83d0-35bc2217230c": "Image",
    "90db814a-c75f-4b54-bc96-df62e554d67d": "Group",
    "ea83fe3a-ac3c-4cce-8045-3dddf35b28b1": "Table",
    ROOT_PANEL_UUID: "Panel",
    "0fc7e20d-f241-460c-bdf4-5ad88e5474a5": "Label",
    "19f8b798-314e-4b4e-8121-905b2a7a03f5": "ListField",
    "36e52348-5d60-4770-8e89-a16ed50a2006": "Separator",
    "d92a805c-98ae-4750-9158-d9ce7cec2f20": "FieldHtml",
    "b1db1f86-abbb-4cf0-8852-fe6ae21650c2": "Indicator",
    "e3c063d8-ef92-41be-9c89-b70290b5368b": "CalendarBox",
    "6c06cd5d-8481-4b6f-a90a-7a97a8bb8bef": "TrackBar",
    "14c4a229-bfc3-42fe-9ce1-2da049fd0109": "TextDocumentField",
    "42248403-7748-49da-b782-e4438fd7bff3": "GraphicalSchemaField",
    "ad37194e-555e-4305-b718-5dca84baf145": "GeographicalSchemaField",
    "a8b97779-1a4b-4059-b09c-807f86d2a461": "Chart",
    "e5fdc112-5c84-4a16-9728-72b85692b6e2": "GanttChart",
    "a26da99e-184a-4823-b0d6-62816d38dc4e": "PivotChart",
    "984981b1-622d-4ebc-94f7-885f0cdfb59a": "Dendrogram",
})


class AdapterError(ValueError):
    """An input cannot be adapted without guessing or losing structure."""


class AmbiguousFormError(AdapterError):
    """Several forms were supplied and the caller did not select one."""

    def __init__(self, addresses: tuple[str, ...]) -> None:
        self.addresses = addresses
        super().__init__(f"form selection is ambiguous; choose one of: {', '.join(addresses)}")


@dataclass(frozen=True, slots=True)
class FormCandidate:
    address: str
    parsed: ParsedForm


@dataclass(frozen=True, slots=True)
class ControlEntry:
    """Fields with decoder-backed semantics plus its lossless source record."""

    control_type: str | None
    type_uuid: str
    identifier: str
    name: str
    parent_identifier: str | None
    page_reference: str | None
    page_name: str | None
    property_identifier: str | None
    property_name: str | None
    geometry: None
    raw_record: Node
    source_span: tuple[int, int]


@dataclass(frozen=True, slots=True)
class AdaptedForm:
    address: str
    profile_id: str
    controls: tuple[ControlEntry, ...]
    by_identifier: Mapping[str, ControlEntry]
    by_name: Mapping[str, tuple[ControlEntry, ...]]
    by_parent: Mapping[str | None, tuple[ControlEntry, ...]]
    unknown_elements: tuple[Node, ...]
    unknown_links: tuple[Node, ...]
    raw_form: Node


def _items(node: Node) -> list[Node | Token]:
    return [
        child for child in node.children
        if not (isinstance(child, Token) and child.kind in {
            "OPEN_BRACE", "CLOSE_BRACE", "COMMA", "WHITESPACE", "BOM"
        })
    ]


def _at(value: Node | Token, index: int, path: str) -> Node | Token:
    if not isinstance(value, Node) or value.kind != "list":
        raise AdapterError(f"expected list at {path}")
    values = _items(value)
    try:
        return values[index]
    except IndexError as exc:
        raise AdapterError(f"missing required item {index} at {path}") from exc


def _scalar(value: Node | Token, encoding: str, path: str) -> str | None:
    if isinstance(value, Node):
        raise AdapterError(f"expected scalar at {path}")
    if value.kind == "EMPTY_VALUE":
        return None
    if value.kind not in {"ATOM", "STRING"}:
        raise AdapterError(f"unsupported scalar token {value.kind} at {path}")
    text = value.raw.decode(encoding, errors="strict")
    if value.kind == "STRING":
        if len(text) < 2 or not (text.startswith('"') and text.endswith('"')):
            raise AdapterError(f"malformed quoted string at {path}")
        return text[1:-1]
    return text


def _value(parent: Node | Token, index: int, encoding: str, path: str) -> str | None:
    return _scalar(_at(parent, index, path), encoding, path)


def _list(value: Node | Token, path: str) -> Node:
    if not isinstance(value, Node) or value.kind != "list":
        raise AdapterError(f"expected list at {path}")
    return value


def _counted_records(value: Node | Token, encoding: str, path: str) -> list[Node]:
    counted = _list(value, path)
    items = _items(counted)
    if not items:
        raise AdapterError(f"missing count at {path}")
    raw_count = _scalar(items[0], encoding, f"{path}[0]")
    try:
        count = int(raw_count) if raw_count is not None else -1
    except ValueError as exc:
        raise AdapterError(f"invalid record count at {path}") from exc
    records = items[1:]
    if count < 0 or len(records) != count:
        raise AdapterError(f"record count mismatch at {path}: declared {count}, found {len(records)}")
    if any(not isinstance(record, Node) or record.kind != "list" for record in records):
        raise AdapterError(f"non-record child in counted list at {path}")
    return list(records)  # type: ignore[return-value]


def _page_offset(raw: Node, encoding: str) -> int:
    # Literal counter path from v8unpack 1.2.13 Panel.pages_offset().
    counters = ((2, 1), (1, 1), (1, 1), (1, 1), (1, 1), (1, 1), (4, 0))
    values = _items(raw)
    index = 0
    for relative, size in counters:
        index += relative
        if size:
            if index >= len(values):
                raise AdapterError("page descriptor offset exceeds its record")
            try:
                count = int(_scalar(values[index], encoding, f"pageDescriptor[{index}]") or "")
            except ValueError as exc:
                raise AdapterError("invalid page descriptor counter") from exc
            if count < 0:
                raise AdapterError("negative page descriptor counter")
            index += count * size
    if index >= len(values):
        raise AdapterError("page descriptor offset has no page list")
    return index


def _decode_pages(raw: Node, encoding: str) -> tuple[str, ...]:
    offset = _page_offset(raw, encoding)
    page_list = _list(_items(raw)[offset], f"pageDescriptor[{offset}]")
    items = _items(page_list)
    if len(items) < 2:
        raise AdapterError("page list is truncated")
    version = _scalar(items[0], encoding, "pageList.version")
    if version != "1":
        raise AdapterError(f"unsupported page list version: {version!r}")
    try:
        count = int(_scalar(items[1], encoding, "pageList.count") or "")
    except ValueError as exc:
        raise AdapterError("invalid page count") from exc
    if count < 0 or len(items) != count + 2:
        raise AdapterError(f"page count mismatch: declared {count}, found {len(items) - 2}")
    names: list[str] = []
    for index, page in enumerate(items[2:]):
        names.append(_value(page, 6, encoding, f"page[{index}].name") or "")
    return tuple(names)


def _property_index(form: Node, encoding: str) -> tuple[dict[str, str], dict[str, tuple[str, str]], tuple[Node, ...]]:
    props = _counted_records(_at(_at(form, 2, "form"), 2, "form[2]"), encoding, "form[2][2]")
    prop_names: dict[str, str] = {}
    for i, prop in enumerate(props):
        prop_id = _value(_at(prop, 0, f"property[{i}]"), 0, encoding, f"property[{i}].id")
        prop_name = _value(prop, 4, encoding, f"property[{i}].name")
        if prop_id is None or prop_name is None:
            raise AdapterError(f"incomplete property record at index {i}")
        if prop_id in prop_names:
            raise AdapterError(f"duplicate property identifier {prop_id!r}")
        prop_names[prop_id] = prop_name

    links = _counted_records(_at(_at(form, 2, "form"), 3, "form[2]"), encoding, "form[2][3]")
    linked_names: dict[str, tuple[str, str]] = {}
    unknown_links: list[Node] = []
    for i, link in enumerate(links):
        elem_id = _value(link, 0, encoding, f"link[{i}].elementId")
        prop_id = _value(_at(_at(link, 1, f"link[{i}]"), 1, f"link[{i}][1]"), 0, encoding, f"link[{i}].propertyId")
        if elem_id is None or prop_id is None:
            raise AdapterError(f"incomplete element-property link at index {i}")
        prop_name = prop_names.get(prop_id)
        if prop_name is not None:
            if elem_id in linked_names:
                raise AdapterError(f"duplicate element-property link for {elem_id!r}")
            linked_names[elem_id] = (prop_id, prop_name)
        else:
            unknown_links.append(link)
    return prop_names, linked_names, tuple(unknown_links)


def _adapt_selected(candidate: FormCandidate) -> AdaptedForm:
    parsed = candidate.parsed
    if parsed.profile_id != "0-27":
        raise AdapterError(f"unsupported form profile {parsed.profile_id!r}; only 0-27 is evidenced")
    form = parsed.form
    if _value(form, 0, parsed.encoding, "form.version") != "27":
        raise AdapterError("profile 0-27 requires form serialization version 27")
    root_panel = _at(_at(form, 1, "form"), 2, "form[1]")
    root_uuid = _value(root_panel, 0, parsed.encoding, "form[1][2]")
    if root_uuid != ROOT_PANEL_UUID:
        raise AdapterError(f"unsupported root controls type {root_uuid!r}")

    _, linked_names, unknown_links = _property_index(form, parsed.encoding)
    controls: list[ControlEntry] = []
    unknown: list[Node] = []
    by_id: dict[str, ControlEntry] = {}
    by_name: dict[str, list[ControlEntry]] = {}
    by_parent: dict[str | None, list[ControlEntry]] = {}

    def walk_panel(panel: Node | Token, parent_id: str | None, path: str, *, is_root: bool = False) -> None:
        panel_node = _list(panel, path)
        if is_root:
            page_field = _at(_at(panel_node, 1, path), 1, f"{path}[1]")
            children_field = _at(panel_node, 2, path)
        else:
            # v8unpack FormElements27.Panel.decode: a list at record[1] is the
            # root panel layout; scalar record[1] marks a child Panel.
            discriminator = _at(panel_node, 1, path)
            child_panel = not isinstance(discriminator, Node)
            if child_panel:
                page_field = _at(_at(panel_node, 2, path), 1, f"{path}[2]")
                children_field = _at(panel_node, len(_items(panel_node)) - 1, path)
            else:
                page_field = _at(discriminator, 1, f"{path}[1]")
                children_field = _at(panel_node, 2, path)
        pages = _decode_pages(_list(page_field, f"{path}.pages"), parsed.encoding)
        records = _counted_records(children_field, parsed.encoding, f"{path}.controls")
        for index, record in enumerate(records):
            record_path = f"{path}.controls[{index}]"
            type_uuid = _value(record, 0, parsed.encoding, f"{record_path}.type")
            identifier = _value(record, 1, parsed.encoding, f"{record_path}.id")
            if type_uuid is None or identifier is None:
                raise AdapterError(f"missing type or identifier at {record_path}")
            control_type = ELEMENT_TYPES.get(type_uuid)
            if control_type is None:
                unknown.append(record)
                continue
            name = _value(_at(record, len(_items(record)) - 2, record_path), 1, parsed.encoding, f"{record_path}.name")
            if name is None:
                raise AdapterError(f"missing control name at {record_path}")
            page_ref = _value(_at(record, len(_items(record)) - 3, record_path), len(_items(_at(record, len(_items(record)) - 3, record_path))) - 5, parsed.encoding, f"{record_path}.page")
            page_name: str | None = None
            if page_ref:
                try:
                    if page_ref != str(int(page_ref)):
                        raise AdapterError(f"non-canonical page index {page_ref!r} at {record_path}")
                    page_index = int(page_ref)
                    page_name = pages[page_index]
                except ValueError as exc:
                    raise AdapterError(f"invalid page index {page_ref!r} at {record_path}") from exc
                except IndexError as exc:
                    raise AdapterError(f"page index out of range {page_ref!r} at {record_path}") from exc
            property_link = linked_names.get(identifier)
            entry = ControlEntry(
                control_type=control_type,
                type_uuid=type_uuid,
                identifier=identifier,
                name=name,
                parent_identifier=parent_id,
                page_reference=page_ref,
                page_name=page_name,
                property_identifier=property_link[0] if property_link else None,
                property_name=property_link[1] if property_link else None,
                geometry=None,
                raw_record=record,
                source_span=(record.start, record.end),
            )
            if identifier in by_id:
                raise AdapterError(f"duplicate control identifier {identifier!r}")
            controls.append(entry)
            by_id[identifier] = entry
            by_name.setdefault(name, []).append(entry)
            by_parent.setdefault(parent_id, []).append(entry)
            if control_type == "Panel":
                walk_panel(record, identifier, record_path)

    walk_panel(root_panel, None, "form[1][2]", is_root=True)
    return AdaptedForm(
        address=candidate.address,
        profile_id=parsed.profile_id,
        controls=tuple(controls),
        by_identifier=MappingProxyType(by_id),
        by_name=MappingProxyType({key: tuple(value) for key, value in by_name.items()}),
        by_parent=MappingProxyType({key: tuple(value) for key, value in by_parent.items()}),
        unknown_elements=tuple(unknown),
        unknown_links=unknown_links,
        raw_form=form,
    )


def adapt_forms(candidates: tuple[FormCandidate, ...], *, selected_address: str | None = None) -> AdaptedForm:
    """Select and index one ordinary form. Empty/ambiguous/errors are read-only."""
    if not candidates:
        raise AdapterError("no form candidates were supplied")
    addresses = tuple(candidate.address for candidate in candidates)
    if any(not address for address in addresses) or len(set(addresses)) != len(addresses):
        raise AdapterError("candidate form addresses must be non-empty and unique")
    if selected_address is None:
        if len(candidates) != 1:
            raise AmbiguousFormError(addresses)
        selected = candidates[0]
    else:
        matches = [candidate for candidate in candidates if candidate.address == selected_address]
        if not matches:
            raise AdapterError(f"selected form address not found: {selected_address!r}")
        selected = matches[0]
    return _adapt_selected(selected)
