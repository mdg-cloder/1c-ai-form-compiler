"""Build a sample external data processor (EPF) carrying one ordinary form.

This is how tests/fixtures/sample_ordinary.epf is made (see tests/fixtures/README.md).

What goes into the EPF:

* the processor: the clean "probe_epf" skeleton shipped with one-c-form-compiler, renamed to
  ПримерОбычнойФормы (no attributes, no tabular sections, empty object module);
* one ordinary form "Форма", set as the default form. Its payload (elements, layout, panels, pages,
  table fields, command panels, data bindings) is copied unchanged from an EDT ordinary form container
  (Form.oform) given on the command line;
* a neutral form module: one empty procedure for every event handler the form payload references,
  with the parameter list of the source module (or the standard one when the source has none). No
  other line of the source module is copied.

Run from the repository root with the package importable and v8unpack 1.2.13 installed:

    set PYTHONPATH=src
    python tools/build_sample_epf.py <path/to/Form.oform> tests/fixtures/sample_ordinary.epf ^
        --ids <path/to/sample_ordinary.ids.json> --overwrite

Options: --ids reuses the UUIDs of a previous build (keys: file_uuid, uuid, object_type_uuid,
manager_type_uuid, form_uuid); --save-ids writes the UUIDs used; --keep-src copies the decoded
v8unpack source tree; --skeleton takes another decoded processor skeleton.

Without --ids, new random UUIDs are generated. The EPF is not byte-for-byte reproducible even with
--ids: v8unpack writes fresh random UUIDs into the container's "versions" entry on every build.
"""

from __future__ import annotations

import argparse
import copy
import json
import re
import shutil
import sys
import tempfile
import uuid
from pathlib import Path

from one_c_form_compiler.core.oform import decode_oform, read_oform
from one_c_form_compiler.core.ordinary_backend import Backend
from one_c_form_compiler.core.ordinary_form import EVENTS, FORM_EVENTS

PROCESSOR_NAME = "ПримерОбычнойФормы"
PROCESSOR_SYNONYM = "Пример обычной формы"
FORM_NAME = "Форма"
FORM_SYNONYM = "Форма"
FORM_TYPE_UUID = "d5b0e5ed-256d-401c-9c36-f630cafd8a62"   # MetaDataTypes.Form of an external processor
HANDLER_KIND = "e1692cc2-605b-4535-84dd-28440238746c"     # event -> module procedure
ID_KEYS = ("file_uuid", "uuid", "object_type_uuid", "manager_type_uuid", "form_uuid")

# {<event id>,e1692cc2-...,{3,"Proc"  - element / form events;  {6,<uuid>,1,e1692cc2-...,{3,"Proc"  - buttons
_EVENT_REF = re.compile(r"\{(\d+)," + HANDLER_KIND + r",\{3,\"([^\"]+)\"")
_BUTTON_REF = re.compile(r"\{6,[0-9a-fA-F-]{36},1," + HANDLER_KIND + r",\{3,\"([^\"]+)\"")
_SIGNATURE = r"^[ \t]*Процедура[ \t]+{name}[ \t]*\(([^)]*)\)"


def referenced_handlers(form_text: str) -> list[tuple[str, str]]:
    """(procedure, kind) in order of first reference; kind is the event id or 'button'."""
    flat = form_text.replace("\r", "").replace("\n", "")
    found: dict[str, str] = {}
    hits = [(m.start(), m.group(2), m.group(1)) for m in _EVENT_REF.finditer(flat)]
    hits += [(m.start(), m.group(1), "button") for m in _BUTTON_REF.finditer(flat)]
    for _pos, name, kind in sorted(hits):
        found.setdefault(name, kind)
    if flat.count(HANDLER_KIND) != len(hits):
        raise SystemExit(f"unrecognised handler references: {flat.count(HANDLER_KIND)} refs, {len(hits)} parsed")
    return list(found.items())


def fallback_params(kind: str) -> str:
    if kind == "button":
        return "Кнопка"
    for name, (event_id, params) in FORM_EVENTS.items():
        if event_id == kind:
            return params
    for events in EVENTS.values():
        for event_id, params in events.values():
            if event_id == kind:
                return params
    return ""


def neutral_module(handlers: list[tuple[str, str]], source_module: str) -> str:
    """Empty procedures; parameter lists follow the source module (the form calls them with those)."""
    blocks = []
    for name, kind in handlers:
        match = re.search(_SIGNATURE.format(name=re.escape(name)), source_module, re.MULTILINE | re.IGNORECASE)
        params = " ".join(match.group(1).split()) if match else fallback_params(kind)
        blocks.append(f"Процедура {name}({params})\r\n\r\nКонецПроцедуры\r\n")
    return "\r\n".join(blocks)


