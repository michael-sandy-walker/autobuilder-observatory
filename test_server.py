import unittest
from io import BytesIO
from unittest.mock import patch
from server import read_endpoint


class BackendResponseTest(unittest.TestCase):
    def test_preserves_real_cycle_and_zero_score(self):
        with patch('server.urlopen', return_value=BytesIO(b'{"cycleName":"cycle-7","score":0}')):
            self.assertEqual(read_endpoint('http://127.0.0.1:8081', '/autobuilder/status'),
                             {'ok': True, 'data': {'cycleName': 'cycle-7', 'score': 0}})

    def test_empty_goal_response_is_not_an_error(self):
        with patch('server.urlopen', return_value=BytesIO(b'')):
            self.assertEqual(read_endpoint('http://127.0.0.1:8081', '/autobuilder/goals/tree'),
                             {'ok': True, 'data': None})

    def test_unavailable_source_is_explicit(self):
        with patch('server.urlopen', side_effect=TimeoutError('timed out')):
            self.assertEqual(read_endpoint('http://127.0.0.1:8081', '/autobuilder/live'),
                             {'ok': False, 'error': 'timed out'})

    def test_non_json_is_not_live_data(self):
        with patch('server.urlopen', return_value=BytesIO(b'<html>error</html>')):
            self.assertFalse(read_endpoint('http://127.0.0.1:8081', '/autobuilder/status')['ok'])

class DevelopmentQueueTest(unittest.TestCase):
    def test_authoritative_markers_and_source_detail(self):
        import tempfile
        from pathlib import Path
        from server import development_data
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'roadmap.md'
            path.write_text('## Tier 1\n- [ ] **T1-A: Open ticket**\n  Evidence\n- [n] **T1-B: Declined ticket**\n  Not built\n- [x] **T1-C: Built ticket**\n')
            value = development_data(path)['data']
            self.assertEqual(value['counts'], {'open': 1, 'declined': 1, 'built': 1})
            detail = development_data(path, 'T1-A')['data']
            self.assertEqual(detail['line'], 2)
            self.assertIn('Evidence', detail['text'])
            self.assertNotIn('Declined ticket', detail['text'])
            self.assertFalse(development_data(path, '../secret')['ok'])

    def test_missing_roadmap_is_unavailable(self):
        from server import development_data
        self.assertFalse(development_data('/does-not-exist/roadmap.md')['ok'])


class DecisionRecordTest(unittest.TestCase):
    def test_unreviewed_first_with_reviews_beside_each(self):
        import json, tempfile
        from pathlib import Path
        from server import decisions_data
        with tempfile.TemporaryDirectory() as root:
            meta = Path(root) / 'data' / 'p' / 'meta'
            meta.mkdir(parents=True)
            (meta / 'decisions.ndjson').write_text(
                json.dumps({'id': 'dec-1', 'at': '2026-11-21T10:00:00Z', 'kind': 'DRAFT_CHECKED'}) + '\n' +
                json.dumps({'id': 'dec-2', 'at': '2026-11-21T09:00:00Z', 'kind': 'DRAFT_CHECKED'}) + '\n{torn')
            (meta / 'decision-reviews.ndjson').write_text(
                json.dumps({'decision': 'dec-1', 'assessment': 'AGREE', 'reason': 'r'}) + '\n')
            with open(meta / 'decisions.ndjson', 'a') as f:
                f.write('\n' + json.dumps({'id': 'T1-A#2', 'at': '2026-11-21T08:00:00Z', 'follows': 'review of dec-1 at x'}) + '\n')
            value = decisions_data(root)['data']
            self.assertEqual([d['entry']['id'] for d in value['decisions']], ['dec-2', 'dec-1', 'T1-A#2'])
            self.assertEqual(value['unreviewed'], 1)
            self.assertEqual(value['decisions'][1]['reviews'][0]['assessment'], 'AGREE')

    def test_no_records_is_empty_not_an_error(self):
        import tempfile
        from server import decisions_data
        with tempfile.TemporaryDirectory() as root:
            self.assertEqual(decisions_data(root)['data']['decisions'], [])


