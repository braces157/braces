#!/usr/bin/env python3
"""Verify Windows PATH binary resolution, shadowing, and SHA-256 build parity."""

from __future__ import annotations

import argparse
import csv
import hashlib
import io
import json
import os
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, NamedTuple, Sequence

DEFAULT_WINDOWS_PATHEXT = (".COM", ".EXE", ".BAT", ".CMD")
HASH_CHUNK_BYTES = 65536


class CmdResult(NamedTuple):
    """Result from running an external subprocess without a shell."""

    returncode: int
    stdout: str
    stderr: str


def configure_stdio() -> None:
    """Force UTF-8 output streams with replacement to prevent Windows cp1252 crashes."""
    for stream in (sys.stdout, sys.stderr):
        reconf = getattr(stream, "reconfigure", None)
        if callable(reconf):
            reconf(encoding="utf-8", errors="replace")


def run_cmd(cmd: Sequence[str], timeout: float = 15.0) -> CmdResult:
    """Execute a command safely with shell=False and handle missing executables."""
    try:
        completed = subprocess.run(
            list(cmd),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            shell=False,
            timeout=timeout,
            check=False,
        )
        return CmdResult(completed.returncode, completed.stdout, completed.stderr)
    except OSError as exc:
        return CmdResult(127, "", str(exc))
    except subprocess.TimeoutExpired as exc:
        out = exc.stdout if isinstance(exc.stdout, str) else ""
        err = exc.stderr if isinstance(exc.stderr, str) else "command timed out"
        return CmdResult(124, out, err)


def norm_path_key(path: Path) -> str:
    """Return a canonical, case-normalized key for file deduplication."""
    return os.path.normcase(os.path.realpath(str(path)))


def compute_sha256(path: Path) -> str:
    """Stream-compute the lowercase SHA-256 hex digest of a file."""
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while True:
            chunk = handle.read(HASH_CHUNK_BYTES)
            if not chunk:
                break
            digest.update(chunk)
    return digest.hexdigest()


def file_metadata(path: Path) -> dict[str, Any]:
    """Collect canonical path, size, UTC mtime, and SHA-256 digest for a file."""
    stat = path.stat()
    resolved = Path(os.path.realpath(str(path)))
    mtime = datetime.fromtimestamp(stat.st_mtime, tz=timezone.utc).strftime(
        "%Y-%m-%dT%H:%M:%SZ"
    )
    return {
        "path": str(path),
        "resolved_path": str(resolved),
        "sha256": compute_sha256(resolved),
        "size_bytes": stat.st_size,
        "mtime_utc": mtime,
    }


def parse_pathext(pathext_raw: str | None) -> list[str]:
    """Parse PATHEXT into normalized uppercase extensions with leading dots."""
    raw = pathext_raw if pathext_raw is not None else os.environ.get("PATHEXT", "")
    exts: list[str] = []
    seen: set[str] = set()
    for part in raw.split(";"):
        cleaned = part.strip()
        if not cleaned:
            continue
        if not cleaned.startswith("."):
            cleaned = "." + cleaned
        upper = cleaned.upper()
        if upper not in seen:
            seen.add(upper)
            exts.append(upper)
    if not exts:
        return list(DEFAULT_WINDOWS_PATHEXT)
    return exts


def split_path_entries(path_env: str, pathsep: str | None = None) -> list[str]:
    """Split a PATH string into cleaned directory entries, stripping outer quotes."""
    sep = pathsep if pathsep is not None else os.pathsep
    entries: list[str] = []
    for raw in path_env.split(sep):
        cleaned = raw.strip()
        if len(cleaned) >= 2 and cleaned[0] == '"' and cleaned[-1] == '"':
            cleaned = cleaned[1:-1].strip()
        if cleaned:
            entries.append(cleaned)
    return entries


