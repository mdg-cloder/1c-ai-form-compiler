"""Declarative edit plans for ordinary forms.

A plan is JSON::

    {"operations": [
      {"op": "add", "type": "Field", "name": "Скидка", "value_type": "Число(15,2)",
       "label": "Скидка:", "tooltip": "...", "container": "ОсновнаяПанель", "page": "Дополнительно",
       "beside": "Контрагент", "left": 8, "top": 300, "width": 120, "height": 19, "handler": "..."},
      {"op": "modify", "name": "Контрагент", "caption": "...", "tooltip": "...",
       "left": 8, "top": 30, "width": 200, "height": 19, "value_type": "...", "handler": "..."},
      {"op": "remove", "name": "НадписьСтарая", "force": false},
      {"op": "set_title", "title": "Новый заголовок"}
    ]}

``add`` with ``"label"`` (Field/Button) creates a caption Label (``Надпись<Name>``) and the element in
one row: a new row (label column + field column), the row of ``beside`` (right after it), a half of a
two-column page (``"column": 1|2``) or at ``left``/``top`` (:meth:`OrdinaryForm.place_pair`). Geometry not
given explicitly is chosen by :meth:`OrdinaryForm.place`. ``set_form_size`` resizes the form by the anchor
model (:meth:`OrdinaryForm.resize_form`).
"""

from __future__ import annotations

import json
from typing import Any

from .ordinary_form import ADDABLE_TYPES, EVENTS, FORM_EVENTS, FormModelError, OrdinaryForm, Rect, loc_to_dict
from .table_field import CELL_EVENTS, ROW_NUMBER

OPS = ("add", "modify", "remove", "set_title", "set_form_events", "set_procedure", "add_variable", "add_page",
       "remove_page", "add_panel", "add_command_panel", "add_button", "remove_button", "add_column", "modify_column",
       "remove_column", "set_form_size")
_ADD_KEYS = {"op", "type", "name", "caption", "label", "tooltip", "value_type", "container", "page",
             "beside", "left", "top", "width", "height", "handler", "template", "data", "events", "columns",
             "stretch", "read_only", "column"}
_COLUMN_KEYS = {
    "add_column": {"op", "table", "name", "data", "caption", "width", "after", "events", "checkbox"},
    "modify_column": {"op", "table", "name", "data", "caption", "width", "events"},
    "remove_column": {"op", "table", "name", "force"},
}
_MODIFY_KEYS = {"op", "name", "caption", "tooltip", "value_type", "handler", "left", "top", "width", "height",
                "data", "events", "read_only"}
_TYPE_ALIASES = {
    "надпись": "Label", "label": "Label", "полеввода": "Field", "поле": "Field", "field": "Field",
    "inputfield": "Field", "кнопка": "Button", "button": "Button", "флажок": "CheckBox",
    "checkbox": "CheckBox", "рамкагруппы": "Group", "рамка": "Group", "group": "Group",
    "табличноеполе": "Table", "таблица": "Table", "table": "Table", "панель": "Panel", "panel": "Panel",
}
_ADD_BUTTON_KEYS = {"op", "panel", "name", "procedure", "standard", "caption", "tooltip", "before", "after",
                    "separator"}
_ADD_PANEL_KEYS = {"op", "name", "pages", "container", "page", "left", "top", "width", "height", "stretch"}
ROW_TYPES = ("Label", "Field", "Button", "CheckBox")   # tab order follows the row (tab_after_row)
MAX_OPERATIONS = 100   # module code (set_procedure) makes real plans long


class PlanError(FormModelError):
    def __init__(self, index: int | None, code: str, message: str) -> None:
        self.index = index
        prefix = f"operation #{index + 1}: " if index is not None else ""
        super().__init__(code, prefix + message)


