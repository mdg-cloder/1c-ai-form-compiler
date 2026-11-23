"""Command panels (КоманднаяПанель) of ordinary forms bound to a table field.

Format (checked over thousands of command panels of typical 1C configurations; see
docs/command-panel-format.md):

* the element record is a plain FormElements27 element; ``raw[-2] = ["14", name, <action source id>, ...]``,
  the action source is the table field's element id ("0" - the form, "4294967295" - none);
* ``raw[2][1]`` (14 items, version "9") holds ``[3]`` autofill flag and ``[7]`` the bar:
  ``["5", BAR_UUID, COUNTER, "1", N, REC x N, M, MENU x M]``; every bar has the root menu
  ``["5", ROOT_MENU, "4", "0", K, (rec_uuid, PROPS) x K, TERM]``; PROPS has 16 items, ``PROPS[6]`` = BAR_UUID,
  ``PROPS[7]`` = button id (unique, <= COUNTER);
* a REC is ``["6", rec_uuid, "1", KIND, ACTION, MASK, optional fields...]`` with ``popcount(MASK)``
  optional fields;
* the panel's height is bound to its own top edge: ``edges[BOTTOM] = ["2", <own id>, TOP, height]``.

Counts are copied verbatim by v8unpack; a wrong one makes 1C fail with "Ошибка формата потока", so every
panel this module writes passes :func:`bar_issues`.
"""

from __future__ import annotations

import copy
import uuid
from typing import Any, TYPE_CHECKING

if TYPE_CHECKING:
    from .ordinary_form import OrdinaryForm

ROOT_MENU = ("b78f2e80-ec68-11d4-9dcf-0050bae2bc79", "4")
NULL_MENU = "9d0a2e40-b978-11d4-84b6-008048da06df"
STANDARD_KIND = "fbe38877-b914-4fd5-8540-07dde06ba2e1"
SEPARATOR_KIND = "abde0c9a-18a6-4e0c-bbaa-af26b911b3e6"
ZERO = "00000000-0000-0000-0000-000000000000"
NO_SOURCE = "4294967295"
PICTURE_NONE = ["4", "0", ["0"], '""', "-1", "-1", "1", "0", '""']
# Standard table actions: key -> (code, group, Текст, Подсказка, button name, caption).
STANDARD_ACTIONS: dict[str, tuple[str, str, str, str, str, str]] = {
    "Добавить": ("80", ZERO, "Добавить", "Добавить новый элемент", "Действие", "&Добавить"),
    "Скопировать": ("82", ZERO, "Добавить копированием", "Добавить новый элемент копированием текущего",
                    "Действие1", "&Скопировать"),
    "Изменить": ("84", ZERO, "Изменить текущий элемент", "Изменить (или открыть) текущий элемент",
                 "Действие2", "&Изменить"),
    "Удалить": ("83", ZERO, "Удалить текущий", "Удалить текущий элемент", "Действие3", "&Удалить"),
    "ПереместитьВверх": ("85", ZERO, "Переместить вверх", "Переместить текущий элемент вверх",
                         "Действие4", "Переместить вверх"),
    "ПереместитьВниз": ("86", ZERO, "Переместить вниз", "Переместить текущий элемент вниз",
                        "Действие5", "Переместить вниз"),
}


def _error(code: str, message: str):
    from .ordinary_form import FormModelError
    return FormModelError(code, message)


def _props(name: str, caption: list, bar: str, number: int, kind: str, visible: str = "1") -> list:
    from .ordinary_form import encode_str
    return ["8", encode_str(name), "0", "1", caption, visible, bar, str(number), "1e2", kind,
            "0", "1", "0", "0", "0", "0"]


