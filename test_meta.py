import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from meta import collect, document


class MetaEvidenceTest(unittest.TestCase):
    def test_test_reports_are_counted_and_corrupt_reports_disclosed(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            reports = root / 'target/surefire-reports'
            reports.mkdir(parents=True)
            (reports / 'TEST-good.xml').write_text('<testsuite name="Example" tests="5" failures="1" errors="0" skipped="2"/>')
            (reports / 'TEST-bad.xml').write_text('broken')
            with patch('meta.git', return_value=''):
                value = collect(root)
            self.assertEqual(value['tests']['totals'], {'tests': 5, 'failures': 1, 'errors': 0, 'skipped': 2})
            self.assertEqual(len(value['tests']['errors']), 1)

    def test_document_access_is_limited_to_development_records(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            (root / 'process').mkdir()
            (root / 'process/README.md').write_text('# Development review\nEvidence')
            self.assertTrue(document('process/README.md', root)['ok'])
            self.assertFalse(document('../secret', root)['ok'])
            self.assertFalse(document('.env', root)['ok'])

    def test_git_failure_does_not_hide_other_sources(self):
        with tempfile.TemporaryDirectory() as folder, patch('meta.git', side_effect=OSError('unavailable')):
            value = collect(Path(folder))
            self.assertFalse(value['git']['ok'])
            self.assertIn('roles', value)
            self.assertIn('tests', value)
