#!/usr/bin/env python3
"""Deterministic validator for portable agentskills.io skill directories."""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

sys.dont_write_bytecode = True

ALLOWED_FRONTMATTER_KEYS = frozenset(
    {
        "name",
        "description",
        "license",
        "compatibility",
        "metadata",
        "allowed-tools",
    }
)
NAME_RE = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
TOP_KEY_RE = re.compile(r"^([A-Za-z0-9_-]+)\s*:(.*)$")
BLOCK_SCALAR_RE = re.compile(r"^([>|])([+-])?(\d+)?(?:\s+#.*)?$")
FENCE_RE = re.compile(r"^\s{0,3}(`{3,}|~{3,})")
MD_LINK_RE = re.compile(r"!?\[[^\]]*\]\(([^)\n]+)\)")
INLINE_CODE_RE = re.compile(r"`([^`\n]+)`")
PROSE_PATH_RE = re.compile(
    r"(?<![A-Za-z0-9_:/\\.-])"
    r"((?:\.\./|\./)?(?:references|scripts|evals|assets|examples|resources)/[^\s\"'`,;)\]]+)"
)
TODO_MARKER_RE = re.compile(r"\[TODO\b[^\]\n]*\]", re.IGNORECASE)
NEGATIVE_BOUNDARY_RE = re.compile(
    r"\b(?:do\s+not\s+use|don't\s+use|not\s+for|avoid\s+when|exclude\s+when)\b",
    re.IGNORECASE,
)
WORKFLOW_SUMMARY_RE = re.compile(
    r"(?:\bstep\s+1\b.*\bstep\s+2\b|\bphase\s+1\b.*\bphase\s+2\b|\bfirst\b.*\bthen\s+runs?\b)",
    re.IGNORECASE,
)
EXTERNAL_SCHEME_RE = re.compile(r"^[a-zA-Z][a-zA-Z0-9+.-]*:")
CLUTTER_FILES = frozenset({".DS_Store", "Thumbs.db"})
CLUTTER_SUFFIXES = frozenset({".pyc", ".pyo"})
ALLOWED_SPLITS = frozenset({"train", "holdout", "test"})


@dataclass
class SkillValidationResult:
    """Validation outcome for one skill directory."""

    skill: str
    path: str
    ok: bool = True
    word_counts: dict[str, int] = field(default_factory=dict)
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    def add_error(self, message: str) -> None:
        self.errors.append(message)
        self.ok = False

    def add_warning(self, message: str) -> None:
        self.warnings.append(message)

    def to_dict(self) -> dict[str, Any]:
        return {
            "skill": self.skill,
            "path": self.path,
            "ok": self.ok,
            "word_counts": self.word_counts,
            "errors": self.errors,
            "warnings": self.warnings,
        }


def configure_stdio() -> None:
    """Force UTF-8 stdout/stderr on Windows to avoid cp1252 encode failures."""
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if callable(reconfigure):
            try:
                reconfigure(encoding="utf-8", errors="replace")
            except OSError:
                pass


def is_within(candidate: Path, root: Path) -> bool:
    """Return True if resolved candidate path stays inside resolved root."""
    try:
        cand_norm = os.path.normcase(os.path.realpath(str(candidate)))
        root_norm = os.path.normcase(os.path.realpath(str(root)))
        return os.path.commonpath([cand_norm, root_norm]) == root_norm
    except ValueError:
        return False


def _strip_inline_comment(raw: str) -> str:
    """Strip an unquoted trailing YAML comment (# ...) outside quotes."""
    in_single = False
    in_double = False
    escaped = False
    for idx, ch in enumerate(raw):
        if escaped:
            escaped = False
            continue
        if ch == "\\" and in_double:
            escaped = True
            continue
        if ch == "'" and not in_double:
            in_single = not in_single
        elif ch == '"' and not in_single:
            in_double = not in_double
        elif ch == "#" and not in_single and not in_double:
            if idx == 0 or raw[idx - 1].isspace():
                return raw[:idx].rstrip()
    return raw.rstrip()


