import unittest
from dataclasses import replace

from helpers import complete_row, incomplete_row, not_applicable_row
from evaluation.scoring import render_markdown, score

GOLD = [incomplete_row('case_33'), complete_row('case_34'), not_applicable_row('case_100')]


def metric(result, name):
    m = result[name]
    return m['numerator'], m['denominator']


class ScoringTests(unittest.TestCase):
    def test_perfect_predictions(self):
        r = score(GOLD, GOLD)
        for name in ('status_accuracy', 'sequence_accuracy', 'exact_row_accuracy', 'step_precision',
                     'step_recall', 'step_f1', 'field_accuracy_positional'):
            self.assertEqual(r[name]['rate'], 1.0, name)
        self.assertEqual(metric(r, 'step_recall'), (5, 5))
        self.assertEqual(r['diffs'], [])

    def test_missing_steps_count_against_recall_and_fields(self):
        pred = [replace(GOLD[0], steps=GOLD[0].steps[:2])] + GOLD[1:]
        r = score(pred, GOLD)
        self.assertEqual(metric(r, 'status_accuracy'), (3, 3))
        self.assertEqual(metric(r, 'exact_row_accuracy'), (2, 3))
        self.assertEqual(metric(r, 'step_precision'), (3, 3))
        self.assertEqual(metric(r, 'step_recall'), (3, 5))
        self.assertEqual(metric(r, 'field_accuracy_positional'), (18, 30))
        self.assertEqual(r['field_errors'], {'unmatched_step': 12})
        self.assertEqual(r['diffs'][0]['first_divergent_step_no'], 3)

    def test_reordering_breaks_sequence_but_not_step_sets(self):
        steps = GOLD[0].steps
        pred = [replace(GOLD[0], steps=steps[:2] + (steps[3], steps[2]))] + GOLD[1:]
        r = score(pred, GOLD)
        self.assertEqual(metric(r, 'sequence_accuracy'), (2, 3))
        self.assertEqual(r['step_f1']['rate'], 1.0)
        self.assertEqual(r['field_errors'], {'resource_id': 2, 'operation': 2, 'state': 2})

    def test_wrong_status_and_evidence(self):
        wrong_ev = replace(GOLD[1], steps=(replace(GOLD[1].steps[0], evidence_id='other'),))
        pred = [GOLD[0], wrong_ev, replace(GOLD[2], deletion_status='COMPLETE')]
        r = score(pred, GOLD)
        self.assertEqual(metric(r, 'status_accuracy'), (2, 3))
        self.assertEqual(metric(r, 'step_precision'), (4, 5))
        self.assertEqual(r['field_errors'], {'evidence_id': 1})
        self.assertEqual({(c['expected'], c['predicted'], c['count']) for c in r['confusion']},
                         {('INCOMPLETE', 'INCOMPLETE', 1), ('COMPLETE', 'COMPLETE', 1),
                          ('NOT_APPLICABLE', 'COMPLETE', 1)})

    def test_empty_prediction_arrays_have_undefined_precision(self):
        pred = [replace(r, deletion_status='NOT_APPLICABLE', steps=()) for r in GOLD]
        r = score(pred, GOLD)
        self.assertIsNone(r['step_precision']['rate'])
        self.assertEqual(metric(r, 'step_recall'), (0, 5))

    def test_case_set_mismatch_raises(self):
        with self.assertRaises(ValueError):
            score(GOLD[:2], GOLD)
        with self.assertRaises(ValueError):
            score(GOLD + [GOLD[0]], GOLD)

    def test_markdown_shows_numerators_and_denominators(self):
        text = render_markdown(score(GOLD, GOLD), 'Fixture score')
        self.assertIn('| status_accuracy | 3/3 = 1.0000 |', text)
        self.assertIn('## Mismatched cases (0)', text)


if __name__ == '__main__':
    unittest.main()
