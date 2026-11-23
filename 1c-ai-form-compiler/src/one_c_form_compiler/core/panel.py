"""Panels (Панель) of ordinary forms: create a panel with pages, remove a page.

Format (checked over the panels of a typical configuration; see docs/panel-format.md):

* the element record ``raw = [PANEL_UUID, id, ["1", P, <events>], <layout>, ["14", name, "4294967295", "0", "0",
  "0"], <children>]``; children are kept by v8unpack in the tree (``node["child"]``) and in
  ``data["<panel key>/<page>/<name>"]``;
* ``P = raw[2][1]`` is the panel record: ``[style, "25", n0, .., n1, .., n2, .., n3, .., A, .., B, .., <pictures>,
  <tabs>, <x>, ["1"], <x>, <x>, <x>, "в отдельном файле", <flag>, 4294967295 x <pages>, "5", <tail>...]``.
  The four groups are the anchor index of the panel's children (entries ``[kind, element id, edge]``);
  ``["1"]`` is the page list (v8unpack moves the pages to ``data["<panel key>/<page>"]`` and their
  guides to ``info``); ``<tabs>`` is "1" when the tabs are shown;
* a page record carries its border guides in ``info``: ``["2", coord, orient, "1", kind, page index, "0", margin,
  "0"]`` with kind 1..4 = left, top, right, bottom; ``coord + margin`` of the right/bottom guide is the client
  width/height of the page;
* after the guides the panel record holds exactly one value per page (4294967295); without it 1C reports
  "Ошибка формата потока".

v8unpack slices the panel's flat guide list positionally when it decodes a form (the first page gets four
guides plus the extra ones, every other page four), so a page's ``info`` is not necessarily its own: the page
index in ``entry[5]`` is what ties a guide to a page.
"""

from __future__ import annotations

import copy
import re
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from .ordinary_form import Container, OrdinaryForm

PANEL_UUID = "09ccdc77-ea1a-4a6d-ab1c-3435eada2433"
PAGE_MARKER = "в отдельном файле"
NO_VALUE = "4294967295"
BORDER_KINDS = ("1", "2", "3", "4")
# Panel size minus the client size of its pages (typical configuration: 2754 of 2793 / 2308 of 2390 pages).
TAB_FRAME = (2, 20)
MIN_PANEL = (40, 50)


def _error(code: str, message: str):
    from .ordinary_form import FormModelError
    return FormModelError(code, message)


def panel_shape(raw: list) -> dict[str, int]:
    """Positions inside the panel record ``raw[2][1]``: ``pages`` (the ["1"] page list) and ``marker``."""
    index = raw[2][1]
    position = 2
    position += int(index[position])
    for _ in range(5):
        position += 1
        position += int(index[position])
    pages = position + 4
    return {"pages": pages, "marker": pages + 4}


def panel_record_issues(raw: list, page_count: int) -> list[str]:
    """Shape problems of a panel element record (empty when this module can edit it)."""
    try:
        issues = []
        if len(raw) != 6 or raw[0] != PANEL_UUID or raw[2][0] != "1" or len(raw[2]) != 3:
            issues.append("element record")
        if not (isinstance(raw[-2], list) and raw[-2][:1] == ["14"]):
            issues.append("name record")
        index = raw[2][1]
        shape = panel_shape(raw)
        if index[shape["pages"]] != ["1"]:
            issues.append("page list")
        marker = shape["marker"]
        if index[marker] != PAGE_MARKER or not str(index[marker + 1]).isdigit():
            issues.append("guide marker")
        values = index[marker + 2:marker + 2 + page_count]
        if len(values) != page_count or not all(isinstance(v, str) and v.isdigit() for v in values):
            issues.append("per-page values")
        if index[marker + 2 + page_count] != "5":
            issues.append("per-page value count")
        return issues
    except (IndexError, TypeError, ValueError) as error:
        return [f"unreadable panel record ({error})"]


def tabs_shown(raw: list) -> bool | None:
    try:
        value = raw[2][1][panel_shape(raw)["pages"] - 2]
    except (IndexError, TypeError, ValueError):
        return None
    return {"1": True, "0": False}.get(value)


def border_guides(form: "OrdinaryForm", container: "Container") -> dict[str, dict[str, list]]:
    """page index -> kind -> guide entry, over the flat guide list of the panel."""
    result: dict[str, dict[str, list]] = {}
    for page in container.pages:
        for entry in form.data.get(container.page_key(page), {}).get("info") or []:
            if isinstance(entry, list) and len(entry) > 7 and entry[4] in BORDER_KINDS:
                result.setdefault(str(entry[5]), {}).setdefault(entry[4], entry)
    return result


def guide_size(guides: dict[str, list]) -> tuple[int, int] | None:
    """Client size from a page's four border guides: right/bottom coordinate + margin."""
    if len(guides) != 4:
        return None
    try:
        width = int(guides["3"][1]) + int(guides["3"][7])
        height = int(guides["4"][1]) + int(guides["4"][7])
    except (ValueError, TypeError, IndexError):
        return None
    return (width, height) if width > 0 and height > 0 else None


