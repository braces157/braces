#!/usr/bin/env python3
"""Unit and CLI tests for the deliver-verify skill scripts and structure."""

from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parents[1]
SKILL_DIR = ROOT_DIR / "skills" / "braces"
SCRIPTS_DIR = SKILL_DIR / "scripts"
WIN_VERIFY_SCRIPT = SCRIPTS_DIR / "win_binary_verify.py"
UI_SLOP_SCRIPT = SCRIPTS_DIR / "ui_slop_check.py"
EVALS_FILE = SKILL_DIR / "evals" / "evals.json"
SKILL_MD = SKILL_DIR / "SKILL.md"
REF_UI_MD = SKILL_DIR / "references" / "review-and-verify.md"


def run_python_script(script: Path, args: list[str]) -> subprocess.CompletedProcess[str]:
    """Run a skill Python script with -B (no __pycache__ pollution) and UTF-8 stdio."""
    env = dict(os.environ)
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    env["PYTHONUTF8"] = "1"
    return subprocess.run(
        [sys.executable, "-B", str(script), *args],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        env=env,
        shell=False,
        check=False,
    )


def write_bytes(path: Path, data: bytes) -> Path:
    """Create parent directories, write bytes, and make executable on POSIX."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    if os.name != "nt":
        path.chmod(0o755)
    return path


def write_text(path: Path, text: str) -> Path:
    """Create parent directories and write UTF-8 text."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