def parse_plan(source: str | dict | list) -> list[dict[str, Any]]:
    if isinstance(source, str):
        try:
            source = json.loads(source)
        except ValueError as error:
            raise PlanError(None, "E_PLAN_JSON", f"plan is not valid JSON: {error}") from None
    if isinstance(source, list):
        source = {"operations": source}
    if not isinstance(source, dict) or not isinstance(source.get("operations"), list):
        raise PlanError(None, "E_PLAN_SHAPE", 'plan must be an object with an "operations" array')
    operations = source["operations"]
    if not operations:
        raise PlanError(None, "E_PLAN_EMPTY", "plan has no operations")
    if len(operations) > MAX_OPERATIONS:
        raise PlanError(None, "E_PLAN_TOO_LARGE", f"plan exceeds {MAX_OPERATIONS} operations")
    for index, operation in enumerate(operations):
        if not isinstance(operation, dict) or operation.get("op") not in OPS:
            raise PlanError(index, "E_PLAN_OP", f"op must be one of {', '.join(OPS)}")
        allowed = {"add": _ADD_KEYS, "modify": _MODIFY_KEYS, "remove": {"op", "name", "force"},
                   "set_title": {"op", "title"}, "add_page": {"op", "container", "name", "caption"},
                   "remove_page": {"op", "container", "page", "force"}, "add_panel": _ADD_PANEL_KEYS,
                   "set_form_events": {"op", "events"}, "set_procedure": {"op", "name", "text"}, "add_variable": {"op", "name"},
                   "add_command_panel": {"op", "name", "table", "autofill", "standard", "buttons", "height", "gap",
                                         "shift"},
                   "set_form_size": {"op", "width", "height"},
                   "add_button": _ADD_BUTTON_KEYS, "remove_button": {"op", "panel", "name", "force"},
                   **_COLUMN_KEYS}[operation["op"]]
        unknown = set(operation) - allowed
        if unknown:
            raise PlanError(index, "E_PLAN_FIELD", f"unknown fields {sorted(unknown)} for {operation['op']}")
        for key in ("left", "top", "width", "height"):
            if key in operation and (isinstance(operation[key], bool) or not isinstance(operation[key], int)):
                raise PlanError(index, "E_PLAN_FIELD", f"{key} must be an integer")
        events = operation.get("events")
        if events is not None and not (isinstance(events, dict)
                                       and all(isinstance(k, str) and isinstance(v, str) for k, v in events.items())):
            raise PlanError(index, "E_PLAN_FIELD", 'events must be an object {"Событие": "Процедура"}')
        columns = operation.get("columns")
        if columns is not None and not (isinstance(columns, list) and all(isinstance(c, str) for c in columns)):
            raise PlanError(index, "E_PLAN_FIELD", "columns must be an array of names")
        if "read_only" in operation and not isinstance(operation["read_only"], bool):
            raise PlanError(index, "E_PLAN_FIELD", "read_only must be true or false")
        if "column" in operation and operation["column"] not in (1, 2) or isinstance(operation.get("column"), bool):
            raise PlanError(index, "E_PLAN_FIELD", "column must be 1 (left half) or 2 (right half)")
        if operation["op"] == "set_form_size" and "width" not in operation and "height" not in operation:
            raise PlanError(index, "E_PLAN_FIELD", "set_form_size requires width and/or height")
        if "shift" in operation and not isinstance(operation["shift"], bool):
            raise PlanError(index, "E_PLAN_FIELD", "shift must be true or false")
        if "checkbox" in operation and not isinstance(operation["checkbox"], bool):
            raise PlanError(index, "E_PLAN_FIELD", "checkbox must be true or false")
        if "stretch" in operation and operation["stretch"] not in ("width", "height", "both"):
            raise PlanError(index, "E_PLAN_FIELD", "stretch must be width, height or both")
        if operation["op"] == "add_command_panel":
            for key in ("name", "table"):
                if not isinstance(operation.get(key), str) or not operation[key].strip():
                    raise PlanError(index, "E_PLAN_FIELD", f"add_command_panel requires {key}")
            if "autofill" in operation and not isinstance(operation["autofill"], bool):
                raise PlanError(index, "E_PLAN_FIELD", "autofill must be true or false")
            standard = operation.get("standard")
            if standard is not None and not (isinstance(standard, list) and all(isinstance(s, str) for s in standard)):
                raise PlanError(index, "E_PLAN_FIELD", "standard must be an array of action names")
            buttons = operation.get("buttons")
            if buttons is not None and not (isinstance(buttons, list) and all(
                    isinstance(b, dict) and isinstance(b.get("name"), str) and isinstance(b.get("procedure"), str)
                    and set(b) <= {"name", "procedure", "caption", "tooltip"} for b in buttons)):
                raise PlanError(index, "E_PLAN_FIELD",
                                'buttons must be [{"name", "procedure", "caption"?, "tooltip"?}]')
        if operation["op"] in ("add_button", "remove_button"):
            for key in ("panel", "name"):
                if not isinstance(operation.get(key), str) or not operation[key].strip():
                    raise PlanError(index, "E_PLAN_FIELD", f"{operation['op']} requires {key}")
            for key in ("procedure", "standard", "before", "after"):
                if key in operation and not isinstance(operation[key], str):
                    raise PlanError(index, "E_PLAN_FIELD", f"{key} must be a string")
            if "separator" in operation and not isinstance(operation["separator"], bool):
                raise PlanError(index, "E_PLAN_FIELD", "separator must be true or false")
            for key in ("caption", "tooltip"):
                value = operation.get(key)
                if value is not None and not (isinstance(value, str) or (
                        isinstance(value, dict) and value and all(isinstance(v, str) for v in value.values()))):
                    raise PlanError(index, "E_PLAN_FIELD", f'{key} must be a string or {{"ru": "...", "uk": "..."}}')
        if operation["op"] == "set_form_events" and not events:
            raise PlanError(index, "E_PLAN_FIELD", "set_form_events requires events")
        if operation["op"] == "add_page":
            for key in ("container", "name"):
                if not isinstance(operation.get(key), str) or not operation[key].strip():
                    raise PlanError(index, "E_PLAN_FIELD", f"add_page requires {key}")
        if operation["op"] == "remove_page":
            for key in ("container", "page"):
                if not isinstance(operation.get(key), str) or not operation[key].strip():
                    raise PlanError(index, "E_PLAN_FIELD", f"remove_page requires {key}")
        if operation["op"] == "add_panel":
            if not isinstance(operation.get("name"), str) or not operation["name"].strip():
                raise PlanError(index, "E_PLAN_FIELD", "add_panel requires name")
            pages = operation.get("pages")
            if not isinstance(pages, list) or not pages:
                raise PlanError(index, "E_PLAN_FIELD", 'add_panel requires pages: ["Имя", ...] or '
                                                       '[{"name": "Имя", "caption": "..."}, ...]')
            for key in ("container", "page"):
                if key in operation and operation[key] is not None and not isinstance(operation[key], str):
                    raise PlanError(index, "E_PLAN_FIELD", f"{key} must be a string")
        if "force" in operation and not isinstance(operation["force"], bool):
            raise PlanError(index, "E_PLAN_FIELD", "force must be true or false")
        if operation["op"] in _COLUMN_KEYS:
            for key in ("data", "after"):
                if key in operation and operation[key] is not None and not isinstance(operation[key], str):
                    raise PlanError(index, "E_PLAN_FIELD", f"{key} must be a string")
            caption = operation.get("caption")
            if caption is not None and not (isinstance(caption, str) or (
                    isinstance(caption, dict) and all(isinstance(v, str) for v in caption.values()))):
                raise PlanError(index, "E_PLAN_FIELD", 'caption must be a string or {"ru": "...", "uk": "..."}')
            for key in ("table", "name"):
                if not isinstance(operation.get(key), str) or not operation[key].strip():
                    raise PlanError(index, "E_PLAN_FIELD", f"{operation['op']} requires {key}")
        if operation["op"] == "add":
            operation["type"] = normalize_type(index, operation.get("type"))
    return operations


