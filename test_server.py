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
