"""Local Observatory host and bounded, read-only proxy for Autobuilder APIs. Its one write: a review appended beside
one of Autobuilder's decisions (POST /api/reviews)."""
from meta import collect, document
import argparse
import os
import json
import importlib.util
from collections import Counter
from urllib.parse import unquote
from concurrent.futures import ThreadPoolExecutor
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import urlopen

ROUTES = {
    'home': ('home', '/home/state'),
    'status': ('run', '/autobuilder/status'),
    'start': ('run', '/autobuilder/start-state'),
    'live': ('run', '/autobuilder/live'),
    'goals': ('run', '/autobuilder/goals/tree'),
    'imports': ('run', '/autobuilder/inputs/imports'),
    'conflicts': ('run', '/autobuilder/inputs/conflicts'),
}


def read_endpoint(base, path):
    try:
        with urlopen(base + path, timeout=4) as response:
            body = response.read(8 * 1024 * 1024 + 1)
            if len(body) > 8 * 1024 * 1024:
                raise ValueError('Response exceeded size limit')
            return {'ok': True, 'data': json.loads(body) if body else None}
    except (HTTPError, URLError, TimeoutError, ValueError, OSError) as error:
        return {'ok': False, 'error': str(error)}


ROOT = Path(__file__).resolve().parent.parent
spec = importlib.util.spec_from_file_location('autobuilder_next_work', ROOT / 'scripts/next_work.py')
queue = importlib.util.module_from_spec(spec)
spec.loader.exec_module(queue)
review_spec = importlib.util.spec_from_file_location('autobuilder_review_decisions', ROOT / 'scripts/review_decisions.py')
reviews = importlib.util.module_from_spec(review_spec)
review_spec.loader.exec_module(reviews)


def recommendations(root=None):
    """M-H: a Claude session's recommendation per open decision, written after reading its evidence
    (process/reviews/recommendations.ndjson; the latest line for a decision wins). Shown beside the decision so the
    operator can confirm or correct it; a recommendation is never a review until the operator acts on it."""
    path = Path(root or ROOT) / 'process' / 'reviews' / 'recommendations.ndjson'
    out = {}
    if not path.is_file():
        return out
    for line in path.read_text(errors='replace').splitlines():
        try:
            r = json.loads(line)
        except ValueError:
            continue
        if isinstance(r, dict) and r.get('decision'):
            out[(r['decision'], r.get('project'))] = r
    return out


def decisions_data(root=None):
    """T4-FB: every project's record of the decisions Autobuilder took on its own, with the reviews appended beside
    them (M-H); unreviewed first, then newest first. Read only -- reviews are written by scripts/review_decisions.py."""
    try:
        recs = recommendations(root)
        rows = [{'project': d['project'], 'entry': d['entry'], 'reviews': d['reviews'],
                 'needs_review': d.get('needs_review', True),
                 'recommendation': recs.get((d['entry'].get('id'), d['project'])) or recs.get((d['entry'].get('id'), None))}
                for d in reviews.load(root)]
        waiting = lambda d: d['needs_review'] and not d['reviews']
        rows.sort(key=lambda d: d['entry'].get('at', ''), reverse=True)
        rows.sort(key=lambda d: not waiting(d))
        return {'ok': True, 'data': {'source': 'data/*/meta/decisions.ndjson and .claude-memory/decisions.ndjson',
                'decisions': rows, 'unreviewed': sum(1 for d in rows if waiting(d))}}
    except (OSError, ValueError) as error:
        return {'ok': False, 'error': str(error)}


CARRY_ROOT = Path.home() / '.autobuilder' / 'carry-through'


def _json(path):
    try:
        return json.loads(path.read_text(errors='replace'))
    except (OSError, ValueError):
        return None


def carry_attempts(ticket_id, root=None):
    """T4-FG rule 7: every carry-through attempt for this ticket -- why it was selected (selection.json: the decision,
    the reason, what was passed over, the stated check, the pinned commit), which run attempted it (run.txt), the
    evidence taken on that run's final tree (evidence.json) and the outcome Autobuilder recorded. Read as written;
    nothing is judged here."""
    out = []
    base = Path(root) if root else CARRY_ROOT
    if not base.is_dir():
        return out
    for d in sorted(base.iterdir(), reverse=True):
        sel = _json(d / 'selection.json') if d.is_dir() else None
        if not sel or sel.get('ticket') != ticket_id:
            continue
        ev = _json(d / 'evidence.json') or {}
        run = (d / 'run.txt').read_text().strip() if (d / 'run.txt').is_file() else None
        outcome = (d / 'conclude.out').read_text(errors='replace').strip() if (d / 'conclude.out').is_file() else None
        out.append({'attempt': d.name, 'dir': str(d), 'selection': sel, 'run': run, 'case':
                    (d / 'case.txt').read_text().strip() if (d / 'case.txt').is_file() else None,
                    'evidence': {k: ev.get(k) for k in ('graded_tree', 'run_terminal', 'run_yardstick_final_tree',
                                                        'patch', 'suite_pinned', 'suite_patched', 'regressions',
                                                        'check', 'error', 'dry_run') if k in ev},
                    'outcome': outcome})
    return out