def normalize_type(index: int, value: Any) -> str:
    if not isinstance(value, str):
        raise PlanError(index, "E_PLAN_FIELD", "add requires type")
    resolved = _TYPE_ALIASES.get(value.strip().casefold().replace(" ", ""), value.strip())
    if resolved == "Panel":
        raise PlanError(index, "E_TYPE_UNSUPPORTED", 'a panel is added with {"op": "add_panel", "name": ..., '
                                                     '"pages": [...]}, not with add')
    if resolved not in ADDABLE_TYPES:
        raise PlanError(index, "E_TYPE_UNSUPPORTED",
                        f"type {value!r} cannot be added; supported: {', '.join(ADDABLE_TYPES)}")
    return resolved


def _rect_override(form: OrdinaryForm, name: str, op: dict) -> Rect | None:
    keys = ("left", "top", "width", "height")
    if not any(key in op for key in keys):
        return None
    current = form.info(form.find(name)).rect
    left = op.get("left", current.left)
    top = op.get("top", current.top)
    return Rect.from_xywh(left, top, op.get("width", current.width), op.get("height", current.height))


def apply_plan(form: OrdinaryForm, source: str | dict | list) -> list[dict[str, Any]]:
    """Apply all operations in order; raise PlanError on the first failure."""
    operations = parse_plan(source)
    results: list[dict[str, Any]] = []
    for index, op in enumerate(operations):
        try:
            results.extend(_apply_one(form, op))
        except PlanError:
            raise
        except FormModelError as error:
            raise PlanError(index, error.code, str(error)) from None
        issues = form.new_issues()
        if issues:
            raise PlanError(index, "E_INTEGRITY", "operation broke form integrity: " + "; ".join(issues[:3]))
    return results


