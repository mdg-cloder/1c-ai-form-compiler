"""Add the built-in "CommandPanel" template to ordinary_templates.json.

Usage::

    python tools/add_command_panel_template.py <Form.oform> <command panel name>

The panel must be a table field's command panel (version "9" record). Its bar is replaced with an empty one,
its name, id, action source, anchors and tab/z order are neutralised - only the structure is kept.
"""

from __future__ import annotations

import copy
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from one_c_form_compiler.core.command_panel import NO_SOURCE, ROOT_MENU, bar_issues  # noqa: E402
from one_c_form_compiler.core.ordinary_form import NONE_BIND, Layout, _style_block, encode_str  # noqa: E402
from one_c_form_compiler.core.oform_session import OformSession  # noqa: E402

OUTPUT = ROOT / "src" / "one_c_form_compiler" / "core" / "ordinary_templates.json"
TEMPLATE_BAR = "6f3c1b1e-0c2a-4a8e-9d55-3b7a2f1c0d01"


def main(argv: list[str]) -> int:
    form_path, name = Path(argv[0]), argv[1]
    with OformSession(form_path) as session:
        form = session.form()
        raw = copy.deepcopy(form.data[form.find(name)]["raw"])
    if form._type(form.find(name)) != "CommandPanel" or len(raw[2][1]) != 14 or raw[2][1][1] != "9":
        print("not a version-9 command panel", file=sys.stderr)
        return 1
    raw[1] = "0"
    raw[-2] = ["14", encode_str("ШаблонCommandPanel"), NO_SOURCE, "0", "0", "0"]
    raw[-1] = ["0"]
    style = _style_block(raw[2])
    if style is not None:
        style[12] = ["1", "0"]
    raw[2][1][3] = "1"
    raw[2][1][7] = ["5", TEMPLATE_BAR, "1", "1", "0", "1",
                    ["5", ROOT_MENU[0], ROOT_MENU[1], "0", "0", ["0", "0", ["0"]]]]
    layout = Layout.parse(raw[3])
    for edge in layout.edges:
        edge[0] = "0"
        edge[1] = list(NONE_BIND)
        edge[2] = list(NONE_BIND)
    layout.reverse = [[], [], [], []]
    layout.tail[:] = ["0", "0", "0", "0", "1", "0", "0"]
    layout.write()
    assert not bar_issues(raw), bar_issues(raw)
    data = json.loads(OUTPUT.read_text(encoding="utf-8"))
    data["templates"]["CommandPanel"] = raw
    OUTPUT.write_text(json.dumps(data, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    print(f"wrote CommandPanel template from {form_path.parent.name}/{name}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