class ReviewWriteTest(unittest.TestCase):
    def test_a_review_is_appended_beside_its_decision_and_a_bad_one_refused(self):
        import json, tempfile
        from pathlib import Path
        from server import append_review
        with tempfile.TemporaryDirectory() as root:
            own = Path(root) / '.claude-memory'
            own.mkdir()
            (own / 'decisions.ndjson').write_text(json.dumps({'id': 'T1-BA#1', 'at': '2026-11-22T01:00:00Z'}) + '\n')
            ok = append_review(json.dumps({'decision': 'T1-BA#1', 'assessment': 'disagree', 'reason': 'wrong',
                                           'reviewer': 'op'}).encode(), root)
            self.assertTrue(ok['ok'])
            line = json.loads((own / 'decision-reviews.ndjson').read_text())
            self.assertEqual((line['decision'], line['assessment'], line['reviewer']), ('T1-BA#1', 'DISAGREE', 'op'))
            self.assertFalse(append_review(json.dumps({'decision': 'T1-BA#1', 'assessment': 'MAYBE', 'reason': 'x'}).encode(), root)['ok'])
            self.assertFalse(append_review(json.dumps({'decision': 'nope', 'assessment': 'AGREE', 'reason': 'x'}).encode(), root)['ok'])
            self.assertFalse(append_review(json.dumps({'decision': 'T1-BA#1', 'assessment': 'AGREE', 'reason': ' '}).encode(), root)['ok'])
            self.assertEqual(len((own / 'decision-reviews.ndjson').read_text().splitlines()), 1)


if __name__ == '__main__':
    unittest.main()


class CarryAttemptTest(unittest.TestCase):
    """T4-FG rule 7: a ticket's carry-through attempts, read as written, only the ticket's own."""

    def test_selection_run_evidence_and_outcome_of_the_tickets_attempts(self):
        import json, tempfile
        from pathlib import Path
        from server import carry_attempts
        with tempfile.TemporaryDirectory() as root:
            mine = Path(root, '2026-11-23_10-00-00'); mine.mkdir()
            (mine / 'selection.json').write_text(json.dumps({'ticket': 'T1-AA', 'decision': 'T1-AA#1', 'pinned': 'abc'}))
            (mine / 'run.txt').write_text('2026-11-23_10-05-00\n')
            (mine / 'evidence.json').write_text(json.dumps({'graded_tree': 'the final tree', 'regressions': [], 'secret': 1}))
            (mine / 'conclude.out').write_text('CARRY-CONCLUDE: decision=T1-AA#2\n')
            other = Path(root, '2026-11-23_11-00-00'); other.mkdir()
            (other / 'selection.json').write_text(json.dumps({'ticket': 'T1-AB'}))
            got = carry_attempts('T1-AA', root=root)
        self.assertEqual(len(got), 1)
        self.assertEqual(got[0]['run'], '2026-11-23_10-05-00')
        self.assertEqual(got[0]['evidence'], {'graded_tree': 'the final tree', 'regressions': []})
        self.assertEqual(got[0]['outcome'], 'CARRY-CONCLUDE: decision=T1-AA#2')

    def test_no_attempts_directory_is_empty(self):
        from server import carry_attempts
        self.assertEqual(carry_attempts('T1-AA', root='/nonexistent/carry'), [])


class RecommendationTest(unittest.TestCase):
    """M-H: a session's recommendation is shown beside its decision; the latest line wins; it is not a review."""

    def test_latest_recommendation_per_decision_and_project(self):
        import json, tempfile
        from pathlib import Path
        from server import recommendations
        with tempfile.TemporaryDirectory() as root:
            d = Path(root, 'process', 'reviews'); d.mkdir(parents=True)
            (d / 'recommendations.ndjson').write_text('\n'.join(json.dumps(x) for x in [
                {'decision': 'a#1', 'project': 'p', 'assessment': 'AGREE', 'reason': 'first'},
                {'decision': 'a#1', 'project': 'p', 'assessment': 'DISAGREE', 'reason': 'second thoughts'},
                'not an object']) + '\nnot json\n')
            got = recommendations(root)
        self.assertEqual(got[('a#1', 'p')]['reason'], 'second thoughts')
        self.assertEqual(len(got), 1)


