from __future__ import annotations

import json
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from scripts.check_detachability import inspect_targets


class DetachabilityTests(unittest.TestCase):
    def test_runtime_governance_reference_fails(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            target = root / "target"
            source = target / "src"
            source.mkdir(parents=True)
            (source / "bad.py").write_text("DB = 'governance-source.sqlite'\n", encoding="utf-8")
            subprocess.run(["git", "init", str(target)], check=True, capture_output=True)
            subprocess.run(["git", "-C", str(target), "config", "user.name", "Governance Test"], check=True)
            subprocess.run(["git", "-C", str(target), "config", "user.email", "governance-test@example.invalid"], check=True)
            subprocess.run(["git", "-C", str(target), "add", "src/bad.py"], check=True)
            subprocess.run(
                ["git", "-C", str(target), "commit", "-m", "test fixture"],
                check=True,
                capture_output=True,
            )
            subprocess.run(["git", "-C", str(target), "remote", "add", "origin", "https://example.test/target.git"], check=True)
            config = root / "targets.json"
            config.write_text(
                json.dumps(
                    {
                        "schema": "cartridgeflow.governance.targets.v1",
                        "targets": [
                            {
                                "id": "target",
                                "role": "test",
                                "path": str(target),
                                "remote": "https://example.test/target.git",
                                "runtime_roots": ["src"],
                            }
                        ],
                    }
                ),
                encoding="utf-8",
            )
            with patch("scripts.check_detachability.ROOT", root / "governance"):
                errors, _ = inspect_targets(config)
            self.assertTrue(any("runtime references governance marker" in error for error in errors))


if __name__ == "__main__":
    unittest.main()
