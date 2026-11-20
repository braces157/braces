#!/usr/bin/env python3
"""Comprehensive unittest suite for skills/braces/scripts/worktree_audit.py."""

from __future__ import annotations

import json
import os
import shutil
import stat
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parent.parent
SCRIPT_DIR = REPO_ROOT / "skills" / "braces" / "scripts"
SCRIPT_PATH = SCRIPT_DIR / "worktree_audit.py"

sys.dont_write_bytecode = True
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

import worktree_audit  # noqa: E402


def _remove_readonly(func: Any, path: str, _exc_info: Any) -> None:
    """Clear Windows read-only attribute before retrying rmtree removal."""
    try:
        os.chmod(path, stat.S_IWRITE)
    except OSError:
        pass
    func(path)


def _rmtree(path: Path) -> None:
    if path.exists():
        shutil.rmtree(path, onerror=_remove_readonly)


def _git(cwd: Path, *args: str) -> str:
    proc = subprocess.run(
        ["git", *args],
        cwd=str(cwd),
        shell=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    if proc.returncode != 0:
        raise AssertionError(
            f"git {' '.join(args)} failed in {cwd} (rc={proc.returncode}):\n"
            f"STDOUT:\n{proc.stdout}\nSTDERR:\n{proc.stderr}"
        )
    return proc.stdout.strip()


def _run_cli(*args: str) -> subprocess.CompletedProcess[str]:
    env = dict(os.environ)
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    return subprocess.run(
        [sys.executable, "-B", str(SCRIPT_PATH), *args],
        shell=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        env=env,
    )


class WorktreeAuditTestBase(unittest.TestCase):
    """Fixture helper creating isolated real git repositories and worktrees."""

    def setUp(self) -> None:
        self.tmp_root = Path(tempfile.mkdtemp(prefix="wt-audit-test-"))
        self.addCleanup(_rmtree, self.tmp_root)

    def _init_repo(
        self,
        name: str = "repo",
        *,
        default_branch: str = "main",
        with_origin: bool = False,
        gitignore: str | None = None,
    ) -> tuple[Path, Path | None]:
        repo = self.tmp_root / name
        repo.mkdir(parents=True, exist_ok=True)
        _git(repo, "init", "-b", default_branch)
        _git(repo, "config", "user.name", "Audit Tester")
        _git(repo, "config", "user.email", "audit-tester@example.com")
        _git(repo, "config", "core.autocrlf", "false")
        _git(repo, "config", "core.logAllRefUpdates", "true")

        (repo / "README.md").write_text("# Test Repo\n", encoding="utf-8")
        if gitignore is not None:
            (repo / ".gitignore").write_text(gitignore, encoding="utf-8")
        _git(repo, "add", ".")
        _git(repo, "commit", "-m", "chore: initial commit")

        bare_origin: Path | None = None
        if with_origin:
            bare_origin = self.tmp_root / f"{name}-origin.git"
            _git(self.tmp_root, "init", "--bare", "-b", default_branch, str(bare_origin))
            _git(repo, "remote", "add", "origin", str(bare_origin))
            _git(repo, "push", "-u", "origin", default_branch)
            _git(repo, "remote", "set-head", "origin", default_branch)

        return repo, bare_origin

    def _find_wt(self, report: dict[str, Any], wt_path: Path) -> dict[str, Any]:
        target_key = worktree_audit._norm_path_key(wt_path)
        for wt in report["worktrees"]:
            if worktree_audit._norm_path_key(Path(wt["path"])) == target_key:
                return wt
        raise AssertionError(
            f"Worktree {wt_path} not found in report: "
            f"{[w['path'] for w in report['worktrees']]}"
        )


class TestWorktreeAudit(WorktreeAuditTestBase):
    """End-to-end and unit tests covering all safety invariants."""

    def test_clean_synced_repo_exits_zero(self) -> None:
        repo, _ = self._init_repo(with_origin=True)

        proc = _run_cli("--repo", str(repo), "--json")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        report = json.loads(proc.stdout)
        self.assertTrue(report["summary"]["all_clean_and_synced"])
        self.assertEqual(report["summary"]["CLEAN_ACTIVE"], 1)
        self.assertEqual(report["default_branch"], "main")
        self.assertTrue(report["default_branch_sync"]["known"])
        self.assertFalse(report["default_branch_sync"]["value"]["behind_origin"])

        primary = self._find_wt(report, repo)
        self.assertEqual(primary["status"], worktree_audit.STATUS_CLEAN_ACTIVE)
        self.assertTrue(primary["is_primary"])

    def test_clean_merged_worktree_is_safe_to_prune(self) -> None:
        repo, _ = self._init_repo(with_origin=True)
        wt_merged = self.tmp_root / "wt-merged"

        _git(repo, "worktree", "add", "-b", "feat/merged", str(wt_merged))
        (wt_merged / "feature.txt").write_text("done\n", encoding="utf-8")
        _git(wt_merged, "add", "feature.txt")
        _git(wt_merged, "commit", "-m", "feat: add feature")

        _git(repo, "merge", "--ff-only", "feat/merged")
        _git(repo, "push", "origin", "main")

        proc = _run_cli("--repo", str(repo), "--json")
        self.assertEqual(proc.returncode, 1, proc.stderr)
        report = json.loads(proc.stdout)
        self.assertFalse(report["summary"]["all_clean_and_synced"])

        wt_entry = self._find_wt(report, wt_merged)
        self.assertEqual(wt_entry["status"], worktree_audit.STATUS_SAFE_TO_PRUNE)
        for fact_name in worktree_audit.REQUIRED_SAFETY_FACTS:
            self.assertTrue(
                wt_entry["facts"][fact_name]["known"],
                f"Expected fact {fact_name} to be known",
            )
        self.assertTrue(wt_entry["facts"]["head_merged_into_default"]["value"])

    def test_dirty_worktree_tracked_and_untracked_held(self) -> None:
        repo, _ = self._init_repo()
        wt_tracked = self.tmp_root / "wt-dirty-tracked"
        wt_untracked = self.tmp_root / "wt-dirty-untracked"

        _git(repo, "worktree", "add", "-b", "feat/dirty-tracked", str(wt_tracked))
        (wt_tracked / "README.md").write_text("# Modified\n", encoding="utf-8")

        _git(repo, "worktree", "add", "-b", "feat/dirty-untracked", str(wt_untracked))
        (wt_untracked / "wip.txt").write_text("unsaved\n", encoding="utf-8")

        proc = _run_cli("--repo", str(repo), "--json")
        self.assertEqual(proc.returncode, 1, proc.stderr)
        report = json.loads(proc.stdout)

        entry_tracked = self._find_wt(report, wt_tracked)
        self.assertEqual(entry_tracked["status"], worktree_audit.STATUS_DIRTY_HOLD)
        self.assertIn("README.md", entry_tracked["facts"]["tracked_dirty"]["value"])

        entry_untracked = self._find_wt(report, wt_untracked)
        self.assertEqual(entry_untracked["status"], worktree_audit.STATUS_DIRTY_HOLD)
        self.assertIn("wip.txt", entry_untracked["facts"]["untracked_files"]["value"])

    def test_locked_worktree_is_held(self) -> None:
        repo, _ = self._init_repo()
        wt_locked = self.tmp_root / "wt-locked"

        _git(repo, "worktree", "add", "-b", "feat/locked", str(wt_locked))
        _git(repo, "worktree", "lock", str(wt_locked), "--reason", "USB checkout hold")

        proc = _run_cli("--repo", str(repo), "--json")
        self.assertEqual(proc.returncode, 1, proc.stderr)
        report = json.loads(proc.stdout)

        entry = self._find_wt(report, wt_locked)
        self.assertEqual(entry["status"], worktree_audit.STATUS_DIRTY_HOLD)
        self.assertTrue(entry["facts"]["locked"]["known"])
        self.assertTrue(entry["facts"]["locked"]["value"])
        self.assertEqual(entry["lock_reason"], "USB checkout hold")

    def test_precious_ignored_env_vs_disposable_pycache(self) -> None:
        gitignore = "\n".join(
            [
                "__pycache__/",
                "node_modules/",
                "target/",
                ".next/",
                ".env",
                ".env.*",
                ".claude/settings.local.json",
                ".tools/",
                "",
            ]
        )
        repo, _ = self._init_repo(gitignore=gitignore)

        wt_cache = self.tmp_root / "wt-cache-only"
        wt_precious = self.tmp_root / "wt-precious-env"
        wt_other = self.tmp_root / "wt-other-ignored"

        _git(repo, "worktree", "add", "-b", "feat/cache-only", str(wt_cache))
        pycache_dir = wt_cache / "__pycache__"
        pycache_dir.mkdir()
        (pycache_dir / "module.cpython-311.pyc").write_bytes(b"\x00\x01")
        node_mod = wt_cache / "node_modules" / "pkg"
        node_mod.mkdir(parents=True)
        (node_mod / "index.js").write_text("module.exports = 1;\n", encoding="utf-8")

        _git(repo, "worktree", "add", "-b", "feat/precious-env", str(wt_precious))
        (wt_precious / ".env").write_text("API_KEY=dummy-test-value\n", encoding="utf-8")
        claude_dir = wt_precious / ".claude"
        claude_dir.mkdir()
        (claude_dir / "settings.local.json").write_text('{"ok": true}\n', encoding="utf-8")

        _git(repo, "worktree", "add", "-b", "feat/other-ignored", str(wt_other))
        tools_dir = wt_other / ".tools"
        tools_dir.mkdir()
        (tools_dir / "tool.bin").write_bytes(b"binary")

        proc = _run_cli("--repo", str(repo), "--json")
        self.assertEqual(proc.returncode, 1, proc.stderr)
        report = json.loads(proc.stdout)

        cache_entry = self._find_wt(report, wt_cache)
        self.assertEqual(cache_entry["status"], worktree_audit.STATUS_SAFE_TO_PRUNE)
        self.assertEqual(cache_entry["facts"]["precious_ignored"]["value"], [])
        self.assertEqual(cache_entry["facts"]["other_ignored"]["value"], [])
        self.assertTrue(len(cache_entry["facts"]["disposable_ignored"]["value"]) >= 2)

        precious_entry = self._find_wt(report, wt_precious)
        self.assertEqual(precious_entry["status"], worktree_audit.STATUS_DIRTY_HOLD)
        precious_paths = precious_entry["facts"]["precious_ignored"]["value"]
        self.assertIn(".env", precious_paths)
        self.assertTrue(
            any("settings.local.json" in p or ".claude" in p for p in precious_paths),
            f"Expected .claude local settings in precious_ignored, got {precious_paths}",
        )

        other_entry = self._find_wt(report, wt_other)
        self.assertEqual(other_entry["status"], worktree_audit.STATUS_DIRTY_HOLD)
        self.assertTrue(
            any(".tools" in p for p in other_entry["facts"]["other_ignored"]["value"])
        )

    def test_detached_head_with_unmerged_commit_held(self) -> None:
        repo, _ = self._init_repo()
        wt_detached = self.tmp_root / "wt-detached-unmerged"

        _git(repo, "worktree", "add", "--detach", str(wt_detached), "HEAD")
        (wt_detached / "experiment.txt").write_text("unmerged work\n", encoding="utf-8")
        _git(wt_detached, "add", "experiment.txt")
        _git(wt_detached, "commit", "-m", "wip: detached commit")
        detached_sha = _git(wt_detached, "rev-parse", "HEAD")

        proc = _run_cli("--repo", str(repo), "--json")
        self.assertEqual(proc.returncode, 1, proc.stderr)
        report = json.loads(proc.stdout)

        entry = self._find_wt(report, wt_detached)
        self.assertTrue(entry["detached"])
        self.assertEqual(entry["status"], worktree_audit.STATUS_DIRTY_HOLD)
        self.assertTrue(entry["facts"]["detached_head_reachable"]["known"])
        self.assertFalse(entry["facts"]["detached_head_reachable"]["value"])
        self.assertIn(detached_sha, entry["facts"]["unreachable_reflog_commits"]["value"])

    def test_detached_head_reset_still_flags_unreachable_reflog_commit(self) -> None:
        repo, _ = self._init_repo()
        wt_detached = self.tmp_root / "wt-detached-reflog"

        _git(repo, "worktree", "add", "--detach", str(wt_detached), "HEAD")
        (wt_detached / "lost.txt").write_text("stranded in reflog\n", encoding="utf-8")
        _git(wt_detached, "add", "lost.txt")
        _git(wt_detached, "commit", "-m", "wip: commit then reset")
        orphaned_sha = _git(wt_detached, "rev-parse", "HEAD")
        _git(wt_detached, "reset", "--hard", "main")

        proc = _run_cli("--repo", str(repo), "--json")
        self.assertEqual(proc.returncode, 1, proc.stderr)
        report = json.loads(proc.stdout)

        entry = self._find_wt(report, wt_detached)
        self.assertEqual(entry["status"], worktree_audit.STATUS_DIRTY_HOLD)
        self.assertTrue(entry["facts"]["detached_head_reachable"]["value"])
        self.assertIn(orphaned_sha, entry["facts"]["unreachable_reflog_commits"]["value"])

    def test_local_main_behind_origin_needs_sync(self) -> None:
        repo, bare_origin = self._init_repo(with_origin=True)
        assert bare_origin is not None

        peer = self.tmp_root / "peer-clone"
        _git(self.tmp_root, "clone", str(bare_origin), str(peer))
        _git(peer, "config", "user.name", "Peer")
        _git(peer, "config", "user.email", "peer@example.com")
        (peer / "remote-update.txt").write_text("from remote\n", encoding="utf-8")
        _git(peer, "add", "remote-update.txt")
        _git(peer, "commit", "-m", "feat: upstream change")
        _git(peer, "push", "origin", "main")

        # Update remote-tracking ref without advancing local main
        _git(repo, "fetch", "origin")

        proc = _run_cli("--repo", str(repo), "--json")
        self.assertEqual(proc.returncode, 1, proc.stderr)
        report = json.loads(proc.stdout)

        dbs = report["default_branch_sync"]
        self.assertTrue(dbs["known"])
        self.assertTrue(dbs["value"]["behind_origin"])
        self.assertEqual(dbs["value"]["behind"], 1)

        primary = self._find_wt(report, repo)
        self.assertEqual(primary["status"], worktree_audit.STATUS_NEEDS_SYNC)

    def test_unknown_fact_downgrades_safe_to_prune_to_unknown(self) -> None:
        clean_merged_facts = {
            "exists_on_disk": worktree_audit.known_fact(True),
            "locked": worktree_audit.known_fact(False),
            "tracked_dirty": worktree_audit.known_fact([]),
            "untracked_files": worktree_audit.known_fact([]),
            "precious_ignored": worktree_audit.known_fact([]),
            "other_ignored": worktree_audit.known_fact([]),
            "disposable_ignored": worktree_audit.known_fact(["__pycache__/"]),
            "upstream_drift": worktree_audit.known_fact(
                {"upstream": None, "ahead": None, "behind": None}
            ),
            "default_branch_drift": worktree_audit.known_fact(
                {"ref": "origin/main", "ahead": 0, "behind": 0}
            ),
            "head_merged_into_default": worktree_audit.known_fact(True),
            "detached_head_reachable": worktree_audit.known_fact(True),
            "unreachable_reflog_commits": worktree_audit.known_fact([]),
        }

        baseline_status, _, _ = worktree_audit.classify_worktree(
            clean_merged_facts,
            is_primary=False,
            is_default_branch=False,
            detached=False,
        )
        self.assertEqual(baseline_status, worktree_audit.STATUS_SAFE_TO_PRUNE)

        for key in worktree_audit.REQUIRED_SAFETY_FACTS:
            mutated = dict(clean_merged_facts)
            mutated[key] = worktree_audit.unknown_fact(f"simulated failure for {key}")
            status, reasons, _ = worktree_audit.classify_worktree(
                mutated,
                is_primary=False,
                is_default_branch=False,
                detached=False,
            )
            self.assertEqual(
                status,
                worktree_audit.STATUS_UNKNOWN,
                f"Expected unknown fact {key!r} to downgrade SAFE_TO_PRUNE -> UNKNOWN",
            )
            self.assertTrue(any(key in r for r in reasons))

    def test_unknown_default_branch_end_to_end_downgrades_to_unknown(self) -> None:
        repo, _ = self._init_repo(default_branch="custom-trunk")
        wt_linked = self.tmp_root / "wt-linked"
        _git(repo, "worktree", "add", "-b", "feat/x", str(wt_linked))

        proc = _run_cli("--repo", str(repo), "--json")
        self.assertEqual(proc.returncode, 1, proc.stderr)
        report = json.loads(proc.stdout)

        self.assertIsNone(report["default_branch"])
        self.assertFalse(report["default_branch_sync"]["known"])
        linked_entry = self._find_wt(report, wt_linked)
        self.assertEqual(linked_entry["status"], worktree_audit.STATUS_UNKNOWN)
        self.assertFalse(linked_entry["facts"]["default_branch_drift"]["known"])

    def test_cli_errors_exit_two_on_stderr(self) -> None:
        missing = self.tmp_root / "does-not-exist"
        proc_missing = _run_cli("--repo", str(missing))
        self.assertEqual(proc_missing.returncode, 2)
        self.assertTrue(proc_missing.stderr.strip())

        non_git = self.tmp_root / "plain-dir"
        non_git.mkdir()
        proc_non_git = _run_cli("--repo", str(non_git))
        self.assertEqual(proc_non_git.returncode, 2)
        self.assertTrue(proc_non_git.stderr.strip())

    def test_text_output_and_strictly_read_only(self) -> None:
        repo, _ = self._init_repo(with_origin=True)
        wt_merged = self.tmp_root / "wt-ro-check"
        _git(repo, "worktree", "add", "-b", "feat/ro", str(wt_merged))

        before_head = _git(repo, "rev-parse", "HEAD")
        before_wt = _git(repo, "worktree", "list", "--porcelain")
        before_branches = _git(repo, "branch", "--list")

        proc = _run_cli("--repo", str(repo))
        self.assertEqual(proc.returncode, 1, proc.stderr)
        self.assertIn("[SAFE_TO_PRUNE]", proc.stdout)
        self.assertIn("[CLEAN_ACTIVE]", proc.stdout)

        self.assertEqual(_git(repo, "rev-parse", "HEAD"), before_head)
        self.assertEqual(_git(repo, "worktree", "list", "--porcelain"), before_wt)
        self.assertEqual(_git(repo, "branch", "--list"), before_branches)


if __name__ == "__main__":
    unittest.main()