class FindingRequestTest(unittest.TestCase):
    """T4-FJ: the operator hands Autobuilder a finding; one at a time; the request and its progress are visible."""

    def test_a_finding_is_started_recorded_and_listed_and_a_second_waits(self):
        import json, os, tempfile
        from pathlib import Path
        from server import start_finding, finding_jobs
        started = []
        def launch(cmd, d):
            started.append(cmd)
            (Path(d) / 'out.log').write_text('')
            return os.getpid()   # alive: the request counts as running
        with tempfile.TemporaryDirectory() as root:
            ok = start_finding(json.dumps({'text': 'the planner adds subgoals under a finished goal',
                                           'about': '2026-11-23_15-51-06#1', 'project': 'no-such-project'}).encode(),
                               root=root, launch=launch)
            self.assertTrue(ok['ok'])
            self.assertIn('--finding', started[0])
            self.assertEqual(started[0][started[0].index('--about') + 1], '2026-11-23_15-51-06#1')
            launcher = started[0].index('org.springframework.boot.loader.launch.PropertiesLauncher')
            self.assertEqual(started[0][launcher + 1], '-', 'a project that is not on disk: no project')
            jobs = finding_jobs(root)
            self.assertEqual([j['state'] for j in jobs], ['running'])
            self.assertEqual(jobs[0]['text'], 'the planner adds subgoals under a finished goal')
            busy = start_finding(json.dumps({'text': 'another'}).encode(), root=root, launch=launch)
            self.assertFalse(busy['ok'])
            self.assertIn('one at a time', busy['error'])

    def test_an_empty_finding_is_refused_and_a_finished_job_shows_autobuilders_answer(self):
        import json, tempfile
        from pathlib import Path
        from server import start_finding, finding_jobs
        with tempfile.TemporaryDirectory() as root:
            self.assertFalse(start_finding(json.dumps({'text': '  '}).encode(), root=root, launch=lambda c, d: 1)['ok'])
            d = Path(root, '2026-11-23_17-00-00'); d.mkdir()
            (d / 'request.json').write_text(json.dumps({'text': 'x'}))
            (d / 'out.log').write_text('noise\nPROPOSE: finding TICKET_CREATED T1-ZZ#1 -- created T1-ZZ\n')
            (d / 'pid').write_text('999999999')
            self.assertEqual(finding_jobs(root)[0]['state'], 'done')
            self.assertEqual(finding_jobs(root)[0]['answer'], 'TICKET_CREATED T1-ZZ#1 -- created T1-ZZ')


class FiledByAutobuilderTest(unittest.TestCase):
    """The Tickets view marks the tickets Autobuilder created itself, and those it filed from the operator's finding."""

    def test_created_tickets_are_marked_by_where_they_came_from(self):
        import json, os, tempfile
        from server import filed_by_autobuilder
        with tempfile.TemporaryDirectory() as root:
            os.makedirs(os.path.join(root, '.claude-memory'))
            os.makedirs(os.path.join(root, 'data', 'p', 'meta'))
            with open(os.path.join(root, 'data', 'p', 'meta', 'decisions.ndjson'), 'w') as f:
                f.write(json.dumps({'id': 'run#1', 'at': '1', 'kind': 'OPERATOR_FINDING'}) + '\n')
            with open(os.path.join(root, '.claude-memory', 'decisions.ndjson'), 'w') as f:
                f.write(json.dumps({'id': 'T1-AA#1', 'at': '2', 'kind': 'TICKET_CREATED', 'tickets': ['T1-AA'], 'follows': 'x#1'}) + '\n')
                f.write(json.dumps({'id': 'T4-BB#1', 'at': '3', 'kind': 'TICKET_CREATED', 'tickets': ['T4-BB'], 'follows': 'run#1'}) + '\n')
                f.write(json.dumps({'id': 'T1-AA#2', 'at': '4', 'kind': 'TICKET_CHANGED', 'tickets': ['T1-CC']}) + '\n')
            self.assertEqual(filed_by_autobuilder(root), {'T1-AA': 'autobuilder', 'T4-BB': 'your finding'})