def _apply_one(form: OrdinaryForm, op: dict) -> list[dict[str, Any]]:
    kind = op["op"]
    if kind in _COLUMN_KEYS:
        from . import table_field
        if kind == "remove_column":
            table_field.remove_column(form, op["table"], op["name"], force=bool(op.get("force")))
            return [{"op": kind, "table": op["table"], "name": op["name"]}]
        call = table_field.add_column if kind == "add_column" else table_field.modify_column
        extra = {"after": op.get("after"), "checkbox": bool(op.get("checkbox"))} if kind == "add_column" else {}
        column = call(form, op["table"], op["name"], data=op.get("data"), caption=op.get("caption"),
                      width=op.get("width"), events=op.get("events"), **extra)
        return [{"op": kind, "table": op["table"], **column.as_dict()}]
    if kind == "add_command_panel":
        from .command_panel import add_command_panel
        options = {key: op[key] for key in ("autofill", "standard", "buttons", "height", "gap", "shift") if key in op}
        return [{"op": kind, **add_command_panel(form, op["name"], op["table"], **options)}]
    if kind == "add_button":
        from .command_panel import add_button
        options = {key: op[key] for key in ("procedure", "standard", "caption", "tooltip", "before", "after")
                   if key in op}
        return [{"op": kind, **add_button(form, op["panel"], op["name"], separator=bool(op.get("separator")),
                                          **options)}]
    if kind == "remove_button":
        from .command_panel import remove_button
        return [{"op": kind, **remove_button(form, op["panel"], op["name"], force=bool(op.get("force")))}]
    if kind == "set_form_size":
        previous = form.size
        moved = form.resize_form(op.get("width"), op.get("height"))
        return [{"op": kind, "width": form.size[0], "height": form.size[1],
                 "previous": {"width": previous[0], "height": previous[1]}, "moved": moved}]
    if kind == "add_variable":
        return [{"op": kind, "name": op.get("name"), "added": form.add_variable(op.get("name"))}]
    if kind == "set_procedure":
        replaced = form.set_procedure(op.get("name"), op.get("text"))
        return [{"op": kind, "name": op.get("name"), "replaced": replaced}]
    if kind == "set_form_events":
        for event, procedure in op["events"].items():
            form.set_form_handler(event, procedure)
        events = form.form_events()
        return [{"op": kind, "events": {event: events[event] for event in sorted(
            op["events"], key=lambda name: int(FORM_EVENTS[name][0]))}}]
    if kind == "add_page":
        return [{"op": kind, **form.add_page(op["container"], op["name"], op.get("caption"))}]
    if kind == "remove_page":
        from .panel import remove_page
        return [{"op": kind, **remove_page(form, op["container"], op["page"], force=bool(op.get("force")))}]
    if kind == "add_panel":
        from .panel import add_panel
        options = {key: op.get(key) for key in ("container", "page", "left", "top", "width", "height", "stretch")}
        return [{"op": kind, **add_panel(form, op["name"], op["pages"], **options)}]
    if kind == "remove":
        name = op.get("name")
        removed = form.remove_element(name, force=bool(op.get("force")))
        result = {"op": "remove", "name": name}
        if len(removed) > 1:
            result["also_removed"] = removed[1:]
        return [result]
    if kind == "set_title":
        title = op.get("title")
        if not isinstance(title, str) or not title.strip():
            raise FormModelError("E_PLAN_FIELD", "set_title requires a non-empty title")
        form.set_title(title)
        return [{"op": "set_title", "title": title}]
    if kind == "modify":
        info = form.modify_element(op["name"], caption=op.get("caption"), tooltip=op.get("tooltip"),
                                   rect=_rect_override(form, op["name"], op),
                                   value_type=op.get("value_type"), handler=op.get("handler"),
                                   data=op.get("data"), events=op.get("events"), read_only=op.get("read_only"))
        return [{"op": "modify", **info.as_dict()}]
    # add
    element_type = op["type"]
    name = op.get("name")
    label = op.get("label")
    column = op.get("column")
    stretch = op.get("stretch")
    if column == 2 and element_type == "Field" and stretch is None and op.get("width") is None:
        stretch = "width"        # the right column's field follows the right border, as in typical headers
    results = []
    geometry = {key: op.get(key) for key in ("width", "height", "left", "top")}
    if label and element_type in ("Field", "Button"):
        label_rect, rect = form.place_pair(element_type, label, container=op.get("container"), page=op.get("page"),
                                           beside=op.get("beside"), column=column, **geometry)
        label_info = form.add_element(element_type="Label", name="Надпись" + name, rect=label_rect,
                                      container=op.get("container"), page=op.get("page"), caption=label)
        form.tab_after_row(label_info.key)
        results.append({"op": "add", **form.info(label_info.key).as_dict()})
    else:
        if label:
            raise FormModelError("E_PLAN_FIELD", "label applies to Field or Button; use caption for "
                                 f"{element_type}")
        caption_text = op.get("caption") if isinstance(op.get("caption"), str) else name
        rect = form.place(element_type, container=op.get("container"), page=op.get("page"), text=caption_text,
                          beside=op.get("beside"), column=column, **geometry)
    info = form.add_element(element_type=element_type, name=name, rect=rect, container=op.get("container"),
                            page=op.get("page"), caption=op.get("caption"), tooltip=op.get("tooltip"),
                            template=op.get("template"), value_type=op.get("value_type"),
                            handler=op.get("handler"), data=op.get("data"), events=op.get("events"),
                            columns=op.get("columns"), stretch=stretch, read_only=op.get("read_only"))
    if element_type in ROW_TYPES:
        form.tab_after_row(info.key)
        info = form.info(info.key)
    added = {"op": "add", **info.as_dict()}
    if element_type == "Table":
        from .table_field import is_value_table, list_columns
        added["columns"] = [column.as_dict() for column in list_columns(form, info.key)]
        if is_value_table(form, info.key):
            added["value_table"] = True
    results.append(added)
    return results