def ticket_decisions(ticket_id, root=None):
    """The decisions Autobuilder recorded that concern this ticket, oldest first, with their reviews."""
    try:
        rows = [{'project': d['project'], 'entry': d['entry'], 'reviews': d['reviews']} for d in reviews.load(root)
                if ticket_id in (d['entry'].get('tickets') or [])]
    except (OSError, ValueError, SystemExit):
        return []
    return sorted(rows, key=lambda d: d['entry'].get('at', ''))


def development_data(path=None, ticket_id=None):
    """Read the authoritative queue with the repository's existing marker semantics."""
    path = Path(path) if path else ROOT / '.claude-memory/roadmap.md'
    try:
        tickets = queue.parse(str(path))
        if ticket_id is not None:
            ticket = next((t for t in tickets if t['id'] == ticket_id), None)
            if ticket is None:
                return {'ok': False, 'error': 'Ticket not found'}
            lines = path.read_text(errors='replace').splitlines()
            return {'ok': True, 'data': {**ticket, 'status': queue.MARKER_NAMES[ticket['marker']],
                    'source': '.claude-memory/roadmap.md',
                    'text': '\n'.join(lines[ticket['line'] - 1:ticket['line']] + ticket['body']),
                    'decisions': ticket_decisions(ticket_id), 'carry': carry_attempts(ticket_id)}}
        rows = [{k: t[k] for k in ('id', 'marker', 'title', 'line')} |
                {'status': queue.MARKER_NAMES[t['marker']], 'tier': queue.tier_of(t['id'])}
                for t in tickets]
        return {'ok': True, 'data': {'source': '.claude-memory/roadmap.md',
                'tickets': rows, 'counts': dict(Counter(t['status'] for t in rows))}}
    except (OSError, ValueError) as error:
        return {'ok': False, 'error': str(error)}


def append_review(body, root=None):
    """T4-EZ, M-H: the Observatory's one write -- a review appended beside a decision (a Claude session's or the
    operator's). It never changes a decision or anything else; Autobuilder follows the review at its next proposal."""
    try:
        value = json.loads(body or b'{}')
        rec = reviews.review(str(value.get('decision', '')), str(value.get('assessment', '')).upper(),
                             str(value.get('reason', '')), str(value.get('reviewer') or 'operator'), root=root,
                             project=value.get('project') or None, revises=value.get('revises') or None)
        return {'ok': True, 'data': rec}
    except SystemExit as refused:
        return {'ok': False, 'error': str(refused)}
    except (OSError, ValueError) as error:
        return {'ok': False, 'error': str(error)}


FINDINGS_ROOT = Path.home() / '.autobuilder' / 'findings'
JAR = ROOT / 'target' / 'autobuilder-1.0.1-SNAPSHOT.jar'


def _alive(pid):
    try:
        os.kill(pid, 0)
        return True
    except (OSError, ValueError):
        return False


def finding_jobs(root=None, limit=5):
    """T4-FJ: the operator's recent requests to Autobuilder, newest first: what was asked, about what, and how far it
    got -- running, done (with Autobuilder's own one-line answer), or failed."""
    base = Path(root) if root else FINDINGS_ROOT
    out = []
    if not base.is_dir():
        return out
    for d in sorted((p for p in base.iterdir() if p.is_dir()), reverse=True)[:limit]:
        req = _json(d / 'request.json') or {}
        log = (d / 'out.log').read_text(errors='replace') if (d / 'out.log').is_file() else ''
        answer = next((l for l in log.splitlines() if l.startswith('PROPOSE: finding')), None)
        pid = int((d / 'pid').read_text().strip()) if (d / 'pid').is_file() else 0
        state = 'done' if answer else 'running' if pid and _alive(pid) else 'failed'
        out.append({'stamp': d.name, 'text': req.get('text', ''), 'about': req.get('about'),
                    'project': req.get('project'), 'state': state,
                    'answer': answer[len('PROPOSE: finding '):] if answer else None,
                    'error': None if answer or state == 'running' else (log.strip().splitlines() or ['no output'])[-1]})
    return out


def run_is_live(port=8081):
    try:
        with urlopen(f'http://127.0.0.1:{port}/autobuilder/status', timeout=1):
            return True
    except (HTTPError, URLError, TimeoutError, OSError):
        return False