class TestWinBinaryVerify(unittest.TestCase):
    """End-to-end tests for scripts/win_binary_verify.py."""

    def _exe_name(self, stem: str) -> str:
        return f"{stem}.exe" if os.name == "nt" else stem

    def test_exact_parity_single_match_returns_zero(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            bin_dir = root / "installed"
            build_dir = root / "target" / "release"
            payload_bytes = b"MZ-fresh-release-binary-v2.0"

            installed = write_bytes(bin_dir / self._exe_name("tuitify"), payload_bytes)
            built = write_bytes(build_dir / self._exe_name("tuitify"), payload_bytes)
            expected_hash = hashlib.sha256(payload_bytes).hexdigest()

            proc = run_python_script(
                WIN_VERIFY_SCRIPT,
                [
                    "--name",
                    "tuitify",
                    "--built",
                    str(built),
                    "--path-env",
                    str(bin_dir),
                    "--json",
                ],
            )
            self.assertEqual(proc.returncode, 0, msg=proc.stderr + proc.stdout)
            data = json.loads(proc.stdout)
            self.assertEqual(data["status"], "ok")
            self.assertTrue(data["verified"])
            self.assertFalse(data["shadowed"])
            self.assertEqual(data["issues"], [])
            self.assertTrue(data["parity"]["checked"])
            self.assertTrue(data["parity"]["hash_match"])
            self.assertFalse(data["parity"]["same_file"])
            self.assertEqual(data["active"]["sha256"], expected_hash)
            self.assertEqual(data["built"]["sha256"], expected_hash)
            self.assertEqual(len(data["path_matches"]), 1)
            self.assertEqual(
                os.path.normcase(os.path.realpath(data["active"]["path"])),
                os.path.normcase(os.path.realpath(str(installed))),
            )

    def test_sha256_mismatch_flags_stale_binary_and_returns_one(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            bin_dir = root / "cargo_bin"
            build_dir = root / "target" / "release"

            old_bytes = b"MZ-stale-installed-v1.0"
            new_bytes = b"MZ-fresh-built-v2.0"
            write_bytes(bin_dir / self._exe_name("tuitify"), old_bytes)
            built = write_bytes(build_dir / self._exe_name("tuitify"), new_bytes)

            proc = run_python_script(
                WIN_VERIFY_SCRIPT,
                [
                    "--name",
                    "tuitify",
                    "--built",
                    str(built),
                    "--path-env",
                    str(bin_dir),
                    "--json",
                ],
            )
            self.assertEqual(proc.returncode, 1, msg=proc.stdout)
            data = json.loads(proc.stdout)
            self.assertFalse(data["verified"])
            self.assertFalse(data["shadowed"])
            self.assertFalse(data["parity"]["hash_match"])
            codes = {item["code"] for item in data["issues"]}
            self.assertEqual(codes, {"SHA256_MISMATCH"})
            self.assertEqual(
                data["active"]["sha256"], hashlib.sha256(old_bytes).hexdigest()
            )
            self.assertEqual(
                data["built"]["sha256"], hashlib.sha256(new_bytes).hexdigest()
            )
            self.assertFalse(data["path_matches"][0]["matches_built"])

    def test_path_shadowing_detected_even_when_active_matches_built(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            dir1 = root / "first_on_path"
            dir2 = root / "second_on_path"
            build_dir = root / "target"

            fresh_bytes = b"MZ-fresh-v2"
            stale_bytes = b"MZ-stale-v1"
            write_bytes(dir1 / self._exe_name("discodex"), fresh_bytes)
            write_bytes(dir2 / self._exe_name("discodex"), stale_bytes)
            built = write_bytes(build_dir / self._exe_name("discodex"), fresh_bytes)

            path_env = os.pathsep.join([str(dir1), str(dir2)])
            proc = run_python_script(
                WIN_VERIFY_SCRIPT,
                [
                    "--name",
                    "discodex",
                    "--built",
                    str(built),
                    "--path-env",
                    path_env,
                    "--json",
                ],
            )
            self.assertEqual(proc.returncode, 1, msg=proc.stdout)
            data = json.loads(proc.stdout)
            self.assertFalse(data["verified"])
            self.assertTrue(data["shadowed"])
            self.assertTrue(data["parity"]["hash_match"])
            codes = {item["code"] for item in data["issues"]}
            self.assertEqual(codes, {"PATH_SHADOWING"})
            self.assertEqual(len(data["path_matches"]), 2)
            self.assertTrue(data["path_matches"][0]["matches_built"])
            self.assertFalse(data["path_matches"][1]["matches_built"])

    def test_shadowing_and_mismatch_both_reported_together(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            dir1 = root / "old_bin"
            dir2 = root / "new_bin"
            build_dir = root / "target"

            stale_bytes = b"MZ-old-shadowing-copy"
            fresh_bytes = b"MZ-fresh-build"
            write_bytes(dir1 / self._exe_name("tuitify"), stale_bytes)
            write_bytes(dir2 / self._exe_name("tuitify"), fresh_bytes)
            built = write_bytes(build_dir / self._exe_name("tuitify"), fresh_bytes)

            path_env = os.pathsep.join([str(dir1), str(dir2)])
            proc = run_python_script(
                WIN_VERIFY_SCRIPT,
                [
                    "--name",
                    "tuitify",
                    "--built",
                    str(built),
                    "--path-env",
                    path_env,
                    "--json",
                ],
            )
            self.assertEqual(proc.returncode, 1)
            data = json.loads(proc.stdout)
            codes = {item["code"] for item in data["issues"]}
            self.assertEqual(codes, {"PATH_SHADOWING", "SHA256_MISMATCH"})

    def test_missing_binary_on_path_returns_one(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            empty_dir = Path(tmp) / "empty_bin"
            empty_dir.mkdir()
            proc = run_python_script(
                WIN_VERIFY_SCRIPT,
                ["--name", "nonexistent_tool", "--path-env", str(empty_dir), "--json"],
            )
            self.assertEqual(proc.returncode, 1)
            data = json.loads(proc.stdout)
            self.assertFalse(data["verified"])
            self.assertIsNone(data["active"])
            codes = {item["code"] for item in data["issues"]}
            self.assertEqual(codes, {"MISSING_ON_PATH"})

    def test_duplicate_path_entries_deduplicated_case_insensitively(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            bin_dir = root / "ToolsBin"
            write_bytes(bin_dir / self._exe_name("mycli"), b"MZ-single-real-file")

            duplicate_entry = (
                str(bin_dir).upper() if os.name == "nt" else str(bin_dir)
            )
            quoted_entry = f'"{bin_dir}"'
            path_env = os.pathsep.join([str(bin_dir), duplicate_entry, quoted_entry])

            proc = run_python_script(
                WIN_VERIFY_SCRIPT,
                ["--name", "mycli", "--path-env", path_env, "--json"],
            )
            self.assertEqual(proc.returncode, 0, msg=proc.stdout)
            data = json.loads(proc.stdout)
            self.assertTrue(data["verified"])
            self.assertFalse(data["shadowed"])
            self.assertEqual(len(data["path_matches"]), 1)

    @unittest.skipUnless(os.name == "nt", "Windows PATHEXT resolution test")
    def test_windows_explicit_cmd_extension_and_pathext_shadow(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            bin_dir = root / "bin"
            write_bytes(bin_dir / "runner.cmd", b"@echo off\r\necho ok\r\n")

            proc_cmd = run_python_script(
                WIN_VERIFY_SCRIPT,
                ["--name", "runner.cmd", "--path-env", str(bin_dir), "--json"],
            )
            self.assertEqual(proc_cmd.returncode, 0, msg=proc_cmd.stdout)
            data_cmd = json.loads(proc_cmd.stdout)
            self.assertEqual(len(data_cmd["path_matches"]), 1)
            self.assertTrue(data_cmd["active"]["path"].lower().endswith("runner.cmd"))

            # Add runner.exe in same dir -> PATHEXT resolves both and flags shadowing.
            write_bytes(bin_dir / "runner.exe", b"MZ-real-exe")
            proc_shadow = run_python_script(
                WIN_VERIFY_SCRIPT,
                ["--name", "runner", "--path-env", str(bin_dir), "--json"],
            )
            self.assertEqual(proc_shadow.returncode, 1)
            data_shadow = json.loads(proc_shadow.stdout)
            self.assertTrue(data_shadow["shadowed"])
            self.assertEqual(len(data_shadow["path_matches"]), 2)
            self.assertTrue(data_shadow["active"]["path"].lower().endswith("runner.exe"))

    def test_invalid_built_and_name_return_exit_two(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            missing_built = Path(tmp) / "does_not_exist.exe"
            proc_missing = run_python_script(
                WIN_VERIFY_SCRIPT,
                ["--name", "tuitify", "--built", str(missing_built), "--path-env", tmp],
            )
            self.assertEqual(proc_missing.returncode, 2)

            proc_bad_name = run_python_script(
                WIN_VERIFY_SCRIPT,
                ["--name", "sub/tuitify.exe", "--path-env", tmp],
            )
            self.assertEqual(proc_bad_name.returncode, 2)

    def test_text_output_and_process_check_flag(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            bin_dir = Path(tmp) / "bin"
            write_bytes(bin_dir / self._exe_name("zz_unique_probe_bin"), b"MZ-probe")
            proc = run_python_script(
                WIN_VERIFY_SCRIPT,
                [
                    "--name",
                    "zz_unique_probe_bin",
                    "--path-env",
                    str(bin_dir),
                    "--check-processes",
                ],
            )
            self.assertEqual(proc.returncode, 0, msg=proc.stderr + proc.stdout)
            self.assertIn("Binary Verification: PASS", proc.stdout)
            self.assertIn("PATH Matches:", proc.stdout)
            self.assertIn("Processes:", proc.stdout)


class TestUiSlopCheck(unittest.TestCase):
    """End-to-end tests for scripts/ui_slop_check.py."""

    def test_clean_repo_returns_zero_and_skips_vendor_dirs(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            write_text(repo / "DESIGN.md", "# Design Tokens\n- Surface: #f8f7f4\n")
            write_text(
                repo / "src" / "styles.css",
                ":root { --font-display: 'Space Grotesk', sans-serif; }\n"
                "body { font-family: var(--font-display); }\n",
            )
            write_text(
                repo / "src" / "Header.tsx",
                '"use client";\n'
                "export function Header({ onClose }: { onClose: () => void }) {\n"
                "  return (\n"
                "    <header>\n"
                "      <h1>Match Hub</h1>\n"
                "      <h2>Live Rounds</h2>\n"
                "      <h3>Table 1</h3>\n"
                "      <button aria-label=\"Close panel\" onClick={() => onClose()}>\n"
                "        <CloseIcon className=\"w-4 h-4\" />\n"
                "      </button>\n"
                "      <button title=\"Refresh\"><RefreshIcon /></button>\n"
                "      <button onClick={() => onClose()}><SaveIcon /> Save</button>\n"
                "      <button><MenuIcon /><span className=\"sr-only\">Menu</span></button>\n"
                "    </header>\n"
                "  );\n"
                "}\n",
            )
            write_text(
                repo / "src" / "components" / "index.ts",
                'export { Header } from "./Header";\n',
            )
            # Noise inside node_modules and .next MUST be ignored.
            write_text(
                repo / "node_modules" / "pkg" / "Bad.tsx",
                "<h1>A</h1><h3>B</h3><button><X /></button>\n",
            )
            write_text(
                repo / ".next" / "index.ts",
                'export * from "./a";\nexport * from "./b";\nexport * from "./c";\n',
            )

            proc = run_python_script(UI_SLOP_SCRIPT, ["--repo", str(repo), "--json"])
            self.assertEqual(proc.returncode, 0, msg=proc.stdout + proc.stderr)
            data = json.loads(proc.stdout)
            self.assertEqual(data["status"], "clean")
            self.assertEqual(data["findings"], [])
            self.assertTrue(data["design_grounding"]["has_design_md"])
            self.assertTrue(data["design_grounding"]["has_custom_font_token"])

    def test_detects_all_slop_a11y_rsc_barrel_and_scratch_png_defects(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            # 1. Scratch screenshot directories & unignored root PNG (plus one ignored PNG)
            write_text(repo / ".gitignore", "ignored-capture.png\n")
            write_bytes(repo / ".taste-skill" / "home.png", b"\x89PNG\r\n")
            write_bytes(repo / "screenshots" / "modal.png", b"\x89PNG\r\n")
            write_bytes(repo / "stray-root.png", b"\x89PNG\r\n")
            write_bytes(repo / "ignored-capture.png", b"\x89PNG\r\n")

            # 2. Skipped heading + unlabeled icon buttons (including JSX arrow fn onClick!) + purple gradient
            write_text(
                repo / "src" / "App.tsx",
                "export function App({ close }: { close: () => void }) {\n"
                "  return (\n"
                "    <main className=\"bg-gradient-to-r from-purple-500 to-indigo-600\">\n"
                "      <h1>Title</h1>\n"
                "      <h3>Skipped H2</h3>\n"
                "      <h2>Back to H2 is fine</h2>\n"
                "      <button onClick={() => close()}>\n"
                "        <XIcon className=\"h-4 w-4\" />\n"
                "      </button>\n"
                "      <button><svg viewBox=\"0 0 24 24\"><path d=\"M0 0\" /></svg></button>\n"
                "      <button aria-label=\"Labeled\"><XIcon /></button>\n"
                "    </main>\n"
                "  );\n"
                "}\n",
            )

            # 3. CSS purple-indigo gradient + unconfigured default font (no DESIGN.md)
            write_text(
                repo / "src" / "app.css",
                "body {\n"
                "  font-family: 'Inter', sans-serif;\n"
                "  background: linear-gradient(135deg, #6366f1, #8b5cf6);\n"
                "}\n",
            )

            # 4. Giant 'use client' monolith (>400 lines)
            monolith_lines = ['"use client";', "export default function HugePage() {"]
            monolith_lines.extend(["  // filler line"] * 410)
            monolith_lines.extend(["  return <div>Huge</div>;", "}"])
            write_text(repo / "src" / "page.tsx", "\n".join(monolith_lines) + "\n")

            # 5. Large wildcard barrel re-export file
            write_text(
                repo / "src" / "components" / "index.ts",
                'export * from "./Button";\n'
                'export * from "./Modal";\n'
                'export * from "./Table";\n',
            )

            proc = run_python_script(UI_SLOP_SCRIPT, ["--repo", str(repo), "--json"])
            self.assertEqual(proc.returncode, 1, msg=proc.stdout + proc.stderr)
            data = json.loads(proc.stdout)
            self.assertEqual(data["status"], "findings")

            findings = data["findings"]
            by_code: dict[str, list[dict[str, object]]] = {}
            for item in findings:
                by_code.setdefault(str(item["code"]), []).append(item)

            # Scratch screenshots: .taste-skill, screenshots, stray-root.png (NOT ignored-capture.png)
            scratch_files = {f["file"] for f in by_code.get("REPO_SCRATCH_SCREENSHOTS", [])}
            self.assertEqual(
                scratch_files, {".taste-skill", "screenshots", "stray-root.png"}
            )

            # Heading skip exact line (line 5 in src/App.tsx)
            heading_hits = by_code.get("HEADING_SKIP", [])
            self.assertEqual(len(heading_hits), 1)
            self.assertEqual(heading_hits[0]["file"], "src/App.tsx")
            self.assertEqual(heading_hits[0]["line"], 5)
            self.assertEqual(heading_hits[0]["severity"], "MED")

            # Icon-only buttons (lines 7 and 10; line 11 is labeled and excluded)
            icon_hits = by_code.get("ICON_BUTTON_NO_LABEL", [])
            self.assertEqual(len(icon_hits), 2)
            self.assertEqual([h["line"] for h in icon_hits], [7, 10])
            self.assertTrue(all(h["severity"] == "HIGH" for h in icon_hits))

            # Purple gradients (src/App.tsx:3 and src/app.css:3)
            grad_hits = by_code.get("SLOP_PURPLE_GRADIENT", [])
            self.assertEqual(
                {(h["file"], h["line"]) for h in grad_hits},
                {("src/App.tsx", 3), ("src/app.css", 3)},
            )

            # Unconfigured default font (src/app.css:2)
            font_hits = by_code.get("UNCONFIGURED_DEFAULT_FONT", [])
            self.assertEqual(len(font_hits), 1)
            self.assertEqual(font_hits[0]["file"], "src/app.css")
            self.assertEqual(font_hits[0]["line"], 2)
            self.assertEqual(font_hits[0]["severity"], "LOW")

            # 'use client' monolith (src/page.tsx:1)
            monolith_hits = by_code.get("CLIENT_MONOLITH", [])
            self.assertEqual(len(monolith_hits), 1)
            self.assertEqual(monolith_hits[0]["file"], "src/page.tsx")
            self.assertEqual(monolith_hits[0]["line"], 1)
            self.assertEqual(monolith_hits[0]["severity"], "HIGH")

            # Large barrel re-export (src/components/index.ts:1)
            barrel_hits = by_code.get("LARGE_BARREL_REEXPORT", [])
            self.assertEqual(len(barrel_hits), 1)
            self.assertEqual(barrel_hits[0]["file"], "src/components/index.ts")
            self.assertEqual(barrel_hits[0]["line"], 1)
            self.assertEqual(barrel_hits[0]["severity"], "MED")

    def test_text_output_formats_file_and_line(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            write_text(repo / "index.html", "<h1>A</h1>\n<h4>B</h4>\n")
            proc = run_python_script(UI_SLOP_SCRIPT, ["--repo", str(repo)])
            self.assertEqual(proc.returncode, 1)
            self.assertIn("UI Anti-Slop Check: FINDINGS", proc.stdout)
            self.assertIn("[MED] index.html:2 (HEADING_SKIP)", proc.stdout)

    def test_nonexistent_repo_returns_exit_two(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            missing = Path(tmp) / "no_such_repo"
            proc = run_python_script(UI_SLOP_SCRIPT, ["--repo", str(missing)])
            self.assertEqual(proc.returncode, 2)


class TestSkillMetadataAndEvals(unittest.TestCase):
    """Sanity checks on SKILL.md, references, and evals.json contracts."""

    def test_word_budgets_and_links(self) -> None:
        skill_text = SKILL_MD.read_text(encoding="utf-8")
        ref_files = sorted((SKILL_DIR / "references").glob("*.md"))
        self.assertEqual(len(ref_files), 9)

        self.assertLessEqual(len(skill_text.split()), 950)
        for ref_file in ref_files:
            ref_text = ref_file.read_text(encoding="utf-8")
            self.assertLessEqual(
                len(ref_text.split()),
                880,
                msg=f"{ref_file.name} exceeds 880 words",
            )
        self.assertIn("Do NOT use", skill_text)

        for doc in (SKILL_MD, *ref_files):
            content = doc.read_text(encoding="utf-8")
            for target in re.findall(r"\[[^\]]+\]\(([^)]+)\)", content):
                if target.startswith(("http://", "https://", "#")):
                    continue
                resolved = (doc.parent / target).resolve()
                self.assertTrue(
                    resolved.is_file(),
                    msg=f"Broken relative link {target!r} in {doc}",
                )

    def test_evals_json_contract_and_splits(self) -> None:
        data = json.loads(EVALS_FILE.read_text(encoding="utf-8"))
        self.assertEqual(data["skill"], "braces")
        triggers = data["trigger_tests"]
        self.assertGreaterEqual(len(triggers), 10)
        positives = [t for t in triggers if t["should_trigger"] is True]
        negatives = [t for t in triggers if t["should_trigger"] is False]
        self.assertGreaterEqual(len(positives), 4)
        self.assertGreaterEqual(len(negatives), 4)
        splits = {t["split"] for t in triggers}
        self.assertEqual(splits, {"train", "holdout"})

        forward = data["forward_tests"]
        self.assertGreaterEqual(len(forward), 2)
        for item in forward:
            self.assertTrue(item["id"])
            self.assertTrue(item["prompt"])
            self.assertGreaterEqual(len(item["invariants"]), 2)


if __name__ == "__main__":
    unittest.main()
