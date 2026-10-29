from pathlib import Path
import re
import sys


def check(root: Path) -> list[str]:
    errors = []
    skill = root / 'skills' / 'braces' / 'SKILL.md'
    required = [skill, root / 'skills/braces/agents/openai.yaml', root / 'LICENSE']
    for path in required:
        if not path.is_file():
            errors.append(f'Missing {path.relative_to(root)}')
    if not skill.is_file():
        return errors
    text = skill.read_text(encoding='utf-8')
    match = re.match(r'\A---\n(.*?)\n---\n', text, re.S)
    if not match:
        errors.append('SKILL.md needs YAML frontmatter')
    else:
        fields = dict(re.findall(r'^(name|description): (.+)$', match[1], re.M))
        if fields.get('name') != 'braces' or not fields.get('description', '').strip():
            errors.append('SKILL.md needs name: braces and a description')
    words = len(text.split())
    if words > 1250:
        errors.append(f'SKILL.md has {words} words; keep the entrypoint at or below 1250')
    for path in root.rglob('*.md'):
        if '.git' in path.relative_to(root).parts:
            continue
        content = path.read_text(encoding='utf-8')
        for target in re.findall(r'\[[^\]]*\]\(([^)]+)\)', content):
            if '://' in target or target.startswith('#'):
                continue
            destination = (path.parent / target.split('#', 1)[0]).resolve()
            if not destination.is_relative_to(root.resolve()) or not destination.exists():
                errors.append(f'{path.relative_to(root)} has a broken local link: {target}')
    return errors


if __name__ == '__main__':
    repository = Path(__file__).resolve().parents[1]
    problems = check(repository)
    if problems:
        print('\n'.join(problems), file=sys.stderr)
        sys.exit(1)
    word_count = len((repository / 'skills/braces/SKILL.md').read_text(encoding='utf-8').split())
    print(f'Package checks passed. SKILL.md: {word_count}/1250 words.')