def frame_size(raw: list) -> tuple[int, int]:
    """Panel size minus page client size, by the tabs flag (no tabs: the pages fill the panel)."""
    return (0, 0) if tabs_shown(raw) is False else TAB_FRAME


def fit_guides(guides: list[list], page_index: int, size: tuple[int, int]) -> list[list]:
    """Copies of four border guides (kinds 1..4) moved to a client area of ``size``."""
    width, height = size
    result = []
    for entry in guides:
        entry = copy.deepcopy(entry)
        entry[5] = str(page_index)
        if entry[4] in ("3", "4"):
            far = width if entry[4] == "3" else height
            near = int(next(g for g in guides if g[4] == ("1" if entry[4] == "3" else "2"))[1])
            margin = int(entry[7]) if str(entry[7]).isdigit() else 6
            if far - margin <= near:
                margin = 0
            entry[1] = str(max(far - margin, near + 1))
            entry[7] = str(far - int(entry[1]))
        result.append(entry)
    return result


def _parse_pages(pages: Any) -> list[tuple[str, Any]]:
    from .ordinary_form import validate_name
    if not isinstance(pages, list) or not pages:
        raise _error("E_PLAN_FIELD", "pages must be a non-empty array of page names or {\"name\", \"caption\"}")
    result: list[tuple[str, Any]] = []
    seen: set[str] = set()
    for item in pages:
        if isinstance(item, str):
            name, caption = item, None
        elif isinstance(item, dict) and isinstance(item.get("name"), str) and set(item) <= {"name", "caption"}:
            name, caption = item["name"], item.get("caption")
            if caption is not None and not (isinstance(caption, str) or (
                    isinstance(caption, dict) and caption and all(isinstance(v, str) for v in caption.values()))):
                raise _error("E_PLAN_FIELD", f'page {name}: caption must be a string or {{"ru": "...", "uk": "..."}}')
        else:
            raise _error("E_PLAN_FIELD", "pages must be a non-empty array of page names or {\"name\", \"caption\"}")
        name = name.strip()
        validate_name(name)
        if name.casefold() in seen:
            raise _error("E_NAME_EXISTS", f"page {name!r} is listed twice")
        seen.add(name.casefold())
        result.append((name, caption))
    return result


def add_panel(form: "OrdinaryForm", name: str, pages: Any, *, container: str | None = None,
              page: str | None = None, left: int | None = None, top: int | None = None, width: int | None = None,
              height: int | None = None, stretch: str | None = None) -> dict[str, Any]:
    """Add a panel with tabs and empty pages; returns name, container, page, rect and pages."""
    from .ordinary_form import (FormModelError, _check_rect, encode_str, loc_to_dict, make_loc, validate_name)
    from .standards import standard_template
    if stretch not in (None, "width", "height", "both"):
        raise _error("E_PLAN_FIELD", "stretch must be width, height or both")
    validate_name(name)
    if form.has_element(name):
        raise _error("E_NAME_EXISTS", f"element {name!r} already exists")
    page_list = _parse_pages(pages)
    template = standard_template("Panel")
    if (width is not None and width < MIN_PANEL[0]) or (height is not None and height < MIN_PANEL[1]):
        raise _error("E_GEOMETRY_INVALID", f"a panel needs at least {MIN_PANEL[0]}x{MIN_PANEL[1]} px")
    rect = form.place("Panel", container=container, page=page, width=width, height=height, left=left, top=top)
    _check_rect(rect)
    if rect.width < MIN_PANEL[0] or rect.height < MIN_PANEL[1]:
        raise _error("E_GEOMETRY_INVALID", f"a panel needs at least {MIN_PANEL[0]}x{MIN_PANEL[1]} px; got "
                                           f"{rect.width}x{rect.height}")
    target, page_name = form.resolve_container(container, page)
    size = form.container_size(target, page_name)
    if rect.right > size[0] or rect.bottom > size[1]:
        raise _error("E_GEOMETRY_OUTSIDE", f"{name}: rectangle {rect.as_dict()} exceeds container "
                                           f"{target.name}/{page_name} client size {size[0]}x{size[1]}")
    raw = copy.deepcopy(template["panel"])
    new_id = form._allocate_id()
    raw[1] = new_id
    raw[-2][1] = encode_str(name)
    raw[-1] = ["0"]                    # no children yet (OrdinaryForm.save keeps this marker in step)
    index = raw[2][1]
    marker = panel_shape(raw)["marker"]
    count = 0
    while str(index[marker + 2 + count]).isdigit() and index[marker + 2 + count] != "5":
        count += 1
    index[marker + 1] = "0"
    index[marker + 2:marker + 2 + count] = [NO_VALUE] * len(page_list)
    flags = list(raw[3][-2:])
    form._init_layout(raw, new_id, target, page_name, rect, stretch=stretch, flags=flags)

    key = "/".join(part for part in (target.page_key(page_name), name) if part)
    form.data[key] = {"id": new_id, "ver": 27, "page": target.page_key(page_name), "raw": raw}
    target.tree.append({"name": name, "type": "Panel", "page": page_name, "child": []})
    form._header[1][1] = new_id
    frame = frame_size(raw)
    client = (rect.width - frame[0], rect.height - frame[1])
    result_pages = []
    for page_index, (page_title, caption) in enumerate(page_list):
        record = copy.deepcopy(template["page"])
        page_raw = record["raw"]
        page_raw[6] = encode_str(page_title)
        page_raw[4] = "1"
        page_raw[1] = make_loc(form._texts(caption if caption is not None else page_title, {}))
        record["info"] = fit_guides(template["guides"], page_index, client)
        form.data[f"{key}/{page_title}"] = record
        result_pages.append({"name": page_title, "caption": loc_to_dict(page_raw[1])})
    form.data[f"{key}/-pages-"] = [title for title, _caption in page_list]
    issues = panel_record_issues(raw, len(page_list))
    if issues:
        raise FormModelError("E_INTEGRITY", f"{name}: inconsistent panel record: {'; '.join(issues)}")
    return {"name": name, "container": target.name, "page": page_name, "rect": rect.as_dict(),
            "stretch": stretch, "pages": result_pages}


