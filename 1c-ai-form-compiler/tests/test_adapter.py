from __future__ import annotations

import unittest
from dataclasses import replace

from one_c_form_compiler.core.adapter import (
    AmbiguousFormError,
    AdapterError,
    FormCandidate,
    adapt_forms,
)
from one_c_form_compiler.core.parser import parse_form


class AdapterTests(unittest.TestCase):
    def test_unknown_profile_is_rejected_without_mutation(self):
        parsed = parse_form(b'{1}')
        changed_profile = FormCandidate("Synthetic.Form", replace(parsed, profile_id="managed-unknown"))
        before = parsed.emit_unchanged()
        with self.assertRaisesRegex(AdapterError, "unsupported form profile"):
            adapt_forms((changed_profile,))
        self.assertEqual(parsed.emit_unchanged(), before)

    def test_synthetic_multiple_form_candidate_selection(self):
        parsed = replace(parse_form(b'{1}'), profile_id="managed-v1")
        a = FormCandidate("Doc.Form.A", parsed)
        b = FormCandidate("Doc.Form.B", parsed)
        before = parsed.emit_unchanged()
        with self.assertRaises(AmbiguousFormError) as caught:
            adapt_forms((a, b))
        self.assertEqual(caught.exception.addresses, ("Doc.Form.A", "Doc.Form.B"))
        with self.assertRaisesRegex(AdapterError, "unsupported form profile"):
            adapt_forms((a, b), selected_address="Doc.Form.B")
        self.assertEqual(parsed.emit_unchanged(), before)

    def test_unknown_profile_and_missing_selection_reject_cleanly(self):
        candidate = FormCandidate("Synthetic.Form", parse_form(b'{1}'))
        with self.assertRaisesRegex(AdapterError, "not found"):
            adapt_forms((candidate,), selected_address="wrong")
        with self.assertRaisesRegex(AdapterError, "no form candidates"):
            adapt_forms(())


if __name__ == "__main__":
    unittest.main()
