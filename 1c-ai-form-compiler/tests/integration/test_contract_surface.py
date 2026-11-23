from __future__ import annotations

import subprocess
import sys
import unittest

from one_c_form_compiler.core.contracts import SourceDocument
from one_c_form_compiler.core.profiles import ORDINARY_0_27


class LocalIntegrationTests(unittest.TestCase):
    def test_module_cli_help(self) -> None:
        result = subprocess.run(
            [sys.executable, "-m", "one_c_form_compiler", "--help"],
            check=False,
            capture_output=True,
            text=True,
            timeout=10,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("Edit 1C ordinary forms", result.stdout)
        for command in ("inspect", "add", "modify", "remove", "apply", "ai", "runtime-check", "doctor"):
            self.assertIn(command, result.stdout)

    def test_profile_and_source_contract_interoperate(self) -> None:
        source = SourceDocument(raw_bytes=b"form payload")
        self.assertEqual(ORDINARY_0_27.profile_id, "0-27")
        self.assertEqual(len(source.sha256), 64)


if __name__ == "__main__":
    unittest.main()