class FollowReviewTest(unittest.TestCase):
    """T6-X: a confirmed review is followed at once -- one follow job per record, one at a time, never beside a live
    run or an operator's finding still being looked into; a queued one starts once the way is clear."""

    def setUp(self):
        import os, tempfile
        from pathlib import Path
        self.tmp = tempfile.TemporaryDirectory()
        base = Path(self.tmp.name)
        self.follows, self.findings, self.data = base / 'follows', base / 'findings', base / 'repo'
        (self.data / 'data' / 'p').mkdir(parents=True)
        self.started = []
        self.alive = True

        def launch(cmd, d):
            self.started.append(cmd)
            (Path(d) / 'out.log').write_text('')
            return os.getpid() if self.alive else 999999999
        self.launch = launch

    def tearDown(self):
        self.tmp.cleanup()

    def follow(self, name, live=False):
        from server import request_follow
        return request_follow(name, root=self.follows, launch=self.launch, live=lambda: live,
                              data_root=self.data, findings_root=self.findings)

    def test_a_review_with_no_run_live_starts_exactly_one_follow_for_its_project(self):
        got = self.follow('p')
        self.assertEqual(got['state'], 'started')
        self.assertEqual(len(self.started), 1)
        cmd = self.started[0]
        launcher = cmd.index('org.springframework.boot.loader.launch.PropertiesLauncher')
        self.assertEqual(cmd[launcher + 1], str(self.data / 'data' / 'p'))
        self.assertIn('--follow-reviews', cmd)

    def test_the_roadmaps_own_record_is_followed_with_no_project_and_an_archive_not_at_all(self):
        self.follow('autobuilder-roadmap')
        launcher = self.started[0].index('org.springframework.boot.loader.launch.PropertiesLauncher')
        self.assertEqual(self.started[0][launcher + 1], '-')
        self.assertEqual(self.follow('p (archive 2026-11-22_01-00-00)')['state'], 'not followed')
        self.assertEqual(len(self.started), 1)

    def test_none_while_a_run_is_live_and_the_queued_one_starts_after(self):
        from server import drain_follows, follow_queue
        self.assertEqual(self.follow('p', live=True)['state'], 'queued')
        self.assertEqual(self.started, [])
        self.assertEqual(follow_queue(self.follows), ['p'])
        self.assertEqual(drain_follows(self.follows, launch=self.launch, live=lambda: False, data_root=self.data,
                                       findings_root=self.findings), 'p')
        self.assertEqual(len(self.started), 1)
        self.assertEqual(follow_queue(self.follows), [])

    def test_a_second_review_while_one_runs_queues_once_rather_than_starting_a_second(self):
        from server import follow_queue, follow_jobs
        self.follow('p')
        self.assertEqual(self.follow('autobuilder-roadmap')['state'], 'queued')
        self.assertEqual(self.follow('autobuilder-roadmap')['state'], 'queued')
        self.assertEqual(len(self.started), 1)
        self.assertEqual(follow_queue(self.follows), ['autobuilder-roadmap'])
        self.assertEqual([j['state'] for j in follow_jobs(self.follows)], ['running'])

    def test_a_finished_follow_shows_autobuilders_answer(self):
        from pathlib import Path
        from server import follow_jobs
        self.alive = False
        self.follow('p')
        d = [x for x in Path(self.follows).iterdir() if x.is_dir()][0]
        (d / 'out.log').write_text('noise\nPROPOSE: followed 2 review(s)\n')
        self.assertEqual(follow_jobs(self.follows)[0]['state'], 'done')
        self.assertEqual(follow_jobs(self.follows)[0]['answer'], 'followed 2 review(s)')
        self.follow('p')
        empty = max(x for x in Path(self.follows).iterdir() if x.is_dir())
        (empty / 'out.log').write_text('PROPOSE: followed 0 review(s)\n')
        self.assertEqual([j['answer'] for j in follow_jobs(self.follows)], ['followed 2 review(s)'],
                         'a pass that carried out nothing is not listed')

    def test_append_review_asks_for_the_follow_of_the_reviewed_decisions_record(self):
        import json
        from pathlib import Path
        from server import append_review
        own = Path(self.data, '.claude-memory')
        own.mkdir()
        (own / 'decisions.ndjson').write_text(json.dumps({'id': 'T1-BA#1', 'at': '2026-11-22T01:00:00Z'}) + '\n')
        asked = []
        ok = append_review(json.dumps({'decision': 'T1-BA#1', 'assessment': 'AGREE', 'reason': 'r'}).encode(),
                           str(self.data), follow=lambda name: asked.append(name) or {'state': 'started'})
        self.assertTrue(ok['ok'])
        self.assertEqual(asked, ['autobuilder-roadmap'])
        self.assertEqual(ok['data']['follow'], {'state': 'started'})


