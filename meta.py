"""Read-only development evidence for Autobuilder itself."""
import re
import subprocess
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent


def git(root, *args):
    result = subprocess.run(['git', '-C', str(root), *args], capture_output=True,
                            text=True, timeout=8, check=True)
    return result.stdout


def documents(root):
    paths = [root / '.claude-memory/meta_process.md', root / 'process/README.md',
             root / '.claude-memory/project_signal_consumer_map.md',
             root / '.claude-memory/project_three_disciplines_lens.md']
    paths += sorted((root / '.claude-memory').glob('*design_note.md'))
    paths += sorted((root / 'process/ports').glob('*/REVIEW.md'))
    paths += sorted((root / 'process/ports').glob('*/ASSESSMENT.md'))
    return [p for p in paths if p.is_file()]


def document(path, root=BASE):
    allowed = {str(p.relative_to(root)): p for p in documents(root)}
    if path not in allowed:
        return {'ok': False, 'error': 'Unknown development document'}
    p = allowed[path]
    return {'ok': True, 'data': {'path': path, 'text': p.read_text(errors='replace'),
            'modified': datetime.fromtimestamp(p.stat().st_mtime, timezone.utc).isoformat()}}


def collect(root=BASE):
    result = {}
    try:
        commits = []
        for row in git(root, 'log', '-40', '--format=%H%x09%aI%x09%s').splitlines():
            sha, at, title = row.split('\t', 2)
            commits.append({'sha': sha, 'at': at, 'title': title,
                            'tickets': re.findall(r'\bT\d-[A-Z]+(?:-\d+)?\b', title)})
        changed = git(root, 'diff', '--name-only', 'HEAD').splitlines()
        untracked = git(root, 'ls-files', '--others', '--exclude-standard').splitlines()
        conflicts = git(root, 'diff', '--name-only', '--diff-filter=U').splitlines()
        result['git'] = {'ok': True, 'data': {'head': git(root, 'rev-parse', 'HEAD').strip(),
            'branch': git(root, 'branch', '--show-current').strip(), 'commits': commits,
            'changed': changed, 'untracked': untracked, 'conflicts': conflicts}}
    except (OSError, subprocess.SubprocessError, ValueError) as e:
        result['git'] = {'ok': False, 'error': str(e)}
    reports, errors = [], []
    for path in sorted((root / 'target/surefire-reports').glob('TEST-*.xml')):
        try:
            suite = ET.parse(path).getroot()
            reports.append({'name': suite.get('name'), 'tests': int(suite.get('tests', 0)),
                'failures': int(suite.get('failures', 0)), 'errors': int(suite.get('errors', 0)),
                'skipped': int(suite.get('skipped', 0)), 'source': str(path.relative_to(root)),
                'modified': datetime.fromtimestamp(path.stat().st_mtime, timezone.utc).isoformat()})
        except (ET.ParseError, OSError, ValueError) as e:
            errors.append({'source': str(path.relative_to(root)), 'error': str(e)})
    result['tests'] = {'reports': reports, 'errors': errors,
                       'totals': {k: sum(r[k] for r in reports) for k in ('tests','failures','errors','skipped')}}
    result['documents'] = [{'path': str(p.relative_to(root)),
                            'title': next((l.lstrip('# ').strip() for l in p.read_text(errors='replace').splitlines() if l.startswith('#')), p.stem)}
                           for p in documents(root)]
    roles = []
    for role in ('planner', 'evolution', 'evaluation', 'merge', 'analysis'):
        folder = root / 'src/main/java/com/walpro/autobuilder/agent' / role
        roles.append({'role': role, 'sources': [str(p.relative_to(root)) for p in sorted(folder.glob('*Agent.java'))],
                      'contracts': [str(p.relative_to(root)) for p in sorted((root / 'src/main/java/com/walpro/autobuilder/protocol' / role).glob('*ProtocolContract.java'))]})
    result['roles'] = roles
    return result