def candidate_filenames(name: str, is_windows: bool, pathext: Sequence[str]) -> list[str]:
    """Build ordered candidate filenames to probe inside each PATH directory."""
    if not is_windows:
        return [name]

    suffix_upper = Path(name).suffix.upper()
    if suffix_upper and suffix_upper in {ext.upper() for ext in pathext}:
        return [name]

    candidates: list[str] = []
    for ext in pathext:
        candidates.append(f"{name}{ext.lower()}")
    if suffix_upper:
        candidates.insert(0, name)
    return candidates


def is_runnable_file(path: Path, is_windows: bool) -> bool:
    """Check whether a candidate path is an existing regular file (executable on POSIX)."""
    try:
        if not path.is_file():
            return False
    except OSError:
        return False
    if is_windows:
        return True
    return os.access(str(path), os.X_OK)


def find_path_matches(
    name: str,
    path_env: str,
    *,
    is_windows: bool | None = None,
    pathext_raw: str | None = None,
    pathsep: str | None = None,
) -> list[dict[str, Any]]:
    """Walk every PATH entry in order and return deduplicated executable matches."""
    win = (os.name == "nt") if is_windows is None else is_windows
    exts = parse_pathext(pathext_raw) if win else []
    filenames = candidate_filenames(name, win, exts)
    entries = split_path_entries(path_env, pathsep=pathsep)

    matches: list[dict[str, Any]] = []
    by_key: dict[str, dict[str, Any]] = {}

    for dir_str in entries:
        directory = Path(dir_str)
        try:
            if not directory.is_dir():
                continue
        except OSError:
            continue

        for filename in filenames:
            candidate = directory / filename
            if not is_runnable_file(candidate, win):
                continue

            key = norm_path_key(candidate)
            if key in by_key:
                existing = by_key[key]
                cand_str = str(candidate)
                if cand_str != existing["path"] and cand_str not in existing["also_reachable_via"]:
                    existing["also_reachable_via"].append(cand_str)
                continue

            meta = file_metadata(candidate)
            meta["rank"] = len(matches) + 1
            meta["also_reachable_via"] = []
            meta["matches_built"] = None
            matches.append(meta)
            by_key[key] = meta

    return matches


def inspect_running_processes(name: str, is_windows: bool | None = None) -> dict[str, Any]:
    """Perform non-destructive inspection of running processes matching name."""
    win = (os.name == "nt") if is_windows is None else is_windows
    stem = Path(name).stem if Path(name).suffix else name
    if not win:
        result = run_cmd(["ps", "-eo", "pid=,comm="])
        if result.returncode != 0:
            return {
                "checked": True,
                "supported": False,
                "running": [],
                "error": result.stderr.strip() or f"ps exited with {result.returncode}",
            }
        found: list[dict[str, Any]] = []
        for line in result.stdout.splitlines():
            parts = line.strip().split(None, 1)
            if len(parts) == 2 and parts[0].isdigit():
                if parts[1].strip().lower() in {name.lower(), stem.lower()}:
                    found.append({"pid": int(parts[0]), "image": parts[1].strip()})
        return {"checked": True, "supported": True, "running": found}

    image_name = name if name.lower().endswith(".exe") else f"{stem}.exe"
    result = run_cmd(
        ["tasklist", "/FO", "CSV", "/NH", "/FI", f"IMAGENAME eq {image_name}"]
    )
    if result.returncode != 0:
        return {
            "checked": True,
            "supported": False,
            "running": [],
            "error": result.stderr.strip() or f"tasklist exited with {result.returncode}",
        }

    running: list[dict[str, Any]] = []
    reader = csv.reader(io.StringIO(result.stdout))
    for row in reader:
        if len(row) < 2:
            continue
        img = row[0].strip()
        pid_str = row[1].strip()
        if img.lower() == image_name.lower() and pid_str.isdigit():
            entry: dict[str, Any] = {"pid": int(pid_str), "image": img}
            if len(row) >= 5:
                entry["mem_usage"] = row[4].strip()
            running.append(entry)
    return {"checked": True, "supported": True, "running": running}


