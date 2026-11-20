#!/usr/bin/env python3
"""Unit and CLI integration tests for skills/braces/scripts/hotspot_scan.py."""

from __future__ import annotations

import importlib.util
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from types import ModuleType

REPO_ROOT = Path(__file__).resolve().parent.parent
SCRIPT_PATH = (
    REPO_ROOT / "skills" / "braces" / "scripts" / "hotspot_scan.py"
)


def _load_hotspot_module() -> ModuleType:
    prev = sys.dont_write_bytecode
    sys.dont_write_bytecode = True
    try:
        spec = importlib.util.spec_from_file_location("hotspot_scan", SCRIPT_PATH)
        assert spec is not None and spec.loader is not None
        module = importlib.util.module_from_spec(spec)
        sys.modules["hotspot_scan"] = module
        spec.loader.exec_module(module)
        return module
    finally:
        sys.dont_write_bytecode = prev


hotspot_scan = _load_hotspot_module()


def _run_cli(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-B", str(SCRIPT_PATH), *args],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        shell=False,
    )


def _git(cwd: Path, *args: str) -> None:
    res = subprocess.run(
        ["git", *args],
        cwd=str(cwd),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        shell=False,
    )
    if res.returncode != 0:
        raise AssertionError(
            f"git {' '.join(args)} failed ({res.returncode}): {res.stderr}"
        )


def _is_subpath(child: Path, parent: Path) -> bool:
    c = os.path.normcase(os.path.realpath(str(child)))
    p = os.path.normcase(os.path.realpath(str(parent)))
    if c == p:
        return True
    if not p.endswith(os.sep):
        p += os.sep
    return c.startswith(p)


