#!/usr/bin/env python3
"""Idempotent cross-agent skill sync CLI with pre-copy structural validation."""

from __future__ import annotations

import argparse
import importlib.util
import json
import os
import shutil
import sys
from pathlib import Path
from types import ModuleType
from typing import Any

sys.dont_write_bytecode = True

REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_SOURCE = REPO_ROOT / "skills"
VALIDATOR_SCRIPT = REPO_ROOT / "skills" / "braces" / "scripts" / "validate_skill.py"

SKIP_DIR_NAMES = frozenset({"__pycache__", ".git", ".pytest_cache"})
SKIP_FILE_NAMES = frozenset({".DS_Store", "Thumbs.db", "README.md"})
SKIP_FILE_SUFFIXES = frozenset({".pyc", ".pyo"})


def configure_stdio() -> None:
    """Force UTF-8 stdout/stderr on Windows to avoid cp1252 encode failures."""
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if callable(reconfigure):
            try:
                reconfigure(encoding="utf-8", errors="replace")
            except OSError:
                pass


def resolve_targets(target: str | None, dest: str | None) -> list[tuple[str, Path]]:
    """Resolve target names and/or custom destination path into (label, Path) pairs."""
    home = Path.home()
    target_map: dict[str, Path] = {
        "antigravity": home / ".gemini" / "config" / "skills",
        "claude": home / ".claude" / "skills",
        "codex": home / ".codex" / "skills",
        "agents": home / ".agents" / "skills",
    }

    resolved: list[tuple[str, Path]] = []
    if target == "all":
        for name in ("antigravity", "claude", "codex", "agents"):
            resolved.append((name, target_map[name]))
    elif target is not None:
        resolved.append((target, target_map[target]))

    if dest is not None:
        resolved.append(("custom", Path(dest).expanduser().resolve()))

    return resolved


