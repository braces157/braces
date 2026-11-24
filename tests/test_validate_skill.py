#!/usr/bin/env python3
"""Unit tests for validate_skill.py and tools/sync_skills.py."""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

sys.dont_write_bytecode = True

REPO_ROOT = Path(__file__).resolve().parent.parent
VALIDATE_SCRIPT = REPO_ROOT / "skills" / "braces" / "scripts" / "validate_skill.py"
SYNC_SCRIPT = REPO_ROOT / "tools" / "sync_skills.py"
BRACES_DIR = REPO_ROOT / "skills" / "braces"
GEMINI_TEMPLATE = REPO_ROOT / "GEMINI.md.template"


def run_python(script: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-B", str(script), *args],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )


def create_valid_skill(parent: Path, name: str = "demo-skill") -> Path:
    skill_dir = parent / name
    (skill_dir / "references").mkdir(parents=True, exist_ok=True)
    (skill_dir / "scripts").mkdir(parents=True, exist_ok=True)
    (skill_dir / "evals").mkdir(parents=True, exist_ok=True)

    (skill_dir / "SKILL.md").write_text(
        f"---\n"
        f"name: {name}\n"
        f"description: >-\n"
        f"  Use whenever auditing {name} targets or checking local invariants.\n"
        f"  Do NOT use when opening pull requests or editing unrelated modules.\n"
        f"compatibility: Requires python3 (>=3.8)\n"
        f"---\n\n"
        f"# Demo Skill\n\n"
        f"Read [guide.md](references/guide.md) when you need deep details.\n"
        f"Run `python scripts/helper.py --json` and inspect `evals/evals.json`.\n"
        f"Placeholder syntax like `references/<topic>.md` is ignored.\n",
        encoding="utf-8",
    )
    (skill_dir / "references" / "guide.md").write_text(
        "# Guide\n\nUse `scripts/helper.py` to check state cleanly.\n",
        encoding="utf-8",
    )
    (skill_dir / "scripts" / "helper.py").write_text(
        "#!/usr/bin/env python3\nprint('ok')\n",
        encoding="utf-8",
    )
    (skill_dir / "evals" / "evals.json").write_text(
        json.dumps(
            {
                "skill": name,
                "trigger_tests": [
                    {"prompt": "Audit demo-skill targets now", "should_trigger": True, "split": "train"},
                    {"prompt": "Open a PR on GitHub", "should_trigger": False, "split": "holdout"},
                ],
                "forward_tests": [
                    {
                        "id": "basic-check",
                        "prompt": "Run the demo audit in this folder",
                        "invariants": ["Helper exits 0", "No scratch files remain"],
                    }
                ],
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    return skill_dir


class ValidateSkillTests(unittest.TestCase):
    def test_valid_skill_folded_frontmatter(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            skill_dir = create_valid_skill(Path(tmp))
            proc = run_python(VALIDATE_SCRIPT, str(skill_dir), "--json")
            self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
            data = json.loads(proc.stdout)
            self.assertEqual(data["status"], "pass")
            self.assertEqual(data["summary"]["error_count"], 0)
            self.assertEqual(data["summary"]["warning_count"], 0)

    def test_valid_skill_literal_block_and_metadata(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            skill_dir = Path(tmp) / "meta-skill"
            skill_dir.mkdir()
            (skill_dir / "SKILL.md").write_text(
                "---\n"
                "name: meta-skill\n"
                "description: |-\n"
                "  Use whenever verifying release artifacts.\n"
                "  Do NOT use when writing feature code.\n"
                "license: MIT\n"
                "allowed-tools:\n"
                "  - Bash\n"
                "  - Read\n"
                "metadata:\n"
                "  owner: braces157\n"
                "  tier: core\n"
                "---\n\n"
                "# Meta Skill\n\nShort valid body.\n",
                encoding="utf-8",
            )
            proc = run_python(VALIDATE_SCRIPT, str(skill_dir), "--json")
            self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
            data = json.loads(proc.stdout)
            self.assertEqual(data["status"], "pass")

    def test_bad_name_and_dir_mismatch(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            skill_dir = create_valid_skill(Path(tmp), "good-dir")
            (skill_dir / "SKILL.md").write_text(
                "---\n"
                "name: Bad_Name\n"
                "description: Use whenever auditing. Do NOT use when shipping PRs.\n"
                "---\n\n# Body\n",
                encoding="utf-8",
            )
            proc = run_python(VALIDATE_SCRIPT, str(skill_dir), "--json")
            self.assertEqual(proc.returncode, 1)
            errors = json.loads(proc.stdout)["results"][0]["errors"]
            self.assertTrue(any("hyphen-case" in err for err in errors))
            self.assertTrue(any("does not match directory name" in err for err in errors))

    def test_missing_and_invalid_description(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            # Missing description
            s1 = root / "no-desc"
            s1.mkdir()
            (s1 / "SKILL.md").write_text("---\nname: no-desc\n---\n\n# Body\n", encoding="utf-8")
            p1 = run_python(VALIDATE_SCRIPT, str(s1), "--json")
            self.assertEqual(p1.returncode, 1)
            self.assertTrue(any("description" in e for e in json.loads(p1.stdout)["results"][0]["errors"]))

            # Angle brackets and >1024 chars
            s2 = root / "bad-desc"
            s2.mkdir()
            long_desc = "Use whenever testing <xml> " + ("x" * 1030)
            (s2 / "SKILL.md").write_text(
                f"---\nname: bad-desc\ndescription: {long_desc}\n---\n\n# Body\n",
                encoding="utf-8",
            )
            p2 = run_python(VALIDATE_SCRIPT, str(s2), "--json")
            self.assertEqual(p2.returncode, 1)
            errs2 = json.loads(p2.stdout)["results"][0]["errors"]
            self.assertTrue(any("1024" in e for e in errs2))
            self.assertTrue(any("'<' or '>'" in e for e in errs2))

    def test_unsupported_frontmatter_key(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            skill_dir = create_valid_skill(Path(tmp))
            text = (skill_dir / "SKILL.md").read_text(encoding="utf-8")
            text = text.replace("compatibility:", "vendor_custom: yes\ncompatibility:")
            (skill_dir / "SKILL.md").write_text(text, encoding="utf-8")
            proc = run_python(VALIDATE_SCRIPT, str(skill_dir), "--json")
            self.assertEqual(proc.returncode, 1)
            errs = json.loads(proc.stdout)["results"][0]["errors"]
            self.assertTrue(any("unsupported key 'vendor_custom'" in e for e in errs))

    def test_advisory_warnings_still_exit_zero(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            skill_dir = create_valid_skill(Path(tmp))
            verbose_desc = "Use whenever auditing local code quality. " + ("detail " * 95)
            (skill_dir / "SKILL.md").write_text(
                f"---\nname: demo-skill\ndescription: {verbose_desc.strip()}\n---\n\n# Body\n",
                encoding="utf-8",
            )
            (skill_dir / "README.md").write_text("# Stray readme\n", encoding="utf-8")
            proc = run_python(VALIDATE_SCRIPT, str(skill_dir), "--json")
            self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
            warnings = json.loads(proc.stdout)["results"][0]["warnings"]
            self.assertTrue(any("negative trigger boundary" in w for w in warnings))
            self.assertTrue(any("verbose" in w for w in warnings))
            self.assertTrue(any("README.md" in w for w in warnings))

    def test_broken_links_prose_paths_and_traversal(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "outside.txt").write_text("secret\n", encoding="utf-8")
            skill_dir = create_valid_skill(root)
            (skill_dir / "SKILL.md").write_text(
                "---\n"
                "name: demo-skill\n"
                "description: Use whenever auditing. Do NOT use when shipping PRs.\n"
                "---\n\n"
                "# Broken Paths\n\n"
                "- [missing](references/nope.md)\n"
                "- [escape](../outside.txt)\n"
                "- Run `scripts/does_not_exist.py`\n"
                "- Bad escape `references/../../outside.txt`\n",
                encoding="utf-8",
            )
            proc = run_python(VALIDATE_SCRIPT, str(skill_dir), "--json")
            self.assertEqual(proc.returncode, 1)
            errs = json.loads(proc.stdout)["results"][0]["errors"]
            self.assertTrue(any("broken markdown link 'references/nope.md'" in e for e in errs))
            self.assertTrue(any("escapes skill directory" in e and "../outside.txt" in e for e in errs))
            self.assertTrue(any("broken prose path 'scripts/does_not_exist.py'" in e for e in errs))
            self.assertTrue(any("escapes skill directory" in e and "references/../../outside.txt" in e for e in errs))

    def test_todo_markers_outside_vs_inside_fences(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            skill_dir = create_valid_skill(Path(tmp))
            # Inside code fence -> should PASS
            (skill_dir / "SKILL.md").write_text(
                "---\n"
                "name: demo-skill\n"
                "description: Use whenever testing. Do NOT use otherwise.\n"
                "---\n\n"
                "```markdown\n"
                "[TODO: example inside fence]\n"
                "```\n",
                encoding="utf-8",
            )
            p_ok = run_python(VALIDATE_SCRIPT, str(skill_dir), "--json")
            self.assertEqual(p_ok.returncode, 0, p_ok.stdout)

            # Outside code fence -> should FAIL
            with (skill_dir / "SKILL.md").open("a", encoding="utf-8") as fh:
                fh.write("\n[TODO: fill in real steps]\n")
            p_fail = run_python(VALIDATE_SCRIPT, str(skill_dir), "--json")
            self.assertEqual(p_fail.returncode, 1)
            errs = json.loads(p_fail.stdout)["results"][0]["errors"]
            self.assertTrue(any("leftover TODO marker" in e for e in errs))

    def test_word_cap_overflow_skill_and_reference(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            skill_dir = create_valid_skill(Path(tmp))
            (skill_dir / "SKILL.md").write_text(
                "---\n"
                "name: demo-skill\n"
                "description: Use whenever testing. Do NOT use otherwise.\n"
                "---\n\n"
                + ("word " * 120),
                encoding="utf-8",
            )
            (skill_dir / "references" / "guide.md").write_text("refword " * 80, encoding="utf-8")
            proc = run_python(
                VALIDATE_SCRIPT,
                str(skill_dir),
                "--max-skill-words",
                "50",
                "--max-ref-words",
                "40",
                "--json",
            )
            self.assertEqual(proc.returncode, 1)
            errs = json.loads(proc.stdout)["results"][0]["errors"]
            self.assertTrue(any("SKILL.md word count" in e for e in errs))
            self.assertTrue(any("references/guide.md word count" in e for e in errs))

    def test_runtime_clutter_detection(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            skill_dir = create_valid_skill(Path(tmp))
            pycache = skill_dir / "scripts" / "__pycache__"
            pycache.mkdir()
            (pycache / "helper.cpython-311.pyc").write_bytes(b"\x00\x01")
            (skill_dir / ".DS_Store").write_bytes(b"junk")
            (skill_dir / "Thumbs.db").write_bytes(b"junk")
            (skill_dir / "stray.pyc").write_bytes(b"junk")

            proc = run_python(VALIDATE_SCRIPT, str(skill_dir), "--json")
            self.assertEqual(proc.returncode, 1)
            errs = json.loads(proc.stdout)["results"][0]["errors"]
            self.assertTrue(any("__pycache__" in e for e in errs))
            self.assertTrue(any(".DS_Store" in e for e in errs))
            self.assertTrue(any("Thumbs.db" in e for e in errs))
            self.assertTrue(any("stray.pyc" in e for e in errs))

    def test_evals_json_validation_cases(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            skill_dir = create_valid_skill(Path(tmp))
            evals_path = skill_dir / "evals" / "evals.json"

            # 1. Invalid JSON
            evals_path.write_text("{broken json", encoding="utf-8")
            p1 = run_python(VALIDATE_SCRIPT, str(skill_dir), "--json")
            self.assertEqual(p1.returncode, 1)
            self.assertTrue(any("invalid JSON" in e for e in json.loads(p1.stdout)["results"][0]["errors"]))

            # 2. Missing negative trigger & missing forward_tests
            evals_path.write_text(
                json.dumps(
                    {
                        "trigger_tests": [{"prompt": "Only positive", "should_trigger": True}],
                    }
                ),
                encoding="utf-8",
            )
            p2 = run_python(VALIDATE_SCRIPT, str(skill_dir), "--json")
            self.assertEqual(p2.returncode, 1)
            errs2 = json.loads(p2.stdout)["results"][0]["errors"]
            self.assertTrue(any("negative case" in e for e in errs2))
            self.assertTrue(any("forward_tests" in e for e in errs2))

            # 3. Dict-form trigger_tests {positive, negative} -> PASS
            evals_path.write_text(
                json.dumps(
                    {
                        "skill": "demo-skill",
                        "trigger_tests": {
                            "positive": ["Run demo audit"],
                            "negative": ["Open a PR"],
                        },
                        "forward_tests": [
                            {"prompt": "Audit repo", "invariants": ["Exits 0"]}
                        ],
                    }
                ),
                encoding="utf-8",
            )
            p3 = run_python(VALIDATE_SCRIPT, str(skill_dir), "--json")
            self.assertEqual(p3.returncode, 0, p3.stdout)

    def test_all_mode_and_cli_errors(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            parent = Path(tmp)
            create_valid_skill(parent, "skill-one")
            create_valid_skill(parent, "skill-two")

            proc = run_python(VALIDATE_SCRIPT, str(parent), "--all", "--json")
            self.assertEqual(proc.returncode, 0, proc.stdout)
            data = json.loads(proc.stdout)
            self.assertEqual(data["summary"]["skills_checked"], 2)
            self.assertEqual(data["summary"]["passed"], 2)

            # Nonexistent path -> exit 2
            p_missing = run_python(VALIDATE_SCRIPT, str(parent / "missing-dir"))
            self.assertEqual(p_missing.returncode, 2)

            # Invalid word cap -> exit 2
            p_cap = run_python(VALIDATE_SCRIPT, str(parent), "--all", "--max-skill-words", "0")
            self.assertEqual(p_cap.returncode, 2)

            # Empty directory passed as single skill -> missing SKILL.md -> exit 1
            empty_dir = parent / "empty-skill"
            empty_dir.mkdir()
            p_empty = run_python(VALIDATE_SCRIPT, str(empty_dir))
            self.assertEqual(p_empty.returncode, 1)

    def test_skill_forge_self_validation_and_gemini_line_budget(self) -> None:
        proc = run_python(VALIDATE_SCRIPT, str(BRACES_DIR), "--json")
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        data = json.loads(proc.stdout)
        self.assertEqual(data["status"], "pass")
        res = data["results"][0]
        self.assertEqual(res["errors"], [])
        self.assertEqual(res["warnings"], [])
        self.assertLessEqual(res["word_counts"]["SKILL.md"], 950)
        ref_counts = {
            k: v for k, v in res["word_counts"].items() if k.startswith("references/")
        }
        self.assertEqual(len(ref_counts), 9)
        for ref_name, count in ref_counts.items():
            self.assertLessEqual(count, 880, f"{ref_name} has {count} words (> 880)")

        gemini_lines = GEMINI_TEMPLATE.read_text(encoding="utf-8").splitlines()
        self.assertLess(len(gemini_lines), 60, f"GEMINI.md.template has {len(gemini_lines)} lines (must be < 60)")


class SyncSkillsTests(unittest.TestCase):
    def test_sync_preserves_pr_workflow_and_excludes_clutter(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            src = root / "skills"
            dest = root / "dest_skills"
            src.mkdir()
            dest.mkdir()

            # Pre-existing unrelated skill that MUST survive untouched.
            pr_dir = dest / "pr-workflow"
            pr_dir.mkdir()
            pr_skill_md = pr_dir / "SKILL.md"
            pr_skill_md.write_text("existing pr-workflow content\n", encoding="utf-8")

            # Valid skill with README.md (warning only -> still passes validation).
            s1 = create_valid_skill(src, "alpha-skill")
            (s1 / "README.md").write_text("# Should not be copied\n", encoding="utf-8")

            proc = run_python(
                SYNC_SCRIPT,
                "--source",
                str(src),
                "--dest",
                str(dest),
                "--json",
            )
            self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
            payload = json.loads(proc.stdout)
            self.assertEqual(payload["status"], "ok")

            # Synced skill files exist, README.md skipped
            self.assertTrue((dest / "alpha-skill" / "SKILL.md").is_file())
            self.assertTrue((dest / "alpha-skill" / "scripts" / "helper.py").is_file())
            self.assertFalse((dest / "alpha-skill" / "README.md").exists())

            # Existing pr-workflow skill preserved intact
            self.assertEqual(pr_skill_md.read_text(encoding="utf-8"), "existing pr-workflow content\n")

    def test_sync_refuses_invalid_skill_unless_forced_and_skips_pycache(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            src = root / "skills"
            dest = root / "dest_skills"
            src.mkdir()
            dest.mkdir()

            bad = create_valid_skill(src, "dirty-skill")
            pycache = bad / "scripts" / "__pycache__"
            pycache.mkdir()
            (pycache / "helper.cpython-311.pyc").write_bytes(b"\x00\x01")
            (bad / ".DS_Store").write_bytes(b"junk")

            # Without --force: must fail validation (exit 1) and copy nothing
            p_refuse = run_python(SYNC_SCRIPT, "--source", str(src), "--dest", str(dest), "--json")
            self.assertEqual(p_refuse.returncode, 1)
            self.assertEqual(json.loads(p_refuse.stdout)["status"], "validation_failed")
            self.assertFalse((dest / "dirty-skill").exists())

            # With --force: copies runtime files while still excluding __pycache__ / *.pyc / .DS_Store
            p_force = run_python(
                SYNC_SCRIPT, "--source", str(src), "--dest", str(dest), "--force", "--json"
            )
            self.assertEqual(p_force.returncode, 0, p_force.stdout + p_force.stderr)
            self.assertTrue((dest / "dirty-skill" / "SKILL.md").is_file())
            self.assertFalse((dest / "dirty-skill" / "scripts" / "__pycache__").exists())
            self.assertFalse((dest / "dirty-skill" / ".DS_Store").exists())

    def test_sync_dry_run_idempotency_stale_removal_and_filters(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            src = root / "skills"
            dest = root / "dest_skills"
            src.mkdir()

            s1 = create_valid_skill(src, "skill-a")
            create_valid_skill(src, "skill-b")

            # 1. Dry-run creates nothing on disk
            p_dry = run_python(
                SYNC_SCRIPT,
                "--source",
                str(src),
                "--dest",
                str(dest),
                "--skill",
                "skill-a",
                "--dry-run",
                "--json",
            )
            self.assertEqual(p_dry.returncode, 0, p_dry.stdout + p_dry.stderr)
            dry_data = json.loads(p_dry.stdout)
            self.assertTrue(dry_data["dry_run"])
            self.assertEqual(dry_data["skills_selected"], ["skill-a"])
            self.assertFalse(dest.exists())

            # 2. Real sync of skill-a only
            p_run1 = run_python(
                SYNC_SCRIPT, "--source", str(src), "--dest", str(dest), "--skill", "skill-a", "--json"
            )
            self.assertEqual(p_run1.returncode, 0, p_run1.stdout)
            self.assertTrue((dest / "skill-a" / "SKILL.md").is_file())
            self.assertFalse((dest / "skill-b").exists())

            # 3. Second sync is idempotent (all unchanged)
            p_run2 = run_python(
                SYNC_SCRIPT, "--source", str(src), "--dest", str(dest), "--skill", "skill-a", "--json"
            )
            self.assertEqual(p_run2.returncode, 0)
            op2 = json.loads(p_run2.stdout)["targets"][0]["skills"][0]
            self.assertEqual(op2["created"], [])
            self.assertEqual(op2["updated"], [])
            self.assertEqual(op2["removed_stale"], [])
            self.assertGreater(len(op2["unchanged"]), 0)

            # 4. Stale file inside dest/skill-a is pruned on next sync
            stale = dest / "skill-a" / "scripts" / "obsolete.py"
            stale.write_text("# stale\n", encoding="utf-8")
            p_run3 = run_python(
                SYNC_SCRIPT, "--source", str(src), "--dest", str(dest), "--skill", "skill-a", "--json"
            )
            self.assertEqual(p_run3.returncode, 0)
            op3 = json.loads(p_run3.stdout)["targets"][0]["skills"][0]
            self.assertEqual(op3["removed_stale"], ["scripts/obsolete.py"])
            self.assertFalse(stale.exists())
            self.assertTrue((s1 / "SKILL.md").is_file())

    def test_sync_cli_errors(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            src = Path(tmp) / "skills"
            src.mkdir()
            create_valid_skill(src, "only-skill")

            # Missing --target and --dest -> exit 2
            p_no_dest = run_python(SYNC_SCRIPT, "--source", str(src))
            self.assertEqual(p_no_dest.returncode, 2)

            # Unknown --skill -> exit 2
            p_unknown = run_python(
                SYNC_SCRIPT, "--source", str(src), "--dest", str(Path(tmp) / "out"), "--skill", "nope"
            )
            self.assertEqual(p_unknown.returncode, 2)

    def test_sync_normalizes_crlf_text_to_lf_and_preserves_binary(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            src = root / "skills"
            dest = root / "dest_skills"
            src.mkdir()

            skill = create_valid_skill(src, "eol-skill")
            skill_md = skill / "SKILL.md"
            lf_bytes = skill_md.read_bytes().replace(b"\r\n", b"\n")
            crlf_bytes = lf_bytes.replace(b"\n", b"\r\n")
            skill_md.write_bytes(crlf_bytes)
            assets_dir = skill / "assets"
            assets_dir.mkdir()
            png_header = b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR"
            (assets_dir / "icon.png").write_bytes(png_header)

            p1 = run_python(SYNC_SCRIPT, "--source", str(src), "--dest", str(dest), "--json")
            self.assertEqual(p1.returncode, 0, p1.stdout + p1.stderr)

            synced_md = (dest / "eol-skill" / "SKILL.md").read_bytes()
            self.assertNotIn(b"\r\n", synced_md)
            self.assertEqual(synced_md, crlf_bytes.replace(b"\r\n", b"\n"))
            self.assertEqual((dest / "eol-skill" / "assets" / "icon.png").read_bytes(), png_header)

            # Second sync with CRLF source must remain idempotent (unchanged)
            p2 = run_python(SYNC_SCRIPT, "--source", str(src), "--dest", str(dest), "--json")
            self.assertEqual(p2.returncode, 0, p2.stdout + p2.stderr)
            op2 = json.loads(p2.stdout)["targets"][0]["skills"][0]
            self.assertEqual(op2["created"], [])
            self.assertEqual(op2["updated"], [])
            self.assertIn("SKILL.md", op2["unchanged"])


if __name__ == "__main__":
    unittest.main()