def processor_json(skeleton: dict, ids: dict) -> dict:
    data = copy.deepcopy(skeleton)
    old_uuid = data["uuid"]
    data["file_uuid"] = ids["file_uuid"]
    data["uuid"] = ids["uuid"]
    data["name"] = PROCESSOR_NAME
    data["name2"] = {"ru": PROCESSOR_SYNONYM}
    data["comment"] = ""
    root = data["header"][0]
    root[1][0] = ids["file_uuid"]
    body = root[3][1]                       # ["1", [object header], "4", <includes>...]
    obj = body[1]
    obj[1], obj[2] = ids["object_type_uuid"], ids["manager_type_uuid"]
    props = obj[3][1]
    assert props[1][2] == old_uuid, "unexpected skeleton layout"
    props[1][2] = ids["uuid"]
    props[2] = f'"{PROCESSOR_NAME}"'
    props[3] = ["1", '"ru"', f'"{PROCESSOR_SYNONYM}"']
    obj[4] = ids["form_uuid"]               # DefaultForm
    for include in body[3:]:
        if isinstance(include, list) and include and include[0] == FORM_TYPE_UUID:
            include[:] = [FORM_TYPE_UUID, "1", ids["form_uuid"]]
            break
    else:
        raise SystemExit("skeleton has no form include list")
    return data


def form_json(decoded: dict) -> dict:
    header = [["1", ["1", ["0", ["13",
                                 ["3", ["1", "0", "в отдельном файле"], f'"{FORM_NAME}"',
                                  ["1", '"ru"', f'"{FORM_SYNONYM}"'], '""', "0", "0",
                                  "00000000-0000-0000-0000-000000000000", "0"],
                                 "0", "0",
                                 ["2", ['"#"', "1708fdaa-cbce-4289-b373-07a5a74bee91", "1"],
                                  ['"#"', "1708fdaa-cbce-4289-b373-07a5a74bee91", "2"]]],
                         ["0"]]],
               "0"]]
    return {"name": FORM_NAME, "name2": {"ru": FORM_SYNONYM}, "comment": "", "header": header,
            "Тип формы": "0", "form": decoded["form"], "code_encoding_obj": "utf-8-sig", "code_info_obj": 1,
            "Версия элементов формы": "0-27", "obj_version": "13"}


def write_json(path: Path, data) -> None:
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def default_skeleton() -> Path:
    from importlib import resources
    return Path(str(resources.files("one_c_form_compiler.core").joinpath("probe_epf")))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("oform", type=Path, help="source EDT ordinary form container (Form.oform), read only")
    parser.add_argument("output", type=Path, help="EPF to create")
    parser.add_argument("--skeleton", type=Path, help="decoded processor skeleton (default: the tool's probe_epf)")
    parser.add_argument("--ids", type=Path, help="JSON with UUIDs to reuse (keys: " + ", ".join(ID_KEYS) + ")")
    parser.add_argument("--save-ids", type=Path, help="write the UUIDs used to this JSON file")
    parser.add_argument("--keep-src", type=Path, help="also copy the decoded v8unpack source tree here")
    parser.add_argument("--overwrite", action="store_true")
    args = parser.parse_args(argv)

    output = args.output.resolve()
    if output.suffix.lower() != ".epf":
        parser.error("output must be an .epf file")
    if output.exists() and not args.overwrite:
        parser.error(f"{output} exists; pass --overwrite")
    skeleton_dir = args.skeleton or default_skeleton()
    ids = json.loads(args.ids.read_text(encoding="utf-8")) if args.ids else {k: str(uuid.uuid4()) for k in ID_KEYS}
    missing = [k for k in ID_KEYS if k not in ids]
    if missing:
        parser.error("--ids misses: " + ", ".join(missing))

    backend = Backend.discover()
    parts = read_oform(args.oform)
    form_text = parts.form.decode("utf-8-sig")
    handlers = referenced_handlers(form_text)
    module = neutral_module(handlers, parts.module.decode("utf-8-sig"))

    with tempfile.TemporaryDirectory(prefix="sample-epf-") as tmp:
        work = Path(tmp)
        decode_oform(args.oform, work / "decoded")
        decoded = json.loads((work / "decoded" / "Form.json").read_text(encoding="utf-8"))

        src = work / "src"
        form_dir = src / "Form" / FORM_NAME
        form_dir.mkdir(parents=True)
        skeleton = json.loads((skeleton_dir / "ExternalDataProcessor.json").read_text(encoding="utf-8-sig"))
        write_json(src / "ExternalDataProcessor.json", processor_json(skeleton, ids))
        (src / "ExternalDataProcessor.obj.bsl").write_bytes(b"\xef\xbb\xbf")   # empty object module
        write_json(form_dir / "Form.id.json", {"uuid": ids["form_uuid"]})
        write_json(form_dir / "Form.json", form_json(decoded))
        shutil.copyfile(work / "decoded" / "Form.elem.json", form_dir / "Form.elem.json")
        (form_dir / "Form.obj.bsl").write_bytes(b"\xef\xbb\xbf" + module.encode("utf-8"))

        if args.keep_src:
            shutil.copytree(src, args.keep_src, dirs_exist_ok=True)
        (work / "tmp").mkdir()
        built = work / "out.epf"
        backend.build(src, built, work / "tmp")
        output.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(built, output)

    if args.save_ids:
        write_json(args.save_ids, ids)
    print(json.dumps({"output": str(output), "ids": ids, "handlers": [n for n, _k in handlers]},
                     ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
