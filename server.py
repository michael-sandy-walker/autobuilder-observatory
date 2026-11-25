"""Local Observatory host and bounded, read-only proxy for Autobuilder APIs. Its writes: a review appended beside one of
Autobuilder's decisions (POST /api/reviews, then followed at once, T6-X), an operator's finding handed to Autobuilder
(POST /api/findings, T4-FJ), and a request for a Claude session's recommendation (POST /api/nudges, M-H)."""
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
nudge_spec = importlib.util.spec_from_file_location('autobuilder_nudges', ROOT / 'scripts/nudges.py')
nudge_script = importlib.util.module_from_spec(nudge_spec)
nudge_spec.loader.exec_module(nudge_script)


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
        loaded = reviews.load(root)
        # T6-X: what Autobuilder wrote in following each review (its entries name the review they follow)
        made = {}
        for d in loaded:
            if d['entry'].get('follows'):
                made.setdefault(d['entry']['follows'], []).append(
                    {k: d['entry'].get(k) for k in ('id', 'kind', 'on', 'tickets')})
        for d in loaded:
            d['reviews'] = [{**r, 'followed': made.get(f"review of {r.get('decision')} at {r.get('at')}", [])}
                            for r in d['reviews']]
        rows = [{'project': d['project'], 'entry': d['entry'], 'reviews': d['reviews'],
                 'needs_review': d.get('needs_review', True),
                 'recommendation': recs.get((d['entry'].get('id'), d['project'])) or recs.get((d['entry'].get('id'), None))}
                for d in loaded]
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


def filed_by_autobuilder(root=None):
    """The tickets Autobuilder created itself, by id, from its decision records: 'autobuilder' for one it filed from its
    own draft, 'your finding' for one it filed from a finding the operator handed it (T4-FJ)."""
    try:
        rows = reviews.load(root)
    except (OSError, ValueError, SystemExit):
        return {}
    kinds = {d['entry'].get('id'): d['entry'].get('kind') for d in rows}
    out = {}
    for d in rows:
        e = d['entry']
        if e.get('kind') == 'TICKET_CREATED' and e.get('tickets'):
            out[e['tickets'][0]] = 'your finding' if kinds.get(e.get('follows')) == 'OPERATOR_FINDING' else 'autobuilder'
    return out


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
        by = filed_by_autobuilder()
        rows = [{k: t[k] for k in ('id', 'marker', 'title', 'line')} |
                {'status': queue.MARKER_NAMES[t['marker']], 'tier': queue.tier_of(t['id']), 'filedBy': by.get(t['id'])}
                for t in tickets]
        return {'ok': True, 'data': {'source': '.claude-memory/roadmap.md',
                'tickets': rows, 'counts': dict(Counter(t['status'] for t in rows))}}
    except (OSError, ValueError) as error:
        return {'ok': False, 'error': str(error)}


def append_review(body, root=None, follow=None):
    """T4-EZ, M-H: a review appended beside a decision (a Claude session's or the operator's). It never changes a
    decision; T6-X: Autobuilder is then asked to follow it at once (request_follow), not only at its next proposal.
    FOLLOW (for tests) replaces request_follow; root given and no FOLLOW = no follow (a test's scratch records)."""
    try:
        value = json.loads(body or b'{}')
        project = value.get('project') or None
        rec = reviews.review(str(value.get('decision', '')), str(value.get('assessment', '')).upper(),
                             str(value.get('reason', '')), str(value.get('reviewer') or 'operator'), root=root,
                             project=project, revises=value.get('revises') or None)
        if follow is None and root is None:
            follow = request_follow
        if follow is not None:
            rec = {**rec, 'follow': follow(project or reviews.resolve(rec['decision'], rec['assessment'],
                                                                        rec['reason'], root)['project'])}
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
    if launch is None and any(j['state'] == 'running' for j in follow_jobs()):
        return {'ok': False, 'error': 'Autobuilder is following confirmed reviews; ask again in a minute'}
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


FOLLOW_ROOT = Path.home() / '.autobuilder' / 'follows'
ROADMAP_RECORD = 'autobuilder-roadmap'


def follow_target(project_name, root=None):
    """T6-X: what `ProposeCommand --follow-reviews` is started on for a review of a decision in this record: the
    project's directory, '-' for Autobuilder's own roadmap record, None for an archived copy (its project's state is
    gone; the review feeds the agreement measure only)."""
    if project_name == ROADMAP_RECORD:
        return '-'
    if not project_name or '(archive ' in project_name:
        return None
    candidate = Path(root or ROOT) / 'data' / project_name
    return str(candidate) if candidate.is_dir() else None


def _epoch(at):
    """An entry's ISO time ('2026-11-25T18:15:48.594988391Z', nanoseconds allowed) as seconds, or None."""
    from datetime import datetime
    import re
    m = re.match(r'(\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d)(\.\d+)?(Z|[+-]\d\d:\d\d)?$', str(at or ''))
    if not m:
        return None
    frac = (m.group(2) or '.0')[:7]
    zone = m.group(3) or 'Z'
    try:
        return datetime.fromisoformat(m.group(1) + frac + ('+00:00' if zone == 'Z' else zone)).timestamp()
    except ValueError:
        return None


