"""End-to-end checks of the documented commands, run as subprocesses."""
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

import helpers

HAS_DATASET = (helpers.DATASET / 'evidence_files.csv').exists()


def run(*args, cwd=None):
    return subprocess.run([sys.executable, *map(str, args)], capture_output=True, text=True,
                          cwd=cwd or tempfile.gettempdir())  # not the repo root: paths must not depend on cwd


class CliTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.out = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def test_fixture_generate_validate_and_score(self):
        r = run(helpers.CODE / 'evaluation/main.py', 'fixtures', '--out', self.out)
        self.assertEqual(r.returncode, 0, r.stderr)
        base = self.out / 'fixture_a'
        req = base / 'input/deletion_requests.csv'
        r = run(helpers.CODE / 'main.py', 'validate', '--dataset', base / 'input',
                '--requests', req, '--predictions', base / 'expected_output.csv')
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn('VALID: 1 rows', r.stdout)
        r = run(helpers.CODE / 'evaluation/main.py', 'score', '--requests', req, '--dataset', base / 'input',
                '--predictions', base / 'expected_output.csv', '--expected', base / 'expected_output.csv',
                '--out-dir', self.out / 'score')
        self.assertEqual(r.returncode, 0, r.stderr)
        metrics = json.loads((self.out / 'score/metrics.json').read_text())
        self.assertEqual(metrics['exact_row_accuracy']['rate'], 1.0)
        self.assertTrue((self.out / 'score/metrics.md').exists())

    def test_validate_rejects_corrupt_predictions_with_exit_1(self):
        run(helpers.CODE / 'evaluation/main.py', 'fixtures', '--out', self.out, '--only', 'fixture_a')
        base = self.out / 'fixture_a'
        bad = self.out / 'bad.csv'
        bad.write_text((base / 'expected_output.csv').read_text().replace('fx_file_A', 'fx_file_Z'))
        r = run(helpers.CODE / 'main.py', 'validate', '--dataset', base / 'input',
                '--requests', base / 'input/deletion_requests.csv', '--predictions', bad)
        self.assertEqual(r.returncode, 1)
        self.assertIn("unknown evidence_id 'fx_file_Z'", r.stderr)

    @unittest.skipUnless(HAS_DATASET, 'supplied dataset not present')
    def test_inventory_and_sample_validation_on_supplied_dataset(self):
        r = run(helpers.CODE / 'main.py', 'inventory', '--out', self.out / 'inv.json')
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(json.loads(r.stdout)['requests'], {'deletion_requests.csv': 518,
                                                           'sample_deletion_requests.csv': 32})
        r = run(helpers.CODE / 'main.py', 'validate', '--requests', helpers.DATASET / 'sample_deletion_requests.csv',
                '--predictions', helpers.DATASET / 'sample_output.csv')
        self.assertEqual(r.returncode, 0, r.stderr)
        r = run(helpers.CODE / 'main.py', 'validate', '--requests', helpers.DATASET / 'deletion_requests.csv',
                '--predictions', helpers.DATASET / 'output.csv')
        self.assertEqual(r.returncode, 1)  # the blank template has no rows
        self.assertIn('518 case(s) missing', r.stderr)


if __name__ == '__main__':
    unittest.main()
