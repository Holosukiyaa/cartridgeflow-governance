from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from scripts.check_workspace_layout import EXPECTED_REPOSITORIES, inspect_workspace


class WorkspaceLayoutTests(unittest.TestCase):
    def _workspace(self, root: Path) -> None:
        for name in EXPECTED_REPOSITORIES:
            (root / name).mkdir(parents=True)
        (root / "AGENTS.md").write_text(
            "CartridgeFlow-governance compile_context.py git worktree",
            encoding="utf-8",
        )

    def test_expected_three_repository_layout_passes(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self._workspace(root)
            self.assertEqual([], inspect_workspace(root, verify_git=False))

    def test_unexpected_root_entry_and_embedded_governance_fail(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self._workspace(root)
            (root / "temp").mkdir()
            (root / "CartridgeFlow" / "DR").mkdir()
            (root / "CartridgeFlow" / "DR" / "old.txt").write_text("old", encoding="utf-8")
            (root / "CartridgeFlow" / "todo.md").write_text("old", encoding="utf-8")
            (root / "CartridgeFlow" / "demos").mkdir()
            (root / "CartridgeFlow" / "demos" / "old.py").write_text("old", encoding="utf-8")
            (root / "CartridgeFlow" / "README.md").write_text(
                "CartridgeFlow-governance",
                encoding="utf-8",
            )

            errors = inspect_workspace(root, verify_git=False)

            self.assertTrue(any("非正式内容: temp" in item for item in errors))
            self.assertTrue(any("DR 必须" in item for item in errors))
            self.assertTrue(any("todo.md" in item for item in errors))
            self.assertTrue(any("demos" in item for item in errors))
            self.assertTrue(any("显式引用外挂治理仓" in item for item in errors))


if __name__ == "__main__":
    unittest.main()