def _parse_scalar(raw: str) -> Any:
    """Parse a single-line YAML scalar, inline list, or empty mapping."""
    cleaned = _strip_inline_comment(raw.strip())
    if not cleaned:
        return ""
    if cleaned == "{}":
        return {}
    if cleaned == "[]":
        return []
    if len(cleaned) >= 2 and cleaned[0] == cleaned[-1] and cleaned[0] in ("'", '"'):
        quote = cleaned[0]
        inner = cleaned[1:-1]
        if quote == "'":
            return inner.replace("''", "'")
        return (
            inner.replace(r"\n", "\n")
            .replace(r"\t", "\t")
            .replace(r"\"", '"')
            .replace(r"\\", "\\")
        )
    if cleaned.startswith("[") and cleaned.endswith("]"):
        inner = cleaned[1:-1].strip()
        if not inner:
            return []
        return [_parse_scalar(item) for item in inner.split(",")]
    if cleaned.startswith("{") and cleaned.endswith("}"):
        inner = cleaned[1:-1].strip()
        if not inner:
            return {}
        mapping: dict[str, Any] = {}
        for pair in inner.split(","):
            if ":" not in pair:
                raise ValueError(f"Invalid inline mapping entry: {pair!r}")
            k, v = pair.split(":", 1)
            mapping[k.strip()] = _parse_scalar(v)
        return mapping
    return cleaned


def _consume_block_scalar(
    lines: list[str], start_idx: int, style: str, chomp: str, parent_indent: int
) -> tuple[str, int]:
    """Parse a YAML folded (> / >-) or literal (| / |-) block scalar."""
    collected: list[str] = []
    idx = start_idx
    block_indent: int | None = None

    while idx < len(lines):
        line = lines[idx]
        if not line.strip():
            collected.append("")
            idx += 1
            continue
        indent = len(line) - len(line.lstrip(" "))
        if indent <= parent_indent:
            break
        if block_indent is None:
            block_indent = indent
        dedented = line[min(indent, block_indent) :]
        collected.append(dedented)
        idx += 1

    trailing_blanks = 0
    while collected and collected[-1] == "":
        collected.pop()
        trailing_blanks += 1

    if not collected:
        return ("", idx)

    if style == "|":
        text = "\n".join(collected)
    else:
        # Folded block scalar: join non-blank base-indent lines with spaces,
        # keep blank lines as paragraph breaks, preserve more-indented lines.
        out_parts: list[str] = []
        prev_was_text = False
        prev_more_indented = False
        pending_blanks = 0
        for item in collected:
            if item == "":
                pending_blanks += 1
                continue
            more_indented = item.startswith(" ")
            if not out_parts:
                out_parts.append(item)
            elif pending_blanks > 0:
                if prev_more_indented or more_indented:
                    out_parts.append("\n" * (pending_blanks + 1) + item)
                else:
                    out_parts.append("\n" * pending_blanks + item)
            elif prev_more_indented or more_indented:
                out_parts.append("\n" + item)
            elif prev_was_text:
                out_parts.append(" " + item)
            else:
                out_parts.append(item)
            pending_blanks = 0
            prev_was_text = True
            prev_more_indented = more_indented
        text = "".join(out_parts)

    if chomp == "-":
        return (text, idx)
    if chomp == "+":
        return (text + "\n" * (1 + trailing_blanks), idx)
    return (text + "\n", idx)


