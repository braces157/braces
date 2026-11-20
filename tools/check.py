#!/usr/bin/env python3
"""Repository validation entrypoint wrapping validate_skill.py and root doc checks."""

from __future__ import annotations

import importlib.util
import re
import sys
from pathlib import Path

sys.dont_write_bytecode = True

MD_LINK_RE = re.compile(r'\[[^\]]*\]\(([^)]+)\)')


def _load_validator(validator_path: Path):
    spec = importlib.util.spec_from_file_location('_braces_validate_skill', str(validator_path))
    if spec is None or spec.loader is None:
        raise RuntimeError(f'Unable to load validator from {validator_path}')
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def check(root: Path) -> list[str]:
    errors: list[str] = []
    skill_dir = root / 'skills' / 'braces'
    validator_path = skill_dir / 'scripts' / 'validate_skill.py'
    required = [
        skill_dir / 'SKILL.md',
        validator_path,
        root / 'LICENSE',
        root / 'ATTRIBUTION.md',
        root / 'GEMINI.md.template',
        root / 'tools' / 'sync_skills.py',
    ]
    for path in required:
        if not path.is_file():
            errors.append(f'Missing {path.relative_to(root)}')
    if errors:
        return errors

    validator = _load_validator(validator_path)
    result = validator.validate_skill_dir(skill_dir)
    for err in result.errors:
        errors.append(f'skills/braces: {err}')

    repo_resolved = root.resolve()
    for doc_name in ('README.md', 'ATTRIBUTION.md'):
        doc_path = root / doc_name
        if not doc_path.is_file():
            continue
        content = doc_path.read_text(encoding='utf-8')
        for target in MD_LINK_RE.findall(content):
            if '://' in target or target.startswith('#') or target.startswith('mailto:'):
                continue
            rel_target = target.split('#', 1)[0].strip()
            if not rel_target:
                continue
            destination = (doc_path.parent / rel_target).resolve()
            if not destination.is_relative_to(repo_resolved) or not destination.exists():
                errors.append(f'{doc_name} has a broken local link: {target}')

    return errors


if __name__ == '__main__':
    repository = Path(__file__).resolve().parents[1]
    problems = check(repository)
    if problems:
        print('\n'.join(problems), file=sys.stderr)
        sys.exit(1)
    word_count = len((repository / 'skills/braces/SKILL.md').read_text(encoding='utf-8').split())
    print(f'Package checks passed. SKILL.md: {word_count} words.')