def describe_form(form: OrdinaryForm) -> dict[str, Any]:
    """Compact, model-friendly description of a form (also used by `inspect`)."""
    containers = form.containers()
    pages = []
    for container in containers.values():
        for page in container.pages:
            try:
                passport = form.style_passport(container.key, page)
            except FormModelError:
                passport = None
            pages.append({"container": container.name, "page": page or "<root>", "style": passport})
    return {
        "title": form.title, "size": {"width": form.size[0], "height": form.size[1]},
        "languages": form.languages(),
        "object_attributes": [attribute.as_dict() for attribute in form.object_attributes.values()],
        "tabular_sections": {name: [ROW_NUMBER, *section.attributes]
                             for name, section in getattr(form, "tabular_sections", {}).items()},
        "events": {**{kind: list(events) for kind, events in EVENTS.items()}, "TableColumn": list(CELL_EVENTS),
                   "Form": list(FORM_EVENTS)},
        "form_events": form.form_events(),
        "pages": pages,
        "elements": [_element(form, control) for control in form.controls()],
    }


def _element(form: OrdinaryForm, control) -> dict[str, Any]:
    result = control.as_dict()
    result.pop("handlers", None)
    if control.type == "Table":
        from .table_field import is_value_table, list_columns, table_events
        if is_value_table(form, control.key):
            result["value_table"] = True
        try:
            events = table_events(form, control.key)
            result["columns"] = [column.as_dict() for column in list_columns(form, control.key)]
        except FormModelError as error:
            events = {}
            result["columns"] = None
            result["table_error"] = str(error)
    else:
        events = form.events(control.key)
        if control.type == "Panel":
            container = form.containers().get(control.key)
            if container is not None:
                result["pages"] = [{"name": page, "caption": {lang: text for lang, text in loc_to_dict(
                    form.data[container.page_key(page)]["raw"][1]).items() if lang != "#"}}
                    for page in container.pages]
        if control.type == "CommandPanel":
            from .command_panel import panel_info
            info = panel_info(form, control.key)
            if info:
                result["command_panel"] = info
    if events:
        result["events"] = events
    return result


__all__ = ["apply_plan", "parse_plan", "describe_form", "PlanError", "OPS"]