def parse_frontmatter(text: str) -> tuple[dict[str, Any] | None, str, list[str]]:
    """Extract and parse YAML frontmatter without external dependencies."""
    errors: list[str] = []
    if text.startswith("\ufeff"):
        text = text[1:]

    lines = text.splitlines()
    if not lines or lines[0].strip() != "---":
        return (None, text, ["SKILL.md must begin with YAML frontmatter delimited by '---'"])

    end_idx: int | None = None
    for idx in range(1, len(lines)):
        if lines[idx].strip() in ("---", "..."):
            end_idx = idx
            break
    if end_idx is None:
        return (None, text, ["SKILL.md YAML frontmatter is missing a closing '---' delimiter"])

    fm_lines = lines[1:end_idx]
    body = "\n".join(lines[end_idx + 1 :])
    data: dict[str, Any] = {}

    idx = 0
    while idx < len(fm_lines):
        raw_line = fm_lines[idx]
        stripped = raw_line.strip()
        if not stripped or stripped.startswith("#"):
            idx += 1
            continue
        if raw_line.startswith((" ", "\t")):
            errors.append(
                f"Frontmatter line {idx + 2}: unexpected indentation outside a block value"
            )
            idx += 1
            continue

        match = TOP_KEY_RE.match(raw_line)
        if not match:
            errors.append(f"Frontmatter line {idx + 2}: invalid YAML entry {raw_line!r}")
            idx += 1
            continue

        key = match.group(1)
        rest = match.group(2).strip()
        if key in data:
            errors.append(f"Frontmatter line {idx + 2}: duplicate key {key!r}")

        block_match = BLOCK_SCALAR_RE.match(rest)
        if block_match:
            style = block_match.group(1)
            chomp = block_match.group(2) or ""
            value, idx = _consume_block_scalar(fm_lines, idx + 1, style, chomp, parent_indent=0)
            data[key] = value
            continue

        if not rest or rest.startswith("#"):
            # Look ahead for an indented list or nested mapping.
            idx += 1
            child_lines: list[str] = []
            while idx < len(fm_lines):
                nxt = fm_lines[idx]
                if not nxt.strip() or nxt.strip().startswith("#"):
                    idx += 1
                    continue
                if nxt.startswith((" ", "\t")):
                    child_lines.append(nxt)
                    idx += 1
                    continue
                break

            if not child_lines:
                data[key] = ""
            elif all(cl.lstrip().startswith("- ") or cl.lstrip() == "-" for cl in child_lines):
                items: list[Any] = []
                for cl in child_lines:
                    item_raw = cl.lstrip()[1:].strip()
                    items.append(_parse_scalar(item_raw))
                data[key] = items
            else:
                nested: dict[str, Any] = {}
                for cl in child_lines:
                    cm = TOP_KEY_RE.match(cl.lstrip())
                    if not cm:
                        errors.append(f"Frontmatter key {key!r}: invalid nested line {cl!r}")
                        continue
                    try:
                        nested[cm.group(1)] = _parse_scalar(cm.group(2))
                    except ValueError as exc:
                        errors.append(f"Frontmatter key {key!r}: {exc}")
                data[key] = nested
            continue

        try:
            data[key] = _parse_scalar(rest)
        except ValueError as exc:
            errors.append(f"Frontmatter line {idx + 2}: {exc}")
            data[key] = rest
        idx += 1

    return (data, body, errors)


def strip_fenced_blocks(text: str) -> str:
    """Replace fenced code block interiors with empty lines, preserving line numbers."""
    out: list[str] = []
    in_fence = False
    fence_char = ""
    fence_len = 0

    for line in text.splitlines():
        match = FENCE_RE.match(line)
        if match:
            token = match.group(1)
            if not in_fence:
                in_fence = True
                fence_char = token[0]
                fence_len = len(token)
                out.append("")
                continue
            if token[0] == fence_char and len(token) >= fence_len and not line.strip().lstrip(fence_char):
                in_fence = False
                fence_char = ""
                fence_len = 0
                out.append("")
                continue
        out.append("" if in_fence else line)

    return "\n".join(out)


def count_words(text: str) -> int:
    """Count whitespace-delimited words deterministically."""
    return len(text.split())


def _is_template_placeholder(raw_path: str) -> bool:
    return any(tok in raw_path for tok in ("<", ">", "*", "{", "}", "..."))


def _clean_md_target(raw_target: str) -> str:
    target = raw_target.strip()
    if target.startswith("<") and target.endswith(">"):
        target = target[1:-1].strip()
    # Strip optional markdown link title: path "title" or path 'title'
    if " " in target:
        target = target.split(None, 1)[0]
    target = target.split("#", 1)[0].split("?", 1)[0].strip()
    return target


