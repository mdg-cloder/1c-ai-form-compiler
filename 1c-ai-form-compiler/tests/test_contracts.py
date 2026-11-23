from __future__ import annotations

import unittest

from one_c_form_compiler import __version__
from one_c_form_compiler.core.contracts import (
    AddControl,
    BuildResult,
    BuildStatus,
    ControlRef,
    Diagnostic,
    FormProfile,
    Node,
    PatchPlan,
    RemoveControl,
    Severity,
    SourceDocument,
    StylePassport,
    Token,
)
from one_c_form_compiler.core.errors import ErrorCode
from one_c_form_compiler.core.profiles import ORDINARY_0_27


class ContractTests(unittest.TestCase):
    def test_source_hash_uses_original_bytes(self) -> None:
        source = SourceDocument(raw_bytes=b"\xef\xbb\xbf{}", source_name="form.bin")
        self.assertEqual(source.sha256, "aa25e978046d680ef8740d837e6de5bc1e2a2dc6089dbda1012544b538d53f65")
        self.assertEqual(source.size_bytes, 5)

    def test_token_span_must_match_raw_bytes(self) -> None:
        with self.assertRaises(ValueError):
            Token(kind="text", raw=b"abc", start=0, end=2)

    def test_remove_has_explicit_target(self) -> None:
        ref = ControlRef(profile_id="0-27", form_address="Form", key="control-1")
        operation = RemoveControl(target=ref)
        plan = PatchPlan("1.0", "0" * 64, "Form", (operation,))
        self.assertEqual(plan.operations[0].target, ref)
        self.assertEqual(operation.operation, "remove")

    def test_profile_is_evidence_bounded(self) -> None:
        self.assertEqual(ORDINARY_0_27.profile_id, "0-27")
        self.assertEqual(ORDINARY_0_27.element_decoder, "FormElements27")
        self.assertTrue(ORDINARY_0_27.verified)

    def test_public_error_codes_are_stable(self) -> None:
        self.assertEqual(ErrorCode.UNSUPPORTED_PROFILE.value, "E_PROFILE_UNSUPPORTED")

    def test_cli_version_exists(self) -> None:
        self.assertEqual(__version__, "0.5.0")


if __name__ == "__main__":
    unittest.main()