def validate_name(name: str) -> str:
    """Validate executable name has no path separators or control characters."""
    cleaned = name.strip()
    if not cleaned:
        raise ValueError("--name must not be empty.")
    if any(sep in cleaned for sep in ("/", "\\", os.sep)) or ":" in cleaned:
        raise ValueError(
            f"--name must be a bare executable name without path separators: {name!r}"
        )
    return cleaned


def verify_binary(
    name: str,
    *,
    built: Path | None = None,
    path_env: str | None = None,
    pathext_raw: str | None = None,
    check_processes: bool = False,
    is_windows: bool | None = None,
) -> tuple[dict[str, Any], int]:
    """Run full PATH, shadowing, and SHA-256 parity verification."""
    win = (os.name == "nt") if is_windows is None else is_windows
    effective_path = path_env if path_env is not None else os.environ.get("PATH", "")

    built_meta: dict[str, Any] | None = None
    if built is not None:
        if not built.exists():
            raise FileNotFoundError(f"--built artifact does not exist: {built}")
        if not built.is_file():
            raise ValueError(f"--built path is not a regular file: {built}")
        built_meta = file_metadata(built)

    matches = find_path_matches(
        name,
        effective_path,
        is_windows=win,
        pathext_raw=pathext_raw,
    )

    if built_meta is not None:
        for item in matches:
            item["matches_built"] = item["sha256"] == built_meta["sha256"]

    issues: list[dict[str, str]] = []
    warnings: list[str] = []

    active = matches[0] if matches else None
    shadowed = len(matches) > 1

    if active is None:
        issues.append(
            {
                "code": "MISSING_ON_PATH",
                "severity": "HIGH",
                "message": f"Executable '{name}' was not found on PATH.",
            }
        )

    if shadowed:
        shadow_paths = ", ".join(item["path"] for item in matches[1:])
        issues.append(
            {
                "code": "PATH_SHADOWING",
                "severity": "HIGH",
                "message": (
                    f"Found {len(matches)} distinct '{name}' executables on PATH; "
                    f"active '{active['path']}' shadows: {shadow_paths}"
                ),
            }
        )

    parity: dict[str, Any] = {
        "checked": built_meta is not None,
        "hash_match": None,
        "same_file": None,
    }
    if built_meta is not None and active is not None:
        hash_match = active["sha256"] == built_meta["sha256"]
        same_file = norm_path_key(Path(active["path"])) == norm_path_key(
            Path(built_meta["path"])
        )
        parity["hash_match"] = hash_match
        parity["same_file"] = same_file
        if not hash_match:
            issues.append(
                {
                    "code": "SHA256_MISMATCH",
                    "severity": "HIGH",
                    "message": (
                        f"Active PATH binary ({active['sha256'][:12]}...) does not match "
                        f"built artifact ({built_meta['sha256'][:12]}...)."
                    ),
                }
            )
    elif built_meta is not None and active is None:
        parity["hash_match"] = False
        parity["same_file"] = False

    if check_processes:
        proc_info = inspect_running_processes(name, is_windows=win)
        if proc_info.get("running"):
            pids = ", ".join(str(p["pid"]) for p in proc_info["running"])
            warnings.append(
                f"Running process(es) detected for '{name}' (PID: {pids}). "
                "On Windows, active processes lock executables and may still run pre-upgrade code."
            )
    else:
        proc_info = {"checked": False, "supported": True, "running": []}

    verified = len(issues) == 0
    payload: dict[str, Any] = {
        "schema_version": 1,
        "status": "ok" if verified else "fail",
        "name": name,
        "platform": "nt" if win else os.name,
        "verified": verified,
        "shadowed": shadowed,
        "active": active,
        "built": built_meta,
        "parity": parity,
        "path_matches": matches,
        "issues": issues,
        "warnings": warnings,
        "processes": proc_info,
    }
    return payload, (0 if verified else 1)


