"""Read-only scan of the ordinary forms (Form.oform) of an EDT project: can the tool open each one?

For every Form.oform under ROOT the scan opens an edit session exactly as the CLI does (decode, defect
diagnosis, byte-exact no-op round trip), loads the form model and records:

* the refusal code when the session refuses the form (E_OFORM_ROUNDTRIP, E_FORM_DUPLICATE_NAMES, ...);
* the repairs the model would make on load (id counter, anchors of missing elements);
* the kinds of baseline integrity issues of the form as shipped.

Nothing under ROOT is written: sessions work on copies in temporary directories and nothing is published.

usage: python tools/scan_forms.py ROOT [--json OUT.json] [--workers N] [--limit N]
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import traceback
from collections import Counter, defaultdict
from multiprocessing import Pool
from pathlib import Path

# Run from a checkout: the package under src/ (also in the worker processes, which import this module).
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

ISSUE_KINDS = (
    ("id counter below max element id", "id_counter_below_max"), ("duplicate name", "duplicate_name"),
    ("duplicate id", "duplicate_id"), ("missing reverse anchor", "missing_reverse_anchor"),
    ("stale reverse anchor", "stale_reverse_anchor"), ("duplicate reverse anchor", "duplicate_reverse_anchor"),
    ("missing panel anchor", "missing_panel_anchor"), ("stale panel anchor", "stale_panel_anchor"),
    ("duplicate panel anchor", "duplicate_panel_anchor"), ("tab order not dense", "tab_order_not_dense"),
    ("z-order not dense", "z_order_not_dense"), ("datasource for missing element", "datasource_missing_element"),
    ("default button refers to missing element", "default_button_missing"),
    ("bound to missing element", "bound_to_missing_element"), ("on unknown page", "element_on_unknown_page"),
    ("bad layout", "bad_layout"),
)


def _issue_kind(issue: str) -> str:
    for text, kind in ISSUE_KINDS:
        if text in issue:
            return kind
    return "other"


def check(path: str) -> dict:
    from one_c_form_compiler.core.oform_session import OformSession
    result: dict = {"path": path, "refused": None}
    try:
        with OformSession(Path(path)) as session:
            form = session.form()
            result["repairs"] = list(form.repairs)
            result["issues"] = sorted({_issue_kind(issue) for issue in form.integrity_issues()})
            ids = [int(form.data[key]["raw"][1]) for key in form.element_keys()]
            result["counter"] = form.id_counter
            result["max_id"] = max(ids) if ids else 0
    except Exception as error:  # noqa: BLE001 - every failure is a scan result
        result["refused"] = getattr(error, "code", None) or f"crash:{type(error).__name__}"
        result["detail"] = str(error)[:300] if getattr(error, "code", None) else traceback.format_exc()[-400:]
    return result


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("root", type=Path, help="EDT project (or any directory) to scan for Form.oform")
    parser.add_argument("--json", type=Path, help="write per-form results here")
    parser.add_argument("--workers", type=int, default=max(1, (os.cpu_count() or 2) - 1))
    parser.add_argument("--limit", type=int, default=0, help="scan only the first N forms")
    args = parser.parse_args(argv)
    files = sorted(str(path) for path in args.root.rglob("Form.oform"))
    if args.limit:
        files = files[:args.limit]
    if not files:
        print(f"no Form.oform under {args.root}")
        return 1
    with Pool(args.workers) as pool:
        results = pool.map(check, files, chunksize=8)
    if args.json:
        args.json.write_text(json.dumps(results, ensure_ascii=False, indent=1), encoding="utf-8")

    def relative(path: str) -> str:
        return str(Path(path).relative_to(args.root))

    refused = [item for item in results if item["refused"]]
    print(f"forms: {len(results)}  open OK: {len(results) - len(refused)}  refused: {len(refused)}")
    by_code = defaultdict(list)
    for item in refused:
        by_code[item["refused"]].append(item)
    for code, items in sorted(by_code.items(), key=lambda pair: -len(pair[1])):
        print(f"\n[{len(items)}] {code}")
        for item in items[:5]:
            print(f"   {relative(item['path'])}  {item.get('detail', '')[:160]}")
    repaired = [item for item in results if item.get("repairs")]
    kinds = Counter()
    for item in repaired:
        for text in item["repairs"]:
            kinds["counter raised" if text.startswith("id counter") else
                  "panels with anchors dropped" if "panel anchors" in text else "elements with anchors dropped"] += 1
    print(f"\nrepairs on load: {len(repaired)} forms; " + ", ".join(f"{k}: {v}" for k, v in kinds.most_common()))
    issues = Counter(kind for item in results for kind in item.get("issues", []))
    print("baseline integrity issues (forms per kind): "
          + (", ".join(f"{kind} {count}" for kind, count in issues.most_common()) or "none"))
    loaded = [item for item in results if "counter" in item]
    print(f"id counter vs highest element id (after repair): "
          f"below {sum(item['counter'] < item['max_id'] for item in loaded)}, "
          f"equal {sum(item['counter'] == item['max_id'] for item in loaded)}, "
          f"above {sum(item['counter'] > item['max_id'] for item in loaded)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
