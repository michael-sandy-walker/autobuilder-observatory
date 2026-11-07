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


if __name__ == '__main__':
    unittest.main()
