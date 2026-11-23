"""Regenerate built-in ordinary-form element templates.

Usage::

    python tools/generate_ordinary_templates.py <decoded-form-dir>

Picks the cleanest element of each addable type from a decoded ordinary form
(v8unpack ``Form.elem.json``), strips every form-specific detail (id, name,
captions, tooltips, event handlers, bindings, anchors, tab/z order) and writes
``src/one_c_form_compiler/core/ordinary_templates.json``.

Templates made by other tools are kept: "CommandPanel" (``tools/add_command_panel_template.py``) and
"Panel" (``tools/add_panel_template.py``).
"""

from __future__ import annotations

import copy
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from one_c_form_compiler.core.ordinary_form import (  # noqa: E402
    ADDABLE_TYPES, NONE_BIND, Layout, OrdinaryForm, _caption_path, _clear_events, _set, _style_block,
    encode_str, loc_to_dict, make_loc,
)

DEFAULT_CAPTIONS = {"Label": "Надпись", "Button": "Кнопка", "CheckBox": "Флажок", "Group": "Рамка"}
OUTPUT = ROOT / "src" / "one_c_form_compiler" / "core" / "ordinary_templates.json"
KEPT = ("CommandPanel", "Panel")      # written by add_command_panel_template.py / add_panel_template.py


def neutralize(form: OrdinaryForm, key: str, element_type: str) -> list:
    raw = copy.deepcopy(form.data[key]["raw"])
    raw[1] = "0"
    raw[-2][1] = encode_str("Шаблон" + element_type)
    _clear_events(raw[2])
    path = _caption_path(raw, element_type)
    if path:
        current = loc_to_dict(_get(raw, path))
        texts = {"ru": DEFAULT_CAPTIONS[element_type], "uk": DEFAULT_CAPTIONS[element_type]}
        node = make_loc({"#": current["#"], **texts}) if "#" in current else make_loc(texts)
        _set(raw, path, node)
    style = _style_block(raw[2])
    if style is not None:
        style[12] = ["1", "0"]
    if element_type == "Field":
        raw[2][1] = ['"Pattern"', ['"S"']]
    if element_type == "Table":
        raw[2][1] = ['"Pattern"']
        columns = raw[2][2][1][23]
        del columns[1:]
        columns[0] = "0"
    layout = Layout.parse(raw[3])
    for edge in layout.edges:
        edge[0] = "0"
        edge[1] = list(NONE_BIND)
        edge[2] = list(NONE_BIND)
    layout.reverse = [[], [], [], []]
    layout.tail[:] = ["0", "0", "0", "0", "1", "1", "1"]
    layout.write()
    return raw


def neutral_column(form: OrdinaryForm, key: str) -> list:
    """First data column with its own cell control, without handlers, data or names."""
    from one_c_form_compiler.core import table_field as tf
    for column in tf.columns_node(form, key)[1:]:
        props = tf.column_props(column)
        if tf.decode_cell(props) is not None and len(tf.column_path(column)) == 2:
            column = copy.deepcopy(column)
            props = tf.column_props(column)
            props[tf.P_NAME] = encode_str("Колонка")
            props[tf.P_CAPTION] = make_loc({"ru": "Колонка", "uk": "Колонка"})
            props[tf.P_PATTERN] = ['"Pattern"', ['"S"']]
            column[1][1][2] = ["-1"]
            cell = tf.decode_cell(props)
            cell[1] = ['"Pattern"', ['"S"']]
            cell[tf.CELL_EVENT_SLOT] = ["0"]
            _clear_events(cell[2])
            tf.encode_cell(props, cell)
            return column
    raise SystemExit("no data column with a cell control in the sample table")


def _get(node, path):
    for index in path:
        node = node[index]
    return node


def main(argv: list[str]) -> int:
    source = Path(argv[0])
    if not (source / "Form.elem.json").is_file():
        from one_c_form_compiler.core.ordinary_form import find_form_dirs
        source = find_form_dirs(source)[0]
    form = OrdinaryForm.load(source)
    templates = {}
    for element_type in ADDABLE_TYPES:
        candidates = [key for key in form.element_keys() if form._type(key) == element_type]
        if not candidates:
            print(f"no {element_type} in sample", file=sys.stderr)
            return 1
        chosen = form._template_raw(element_type, None)     # dominant style, plain look
        key = next(key for key in candidates if form.data[key]["raw"] is chosen)
        if element_type == "Table":
            templates["TableColumn"] = neutral_column(form, key)
        templates[element_type] = neutralize(form, key, element_type)
    if OUTPUT.is_file():
        previous = json.loads(OUTPUT.read_text(encoding="utf-8")).get("templates", {})
        templates.update({name: previous[name] for name in KEPT if name in previous})
    OUTPUT.write_text(json.dumps({"profile": "FormElements27", "templates": templates},
                                 ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    print(f"wrote {OUTPUT.relative_to(ROOT)}: {', '.join(templates)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
