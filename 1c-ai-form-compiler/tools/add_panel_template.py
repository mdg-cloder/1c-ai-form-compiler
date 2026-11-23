"""Collect panel (Панель) statistics over an EDT project and add the built-in "Panel" template.

Usage::

    python tools/add_panel_template.py <EDT project dir> [--stats-only]

Every ordinary form (``src/**/Form.oform``) of the project is decoded in memory. For each panel
(a Panel element that is not the form's root panel) the script checks the record shape that
``core/panel.py`` relies on and prints statistics (they are summarised in docs/panel-format.md).

Then it picks a panel with tabs whose record matches the dominant shape in every checked slot,
neutralises it (id, name "Панель", one page "Страница1", no anchors, no children, empty tab/z order)
and stores it in ``core/ordinary_templates.json`` under "Panel" as::

    {"panel": <element raw record>, "page": <page record>, "guides": [<4 border guides>]}

Other templates in the file (including "CommandPanel") are kept.
"""

from __future__ import annotations

import copy
import json
import sys
import tempfile
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from one_c_form_compiler.core.oform import _FormStub, _elements27, read_oform  # noqa: E402
from one_c_form_compiler.core.ordinary_form import (  # noqa: E402
    NONE_BIND, Layout, OrdinaryForm, encode_str, make_loc,
)
from one_c_form_compiler.core.panel import (  # noqa: E402
    NO_VALUE, PAGE_MARKER, panel_record_issues, panel_shape, tabs_shown,
)

OUTPUT = ROOT / "src" / "one_c_form_compiler" / "core" / "ordinary_templates.json"


def load_form(path: Path) -> OrdinaryForm:
    from v8unpack import helper
    parts = read_oform(path)
    with tempfile.TemporaryDirectory(prefix="panel-stats-") as tmp:
        (Path(tmp) / "form").write_bytes(parts.form)
        raw = helper.brace_file_read(tmp, "form")
    stub = _FormStub(raw, path.parent.name)
    _elements27()(stub).decode(None, None, "", raw[0])
    form_json = {"name": stub.header["name"], "form": stub.form}
    elem_json = dict(params=[], props=stub.props, commands=[], tree=stub.elements_tree, data=stub.elements_data)
    return OrdinaryForm(path.parent, form_json, elem_json, "")


def _j(value) -> str:
    return json.dumps(value, ensure_ascii=False)


def _page_signature(record: dict) -> str:
    raw = record["raw"]
    return _j([record.get("page_format_version"), raw[0], raw[2], raw[3:6], raw[7:]])


def _client_from_bindings(form: OrdinaryForm, container, page: str) -> tuple[int | None, int | None]:
    width = height = None
    for key in form.page_elements(container, page):
        try:
            layout = form._layout(key)
        except Exception:
            continue
        rect = layout.rect
        for edge, _slot, bind in layout.bindings():
            if bind[1] != "0" or edge > 3:
                continue
            if int(bind[2]) == 3 and width is None:
                width = rect.edge(edge) - int(bind[3])
            if int(bind[2]) == 1 and height is None:
                height = rect.edge(edge) - int(bind[3])
    return width, height