def render_text(payload: dict[str, Any]) -> str:
    """Render a concise human-readable verification table."""
    lines: list[str] = []
    verdict = "PASS" if payload["verified"] else "FAIL"
    lines.append(f"Binary Verification: {verdict} ({payload['name']})")

    active = payload["active"]
    if active:
        lines.append(
            f"  Active PATH : {active['path']} [sha256={active['sha256'][:16]}...]"
        )
    else:
        lines.append("  Active PATH : <not found>")

    built = payload["built"]
    if built:
        parity_str = "MATCH" if payload["parity"]["hash_match"] else "MISMATCH"
        lines.append(
            f"  Built File  : {built['path']} [sha256={built['sha256'][:16]}...] ({parity_str})"
        )

    matches = payload["path_matches"]
    if matches:
        lines.append("")
        lines.append("PATH Matches:")
        lines.append("  Rank | SHA-256 (12) | Size (B) | Parity   | Path")
        lines.append("  -----+--------------+----------+----------+------------------------------")
        for item in matches:
            mb = item["matches_built"]
            parity_col = "n/a" if mb is None else ("MATCH" if mb else "MISMATCH")
            lines.append(
                f"  {item['rank']:>4} | {item['sha256'][:12]} | "
                f"{item['size_bytes']:>8} | {parity_col:<8} | {item['path']}"
            )
            for alias in item.get("also_reachable_via", []):
                lines.append(f"       | (same file via PATH alias: {alias})")

    if payload["issues"]:
        lines.append("")
        lines.append("Issues:")
        for issue in payload["issues"]:
            lines.append(f"  [{issue['severity']}] {issue['code']}: {issue['message']}")

    if payload["warnings"]:
        lines.append("")
        lines.append("Warnings:")
        for warning in payload["warnings"]:
            lines.append(f"  [WARN] {warning}")

    procs = payload["processes"]
    if procs.get("checked"):
        lines.append("")
        if not procs.get("supported", True):
            lines.append(f"Processes: check unavailable ({procs.get('error', 'unsupported')})")
        elif procs["running"]:
            summary = ", ".join(f"{p['image']} (PID {p['pid']})" for p in procs["running"])
            lines.append(f"Processes: {summary}")
        else:
            lines.append("Processes: none running")

    return "\n".join(lines)


def build_parser() -> argparse.ArgumentParser:
    """Create CLI argument parser."""
    parser = argparse.ArgumentParser(
        description=(
            "Verify executable resolution on PATH, flag PATH shadowing, and check "
            "SHA-256 parity against a freshly built binary."
        )
    )
    parser.add_argument(
        "--name",
        required=True,
        help="Bare executable name to locate on PATH (e.g. tuitify or discodex).",
    )
    parser.add_argument(
        "--built",
        type=Path,
        default=None,
        help="Optional path to freshly built artifact to compare SHA-256 against active PATH binary.",
    )
    parser.add_argument(
        "--path-env",
        default=None,
        help="Optional PATH string override (useful for isolated verification and tests).",
    )
    parser.add_argument(
        "--check-processes",
        action="store_true",
        help="Non-destructively inspect running processes matching --name.",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        dest="as_json",
        help="Emit structured JSON output.",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """CLI entry point. Exit codes: 0=verified, 1=mismatch/shadow/missing, 2=CLI/arg error."""
    configure_stdio()
    parser = build_parser()
    try:
        args = parser.parse_args(argv)
    except SystemExit as exc:
        code = exc.code if isinstance(exc.code, int) else 2
        return 0 if code == 0 else 2

    try:
        clean_name = validate_name(args.name)
        payload, exit_code = verify_binary(
            clean_name,
            built=args.built,
            path_env=args.path_env,
            check_processes=args.check_processes,
        )
    except (ValueError, FileNotFoundError, OSError) as exc:
        if args.as_json:
            err_payload = {
                "schema_version": 1,
                "status": "error",
                "error": str(exc),
            }
            print(json.dumps(err_payload, indent=2, ensure_ascii=False))
        print(f"error: {exc}", file=sys.stderr)
        return 2

    if args.as_json:
        print(json.dumps(payload, indent=2, ensure_ascii=False))
    else:
        print(render_text(payload))
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