def start_finding(body, root=None, jar=None, launch=None):
    """T4-FJ: the Observatory's second write -- hands Autobuilder the operator's finding and starts its investigation
    in the background (ProposeCommand --finding). One request at a time, never while a run is live; the decision it
    leads to is recorded by Autobuilder like any other. LAUNCH (for tests) replaces starting the process."""
    try:
        value = json.loads(body or b'{}')
    except ValueError:
        return {'ok': False, 'error': 'not JSON'}
    text = str(value.get('text') or '').strip()
    if not text:
        return {'ok': False, 'error': 'write the finding first'}
    base = Path(root) if root else FINDINGS_ROOT
    if any(j['state'] == 'running' for j in finding_jobs(base)):
        return {'ok': False, 'error': 'Autobuilder is still looking into the previous request; one at a time'}
    if launch is None and run_is_live():
        return {'ok': False, 'error': 'a run is live; ask again when it has ended'}
    about = str(value.get('about') or '').strip() or None
    project_name = str(value.get('project') or '').strip() or None
    project = None
    if project_name and '(archive ' not in project_name and project_name != 'autobuilder-roadmap':
        candidate = ROOT / 'data' / project_name
        if candidate.is_dir():
            project = candidate
    from datetime import datetime
    d = base / datetime.now().strftime('%Y-%m-%d_%H-%M-%S')
    d.mkdir(parents=True, exist_ok=True)
    (d / 'request.json').write_text(json.dumps({'text': text, 'about': about, 'project': project_name}, indent=2))
    (d / 'finding.txt').write_text(text + '\n')
    cmd = ['java', f'-Dautobuilder.log.root={d / "logs"}',
           '-Dloader.main=com.walpro.autobuilder.core.propose.ProposeCommand', '-cp', str(jar or JAR),
           'org.springframework.boot.loader.launch.PropertiesLauncher', str(project) if project else '-',
           '--finding', str(d / 'finding.txt')] + (['--about', about] if about else []) + [
           '--spring.profiles.active=dev-auto,fast', f'--autobuilder.propose.roadmap={ROOT / ".claude-memory" / "roadmap.md"}']
    if launch is not None:
        pid = launch(cmd, d)
    else:
        import subprocess
        with open(d / 'out.log', 'w') as out:
            pid = subprocess.Popen(cmd, cwd=str(ROOT), stdout=out, stderr=subprocess.STDOUT,
                                   start_new_session=True).pid
    (d / 'pid').write_text(str(pid))
    return {'ok': True, 'data': {'stamp': d.name, 'project': str(project) if project else None, 'about': about}}


class Handler(SimpleHTTPRequestHandler):
    def end_headers(self):
        # The page's own files change as the Observatory is developed; without this a browser keeps running a cached
        # live.js after an update (2026-11-23: the review walkthrough was served but not shown).
        self.send_header('Cache-Control', 'no-cache')
        super().end_headers()

    def send_json(self, value, status=200):
        body = json.dumps(value).encode()
        self.send_response(status)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Cache-Control', 'no-store')
        self.send_header('Content-Length', str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self):
        if self.path not in ('/api/reviews', '/api/findings'):
            self.send_error(404)
            return
        length = int(self.headers.get('Content-Length') or 0)
        body = self.rfile.read(min(length, 64 * 1024))
        value = append_review(body) if self.path == '/api/reviews' else start_finding(body)
        self.send_json(value, 200 if value['ok'] else 400)

    def do_GET(self):
        if self.path.startswith('/api/documents/'):
            value = document(unquote(self.path[len('/api/documents/'):]))
            self.send_json(value, 200 if value['ok'] else 404)
            return
        if self.path.startswith('/api/tickets/'):
            value = development_data(ticket_id=unquote(self.path[len('/api/tickets/'):]))
            self.send_json(value, 200 if value['ok'] else 404)
            return
        if self.path == '/api/snapshot':
            with ThreadPoolExecutor(max_workers=len(ROUTES)) as pool:
                jobs = {key: pool.submit(read_endpoint, self.server.backends[target], path)
                        for key, (target, path) in ROUTES.items()}
                result = {key: job.result() for key, job in jobs.items()}
            result['development'] = development_data()
            result['decisions'] = decisions_data()
            result['findings'] = {'ok': True, 'data': finding_jobs()}
            result['meta'] = {'ok': True, 'data': collect()}
            self.send_json(result)
        elif self.path.startswith('/api/'):
            self.send_error(404)
        else:
            super().do_GET()


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--port', type=int, default=8090)
    parser.add_argument('--run-port', type=int, default=8081)
    parser.add_argument('--home-port', type=int, default=8080)
    args = parser.parse_args()
    handler = partial(Handler, directory=str(Path(__file__).parent / 'dist'))
    server = ThreadingHTTPServer(('127.0.0.1', args.port), handler)
    server.backends = {'run': f'http://127.0.0.1:{args.run_port}',
                       'home': f'http://127.0.0.1:{args.home_port}'}
    print(f'Observatory: http://127.0.0.1:{args.port}', flush=True)
    server.serve_forever()