def collect(project: Path):
    stats: dict[str, Counter] = {name: Counter() for name in (
        "forms", "panels", "nested", "shape_issues", "index_version", "tabs", "after_marker", "per_page",
        "five", "tail", "pictures", "page_format", "page_shape", "border_guides", "client_dw", "client_dh",
        "guide_width", "guide_height", "layout_flags", "children", "events")}
    candidates: list[tuple[str, OrdinaryForm, str]] = []
    signatures: dict[str, Counter] = {"panel": Counter(), "page": Counter()}
    paths = sorted(project.glob("src/**/Form.oform"))
    for path in paths:
        try:
            form = load_form(path)
        except Exception:
            stats["forms"]["unreadable"] += 1
            continue
        stats["forms"]["read"] += 1
        panels = [c for key, c in form.containers().items() if key]
        if panels:
            stats["forms"]["with panels"] += 1
        for container in panels:
            stats["panels"]["total"] += 1
            raw = form.data[container.key]["raw"]
            owner, _page = form.container_of(container.key)
            stats["nested"]["nested" if owner.key else "on the form"] += 1
            issues = panel_record_issues(raw, len(container.pages))
            stats["shape_issues"][", ".join(issues) or "none"] += 1
            if issues:
                continue
            shape = panel_shape(raw)
            index = raw[2][1]
            tabs = tabs_shown(raw)
            stats["index_version"][index[1]] += 1
            stats["tabs"][{True: "shown", False: "hidden", None: "other"}[tabs]] += 1
            marker = shape["marker"]
            stats["after_marker"][index[marker + 1]] += 1
            stats["per_page"]["all 4294967295" if set(index[marker + 2:marker + 2 + len(container.pages)]) == {NO_VALUE}
                              else "other"] += 1
            stats["five"][index[marker + 2 + len(container.pages)]] += 1
            stats["tail"][_j(index[marker + 3 + len(container.pages):])] += 1
            stats["pictures"]["none" if index[shape["pages"] - 3] == TEMPLATE_PICTURES else "set"] += 1
            stats["events"]["none" if raw[2][2] == ["0"] else "handlers"] += 1
            stats["children"]["listed separately" if raw[-1] == ["Дочерние элементы отдельно"] else _j(raw[-1])] += 1
            try:
                layout = Layout.parse(raw[3])
                stats["layout_flags"][_j(layout.tail[5:7])] += 1
            except Exception:
                pass
            rect = Layout.parse(raw[3]).rect
            for page in container.pages:
                record = form.data[container.page_key(page)]
                stats["page_format"][record.get("page_format_version")] += 1
                stats["page_shape"][f"{len(record['raw'])} items"] += 1
                guides: dict[str, list] = {}
                for entry in record.get("info") or []:
                    if isinstance(entry, list) and len(entry) > 7 and entry[4] in ("1", "2", "3", "4"):
                        guides.setdefault(entry[4], entry)
                stats["border_guides"]["4 kinds" if len(guides) == 4 else f"{len(guides)} kinds"] += 1
                width, height = _client_from_bindings(form, container, page)
                label = {True: "tabs", False: "no tabs", None: "other"}[tabs]
                if width is not None:
                    stats["client_dw"][f"{label}: {rect.width - width}"] += 1
                if height is not None:
                    stats["client_dh"][f"{label}: {rect.height - height}"] += 1
                if len(guides) == 4:
                    if width is not None:
                        stats["guide_width"]["coord+margin == client" if int(guides["3"][1]) + int(guides["3"][7]) == width
                                             else "differs"] += 1
                    if height is not None:
                        stats["guide_height"]["coord+margin == client" if int(guides["4"][1]) + int(guides["4"][7]) == height
                                              else "differs"] += 1
                if tabs:
                    signatures["page"][_page_signature(record)] += 1
            if tabs:
                signatures["panel"][_panel_signature(form, container)] += 1
                candidates.append((str(path.relative_to(project)), form, container.key))
    return stats, candidates, signatures


TEMPLATE_PICTURES = ["4", "1"] + [["4", "0", ["0"], '""', "-1", "-1", "1", "0", '""'] for _ in range(3)]


def _panel_signature(form: OrdinaryForm, container) -> str:
    raw = form.data[container.key]["raw"]
    index = raw[2][1]
    shape = panel_shape(raw)
    marker = shape["marker"]
    count = len(container.pages)
    layout = Layout.parse(raw[3])
    return _j([index[0], index[1], index[shape["pages"] - 3:marker + 2], index[marker + 2 + count:],
               raw[2][2], [raw[-2][0], *raw[-2][2:]], layout.tail[5:7]])