def validate_markdown_prose(
    md_file: Path,
    skill_root: Path,
    text: str,
    result: SkillValidationResult,
) -> None:
    """Validate markdown links, backticked relative paths, and leftover TODO markers."""
    rel_display = md_file.relative_to(skill_root).as_posix()
    unfenced = strip_fenced_blocks(text)

    for line_no, line in enumerate(unfenced.splitlines(), start=1):
        # 1. Leftover [TODO...] markers outside fenced blocks.
        for todo_match in TODO_MARKER_RE.finditer(line):
            result.add_error(
                f"{rel_display}:{line_no}: leftover TODO marker {todo_match.group(0)!r}"
            )

        # 2. Markdown links [text](target).
        for link_match in MD_LINK_RE.finditer(line):
            target = _clean_md_target(link_match.group(1))
            if not target or target.startswith("#") or EXTERNAL_SCHEME_RE.match(target):
                continue
            if _is_template_placeholder(target):
                continue
            candidate = (md_file.parent / target).resolve()
            if not is_within(candidate, skill_root):
                result.add_error(
                    f"{rel_display}:{line_no}: markdown link {target!r} escapes skill directory"
                )
            elif not candidate.exists():
                result.add_error(
                    f"{rel_display}:{line_no}: broken markdown link {target!r}"
                )

        # 3. Backticked relative prose paths (references/..., scripts/..., evals/..., assets/...).
        for code_match in INLINE_CODE_RE.finditer(line):
            code_span = code_match.group(1)
            for path_match in PROSE_PATH_RE.finditer(code_span):
                raw_rel = path_match.group(1)
                if raw_rel.endswith("/") or _is_template_placeholder(raw_rel):
                    continue
                cleaned = raw_rel.rstrip(".:;,")
                if not cleaned or cleaned.endswith("/") or _is_template_placeholder(cleaned):
                    continue

                if cleaned.startswith("../"):
                    candidate = (md_file.parent / cleaned).resolve()
                else:
                    candidate = (skill_root / cleaned).resolve()
                    if not candidate.exists() and md_file.parent != skill_root:
                        alt = (md_file.parent / cleaned).resolve()
                        if is_within(alt, skill_root) and alt.exists():
                            candidate = alt

                if not is_within(candidate, skill_root):
                    result.add_error(
                        f"{rel_display}:{line_no}: prose path {cleaned!r} escapes skill directory"
                    )
                elif not candidate.exists():
                    result.add_error(
                        f"{rel_display}:{line_no}: broken prose path {cleaned!r}"
                    )


def validate_frontmatter(
    frontmatter: dict[str, Any],
    skill_dir: Path,
    result: SkillValidationResult,
) -> None:
    """Validate agentskills.io frontmatter schema and CSO hygiene."""
    unknown_keys = sorted(set(frontmatter.keys()) - ALLOWED_FRONTMATTER_KEYS)
    for key in unknown_keys:
        result.add_error(
            f"SKILL.md frontmatter contains unsupported key {key!r} "
            f"(allowed: {', '.join(sorted(ALLOWED_FRONTMATTER_KEYS))})"
        )

    # Validate name
    name = frontmatter.get("name")
    if not isinstance(name, str) or not name.strip():
        result.add_error("SKILL.md frontmatter is missing a non-empty 'name'")
    else:
        clean_name = name.strip()
        if len(clean_name) > 64:
            result.add_error(f"Frontmatter 'name' exceeds 64 chars ({len(clean_name)})")
        if not NAME_RE.match(clean_name):
            result.add_error(
                f"Frontmatter 'name' {clean_name!r} must be lowercase hyphen-case (a-z, 0-9, single '-')"
            )
        if clean_name != skill_dir.name:
            result.add_error(
                f"Frontmatter 'name' {clean_name!r} does not match directory name {skill_dir.name!r}"
            )

    # Validate description
    desc = frontmatter.get("description")
    if not isinstance(desc, str) or not desc.strip():
        result.add_error("SKILL.md frontmatter is missing a non-empty 'description'")
    else:
        clean_desc = desc.strip()
        if len(clean_desc) > 1024:
            result.add_error(
                f"Frontmatter 'description' exceeds 1024 chars ({len(clean_desc)})"
            )
        if "<" in clean_desc or ">" in clean_desc:
            result.add_error(
                "Frontmatter 'description' must not contain '<' or '>' characters"
            )
        if not NEGATIVE_BOUNDARY_RE.search(clean_desc):
            result.add_warning(
                "Frontmatter 'description' lacks an explicit negative trigger boundary (e.g. 'Do NOT use when...')"
            )
        if len(clean_desc) > 650:
            result.add_warning(
                f"Frontmatter 'description' is verbose ({len(clean_desc)} chars; consider tightening <= 650 chars)"
            )
        if WORKFLOW_SUMMARY_RE.search(clean_desc):
            result.add_warning(
                "Frontmatter 'description' appears to summarize workflow steps (CSO risk: agents may skip SKILL.md)"
            )

    # Optional fields
    if "compatibility" in frontmatter:
        compat = frontmatter["compatibility"]
        if not isinstance(compat, str) or not compat.strip():
            result.add_error("Frontmatter 'compatibility' must be a non-empty string")
        elif len(compat.strip()) > 500:
            result.add_error(
                f"Frontmatter 'compatibility' exceeds 500 chars ({len(compat.strip())})"
            )

    if "license" in frontmatter:
        lic = frontmatter["license"]
        if not isinstance(lic, str) or not lic.strip():
            result.add_error("Frontmatter 'license' must be a non-empty string")

    if "metadata" in frontmatter and not isinstance(frontmatter["metadata"], dict):
        result.add_error("Frontmatter 'metadata' must be a YAML key-value mapping")

    if "allowed-tools" in frontmatter:
        allowed_tools = frontmatter["allowed-tools"]
        valid_str = isinstance(allowed_tools, str) and bool(allowed_tools.strip())
        valid_list = isinstance(allowed_tools, list) and all(
            isinstance(item, str) and bool(item.strip()) for item in allowed_tools
        )
        if not (valid_str or valid_list):
            result.add_error("Frontmatter 'allowed-tools' must be a non-empty string or list of strings")


