"""Local Observatory host and bounded, read-only proxy for Autobuilder APIs. Its one write: a review appended beside
one of Autobuilder's decisions (POST /api/reviews)."""
from meta import collect, document
import argparse
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


def decisions_data(root=None):
    """T4-FB: every project's record of the decisions Autobuilder took on its own, with the reviews appended beside
    them (M-H); unreviewed first, then newest first. Read only -- reviews are written by scripts/review_decisions.py."""
    try:
        rows = [{'project': d['project'], 'entry': d['entry'], 'reviews': d['reviews'],
                 'needs_review': d.get('needs_review', True)} for d in reviews.load(root)]
        waiting = lambda d: d['needs_review'] and not d['reviews']
        rows.sort(key=lambda d: d['entry'].get('at', ''), reverse=True)
        rows.sort(key=lambda d: not waiting(d))
        return {'ok': True, 'data': {'source': 'data/*/meta/decisions.ndjson and .claude-memory/decisions.ndjson',
                'decisions': rows, 'unreviewed': sum(1 for d in rows if waiting(d))}}
    except (OSError, ValueError) as error:
        return {'ok': False, 'error': str(error)}


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
                    'text': '\n'.join(lines[ticket['line'] - 1:ticket['line']] + ticket['body'])}}
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
                             project=value.get('project') or None)
        return {'ok': True, 'data': rec}
    except SystemExit as refused:
        return {'ok': False, 'error': str(refused)}
    except (OSError, ValueError) as error:
        return {'ok': False, 'error': str(error)}


class Handler(SimpleHTTPRequestHandler):
    def send_json(self, value, status=200):
        body = json.dumps(value).encode()
        self.send_response(status)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Cache-Control', 'no-store')
        self.send_header('Content-Length', str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self):
        if self.path != '/api/reviews':
            self.send_error(404)
            return
        length = int(self.headers.get('Content-Length') or 0)
        value = append_review(self.rfile.read(min(length, 64 * 1024)))
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