def follow_detail(job_dir, loaded, roadmap_tickets):
    """T6-X, what one follow-up did: each entry Autobuilder wrote while it ran (an entry naming the review it follows),
    with that review, the decision the review was about, and the roadmap lines the follow-up added to the ticket.
    LOADED: reviews.load(); ROADMAP_TICKETS: next_work.parse() of the roadmap."""
    start_file, end_file = job_dir / 'request.json', job_dir / 'out.log'
    if not start_file.is_file():
        return []
    start = start_file.stat().st_mtime - 1
    end = (end_file.stat().st_mtime if end_file.is_file() else start) + 1
    by_id = {}
    for d in loaded:
        by_id.setdefault(d['entry'].get('id'), d)
    tickets = {t['id']: t for t in roadmap_tickets}
    out = []
    for d in loaded:
        e = d['entry']
        t = _epoch(e.get('at'))
        follows = str(e.get('follows') or '')
        if not follows.startswith('review of ') or t is None or not (start <= t <= end):
            continue
        reviewed_id, _, review_at = follows[len('review of '):].partition(' at ')
        reviewed = by_id.get(reviewed_id)
        review = next((r for r in (reviewed or {}).get('reviews', []) if str(r.get('at')) == review_at), None)
        reason = str((review or {}).get('reason') or '')
        lines = []
        for tid in e.get('tickets') or []:
            body = tickets.get(tid, {}).get('body', [])
            lines += [l.strip() for l in body if f'`{reviewed_id}`' in l and (not reason or reason[:60] in l)]
        out.append({'entry': {k: e.get(k) for k in ('id', 'kind', 'at', 'on', 'decision', 'tickets')},
                    'project': d['project'],
                    'review': review and {k: review.get(k) for k in ('assessment', 'reviewer', 'reason', 'at')},
                    'reviewed': reviewed and {'project': reviewed['project'], **{k: reviewed['entry'].get(k)
                                              for k in ('id', 'kind', 'at', 'on', 'decision', 'tickets')}},
                    'roadmap': lines})
    return sorted(out, key=lambda x: x['entry'].get('at') or '')


def follow_jobs(root=None, limit=5, loaded=None, roadmap_tickets=None):
    """T6-X: Autobuilder's recent follow-ups of confirmed reviews, newest first: on which record, how far it got --
    running, done (with its own answer, 'followed N review(s)'), or failed -- and, given LOADED and ROADMAP_TICKETS,
    what each one did (follow_detail)."""
    base = Path(root) if root else FOLLOW_ROOT
    out = []
    if not base.is_dir():
        return out
    for d in sorted((p for p in base.iterdir() if p.is_dir()), reverse=True):
        if len(out) >= limit:
            break
        req = _json(d / 'request.json') or {}
        log = (d / 'out.log').read_text(errors='replace') if (d / 'out.log').is_file() else ''
        answer = next((l for l in log.splitlines() if l.startswith('PROPOSE: followed')), None)
        if answer == 'PROPOSE: followed 0 review(s)':
            continue   # a pass queued behind one that already carried out its reviews: it did nothing to show
        pid = int((d / 'pid').read_text().strip()) if (d / 'pid').is_file() else 0
        state = 'done' if answer else 'running' if pid and _alive(pid) else 'failed'
        out.append({'stamp': d.name, 'project': req.get('project'), 'state': state,
                    'answer': answer[len('PROPOSE: '):] if answer else None,
                    'error': None if answer or state == 'running' else (log.strip().splitlines() or ['no output'])[-1],
                    'did': follow_detail(d, loaded, roadmap_tickets) if loaded is not None and state != 'running' else []})
    return out


def follow_queue(root=None):
    path = (Path(root) if root else FOLLOW_ROOT) / 'queue.json'
    value = _json(path) if path.is_file() else None
    return [p for p in value if isinstance(p, str)] if isinstance(value, list) else []


def _write_queue(names, root=None):
    base = Path(root) if root else FOLLOW_ROOT
    base.mkdir(parents=True, exist_ok=True)
    (base / 'queue.json').write_text(json.dumps(names))


def request_follow(project_name, root=None, jar=None, launch=None, live=None, data_root=None, findings_root=None):
    """T6-X: a confirmed review is followed at once. The record's name is queued (once: one follow covers all of a
    record's new reviews) and the queue is worked off (drain_follows). Returns what the operator is told."""
    if follow_target(project_name, data_root) is None:
        return {'state': 'not followed', 'why': 'an archived copy: its project state is gone, so Autobuilder does not '
                                                'act on it (the review still counts toward the agreement measure)'}
    names = follow_queue(root)
    if project_name not in names:
        _write_queue(names + [project_name], root)
    started = drain_follows(root, jar, launch, live, data_root, findings_root)
    return {'state': 'started' if started == project_name else 'queued', 'project': project_name}