def validate_clutter(skill_dir: Path, result: SkillValidationResult) -> None:
    """Flag runtime clutter (__pycache__, *.pyc, .DS_Store, Thumbs.db) and warn on README.md."""
    for dirpath, dirnames, filenames in os.walk(skill_dir):
        current = Path(dirpath)
        rel_dir = current.relative_to(skill_dir)

        pruned_dirs: list[str] = []
        for dname in list(dirnames):
            if dname == "__pycache__":
                rel_p = (rel_dir / dname).as_posix()
                result.add_error(f"Runtime clutter directory present: {rel_p}")
                pruned_dirs.append(dname)
        for dname in pruned_dirs:
            dirnames.remove(dname)

        for fname in filenames:
            fpath = current / fname
            rel_p = fpath.relative_to(skill_dir).as_posix()
            if fname in CLUTTER_FILES or fpath.suffix.lower() in CLUTTER_SUFFIXES:
                result.add_error(f"Runtime clutter file present: {rel_p}")
            elif fname.lower() == "readme.md":
                result.add_warning(
                    f"Runtime skill directory contains {rel_p}; keep repository README.md at repo root"
                )


def _valid_prompt_entry(item: Any) -> bool:
    if isinstance(item, str):
        return bool(item.strip())
    if isinstance(item, dict):
        prompt = item.get("prompt")
        return isinstance(prompt, str) and bool(prompt.strip())
    return False