def neutralize(form: OrdinaryForm, key: str) -> dict:
    container = form.containers()[key]
    raw = copy.deepcopy(form.data[key]["raw"])
    raw[1] = "0"
    raw[-2] = ["14", encode_str("Панель"), NO_VALUE, "0", "0", "0"]
    raw[-1] = ["Дочерние элементы отдельно"]
    index = raw[2][1]
    groups_end = 2
    for _ in range(4):
        count = int(index[groups_end])
        del index[groups_end + 1:groups_end + 1 + count]
        index[groups_end] = "0"
        groups_end += 1
    marker = index.index(PAGE_MARKER)
    count = len(container.pages)
    index[marker + 1] = "0"
    index[marker + 2:marker + 2 + count] = [NO_VALUE]
    layout = Layout.parse(raw[3])
    for edge in layout.edges:
        edge[0] = "0"
        edge[1] = list(NONE_BIND)
        edge[2] = list(NONE_BIND)
    layout.reverse = [[], [], [], []]
    layout.tail[:5] = ["0", "0", "0", "0", "1"]
    layout.set_rect(layout.rect.__class__(0, 0, 300, 150))
    layout.write()
    page_key = container.page_key(container.pages[0])
    page = copy.deepcopy(form.data[page_key])
    page_raw = page["raw"]
    page_raw[1] = make_loc({"ru": "Страница1", "uk": "Страница1"})
    page_raw[4] = "1"
    page_raw[6] = encode_str("Страница1")
    guides: dict[str, list] = {}
    for entry in page.get("info") or []:
        if isinstance(entry, list) and len(entry) > 7 and entry[4] in ("1", "2", "3", "4"):
            guides.setdefault(entry[4], copy.deepcopy(entry))
    template_guides = [guides[kind] for kind in ("1", "2", "3", "4")]
    for entry in template_guides:
        entry[5] = "0"
    template_guides[2][1] = str(298 - int(template_guides[2][7]))
    template_guides[3][1] = str(130 - int(template_guides[3][7]))
    record = {"ver": page.get("ver", 27), "page_format_version": page.get("page_format_version"), "raw": page_raw}
    return {"panel": raw, "page": record, "guides": template_guides}


def choose(candidates, signatures) -> tuple[str, OrdinaryForm, str]:
    best_panel = signatures["panel"].most_common(1)[0][0]
    best_page = signatures["page"].most_common(1)[0][0]
    for rel, form, key in candidates:
        container = form.containers()[key]
        if _panel_signature(form, container) != best_panel:
            continue
        page = form.data[container.page_key(container.pages[0])]
        if _page_signature(page) != best_page:
            continue
        guides = {entry[4] for entry in page.get("info") or [] if isinstance(entry, list) and len(entry) > 7}
        if {"1", "2", "3", "4"} <= guides:
            return rel, form, key
    raise SystemExit("no panel matches the dominant shape")


def main(argv: list[str]) -> int:
    if not argv:
        print(__doc__)
        return 2
    project = Path(argv[0])
    stats, candidates, signatures = collect(project)
    for name, counter in stats.items():
        print(f"== {name}")
        for value, count in counter.most_common(10):
            print(f"   {count:6}  {value}")
    panel_total = sum(signatures["panel"].values())
    print(f"== dominant tabbed panel shape: {signatures['panel'].most_common(1)[0][1]} of {panel_total}")
    print(f"== dominant page shape: {signatures['page'].most_common(1)[0][1]} of {sum(signatures['page'].values())}")
    if "--stats-only" in argv:
        return 0
    rel, form, key = choose(candidates, signatures)
    template = neutralize(form, key)
    issues = panel_record_issues(template["panel"], 1)
    if issues:
        raise SystemExit(f"template is inconsistent: {issues}")
    data = json.loads(OUTPUT.read_text(encoding="utf-8"))
    data["templates"]["Panel"] = template
    OUTPUT.write_text(json.dumps(data, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    print(f"wrote Panel template from a panel of {len(candidates)} tabbed panels")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