class NudgeTest(unittest.TestCase):
    """M-H part 2: a nudge names known topics only, carries ids and the operator's own note, and counts as answered once
    a recommendation for the decision is written after it."""

    def test_nudges_are_appended_checked_and_answered_by_a_later_recommendation(self):
        import json, tempfile
        from pathlib import Path
        from server import append_nudge, nudges
        with tempfile.TemporaryDirectory() as root:
            own = Path(root, '.claude-memory'); own.mkdir()
            (own / 'decisions.ndjson').write_text(json.dumps({'id': 'T1-BA#1', 'at': '1'}) + '\n'
                                                  + json.dumps({'id': 'T1-BA#2', 'at': '2'}) + '\n')
            bad = append_nudge(json.dumps({'topics': [{'decision': 'T1-BA#1', 'project': 'autobuilder-roadmap'},
                                                      {'decision': 'nope', 'project': 'autobuilder-roadmap'}]}).encode(), root)
            self.assertFalse(bad['ok'])
            self.assertEqual(nudges(root), [], 'one unknown topic writes nothing')
            ok = append_nudge(json.dumps({'topics': [{'decision': 'T1-BA#1', 'project': 'autobuilder-roadmap'},
                                                     {'decision': 'T1-BA#2', 'project': 'autobuilder-roadmap'}],
                                          'note': 'is this one still true?'}).encode(), root)
            self.assertTrue(ok['ok'])
            got = nudges(root)
            self.assertEqual([(n['decision'], n['answered'], n['note'], n['by']) for n in got],
                             [('T1-BA#1', False, 'is this one still true?', 'operator'),
                              ('T1-BA#2', False, 'is this one still true?', 'operator')])
            rec = Path(root, 'process', 'reviews', 'recommendations.ndjson')
            rec.write_text(json.dumps({'decision': 'T1-BA#1', 'assessment': 'AGREE', 'reason': 'r',
                                       'at': '2000-01-01T00:00:00+00:00'}) + '\n')
            self.assertFalse(nudges(root)[0]['answered'], 'a recommendation from before the nudge does not answer it')
            with open(rec, 'a') as f:
                f.write(json.dumps({'decision': 'T1-BA#1', 'assessment': 'AGREE', 'reason': 'r',
                                    'at': '2999-01-01T00:00:00+00:00'}) + '\n')
            self.assertEqual([n['answered'] for n in nudges(root)], [True, False])