def validate_evals_json(evals_file: Path, result: SkillValidationResult) -> None:
    """Validate evals/evals.json JSON syntax and required structure."""
    try:
        raw = evals_file.read_text(encoding="utf-8")
    except OSError as exc:
        result.add_error(f"Cannot read evals/evals.json: {exc}")
        return

    try:
        payload = json.loads(raw)
    except json.JSONDecodeError as exc:
        result.add_error(f"evals/evals.json is invalid JSON: {exc}")
        return

    if not isinstance(payload, dict):
        result.add_error("evals/evals.json root value must be a JSON object")
        return

    # Validate trigger_tests
    triggers = payload.get("trigger_tests")
    if triggers is None:
        result.add_error("evals/evals.json is missing required 'trigger_tests'")
    elif isinstance(triggers, list):
        if not triggers:
            result.add_error("evals/evals.json 'trigger_tests' must be a non-empty list")
        else:
            pos_count = 0
            neg_count = 0
            for idx, entry in enumerate(triggers):
                if not isinstance(entry, dict):
                    result.add_error(f"evals/evals.json trigger_tests[{idx}] must be an object")
                    continue
                prompt = entry.get("prompt")
                should_trigger = entry.get("should_trigger")
                if not isinstance(prompt, str) or not prompt.strip():
                    result.add_error(
                        f"evals/evals.json trigger_tests[{idx}] missing non-empty string 'prompt'"
                    )
                if not isinstance(should_trigger, bool):
                    result.add_error(
                        f"evals/evals.json trigger_tests[{idx}] missing boolean 'should_trigger'"
                    )
                elif should_trigger:
                    pos_count += 1
                else:
                    neg_count += 1
                if "split" in entry:
                    split_val = entry["split"]
                    if not isinstance(split_val, str) or split_val not in ALLOWED_SPLITS:
                        result.add_error(
                            f"evals/evals.json trigger_tests[{idx}] invalid 'split' {split_val!r} "
                            f"(expected one of {sorted(ALLOWED_SPLITS)})"
                        )
            if pos_count == 0:
                result.add_error(
                    "evals/evals.json 'trigger_tests' must include at least one positive case (should_trigger: true)"
                )
            if neg_count == 0:
                result.add_error(
                    "evals/evals.json 'trigger_tests' must include at least one negative case (should_trigger: false)"
                )
    elif isinstance(triggers, dict):
        pos = triggers.get("positive")
        neg = triggers.get("negative")
        if not isinstance(pos, list) or not pos or not all(_valid_prompt_entry(x) for x in pos):
            result.add_error(
                "evals/evals.json 'trigger_tests.positive' must be a non-empty list of prompts"
            )
        if not isinstance(neg, list) or not neg or not all(_valid_prompt_entry(x) for x in neg):
            result.add_error(
                "evals/evals.json 'trigger_tests.negative' must be a non-empty list of prompts"
            )
    else:
        result.add_error("evals/evals.json 'trigger_tests' must be a list or {positive, negative} object")

    # Validate forward_tests
    forwards = payload.get("forward_tests")
    if not isinstance(forwards, list) or not forwards:
        result.add_error("evals/evals.json is missing required non-empty 'forward_tests' list")
    else:
        for idx, entry in enumerate(forwards):
            if not isinstance(entry, dict):
                result.add_error(f"evals/evals.json forward_tests[{idx}] must be an object")
                continue
            prompt = entry.get("prompt") if "prompt" in entry else entry.get("scenario")
            if not isinstance(prompt, str) or not prompt.strip():
                result.add_error(
                    f"evals/evals.json forward_tests[{idx}] missing non-empty 'prompt' (or 'scenario')"
                )
            invariants = entry.get("invariants")
            if (
                not isinstance(invariants, list)
                or not invariants
                or not all(isinstance(inv, str) and bool(inv.strip()) for inv in invariants)
            ):
                result.add_error(
                    f"evals/evals.json forward_tests[{idx}] missing non-empty string list 'invariants'"
                )


def validate_skill_dir(
    skill_dir: Path,
    max_skill_words: int = 1250,
    max_ref_words: int = 900,
) -> SkillValidationResult:
    """Run all structural, budget, path, clutter, and eval checks on one skill directory."""
    resolved_dir = skill_dir.resolve()
    result = SkillValidationResult(skill=resolved_dir.name, path=str(resolved_dir))

    skill_md = resolved_dir / "SKILL.md"
    if not skill_md.is_file():
        result.add_error("Missing required SKILL.md")
    else:
        try:
            text = skill_md.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError) as exc:
            result.add_error(f"Failed to read SKILL.md as UTF-8: {exc}")
            text = None

        if text is not None:
            words = count_words(text)
            result.word_counts["SKILL.md"] = words
            if words > max_skill_words:
                result.add_error(
                    f"SKILL.md word count {words} exceeds limit of {max_skill_words} words"
                )

            frontmatter, _body, fm_errors = parse_frontmatter(text)
            for err in fm_errors:
                result.add_error(err)
            if frontmatter is not None:
                validate_frontmatter(frontmatter, resolved_dir, result)

            validate_markdown_prose(skill_md, resolved_dir, text, result)

    # Check references/*.md
    refs_dir = resolved_dir / "references"
    if refs_dir.is_dir():
        for ref_file in sorted(refs_dir.rglob("*.md")):
            rel_ref = ref_file.relative_to(resolved_dir).as_posix()
            try:
                ref_text = ref_file.read_text(encoding="utf-8")
            except (OSError, UnicodeDecodeError) as exc:
                result.add_error(f"Failed to read {rel_ref} as UTF-8: {exc}")
                continue
            ref_words = count_words(ref_text)
            result.word_counts[rel_ref] = ref_words
            if ref_words > max_ref_words:
                result.add_error(
                    f"{rel_ref} word count {ref_words} exceeds limit of {max_ref_words} words"
                )
            validate_markdown_prose(ref_file, resolved_dir, ref_text, result)

    # Check runtime clutter
    validate_clutter(resolved_dir, result)

    # Check evals/evals.json when present
    evals_file = resolved_dir / "evals" / "evals.json"
    if evals_file.is_file():
        validate_evals_json(evals_file, result)

    return result