class RustCfgTestSplitTests(unittest.TestCase):
    """Verify Rust inline #[cfg(test)] prod-vs-test line splitting accuracy."""

    def test_splits_complex_cfg_test_blocks_accurately(self) -> None:
        source = "\n".join(
            [
                "use std::sync::Mutex;",
                "pub fn prod_before<'a>(s: &'a str) -> usize {",
                "    // #[cfg(test)] in comment must not trigger test mode",
                '    let fake = "#[cfg(test)]";',
                "    s.len() + fake.len()",
                "}",
                "",
                "#[cfg(test)]",
                "mod external_tests;",
                "",
                "pub fn prod_middle() -> i32 {",
                "    42",
                "}",
                "",
                "#[cfg(all(test, not(miri)))]",
                "#[allow(clippy::unwrap_used)]",
                "mod tests {",
                "    use super::*;",
                "    #[test]",
                "    fn tricky_literals() {",
                '        let braces = "}}} {{{";',
                '        let raw = r#"',
                "            } } } still inside raw string",
                '        "#;',
                "        let ch_close = '}';",
                "        /* nested /* comment } */ still comment } */",
                "        let m = Mutex::new(1);",
                "        let _g = m.lock().unwrap();",
                "        unsafe { let _ = 1; }",
                "        assert_eq!(ch_close, '}');",
                "    }",
                "}",
                "",
                "pub fn prod_after() -> bool {",
                "    true",
                "}",
            ]
        )
        lines = source.splitlines()
        prod_lines, test_lines = hotspot_scan.split_rust_prod_test_lines(lines)

        self.assertEqual(len(prod_lines) + len(test_lines), len(lines))
        prod_text = "\n".join(text for _, text in prod_lines)
        test_text = "\n".join(text for _, text in test_lines)

        self.assertIn("pub fn prod_before", prod_text)
        self.assertIn("pub fn prod_middle", prod_text)
        self.assertIn("pub fn prod_after", prod_text)
        self.assertNotIn("fn tricky_literals", prod_text)
        self.assertNotIn("mod external_tests;", prod_text)

        self.assertIn("mod external_tests;", test_text)
        self.assertIn("fn tricky_literals", test_text)
        self.assertIn("m.lock().unwrap()", test_text)
        self.assertNotIn("pub fn prod_after", test_text)

        # Smells inside #[cfg(test)] must NOT be counted as prod smells.
        prod_smells = hotspot_scan.detect_smells("rust", prod_lines)
        self.assertEqual(prod_smells, {})

    def test_heavy_inline_tests_not_misclassified_as_prod_monolith(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            src_dir = root / "src"
            src_dir.mkdir()

            prod_block = [f"pub fn f_{i}() -> usize {{ {i} }}" for i in range(40)]
            test_block = [
                "#[cfg(test)]",
                "mod tests {",
                *[f"    #[test] fn t_{i}() {{ assert_eq!({i}, {i}); }}" for i in range(1100)],
                "}",
            ]
            (src_dir / "codec.rs").write_text(
                "\n".join(prod_block + test_block) + "\n", encoding="utf-8"
            )

            proc = _run_cli("--repo", str(root), "--json")
            self.assertEqual(proc.returncode, 0, msg=proc.stderr or proc.stdout)
            data = json.loads(proc.stdout)
            self.assertEqual(data["summary"]["hard_limit_violations"], 0)
            self.assertEqual(data["summary"]["soft_limit_warnings"], 0)
            self.assertEqual(len(data["hotspots"]), 1)
            entry = data["hotspots"][0]
            self.assertEqual(entry["path"], "src/codec.rs")
            self.assertEqual(entry["prod_loc"], 40)
            self.assertEqual(entry["test_loc"], 1103)
            self.assertEqual(entry["status"], "ok")


class PolyglotSmellTests(unittest.TestCase):
    """Verify smell detection across Rust, TypeScript, Python, and Java."""

    def test_detects_polyglot_smells_and_ignores_comments(self) -> None:
        rs_lines = list(
            enumerate(
                [
                    "// Avoid .lock().unwrap() and unsafe { in prod",
                    "let guard = state.lock().unwrap();",
                    "let r = rw.read().unwrap();",
                    "unsafe { ptr.read() }",
                ],
                start=1,
            )
        )
        self.assertEqual(
            hotspot_scan.detect_smells("rust", rs_lines),
            {"rust_lock_unwrap": 2, "rust_unsafe": 1},
        )

        ts_lines = list(
            enumerate(
                [
                    "// Do not write : any here",
                    "// @ts-ignore",
                    "export function parse(input: any): number {",
                    "  return (input as any).count;",
                    "}",
                ],
                start=1,
            )
        )
        self.assertEqual(
            hotspot_scan.detect_smells("typescript", ts_lines),
            {"ts_any": 2, "ts_ignore": 1},
        )

        py_lines = list(
            enumerate(
                [
                    "# except Exception: comment ignored",
                    "try:",
                    "    run()",
                    "except:",
                    "    pass",
                    "try:",
                    "    run_more()",
                    "except Exception as exc:",
                    "    log(exc)",
                ],
                start=1,
            )
        )
        self.assertEqual(
            hotspot_scan.detect_smells("python", py_lines),
            {"py_bare_except": 1, "py_broad_except": 1},
        )

        java_lines = list(
            enumerate(
                [
                    "// catch (Exception e) in comment ignored",
                    "try { work(); } catch (IOException e) { throw e; }",
                    "try { work(); } catch (Exception e) { return null; }",
                    "try { work(); } catch (final java.lang.Exception ex) {}",
                ],
                start=1,
            )
        )
        self.assertEqual(
            hotspot_scan.detect_smells("java", java_lines),
            {"java_broad_catch": 2},
        )


class CliIntegrationTests(unittest.TestCase):
    """End-to-end CLI tests covering JSON schema, git churn, exit codes, and HTML reports."""

    def test_git_repo_churn_hard_limit_and_json_schema(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            _git(repo, "init")
            _git(repo, "config", "user.email", "dev@example.com")
            _git(repo, "config", "user.name", "Dev")

            (repo / ".gitignore").write_text("target/\nnode_modules/\n", encoding="utf-8")
            (repo / "target").mkdir()
            (repo / "target" / "generated.rs").write_text(
                "\n".join(["fn gen() {}"] * 2000) + "\n", encoding="utf-8"
            )

            src = repo / "src"
            src.mkdir()
            big_py = src / "pipeline.py"
            big_py.write_text(
                "\n".join([f"x_{i} = {i}" for i in range(60)])
                + "\ntry:\n    pass\nexcept Exception:\n    pass\n",
                encoding="utf-8",
            )
            small_ts = src / "clean.ts"
            small_ts.write_text("export const ok: number = 1;\n", encoding="utf-8")

            _git(repo, "add", ".gitignore", "src/pipeline.py", "src/clean.ts")
            _git(repo, "commit", "-m", "initial commit")

            # Add second commit touching pipeline.py to verify churn == 2.
            with big_py.open("a", encoding="utf-8") as fh:
                fh.write("x_extra = 999\n")
            _git(repo, "add", "src/pipeline.py")
            _git(repo, "commit", "-m", "second edit to pipeline")

            proc = _run_cli(
                "--repo",
                str(repo),
                "--soft-limit",
                "30",
                "--hard-limit",
                "50",
                "--top",
                "10",
                "--json",
            )
            self.assertEqual(proc.returncode, 1, msg=proc.stderr)
            payload = json.loads(proc.stdout)

            # Verify JSON contract
            for key in (
                "schema_version",
                "repo_root",
                "scan_root",
                "is_git_repo",
                "limits",
                "summary",
                "hotspots",
                "html_report_path",
            ):
                self.assertIn(key, payload)

            self.assertTrue(payload["is_git_repo"])
            self.assertIsNone(payload["html_report_path"])
            self.assertEqual(payload["summary"]["files_scanned"], 2)
            self.assertEqual(payload["summary"]["hard_limit_violations"], 1)
            self.assertEqual(payload["summary"]["total_high_risk_smells"], 1)

            top = payload["hotspots"][0]
            self.assertEqual(top["path"], "src/pipeline.py")
            self.assertEqual(top["status"], "hard")
            self.assertEqual(top["churn_commits"], 2)
            self.assertEqual(top["smells"], {"py_broad_except": 1})

            # Ensure ignored target/generated.rs was not scanned.
            scanned_paths = {h["path"] for h in payload["hotspots"]}
            self.assertNotIn("target/generated.rs", scanned_paths)

    def test_exit_codes_zero_one_two(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            # Clean repo -> exit 0
            clean_dir = root / "clean_repo"
            clean_dir.mkdir()
            (clean_dir / "app.ts").write_text(
                "export function add(a: number, b: number): number { return a + b; }\n",
                encoding="utf-8",
            )
            res_clean = _run_cli("--repo", str(clean_dir), "--json")
            self.assertEqual(res_clean.returncode, 0, msg=res_clean.stderr)
            self.assertEqual(json.loads(res_clean.stdout)["summary"]["exit_reason"], "clean")

            # Small file with high-risk smell only -> exit 1
            smell_dir = root / "smell_repo"
            smell_dir.mkdir()
            (smell_dir / "dash.rs").write_text(
                "pub fn read(m: &std::sync::Mutex<u32>) -> u32 { *m.lock().unwrap() }\n",
                encoding="utf-8",
            )
            res_smell = _run_cli("--repo", str(smell_dir), "--json")
            self.assertEqual(res_smell.returncode, 1, msg=res_smell.stderr)
            smell_json = json.loads(res_smell.stdout)
            self.assertEqual(smell_json["summary"]["hard_limit_violations"], 0)
            self.assertEqual(smell_json["summary"]["total_high_risk_smells"], 1)

            # Non-existent path -> exit 2 on stderr
            res_missing = _run_cli("--repo", str(root / "does-not-exist"))
            self.assertEqual(res_missing.returncode, 2)
            self.assertIn("ERROR:", res_missing.stderr)

            # File path instead of directory -> exit 2
            res_not_dir = _run_cli("--repo", str(clean_dir / "app.ts"))
            self.assertEqual(res_not_dir.returncode, 2)
            self.assertIn("ERROR:", res_not_dir.stderr)

            # Invalid limits -> exit 2
            res_bad_limits = _run_cli(
                "--repo", str(clean_dir), "--soft-limit", "500", "--hard-limit", "300"
            )
            self.assertEqual(res_bad_limits.returncode, 2)
            self.assertIn("ERROR:", res_bad_limits.stderr)

    def test_html_report_written_strictly_into_os_temp_outside_repo(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp) / "sample-project"
            repo.mkdir()
            (repo / "page.tsx").write_text(
                "\n".join([f"const row_{i} = {i};" for i in range(45)])
                + "\n// @ts-ignore\nexport default function Page() { return null; }\n",
                encoding="utf-8",
            )

            before_files = sorted(p.relative_to(repo).as_posix() for p in repo.rglob("*"))
            proc = _run_cli(
                "--repo",
                str(repo),
                "--soft-limit",
                "20",
                "--hard-limit",
                "40",
                "--html-report",
                "--json",
            )
            self.assertEqual(proc.returncode, 1, msg=proc.stderr)
            data = json.loads(proc.stdout)

            report_path_str = data["html_report_path"]
            self.assertIsNotNone(report_path_str)
            report_path = Path(report_path_str)
            self.addCleanup(lambda: report_path.unlink(missing_ok=True))

            self.assertTrue(report_path.is_file())
            self.assertTrue(
                _is_subpath(report_path, Path(tempfile.gettempdir())),
                msg=f"Report {report_path} must be inside OS temp {tempfile.gettempdir()}",
            )
            self.assertFalse(
                _is_subpath(report_path, repo),
                msg=f"Report {report_path} must NEVER be inside scanned repo {repo}",
            )

            # Confirm target repo remained completely untouched.
            after_files = sorted(p.relative_to(repo).as_posix() for p in repo.rglob("*"))
            self.assertEqual(before_files, after_files)

            html_text = report_path.read_text(encoding="utf-8")
            self.assertIn("<!DOCTYPE html>", html_text)
            self.assertIn('<pre class="mermaid">', html_text)
            self.assertIn("flowchart TD", html_text)
            self.assertIn("page.tsx", html_text)

    def test_standalone_test_files_excluded_from_prod_loc_violations(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            tests_dir = repo / "tests"
            tests_dir.mkdir()
            (tests_dir / "test_massive.py").write_text(
                "\n".join([f"def test_{i}(): assert {i} == {i}" for i in range(1200)]) + "\n",
                encoding="utf-8",
            )
            (repo / "widget.spec.ts").write_text(
                "\n".join([f"const c_{i}: any = {i};" for i in range(1100)]) + "\n",
                encoding="utf-8",
            )
            proc = _run_cli("--repo", str(repo), "--json")
            self.assertEqual(proc.returncode, 0, msg=proc.stderr)
            data = json.loads(proc.stdout)
            self.assertEqual(data["summary"]["hard_limit_violations"], 0)
            self.assertEqual(data["summary"]["total_prod_loc"], 0)
            self.assertEqual(data["summary"]["total_test_loc"], 2300)
            self.assertEqual(data["summary"]["test_files"], 2)

    def test_non_git_fallback_top_truncation_and_text_output(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            for idx in range(5):
                (repo / f"mod_{idx}.py").write_text(
                    "\n".join([f"v = {n}" for n in range((idx + 1) * 10)]) + "\n",
                    encoding="utf-8",
                )
            proc_json = _run_cli("--repo", str(repo), "--top", "2", "--json")
            self.assertEqual(proc_json.returncode, 0)
            payload = json.loads(proc_json.stdout)
            self.assertFalse(payload["is_git_repo"])
            self.assertEqual(payload["summary"]["files_scanned"], 5)
            self.assertEqual(len(payload["hotspots"]), 2)
            self.assertEqual(payload["hotspots"][0]["path"], "mod_4.py")

            proc_txt = _run_cli("--repo", str(repo), "--top", "2")
            self.assertEqual(proc_txt.returncode, 0)
            self.assertIn("STATUS", proc_txt.stdout)
            self.assertIn("mod_4.py", proc_txt.stdout)

    def test_run_cmd_oserror_returns_127(self) -> None:
        res = hotspot_scan.run_cmd(["__nonexistent_binary_arch_refactor_test__"])
        self.assertEqual(res.returncode, 127)
        self.assertTrue(len(res.stderr) > 0)


if __name__ == "__main__":
    unittest.main()