def load_validator(validator_path: Path = VALIDATOR_SCRIPT) -> ModuleType:
    """Load validate_skill.py dynamically without emitting __pycache__ bytecode."""
    sys.dont_write_bytecode = True
    if not validator_path.is_file():
        raise FileNotFoundError(f"Validator script not found: {validator_path}")
    spec = importlib.util.spec_from_file_location("_braces_validate_skill", str(validator_path))
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Unable to load validator module from {validator_path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def discover_source_skills(source_dir: Path, requested: list[str] | None) -> tuple[list[Path], list[str]]:
    """Discover skill directories under source_dir, optionally filtered by requested names."""
    available: dict[str, Path] = {}
    for child in sorted(source_dir.iterdir()):
        if child.is_dir() and not child.name.startswith(".") and child.name not in SKIP_DIR_NAMES:
            available[child.name] = child

    if not requested:
        return (list(available.values()), [])

    wanted: list[str] = []
    for raw in requested:
        for part in raw.split(","):
            name = part.strip()
            if name and name not in wanted:
                wanted.append(name)

    missing = [name for name in wanted if name not in available]
    selected = [available[name] for name in wanted if name in available]
    return (selected, missing)


def collect_clean_files(skill_dir: Path) -> tuple[dict[str, Path], list[str]]:
    """Collect runtime files under skill_dir while skipping clutter and README.md."""
    clean_files: dict[str, Path] = {}
    skipped: list[str] = []

    for dirpath, dirnames, filenames in os.walk(skill_dir):
        current = Path(dirpath)
        rel_dir = current.relative_to(skill_dir)

        keep_dirs: list[str] = []
        for dname in sorted(dirnames):
            if dname in SKIP_DIR_NAMES:
                rel_p = (rel_dir / dname).as_posix()
                skipped.append(rel_p)
            else:
                keep_dirs.append(dname)
        dirnames[:] = keep_dirs

        for fname in sorted(filenames):
            fpath = current / fname
            rel_p = fpath.relative_to(skill_dir).as_posix()
            if (
                fname in SKIP_FILE_NAMES
                or fname.lower() == "readme.md"
                or fpath.suffix.lower() in SKIP_FILE_SUFFIXES
            ):
                skipped.append(rel_p)
                continue
            clean_files[rel_p] = fpath

    return (clean_files, skipped)


def _files_identical(src: Path, dst: Path) -> bool:
    if not dst.is_file():
        return False
    try:
        if src.stat().st_size != dst.stat().st_size:
            return False
        return src.read_bytes() == dst.read_bytes()
    except OSError:
        return False


def _prune_empty_dirs(root: Path) -> None:
    """Remove empty subdirectories inside a single synced skill folder."""
    if not root.is_dir():
        return
    for dirpath, dirnames, filenames in os.walk(root, topdown=False):
        current = Path(dirpath)
        if current == root:
            continue
        if not dirnames and not filenames:
            try:
                current.rmdir()
            except OSError:
                pass
        else:
            try:
                if not any(current.iterdir()):
                    current.rmdir()
            except OSError:
                pass


def sync_one_skill(skill_dir: Path, dest_root: Path, dry_run: bool) -> dict[str, Any]:
    """Idempotently sync one skill directory into dest_root/<skill_name>."""
    skill_name = skill_dir.name
    target_dir = dest_root / skill_name
    clean_files, skipped = collect_clean_files(skill_dir)

    created: list[str] = []
    updated: list[str] = []
    unchanged: list[str] = []
    removed_stale: list[str] = []

    existing_rel_files: dict[str, Path] = {}
    if target_dir.is_dir():
        for dirpath, _dirnames, filenames in os.walk(target_dir):
            current = Path(dirpath)
            for fname in filenames:
                fpath = current / fname
                rel_p = fpath.relative_to(target_dir).as_posix()
                existing_rel_files[rel_p] = fpath

    for rel_p, src_path in sorted(clean_files.items()):
        dst_path = target_dir / Path(rel_p)
        if rel_p not in existing_rel_files:
            created.append(rel_p)
            if not dry_run:
                dst_path.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(src_path, dst_path)
        elif _files_identical(src_path, dst_path):
            unchanged.append(rel_p)
        else:
            updated.append(rel_p)
            if not dry_run:
                dst_path.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(src_path, dst_path)

    for rel_p, stale_path in sorted(existing_rel_files.items()):
        if rel_p not in clean_files:
            removed_stale.append(rel_p)
            if not dry_run:
                try:
                    stale_path.unlink()
                except OSError:
                    pass

    if not dry_run:
        if not target_dir.exists():
            target_dir.mkdir(parents=True, exist_ok=True)
        _prune_empty_dirs(target_dir)

    return {
        "skill": skill_name,
        "dest": str(target_dir),
        "created": created,
        "updated": updated,
        "unchanged": unchanged,
        "removed_stale": removed_stale,
        "skipped_clutter": skipped,
    }


def render_text(payload: dict[str, Any]) -> str:
    """Format sync summary as concise human-readable text."""
    lines: list[str] = []
    mode = "DRY-RUN" if payload["dry_run"] else "SYNC"
    lines.append(f"[{mode}] status={payload['status']} source={payload['source']}")

    if payload["validation_failures"]:
        lines.append("Validation failures:")
        for item in payload["validation_failures"]:
            lines.append(f"  [FAIL] {item['skill']}")
            for err in item["errors"]:
                lines.append(f"    ERROR: {err}")

    for target_entry in payload["targets"]:
        lines.append(f"Target {target_entry['target']} -> {target_entry['dest_root']}")
        for op in target_entry["skills"]:
            lines.append(
                f"  - {op['skill']}: +{len(op['created'])} created, "
                f"~{len(op['updated'])} updated, "
                f"={len(op['unchanged'])} unchanged, "
                f"-{len(op['removed_stale'])} stale removed, "
                f"skipped={len(op['skipped_clutter'])}"
            )
    return "\n".join(lines)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Validate and idempotently sync runtime skills into agent skill directories."
    )
    parser.add_argument(
        "--target",
        choices=("antigravity", "claude", "codex", "agents", "all"),
        default=None,
        help="Built-in agent skill target directory (or 'all').",
    )
    parser.add_argument(
        "--dest",
        default=None,
        help="Custom destination skills directory (useful for testing or custom harnesses).",
    )
    parser.add_argument(
        "--source",
        default=str(DEFAULT_SOURCE),
        help="Source skills parent directory (default: <repo>/skills).",
    )
    parser.add_argument(
        "--skill",
        action="append",
        dest="skills",
        default=None,
        help="Sync only the named skill(s). Repeatable or comma-separated.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Validate and report planned sync actions without modifying disk.",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Proceed with syncing even when validate_skill.py reports errors.",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        dest="as_json",
        help="Emit structured JSON receipt.",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    configure_stdio()
    parser = build_parser()
    args = parser.parse_args(argv)

    if args.target is None and args.dest is None:
        print(
            "Error: specify --target {antigravity,claude,codex,agents,all} and/or --dest <dir>.",
            file=sys.stderr,
        )
        return 2

    source_dir = Path(args.source).expanduser().resolve()
    if not source_dir.is_dir():
        print(f"Error: source skills directory not found: {source_dir}", file=sys.stderr)
        return 2

    skill_dirs, missing_skills = discover_source_skills(source_dir, args.skills)
    if missing_skills:
        print(
            f"Error: requested skill(s) not found under {source_dir}: {', '.join(missing_skills)}",
            file=sys.stderr,
        )
        return 2
    if not skill_dirs:
        print(f"Error: no skill directories found under {source_dir}", file=sys.stderr)
        return 2

    try:
        validator = load_validator()
    except (FileNotFoundError, RuntimeError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 2

    validation_results = [validator.validate_skill_dir(sdir) for sdir in skill_dirs]
    failures = [res.to_dict() for res in validation_results if not res.ok]

    targets = resolve_targets(args.target, args.dest)
    payload: dict[str, Any] = {
        "status": "ok",
        "dry_run": args.dry_run,
        "forced": args.force,
        "source": str(source_dir),
        "skills_selected": [sdir.name for sdir in skill_dirs],
        "validation_failures": failures,
        "targets": [],
    }

    if failures and not args.force:
        payload["status"] = "validation_failed"
        if args.as_json:
            print(json.dumps(payload, indent=2, ensure_ascii=False))
        else:
            print(render_text(payload))
        return 1

    try:
        for label, dest_root in targets:
            if not args.dry_run:
                dest_root.mkdir(parents=True, exist_ok=True)
            ops = [sync_one_skill(sdir, dest_root, dry_run=args.dry_run) for sdir in skill_dirs]
            payload["targets"].append(
                {
                    "target": label,
                    "dest_root": str(dest_root),
                    "skills": ops,
                }
            )
    except OSError as exc:
        print(f"Error during sync: {exc}", file=sys.stderr)
        return 1

    if args.as_json:
        print(json.dumps(payload, indent=2, ensure_ascii=False))
    else:
        print(render_text(payload))

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