def discover_skill_dirs(target: Path, validate_all: bool) -> list[Path]:
    """Resolve target path to one or more skill directories."""
    if validate_all:
        children = sorted(
            child
            for child in target.iterdir()
            if child.is_dir() and not child.name.startswith(".") and child.name != "__pycache__"
        )
        return children

    if (target / "SKILL.md").is_file():
        return [target]

    # Auto-detect parent skills directory if child subdirectories contain SKILL.md.
    child_dirs = sorted(
        child
        for child in target.iterdir()
        if child.is_dir() and not child.name.startswith(".") and child.name != "__pycache__"
    )
    if any((child / "SKILL.md").is_file() for child in child_dirs):
        return child_dirs

    # Otherwise treat as a single skill directory (so missing SKILL.md reports exit 1).
    return [target]


def render_text(results: list[SkillValidationResult]) -> str:
    """Format validation results as concise human-readable text."""
    lines: list[str] = []
    for res in results:
        tag = "PASS" if res.ok else "FAIL"
        counts = ", ".join(f"{k}={v}w" for k, v in res.word_counts.items()) or "no markdown files"
        lines.append(f"[{tag}] {res.skill} ({counts})")
        for err in res.errors:
            lines.append(f"  ERROR: {err}")
        for warn in res.warnings:
            lines.append(f"  WARN:  {warn}")
    passed = sum(1 for r in results if r.ok)
    failed = len(results) - passed
    lines.append(f"Summary: {passed}/{len(results)} passed, {failed} failed")
    return "\n".join(lines)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Validate portable agentskills.io skill directories."
    )
    parser.add_argument(
        "target",
        help="Path to a single skill directory or a parent skills directory.",
    )
    parser.add_argument(
        "--all",
        action="store_true",
        dest="validate_all",
        help="Treat target as a parent directory and validate all child skill directories.",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        dest="as_json",
        help="Emit structured JSON output.",
    )
    parser.add_argument(
        "--max-skill-words",
        type=int,
        default=1250,
        help="Maximum allowed word count for SKILL.md (default: 1250).",
    )
    parser.add_argument(
        "--max-ref-words",
        type=int,
        default=900,
        help="Maximum allowed word count for each references/*.md file (default: 900).",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    configure_stdio()
    parser = build_parser()
    args = parser.parse_args(argv)

    if args.max_skill_words <= 0 or args.max_ref_words <= 0:
        print("Error: word limits must be positive integers.", file=sys.stderr)
        return 2

    target = Path(args.target)
    if not target.exists():
        print(f"Error: path does not exist: {target}", file=sys.stderr)
        return 2
    if not target.is_dir():
        print(f"Error: path is not a directory: {target}", file=sys.stderr)
        return 2

    skill_dirs = discover_skill_dirs(target, args.validate_all)
    if not skill_dirs:
        print(f"Error: no skill directories found under {target}", file=sys.stderr)
        return 2

    results = [
        validate_skill_dir(
            skill_dir,
            max_skill_words=args.max_skill_words,
            max_ref_words=args.max_ref_words,
        )
        for skill_dir in skill_dirs
    ]
    all_ok = all(r.ok for r in results)

    if args.as_json:
        payload = {
            "status": "pass" if all_ok else "fail",
            "summary": {
                "skills_checked": len(results),
                "passed": sum(1 for r in results if r.ok),
                "failed": sum(1 for r in results if not r.ok),
                "error_count": sum(len(r.errors) for r in results),
                "warning_count": sum(len(r.warnings) for r in results),
            },
            "results": [r.to_dict() for r in results],
        }
        print(json.dumps(payload, indent=2, ensure_ascii=False))
    else:
        print(render_text(results))

    return 0 if all_ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