class FollowDetailTest(unittest.TestCase):
    """T6-X: a follow-up opened shows what it did -- the entry written while it ran, the review it carried out, the
    decision that review was about, and the roadmap line it added; an entry from outside its time is not its own."""

    def test_the_entries_written_while_it_ran_with_their_review_decision_and_roadmap_line(self):
        import os, tempfile, time
        from datetime import datetime, timezone
        from pathlib import Path
        from server import follow_detail
        with tempfile.TemporaryDirectory() as root:
            job = Path(root, 'job'); job.mkdir()
            (job / 'request.json').write_text('{}'); (job / 'out.log').write_text('PROPOSE: followed 1 review(s)\n')
            now = time.time()
            os.utime(job / 'request.json', (now, now)); os.utime(job / 'out.log', (now + 2, now + 2))
            iso = lambda t: datetime.fromtimestamp(t, timezone.utc).strftime('%Y-%m-%dT%H:%M:%S.%f') + '123Z'
            review = {'decision': 'T4-AA#2', 'assessment': 'AGREE', 'reason': 'Right: a duplicate', 'reviewer': 'op',
                      'at': '2026-11-25T18:20:35.99+00:00'}
            loaded = [
                {'project': 'autobuilder-roadmap', 'entry': {'id': 'T4-AA#2', 'kind': 'TICKET_CHANGED', 'on': 'T4-AA: x',
                 'decision': 'appended evidence', 'tickets': ['T4-AA']}, 'reviews': [review]},
                {'project': 'autobuilder-roadmap', 'entry': {'id': 'T4-AA#4', 'kind': 'TICKET_CHANGED', 'at': iso(now + 1),
                 'decision': 'appended the agree', 'tickets': ['T4-AA'],
                 'follows': 'review of T4-AA#2 at 2026-11-25T18:20:35.99+00:00'}, 'reviews': []},
                {'project': 'autobuilder-roadmap', 'entry': {'id': 'T4-AA#3', 'kind': 'TICKET_CHANGED', 'at': iso(now - 3600),
                 'tickets': ['T4-AA'], 'follows': 'review of T4-AA#1 at x'}, 'reviews': []}]
            roadmap = [{'id': 'T4-AA', 'body': ['  - an older note on `T4-AA#2`',
                                                '  - **reviewed: op agreed with decision `T4-AA#2`.** Right: a duplicate']}]
            got = follow_detail(job, loaded, roadmap)
        self.assertEqual([g['entry']['id'] for g in got], ['T4-AA#4'])
        self.assertEqual(got[0]['review']['assessment'], 'AGREE')
        self.assertEqual(got[0]['reviewed']['decision'], 'appended evidence')
        self.assertEqual(got[0]['roadmap'], ['- **reviewed: op agreed with decision `T4-AA#2`.** Right: a duplicate'])


