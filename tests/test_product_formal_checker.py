from __future__ import annotations

import io
import json
import unittest
from contextlib import redirect_stdout
from unittest.mock import patch

from scripts import check_product_formal


class ProductFormalCheckerTests(unittest.TestCase):
    @patch("scripts.check_product_formal.subprocess.run")
    def test_runs_both_official_commands_when_lock_audit_fails(self, run) -> None:
        run.side_effect = [
            type("Completed", (), {"returncode": 1, "stdout": "", "stderr": "lock mismatch"})(),
            type("Completed", (), {"returncode": 0, "stdout": "conformance ok", "stderr": ""})(),
        ]
        output = io.StringIO()
        with redirect_stdout(output):
            exit_code = check_product_formal.main()
        payload = json.loads(output.getvalue())
        self.assertEqual(1, exit_code)
        self.assertFalse(payload["ok"])
        self.assertEqual(2, run.call_count)
        self.assertTrue(any("audit_protocol_registry.py" in item for item in run.call_args_list[0].args[0]))
        self.assertTrue(any("run_conformance.py" in item for item in run.call_args_list[1].args[0]))


if __name__ == "__main__":
    unittest.main()