def build_bar(form: "OrdinaryForm", standard: list[str], buttons: list[dict]) -> list:
    """A new bar: standard actions, a separator, then buttons calling form-module procedures."""
    from .ordinary_form import EVENT_HANDLER_KIND, encode_str, make_loc, validate_name

    def loc(text: str | dict[str, str]) -> list:
        return make_loc(form._texts(text, {}))

    bar = str(uuid.uuid4())
    records: list = []
    pairs: list = []
    number = 0
    names: set[str] = set()
    for action in standard:
        if action not in STANDARD_ACTIONS:
            raise _error("E_PLAN_FIELD", f"unknown standard action {action!r}; supported: {', '.join(STANDARD_ACTIONS)}")
        code, group, text, tip, button_name, caption = STANDARD_ACTIONS[action]
        record_id = str(uuid.uuid4())
        number += 1
        records.append(["6", record_id, "1", STANDARD_KIND,
                        ["6", NO_SOURCE, group, code, ["1", "99", ROOT_MENU[0], ROOT_MENU[1], "1"], "0", "1"],
                        "6", loc(text), loc(tip)])
        pairs += [record_id, _props(button_name, loc(caption), bar, number, "0")]
        names.add(button_name.casefold())
    if buttons:
        record_id = str(uuid.uuid4())
        number += 1
        records.append(["6", record_id, "1", SEPARATOR_KIND, ["1", NULL_MENU, "0"], "0"])
        pairs += [record_id, _props("Разделитель", ["1", "0"], bar, number, "2", "0")]
    for button in buttons:
        name, procedure = button.get("name"), button.get("procedure")
        validate_name(name)
        validate_name(procedure)
        if name.casefold() in names:
            raise _error("E_NAME_EXISTS", f"button {name!r} is already on the panel")
        names.add(name.casefold())
        caption = button.get("caption") or name
        tooltip = button.get("tooltip") or caption
        synonym = ["1", "1", ['"ru"', encode_str(caption if isinstance(caption, str) else next(iter(caption.values())))]]
        record_id = str(uuid.uuid4())
        number += 1
        records.append(["6", record_id, "1", EVENT_HANDLER_KIND,
                        ["3", encode_str(procedure),
                         ["1", encode_str(procedure), synonym, copy.deepcopy(synonym), copy.deepcopy(synonym),
                          list(PICTURE_NONE), ["0", "0", "0"]]],
                        "6", loc(tooltip), loc(tooltip)])
        pairs += [record_id, _props(name, loc(caption), bar, number, "0")]
    root = ["5", ROOT_MENU[0], ROOT_MENU[1], "0", str(len(pairs) // 2), *pairs, ["0", "0", ["0"]]]
    return ["5", bar, str(max(number, 1)), "1", str(len(records)), *records, "1", root]


def bar_issues(raw: list) -> list[str]:
    """Structural problems of a command panel record (empty list when it is consistent)."""
    try:
        panel = raw[2][1]
        bar = panel[7]
        count = int(bar[4])
        records = bar[5:5 + count]
        menu_count = int(bar[5 + count])
        menus = bar[6 + count:]
        issues = []
        if len(bar) != 6 + count + menu_count or len(menus) != menu_count:
            issues.append("bar record/menu counts")
        pairs, ids = [], []
        for menu in menus:
            size = int(menu[4])
            if len(menu) != 6 + 2 * size:
                issues.append("menu pair count")
                continue
            for index in range(size):
                pairs.append(menu[5 + 2 * index])
                props = menu[6 + 2 * index]
                if len(props) != 16 or props[6] != bar[1]:
                    issues.append("button props shape")
                ids.append(int(props[7]))
        if sorted(pairs) != sorted(record[1] for record in records):
            issues.append("menu pairs do not match records")
        if len(set(ids)) != len(ids) or (ids and int(bar[2]) < max(ids)):
            issues.append("button ids / counter")
        if not any((menu[1], menu[2]) == (panel[8], panel[9]) for menu in menus):
            issues.append("root menu missing")
        for record in records:
            if len(record) != 6 + bin(int(record[5])).count("1"):
                issues.append("record optional field count")
        return issues
    except (IndexError, TypeError, ValueError) as error:
        return [f"unreadable bar ({error})"]


def _is_table_panel(form: "OrdinaryForm", key: str) -> bool:
    raw = form.data[key]["raw"]
    if form._type(key) != "CommandPanel":
        return False
    try:
        panel = raw[2][1]
        source = raw[-2][2]
    except (IndexError, TypeError):
        return False
    source_key = form._key_by_id(source) if source not in ("0", NO_SOURCE) else None
    return (len(panel) == 14 and panel[1] == "9" and source_key is not None
            and form._type(source_key) == "Table" and not bar_issues(raw))


def panel_template(form: "OrdinaryForm") -> list:
    for key in form.element_keys():
        if _is_table_panel(form, key):
            return form.data[key]["raw"]
    from .standards import standard_template
    return standard_template("CommandPanel")


def add_command_panel(form: "OrdinaryForm", name: str, table: str, *, autofill: bool = True,
                      standard: list[str] | None = None, buttons: list[dict] | None = None,
                      height: int = 24, gap: int = 3, shift: bool = True) -> dict[str, Any]:
    """Add a command panel above ``table`` with the table as its action source.

    Without room above the table (another element or the container top in the way) the table and everything
    below it on its page move down (:meth:`OrdinaryForm.open_band`: free room first, else the form or a
    top-level panel grows); ``shift=False`` refuses instead (``E_NO_SPACE``)."""
    from .ordinary_form import (BOTTOM, LEFT, NONE_BIND, RIGHT, TOP, Layout, Rect, _entry_edge, _style_block,
                                encode_str, validate_name)
    validate_name(name)
    if form.has_element(name):
        raise _error("E_NAME_EXISTS", f"element {name!r} already exists")
    table_key = form.find(table)
    if form._type(table_key) != "Table":
        raise _error("E_TYPE_UNSUPPORTED", f"{table} is not a table field")
    if not 16 <= int(height) <= 60 or not 0 <= int(gap) <= 20:
        raise _error("E_GEOMETRY_INVALID", "height must be 16..60 and gap 0..20")
    standard = list(standard or [])
    buttons = list(buttons or [])
    if standard and autofill:
        raise _error("E_PLAN_FIELD", "standard actions are listed only with autofill=false")
    unknown = [action for action in standard if action not in STANDARD_ACTIONS]
    if unknown:
        raise _error("E_PLAN_FIELD", f"unknown standard actions {unknown}; supported: {', '.join(STANDARD_ACTIONS)}")
    target, page = form.container_of(table_key)
    table_raw = form.data[table_key]["raw"]
    shifted = _make_room(form, target, page, table_key, height, gap) if shift else 0
    table_layout = form._layout(table_key)
    table_rect = table_layout.rect
    rect = Rect(table_rect.left, table_rect.top - gap - height, table_rect.right, table_rect.top - gap)
    if rect.top < 0:
        raise _error("E_NO_SPACE", f"no room above {table} for a {height}px panel; move the table down first")
    for other in form.page_elements(target, page):
        if other != table_key and form._layout(other).rect.overlaps(rect):
            raise _error("E_NO_SPACE", f"the panel would overlap {form._name(other)}; move the table down first")

    raw = copy.deepcopy(panel_template(form))
    new_id = form._allocate_id()
    raw[1] = new_id
    raw[-2] = ["14", encode_str(name), table_raw[1], "0", "0", "0"]
    raw[-1] = ["0"]
    style = _style_block(raw[2])
    if style is not None:
        style[12] = ["1", "0"]
    panel = raw[2][1]
    panel[3] = "1" if autofill else "0"
    panel[7] = build_bar(form, standard, buttons)

    layout = Layout.parse(raw[3])
    layout.set_rect(rect)
    for edge in layout.edges:
        edge[1] = list(NONE_BIND)
        edge[2] = list(NONE_BIND)
    layout.reverse = [[], [], [], []]
    size = form.container_size(target, page)
    table_binds = {edge_index: bind for edge_index, _slot, bind in table_layout.bindings()
                   if edge_index <= 3 and bind[1] == "0"}
    if TOP in table_binds:
        form._bind_to_container(layout, new_id, target, TOP, rect, int(table_binds[TOP][2]), size)
    layout.edges[BOTTOM][1] = ["2", new_id, str(TOP), str(height)]        # height bound to its own top
    layout.reverse[TOP].append(["0", new_id, _entry_edge(BOTTOM)])
    for edge_index in (LEFT, RIGHT):
        if edge_index in table_binds:
            form._bind_to_container(layout, new_id, target, edge_index, rect, int(table_binds[edge_index][2]), size)
    table_tab, table_z = table_layout.tab_index, table_layout.z_index
    for other in form.page_elements(target, page):
        other_layout = form._layout(other)
        if other_layout.tab_index > table_tab:
            other_layout.tail[3] = str(other_layout.tab_index + 1)
        if other_layout.z_index >= table_z:
            other_layout.tail[4] = str(other_layout.z_index + 1)
        other_layout.write()
    layout.tail[2] = str(target.pages.index(page))
    layout.tail[3] = str(table_tab + 1)
    layout.tail[4] = str(table_z)
    layout.tail[5:7] = ["0", "0"]
    layout.write()

    key = "/".join(part for part in (target.page_key(page), name) if part)
    form.data[key] = {"id": new_id, "ver": 27, "page": target.page_key(page), "raw": raw}
    index = next(i for i, node in enumerate(target.tree) if node["name"] == table and node.get("page") == page)
    target.tree.insert(index + 1, {"name": name, "type": "CommandPanel", "page": page})
    form._header[1][1] = new_id
    issues = bar_issues(raw)
    if issues:
        raise _error("E_INTEGRITY", f"{name}: inconsistent command panel: {'; '.join(issues)}")
    for button in buttons:
        form.ensure_procedure(button["procedure"], "Кнопка")
    return {"name": name, "table": table, "rect": rect.as_dict(), "autofill": autofill,
            "standard": standard, "buttons": [button["name"] for button in buttons], "shifted": shifted}


def _make_room(form: "OrdinaryForm", target, page: str, table_key: str, height: int, gap: int) -> int:
    """Move the table (and what is below it) down until a ``height`` px panel fits above it; returns the shift.

    The panel goes ``gap`` px above the table and keeps ``gap`` px to the element above it (or sits at the
    page top). Elements at or below the table's top move with it (:meth:`OrdinaryForm.open_band`)."""
    from .ordinary_form import Rect
    table_rect = form._layout(table_key).rect
    wanted = Rect(table_rect.left, table_rect.top - gap - height, table_rect.right, table_rect.top - gap)
    blockers = [form._layout(other).rect for other in form.page_elements(target, page) if other != table_key]
    blockers = [rect for rect in blockers if rect.top < table_rect.top and rect.overlaps(wanted)]
    if not blockers and wanted.top >= 0:
        return 0
    floor = max((rect.bottom + gap for rect in blockers), default=0)
    delta = max(floor - wanted.top, 0)
    form.open_band(target, page, table_rect.top, delta)
    return delta


def panel_info(form: "OrdinaryForm", key: str) -> dict[str, Any] | None:
    """Action source and buttons of a command panel (for inspect)."""
    from .ordinary_form import decode_str, loc_to_dict
    raw = form.data[key]["raw"]
    try:
        panel = raw[2][1]
        bar = panel[7]
        count = int(bar[4])
        records = {record[1]: record for record in bar[5:5 + count]}
        source = raw[-2][2]
        source_key = form._key_by_id(source) if source not in ("0", NO_SOURCE) else None
        buttons = []
        for menu in bar[6 + count:]:
            if (menu[1], menu[2]) != (panel[8], panel[9]):
                continue
            for index in range(int(menu[4])):
                record = records.get(menu[5 + 2 * index])
                props = menu[6 + 2 * index]
                item = {"name": decode_str(props[1]), "caption": loc_to_dict(props[4])}
                item["caption"].pop("#", None)
                if record is not None and record[3] == STANDARD_KIND:
                    item["standard"] = record[4][3]
                elif record is not None and record[3] == SEPARATOR_KIND:
                    item["separator"] = True
                elif record is not None and record[3] == SUBMENU_KIND:
                    item["submenu"] = True
                elif record is not None and isinstance(record[4], list) and len(record[4]) > 1:
                    item["procedure"] = decode_str(record[4][1])
                buttons.append(item)
        return {"source": form._name(source_key) if source_key else ("<form>" if source == "0" else None),
                "autofill": panel[3] == "1", "buttons": buttons}
    except (IndexError, TypeError, ValueError):
        return None


# --------------------------------------------------------------------------- buttons of an existing panel

HANDLER_KIND = "e1692cc2-605b-4535-84dd-28440238746c"     # the same kind as element event handlers
SUBMENU_KIND = "c93a51ed-b2d1-47f1-948d-38373f8710af"
# PROPS[10] - how a button is shown: "0" auto, "1" text, "2" picture, "3" picture and text (buttons with a
# picture use 2/3). A new button has no picture, so it takes "0"/"1" from its neighbour (the bottom form panel
# shows its OK/Записать/Закрыть as text, "1").
DISPLAY_SLOT = 10
TEXT_DISPLAY = ("0", "1")


def _command_panel_key(form: "OrdinaryForm", panel: str) -> str:
    key = form.find(panel)
    if form._type(key) != "CommandPanel":
        raise _error("E_TYPE_UNSUPPORTED", f"{panel} is not a command panel (КоманднаяПанель)")
    issues = bar_issues(form.data[key]["raw"])
    if issues:
        raise _error("E_PANEL_FORMAT", f"{panel}: command panel record is not in the supported format "
                                       f"({'; '.join(issues)}); edit its buttons in Designer")
    return key


def _bar(raw: list) -> tuple[list, list, int, list]:
    """(panel record, bar, record count, menus) of a consistent command panel."""
    panel = raw[2][1]
    bar = panel[7]
    count = int(bar[4])
    return panel, bar, count, bar[6 + count:]


def _root_menu(panel: list, menus: list) -> list:
    return next(menu for menu in menus if (menu[1], menu[2]) == (panel[8], panel[9]))


def _menu_buttons(menu: list) -> list[tuple[str, list]]:
    return [(menu[5 + 2 * index], menu[6 + 2 * index]) for index in range(int(menu[4]))]


def _set_menu_buttons(menu: list, pairs: list[tuple[str, list]]) -> None:
    menu[4:-1] = [str(len(pairs)), *(item for pair in pairs for item in pair)]


def _button_names(menus: list) -> list[str]:
    from .ordinary_form import decode_str
    return [decode_str(props[1]) for menu in menus for _uuid, props in _menu_buttons(menu)]


def _unique_name(base: str, taken: set[str]) -> str:
    if base.casefold() not in taken:
        return base
    number = 1
    while f"{base}{number}".casefold() in taken:
        number += 1
    return f"{base}{number}"


def _new_props(neighbour: list | None, name: str, caption: list, bar: str, number: int, kind: str,
               visible: str) -> list:
    """Button properties: own name, caption and id; the text display mode of the neighbouring button."""
    props = _props(name, caption, bar, number, kind, visible)
    if neighbour is not None and len(neighbour) == 16 and neighbour[9] == kind             and neighbour[DISPLAY_SLOT] in TEXT_DISPLAY:
        props[DISPLAY_SLOT] = neighbour[DISPLAY_SLOT]
    return props


def add_button(form: "OrdinaryForm", panel: str, name: str, *, procedure: str | None = None,
               standard: str | None = None, caption: str | dict[str, str] | None = None,
               tooltip: str | dict[str, str] | None = None, before: str | None = None, after: str | None = None,
               separator: bool = False) -> dict[str, Any]:
    """Add a button (a form-module procedure or a standard table action) to an existing command panel."""
    from .ordinary_form import decode_str, encode_str, make_loc, validate_name
    key = _command_panel_key(form, panel)
    raw = form.data[key]["raw"]
    validate_name(name)
    if (procedure is None) == (standard is None):
        raise _error("E_PLAN_FIELD", "add_button needs exactly one of procedure or standard")
    if before is not None and after is not None:
        raise _error("E_PLAN_FIELD", "give before or after, not both")
    if procedure is not None:
        validate_name(procedure)
    if standard is not None:
        if standard not in STANDARD_ACTIONS:
            raise _error("E_PLAN_FIELD", f"unknown standard action {standard!r}; supported: {', '.join(STANDARD_ACTIONS)}")
        source = raw[-2][2]
        source_key = form._key_by_id(source) if source not in ("0", NO_SOURCE) else None
        if source_key is None or form._type(source_key) != "Table":
            raise _error("E_TYPE_UNSUPPORTED", f"standard actions need a panel whose action source is a table "
                                               f"field; {panel} is not one (use procedure)")
    panel_record, bar, count, menus = _bar(raw)
    taken = {item.casefold() for item in _button_names(menus)}
    if name.casefold() in taken:
        raise _error("E_NAME_EXISTS", f"button {name!r} is already on {panel}")
    root = _root_menu(panel_record, menus)
    pairs = _menu_buttons(root)
    position = len(pairs)
    anchor = before if before is not None else after
    if anchor is not None:
        names = [decode_str(props[1]) for _uuid, props in pairs]
        if anchor not in names:
            raise _error("E_ELEMENT_NOT_FOUND", f"button {anchor!r} is not on {panel} (top level); "
                                                f"buttons: {', '.join(names) or 'none'}")
        position = names.index(anchor) + (0 if before is not None else 1)

    def loc(text: str | dict[str, str]) -> list:
        return make_loc(form._texts(text, {}))

    # Nearest buttons first (before the insertion point, then after it): their display flags are reused.
    neighbours = [props for _uuid, props in pairs[:position][::-1] + pairs[position:]]
    number = int(bar[2])
    records, new_pairs = [], []
    if separator:
        number += 1
        record_id = str(uuid.uuid4())
        records.append(["6", record_id, "1", SEPARATOR_KIND, ["1", NULL_MENU, "0"], "0"])
        separator_name = _unique_name("Разделитель", taken)
        taken.add(separator_name.casefold())
        neighbour = next((props for props in neighbours if props[9] == "2"), None)
        new_pairs.append((record_id, _new_props(neighbour, separator_name, ["1", "0"], bar[1], number, "2", "0")))
    caption_value = caption if caption is not None else (STANDARD_ACTIONS[standard][5] if standard else name)
    number += 1
    record_id = str(uuid.uuid4())
    if standard is not None:
        code, group, text, tip, _button, _caption = STANDARD_ACTIONS[standard]
        records.append(["6", record_id, "1", STANDARD_KIND,
                        ["6", NO_SOURCE, group, code, ["1", "99", ROOT_MENU[0], ROOT_MENU[1], "1"], "0", "1"],
                        "6", loc(text), loc(tooltip if tooltip is not None else tip)])
    else:
        tip = tooltip if tooltip is not None else caption_value
        title = caption_value if isinstance(caption_value, str) else next(iter(caption_value.values()))
        synonym = ["1", "1", ['"ru"', encode_str(title)]]
        records.append(["6", record_id, "1", HANDLER_KIND,
                        ["3", encode_str(procedure),
                         ["1", encode_str(procedure), synonym, copy.deepcopy(synonym), copy.deepcopy(synonym),
                          list(PICTURE_NONE), ["0", "0", "0"]]],
                        "6", loc(caption_value), loc(tip)])          # Текст, Подсказка of the action
    neighbour = next((props for props in neighbours if props[9] == "0" and props[5] == "1"), None)
    new_pairs.append((record_id, _new_props(neighbour, name, loc(caption_value), bar[1], number, "0", "1")))

    bar[5 + count:5 + count] = records
    bar[4] = str(count + len(records))
    bar[2] = str(number)
    _set_menu_buttons(root, pairs[:position] + new_pairs + pairs[position:])
    issues = bar_issues(raw)
    if issues:
        raise _error("E_INTEGRITY", f"{panel}: inconsistent command panel: {'; '.join(issues)}")
    if procedure is not None:
        form.ensure_procedure(procedure, "Кнопка")
    result: dict[str, Any] = {"panel": panel, "name": name, "position": position + (1 if separator else 0)}
    if procedure is not None:
        result["procedure"] = procedure
    else:
        result["standard"] = standard
    if separator:
        result["separator"] = decode_str(new_pairs[0][1][1])
    return result


def button_references(form: "OrdinaryForm", name: str) -> int:
    """``…Кнопки.<name>`` / ``Кнопки.Найти("<name>")`` in the form module."""
    import re
    if not form.module_text:
        return 0
    pattern = re.compile(r"Кнопки\s*\.\s*" + re.escape(name) + r"(?![0-9A-Za-zА-Яа-яЁёІіЇїЄєҐґ_])"
                         r"|Кнопки\s*\.\s*Найти\s*\(\s*\"" + re.escape(name) + r"\"", re.IGNORECASE)
    return len(pattern.findall(form.module_text))


def remove_button(form: "OrdinaryForm", panel: str, name: str, *, force: bool = False) -> dict[str, Any]:
    """Remove a button or a separator (any menu level; not a submenu) from a command panel."""
    from .ordinary_form import decode_str
    key = _command_panel_key(form, panel)
    raw = form.data[key]["raw"]
    _panel, bar, count, menus = _bar(raw)
    records = {record[1]: record for record in bar[5:5 + count]}
    found = [(menu, index) for menu in menus for index, (_uuid, props) in enumerate(_menu_buttons(menu))
             if decode_str(props[1]) == name]
    if not found:
        raise _error("E_ELEMENT_NOT_FOUND", f"button {name!r} is not on {panel}; buttons: "
                                            f"{', '.join(_button_names(menus)) or 'none'}")
    if len(found) > 1:
        raise _error("E_ELEMENT_AMBIGUOUS", f"{panel} has {len(found)} buttons named {name!r}")
    menu, index = found[0]
    pairs = _menu_buttons(menu)
    record_id, props = pairs[index]
    record = records.get(record_id)
    if props[9] == "1" or (record is not None and record[3] == SUBMENU_KIND):
        raise _error("E_TYPE_UNSUPPORTED", f"{name} is a submenu; remove it in Designer")
    references = button_references(form, name)
    if references and not force:
        raise _error("E_ELEMENT_REFERENCED", f"button {name} is used {references} time(s) in the form module "
                                             f"(…Кнопки.{name}); fix the code first or remove with force")
    del pairs[index]
    _set_menu_buttons(menu, pairs)
    still_used = any(other_id == record_id for other in menus for other_id, _props in _menu_buttons(other))
    if record is not None and not still_used:
        bar[5:5 + count] = [item for item in bar[5:5 + count] if item[1] != record_id]
        bar[4] = str(count - 1)
    issues = bar_issues(raw)
    if issues:
        raise _error("E_INTEGRITY", f"{panel}: inconsistent command panel: {'; '.join(issues)}")
    kind = "separator" if record is not None and record[3] == SEPARATOR_KIND else "button"
    return {"panel": panel, "name": name, "kind": kind}