class PageDrawsTest(unittest.TestCase):
    """2026-11-25: a helper inserted inside another function left 'Own decisions' unable to draw ('data unavailable')
    while every syntax check passed. The page is drawn here against a snapshot with a reviewed decision, a follow-up
    carrying out its review, a recommendation and a nudge; any view that throws fails the test."""

    def test_every_view_draws(self):
        import json, shutil, subprocess, tempfile
        from pathlib import Path
        from server import decisions_data, development_data
        if not shutil.which('node'):
            self.skipTest('node not installed')
        here = Path(__file__).resolve().parent
        with tempfile.TemporaryDirectory() as root:
            own = Path(root, '.claude-memory'); own.mkdir()
            (own / 'decisions.ndjson').write_text('\n'.join(json.dumps(x) for x in [
                {'id': 'T4-AA#1', 'at': '2026-11-22T18:16:22.399937083Z', 'kind': 'TICKET_CHANGED', 'on': 'T4-AA: x',
                 'tickets': ['T4-AA'], 'decision': "appended the run's evidence to T4-AA", 'why': 'w',
                 'read': [{'source': 'a', 'line': 1, 'text': 't', 'holds': True}]},
                {'id': 'T4-AA#2', 'at': '2026-11-25T18:20:38.042255310Z', 'kind': 'TICKET_CHANGED', 'on': 'T4-AA',
                 'tickets': ['T4-AA'], 'decision': "appended the review by op's disagree to T4-AA", 'why': 'r',
                 'follows': 'review of T4-AA#1 at 2026-11-25T18:20:35+00:00'},
                {'id': 'T4-AA#3', 'at': '2026-11-25T19:00:00Z', 'kind': 'TICKET_CREATED', 'on': 'T4-AB: y',
                 'tickets': ['T4-AB'], 'decision': 'created T4-AB', 'why': 'w'}]) + '\n')
            (own / 'decision-reviews.ndjson').write_text(json.dumps(
                {'decision': 'T4-AA#1', 'assessment': 'DISAGREE', 'reason': 'r', 'reviewer': 'op',
                 'at': '2026-11-25T18:20:35+00:00'}) + '\n')
            (own / 'roadmap.md').write_text('## Tier 4\n- [ ] **T4-AA: x** body\n')
            snap = {'decisions': decisions_data(root), 'development': development_data(own / 'roadmap.md'),
                    'findings': {'ok': True, 'data': []},
                    'follows': {'ok': True, 'data': {'jobs': [], 'queued': ['autobuilder-roadmap']}},
                    'nudgeWatch': {'ok': True, 'data': {'watching': True, 'since': 1}},
                    'nudges': {'ok': True, 'data': [{'decision': 'T4-AA#3', 'project': 'autobuilder-roadmap',
                                                     'at': '2026-11-25T19:01:00+00:00', 'answered': False}]},
                    'meta': {'ok': False, 'error': 'not read in this test'}}
            snap['decisions']['data']['decisions'][0]['recommendation'] = {
                'assessment': 'AGREE', 'reason': 'r', 'by': 'a session', 'at': '2026-11-25T17:00:00+00:00'}
            path = Path(root, 'snap.json'); path.write_text(json.dumps(snap))
            out = subprocess.run(['node', str(here / 'check_page.js'), str(here / 'dist' / 'live.js'), str(path)],
                                 capture_output=True, text=True, timeout=60)
        self.assertEqual(out.returncode, 0, out.stdout + out.stderr)
        self.assertNotIn('ERROR', out.stdout)


class NudgeWatchTest(unittest.TestCase):
    """M-H: one watcher reports at a time; a heartbeat older than FRESH or of a dead process means nobody is watching;
    the SessionStart hook lists the waiting nudges and says to start the watch."""

    def test_one_watcher_at_a_time_and_a_stale_or_dead_one_is_taken_over(self):
        import os, tempfile, time
        from server import nudge_script as ns
        with tempfile.TemporaryDirectory() as root:
            hb = os.path.join(root, 'w.json')
            now = time.time()
            self.assertIsNone(ns.watcher(hb, now))
            self.assertTrue(ns.beat(hb, pid=os.getpid(), now=now))
            self.assertEqual(ns.watcher(hb, now)['pid'], os.getpid())
            self.assertFalse(ns.beat(hb, pid=os.getppid(), now=now + 5), 'a second watcher waits while the first beats')
            self.assertTrue(ns.beat(hb, pid=os.getppid(), now=now + ns.FRESH + 1), 'a stale heartbeat is taken over')
            with open(hb, 'w') as f:
                f.write('{"pid": 999999999, "at": %f}' % now)
            self.assertIsNone(ns.watcher(hb, now), 'a dead process is not watching')

    def test_the_session_start_hook_lists_waiting_nudges_and_says_to_watch(self):
        import json, tempfile
        from pathlib import Path
        from server import nudge_script as ns
        with tempfile.TemporaryDirectory() as root:
            d = Path(root, 'process', 'reviews'); d.mkdir(parents=True)
            (d / 'nudges.ndjson').write_text(json.dumps({'decision': 'T1-AA#1', 'project': 'autobuilder-roadmap',
                                                         'at': '2026-11-25T19:00:00+00:00', 'note': 'look at this'}) + '\n')
            ctx = ns.session_start(root)['hookSpecificOutput']
        self.assertEqual(ctx['hookEventName'], 'SessionStart')
        self.assertIn('NUDGE T1-AA#1 in autobuilder-roadmap', ctx['additionalContext'])
        self.assertIn('the operator\'s note: "look at this"', ctx['additionalContext'])
        self.assertIn('python3 scripts/nudges.py --wait', ctx['additionalContext'])