def drain_follows(root=None, jar=None, launch=None, live=None, data_root=None, findings_root=None):
    """T6-X: starts the next queued follow-up -- one at a time, not beside an operator's finding still being looked
    into (both write the roadmap), never while a run is live (the run's own proposal step follows its project's
    reviews at its end). Called after each review and on each snapshot, so a queued one starts once the way is clear.
    Returns the record name started, or None."""
    names = follow_queue(root)
    if not names:
        return None
    if any(j['state'] == 'running' for j in follow_jobs(root)):
        return None
    if any(j['state'] == 'running' for j in finding_jobs(findings_root)):
        return None
    if (live if live is not None else run_is_live)():
        return None
    name, rest = names[0], names[1:]
    _write_queue(rest, root)
    target = follow_target(name, data_root)
    if target is None:
        return None
    from datetime import datetime
    base = Path(root) if root else FOLLOW_ROOT
    d = base / datetime.now().strftime('%Y-%m-%d_%H-%M-%S-%f')
    d.mkdir(parents=True, exist_ok=True)
    (d / 'request.json').write_text(json.dumps({'project': name, 'target': target}, indent=2))
    cmd = ['java', f'-Dautobuilder.log.root={d / "logs"}',
           '-Dloader.main=com.walpro.autobuilder.core.propose.ProposeCommand', '-cp', str(jar or JAR),
           'org.springframework.boot.loader.launch.PropertiesLauncher', target, '--follow-reviews',
           '--spring.profiles.active=dev-auto,fast', f'--autobuilder.propose.roadmap={ROOT / ".claude-memory" / "roadmap.md"}']
    if launch is not None:
        pid = launch(cmd, d)
    else:
        import subprocess
        with open(d / 'out.log', 'w') as out:
            pid = subprocess.Popen(cmd, cwd=str(ROOT), stdout=out, stderr=subprocess.STDOUT,
                                   start_new_session=True).pid
    (d / 'pid').write_text(str(pid))
    return name


def nudges_file(root=None):
    return Path(root or ROOT) / 'process' / 'reviews' / 'nudges.ndjson'


def nudges(root=None):
    """M-H part 2: the operator's requests for a Claude session's recommendation, each with whether one has come since
    (scripts/nudges.py: a running session watches the file with --watch; the next session's start reads what is left)."""
    return nudge_script.load(root)


def append_nudge(body, root=None):
    """M-H part 2: the Observatory asks a running Claude session for a recommendation on open topics. The line carries
    the decision's id and record name, never text from a run or a draft (the session reads Autobuilder's records
    itself); a note is the operator's own words and is kept as theirs. A session only RECOMMENDS in answer."""
    try:
        value = json.loads(body or b'{}')
    except ValueError:
        return {'ok': False, 'error': 'not JSON'}
    items = value.get('topics') if isinstance(value.get('topics'), list) else [value]
    note = str(value.get('note') or '').strip()[:2000] or None
    try:
        known = {(d['entry'].get('id'), d['project']) for d in reviews.load(root)}
    except (OSError, ValueError, SystemExit) as error:
        return {'ok': False, 'error': str(error)}
    rows = []
    for it in items:
        did, project = str((it or {}).get('decision') or ''), str((it or {}).get('project') or '')
        if (did, project) not in known:
            return {'ok': False, 'error': f'no decision {did} in {project or "any record"}; nothing written'}
        rows.append({'decision': did, 'project': project})
    if not rows:
        return {'ok': False, 'error': 'no topic named'}
    from datetime import datetime, timezone
    at = datetime.now(timezone.utc).isoformat()
    path = nudges_file(root)
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, 'a', encoding='utf-8') as f:
        for r in rows:
            f.write(json.dumps({**r, 'at': at, 'by': 'operator', **({'note': note} if note else {})},
                               ensure_ascii=False) + '\n')
    return {'ok': True, 'data': {'nudged': len(rows), 'at': at}}


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
        writes = {'/api/reviews': append_review, '/api/findings': start_finding, '/api/nudges': append_nudge}
        if self.path not in writes:
            self.send_error(404)
            return
        length = int(self.headers.get('Content-Length') or 0)
        body = self.rfile.read(min(length, 64 * 1024))
        value = writes[self.path](body)
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
            drain_follows()   # T6-X: a follow-up queued behind a run or a request starts once the way is clear
            try:
                loaded, roadmap_tickets = reviews.load(), queue.parse(str(ROOT / '.claude-memory' / 'roadmap.md'))
            except (OSError, ValueError, SystemExit):
                loaded, roadmap_tickets = None, None
            result['follows'] = {'ok': True, 'data': {'jobs': follow_jobs(loaded=loaded, roadmap_tickets=roadmap_tickets),
                                                      'queued': follow_queue()}}
            result['nudges'] = {'ok': True, 'data': nudges()}
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
