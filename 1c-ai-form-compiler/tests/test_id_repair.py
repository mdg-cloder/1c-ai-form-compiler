"""Element ids: one allocator above every id the form mentions, load-time repair, multiset anchor checks.

Synthetic: the ordinary form of tests/fixtures/sample_ordinary.epf with its id bookkeeping changed.
"""

from __future__ import annotations

import json
import shutil
import tempfile
import unittest
from pathlib import Path

from tests._support import HAS_SAMPLE, SAMPLE_REASON


@unittest.skipUnless(HAS_SAMPLE, SAMPLE_REASON)
class IdRepairTests(unittest.TestCase):
    root: Path
    base: Path

    @classmethod
    def setUpClass(cls) -> None:
        from one_c_form_compiler.core.oform import decode_oform
        from one_c_form_compiler.core.ordinary_form import OrdinaryForm
        from tests.test_oform import make_sample_oform
        cls.root = Path(tempfile.mkdtemp(prefix="id-repair-"))
        sample = make_sample_oform(cls.root / "sample" / "Форма" / "Form.oform")
        cls.base = cls.root / "base" / "Форма"
        decode_oform(sample, cls.base)
        cls.sample_repairs = OrdinaryForm.load(cls.base).repairs
        OrdinaryForm.load(cls.base).save()          # the base without the sample's stale anchor

    @classmethod
    def tearDownClass(cls) -> None:
        shutil.rmtree(cls.root, ignore_errors=True)

    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="id-repair-case-"))
        self.dir = self.tmp / "Форма"
        shutil.copytree(self.base, self.dir)

    def tearDown(self) -> None:
        shutil.rmtree(self.tmp, ignore_errors=True)

    # ------------------------------------------------------------------ helpers
    def mutate(self, change) -> None:
        """Change the decoded JSON (form_json, elem_json) on disk, as another tool could have written it."""
        form_json = json.loads((self.dir / "Form.json").read_text(encoding="utf-8"))
        elem_json = json.loads((self.dir / "Form.elem.json").read_text(encoding="utf-8"))
        change(form_json, elem_json)
        (self.dir / "Form.json").write_text(json.dumps(form_json, ensure_ascii=False), encoding="utf-8")
        (self.dir / "Form.elem.json").write_text(json.dumps(elem_json, ensure_ascii=False), encoding="utf-8")

    def load(self):
        from one_c_form_compiler.core.ordinary_form import OrdinaryForm
        return OrdinaryForm.load(self.dir)

    @staticmethod
    def header(form_json: dict) -> list:
        return form_json["form"][0][0][1]

    @staticmethod
    def ids(elem_json: dict) -> list[int]:
        return [int(v["raw"][1]) for v in elem_json["data"].values() if isinstance(v, dict) and "id" in v]

    def element(self, elem_json: dict, name: str) -> list:
        return next(v["raw"] for k, v in elem_json["data"].items() if k.endswith("/" + name))

    @staticmethod
    def reverse_group(raw: list, group: int) -> tuple[int, int]:
        """(position of the count, count) of a reverse-anchor group in a layout record raw[3]."""
        index = 12
        for _ in range(group):
            index += 1 + int(raw[3][index])
        return index, int(raw[3][index])

    def add_reverse(self, raw: list, group: int, entry: list) -> None:
        index, count = self.reverse_group(raw, group)
        raw[3].insert(index + 1 + count, entry)
        raw[3][index] = str(count + 1)

    def add_label(self, form, name: str):
        return form.add_element(element_type="Label", name=name, rect=form.place("Label"))

    # ------------------------------------------------------------------ tests
    def test_sample_repair_is_reported(self) -> None:
        self.assertEqual(len(self.sample_repairs), 1)
        self.assertIn("dropped anchors of missing elements", self.sample_repairs[0])
        self.assertEqual(self.load().repairs, [])

    def test_counter_below_max_id_is_raised(self) -> None:
        top = {}

        def lower(form_json, elem_json):
            top["max"] = max(self.ids(elem_json))
            self.header(form_json)[1][1] = str(top["max"] - 5)
        self.mutate(lower)
        form = self.load()
        self.assertEqual(form.id_counter, top["max"])
        self.assertEqual(len(form.repairs), 1)
        self.assertIn(f"raised to {top['max']}", form.repairs[0])
        info = self.add_label(form, "НадписьПослеРемонта")
        self.assertEqual(info.id, top["max"] + 1)
        form.save()                                          # no E_INTEGRITY (duplicate id) any more
        self.assertNotIn("id counter below max element id", form.integrity_issues())

    def test_dangling_entries_above_counter_are_dropped(self) -> None:
        state = {}

        def inject(form_json, elem_json):
            counter = int(self.header(form_json)[1][1])
            state["next"] = str(counter + 1)
            self.add_reverse(self.element(elem_json, "Надпись1"), 0, ["0", state["next"], "1"])
            index = self.header(form_json)[2][1][1]           # the form's panel index
            count = int(index[2])
            index.insert(3 + count, ["0", state["next"], "0"])
            index[2] = str(count + 1)
        self.mutate(inject)
        form = self.load()
        self.assertEqual(len(form.repairs), 2, form.repairs)
        self.assertTrue(all(state["next"] in item for item in form.repairs))
        info = self.add_label(form, "НадписьНовая")
        self.assertEqual(str(info.id), state["next"])       # the stale entries are gone: the id is free
        issues = form.integrity_issues()
        self.assertFalse([issue for issue in issues if state["next"] in issue and "stale" in issue], issues)
        self.assertFalse([issue for issue in issues if "duplicate" in issue], issues)
        form.save()

    def test_allocator_skips_ids_still_referenced(self) -> None:
        state = {}

        def refer(form_json, elem_json):
            counter = int(self.header(form_json)[1][1])
            state["counter"] = counter
            datasources = form_json["form"][0][0][2][3]
            datasources.append([str(counter + 1), ["1", ["0"]]])
            datasources[0] = str(int(datasources[0]) + 1)
            self.header(form_json)[1][2] = str(counter + 2)     # default button: a missing element
        self.mutate(refer)
        form = self.load()
        self.assertEqual(form.repairs, [])                    # data sources / default button are not repaired
        info = self.add_label(form, "НадписьМимо")
        self.assertEqual(info.id, state["counter"] + 3)
        self.assertEqual(form.id_counter, state["counter"] + 3)

    def test_default_button_none_is_not_an_issue(self) -> None:
        self.mutate(lambda form_json, elem_json: self.header(form_json)[1].__setitem__(2, "4294967295"))
        form = self.load()
        self.assertNotIn("default button refers to missing element", form.integrity_issues())
        self.assertEqual(int(self.add_label(form, "НадписьК").id), form.id_counter)

    def test_duplicate_panel_index_entry_is_reported(self) -> None:
        from one_c_form_compiler.core.ordinary_form import _panel_index_groups, _panel_index_write
        form = self.load()
        container = next(c for c in form.containers().values() if any(_panel_index_groups(c.raw_index)))
        groups = _panel_index_groups(container.raw_index)
        entries = next(entries for entries in groups if entries)
        entries.append(list(entries[0]))
        _panel_index_write(container.raw_index, groups)
        for name, payload in (("Form.json", form.form_json), ("Form.elem.json", form.elem_json)):
            (self.dir / name).write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
        form = self.load()
        self.assertEqual(form.repairs, [])                    # the element exists: nothing to drop
        self.assertTrue([issue for issue in form.integrity_issues() if issue.startswith("duplicate panel anchor")])

    def test_self_binding_double_is_accepted(self) -> None:
        def double(form_json, elem_json):
            raw = self.element(elem_json, "Надпись1")
            self.add_reverse(raw, 0, ["0", raw[1], "1"])
            self.add_reverse(raw, 0, ["0", raw[1], "1"])
        before = [issue for issue in self.load().integrity_issues()]
        self.mutate(double)
        form = self.load()
        self.assertEqual(form.repairs, [])
        self.assertEqual(form.integrity_issues(), before)


if __name__ == "__main__":
    unittest.main()