def page_references(form: "OrdinaryForm", page: str) -> int:
    """Count ``Страницы.<page>`` / ``Pages.<page>`` references in the form module."""
    if not form.module_text:
        return 0
    pattern = re.compile(r"(?:Страницы|Pages)\s*\.\s*" + re.escape(page) + r"(?![0-9A-Za-zА-Яа-яЁёІіЇїЄєҐґ_])",
                         re.IGNORECASE)
    return len(pattern.findall(form.module_text))


def find_panel(form: "OrdinaryForm", container: str) -> "Container":
    name = (container or "").strip()
    matches = [c for c in form.containers().values() if c.key and (c.name == name or c.key == name)]
    if len(matches) != 1:
        raise _error("E_CONTAINER_UNKNOWN", f"panel {container!r} not found")
    return matches[0]


def remove_page(form: "OrdinaryForm", container: str, page: str, *, force: bool = False) -> dict[str, Any]:
    """Remove a page of a panel; with ``force`` the elements on it go too."""
    target = find_panel(form, container)
    if page not in target.pages:
        raise _error("E_PAGE_UNKNOWN", f"page {page!r} not found in {target.name}: {target.pages}")
    if len(target.pages) < 2:
        raise _error("E_PLAN_FIELD", f"{target.name} has only this page; remove the panel instead")
    raw = form.data[target.key]["raw"]
    issues = panel_record_issues(raw, len(target.pages))
    if issues:
        raise _error("E_PANEL_FORMAT", f"{target.name}: unexpected panel record ({', '.join(issues)}); "
                                       "remove the page in Designer")
    elements = [form._name(key) for key in form.page_elements(target, page)]
    references = page_references(form, page)
    if not force:
        if elements:
            raise _error("E_ELEMENT_REFERENCED", f"page {page} holds {len(elements)} element(s): "
                                                 f"{', '.join(elements[:10])}; remove them first or use force")
        if references:
            raise _error("E_ELEMENT_REFERENCED", f"page {page} is used {references} time(s) in the form module "
                                                 f"(Страницы.{page}); fix the code first or remove with force")
    removed: list[str] = []
    for element in elements:
        if form.has_element(element):
            removed += form.remove_element(element, force=True)
    target = find_panel(form, target.key)
    position = target.pages.index(page)
    # Guides: drop the page's own entries, renumber the later pages and re-slice the flat list the way
    # v8unpack does (first page: four + the extra ones, every other page: four).
    flat = [entry for name in target.pages for entry in form.data[target.page_key(name)].get("info") or []]
    kept = []
    for entry in flat:
        if isinstance(entry, list) and len(entry) > 5 and str(entry[5]).isdigit():
            number = int(entry[5])
            if number == position:
                continue
            if number > position:
                entry[5] = str(number - 1)
        kept.append(entry)
    remaining = [name for name in target.pages if name != page]
    extra = len(kept) - 4 * len(remaining)
    if extra < 0:
        raise _error("E_PANEL_FORMAT", f"{target.name}: page guides do not match the pages; remove the page in Designer")
    del form.data[target.page_key(page)]
    offset = 0
    for number, name in enumerate(remaining):
        size = 4 + extra if number == 0 else 4
        form.data[target.page_key(name)]["info"] = kept[offset:offset + size]
        offset += size
    form.data[f"{target.key}/-pages-"][:] = remaining
    index = raw[2][1]
    marker = panel_shape(raw)["marker"]
    del index[marker + 2 + position]
    if str(index[marker + 1]).isdigit() and int(index[marker + 1]) >= len(remaining):
        index[marker + 1] = "0"
    for name in remaining[position:]:
        for key in form.page_elements(target, name):
            layout = form._layout(key)
            layout.tail[2] = str(layout.page_index - 1)
            layout.write()
    target.tree[:] = [node for node in target.tree if node.get("page", "") != page]
    return {"container": target.name, "page": page, "removed": removed}
