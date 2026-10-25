"""Adversarial source visibility checks, independent of dataset case labels."""
import json
import tempfile
import unittest

import helpers  # noqa: F401
from phase4_world import EMAIL
from test_phase4_casefile import setup_case


class ExactVisibility(unittest.TestCase):
    def test_a_complete_line_that_fits_the_response_budget_can_be_cited(self):
        line = f'fx_attach_long {EMAIL} present ' + 'x' * 400
        with tempfile.TemporaryDirectory() as tmp:
            s = setup_case(tmp, files={'evidence/logs/long.log': (
                'fx_file_LONG', '2026-03-01T10:00:00Z', 'support_platform', (line + '\n').encode())})
            result = s['tools'].call('inspect_source', {'source_id': 'fx_file_LONG', 'max_lines': 1})
            self.assertEqual(result['lines'][0]['text'], line)
            fact, reason = s['validator'].validate({
                'kind': 'OWNERSHIP', 'system_id': 'support_platform', 'resource_id': 'fx_attach_long',
                'source_id': 'fx_file_LONG', 'locator': 'L1', 'literal': line, 'value': ''}, s['case'])
            self.assertIsNotNone(fact, reason)

    def test_repeated_clipped_lines_do_not_register_unseen_locators(self):
        line = f'fx_attach_dup {EMAIL} present ' + 'x' * 180
        with tempfile.TemporaryDirectory() as tmp:
            s = setup_case(tmp, files={'evidence/logs/dup.log': (
                'fx_file_DUP', '2026-03-01T10:00:00Z', 'support_platform', ((line + '\n') * 40).encode())})
            result = s['tools'].call('inspect_source', {'source_id': 'fx_file_DUP', 'max_lines': 40})
            self.assertTrue(result['truncated'])
            self.assertIn('L1', s['case'].shown['fx_file_DUP'])
            self.assertNotIn('L40', s['case'].shown['fx_file_DUP'])
            fact, _ = s['validator'].validate({
                'kind': 'OWNERSHIP', 'system_id': 'support_platform', 'resource_id': 'fx_attach_dup',
                'source_id': 'fx_file_DUP', 'locator': 'L40', 'literal': line, 'value': ''}, s['case'])
            self.assertIsNone(fact)

    def test_sql_decoded_rows_cannot_authorize_unseen_raw_statements(self):
        inserts = ' '.join(f"INSERT INTO t VALUES ('fx_row_{n}','{EMAIL}');" for n in range(1, 22))
        sql = 'BEGIN TRANSACTION;\nCREATE TABLE t (resource_id TEXT, email TEXT);\n' + inserts + '\nCOMMIT;\n'
        with tempfile.TemporaryDirectory() as tmp:
            s = setup_case(tmp, files={'evidence/databases/rows.sql': (
                'fx_file_ROWS', '2026-03-01T10:00:00Z', 'support_platform', sql.encode())})
            s['case'].handles.add('fx_row_21')
            result = s['tools'].call('query_sql_snapshot', {
                'file_id': 'fx_file_ROWS', 'table': 't', 'column': 'email', 'value': EMAIL})
            self.assertEqual(result['returned'], 20)
            self.assertNotIn('fx_row_21', json.dumps(result))
            proposal = {'kind': 'OWNERSHIP', 'system_id': 'support_platform', 'resource_id': 'fx_row_21',
                        'source_id': 'fx_file_ROWS', 'locator': 'L3',
                        'literal': f"'fx_row_21','{EMAIL}'", 'value': ''}
            fact, _ = s['validator'].validate(proposal, s['case'])
            self.assertIsNone(fact)
            # Explicitly inspecting the full raw line makes that same real evidence citable.
            shown = s['tools'].call('inspect_source', {'source_id': 'fx_file_ROWS', 'start_line': 3, 'max_lines': 1})
            self.assertIn('fx_row_21', shown['lines'][0]['text'])
            fact, reason = s['validator'].validate(proposal, s['case'])
            self.assertIsNotNone(fact, reason)

    def test_sql_locator_does_not_match_a_resource_id_prefix(self):
        sql = ('BEGIN TRANSACTION;\nCREATE TABLE t (resource_id TEXT, email TEXT);\n'
               f"INSERT INTO t VALUES ('fx_row_10','{EMAIL}');\n"
               f"INSERT INTO t VALUES ('fx_row_1','{EMAIL}');\nCOMMIT;\n")
        with tempfile.TemporaryDirectory() as tmp:
            s = setup_case(tmp, files={'evidence/databases/rows.sql': (
                'fx_file_ROWS', '2026-03-01T10:00:00Z', 'support_platform', sql.encode())})
            result = s['tools'].call('query_sql_snapshot', {
                'file_id': 'fx_file_ROWS', 'table': 't', 'column': 'email', 'value': EMAIL})
            row = next(r for r in result['items'] if r['resource_id'] == 'fx_row_1')
            self.assertEqual(row['locator'], 'L4')
            self.assertIn('fx_row_1', s['case'].handles)
            s['tools'].call('inspect_source', {'source_id': 'fx_file_ROWS', 'start_line': 4, 'max_lines': 1})
            fact, reason = s['validator'].validate({
                'kind': 'OWNERSHIP', 'system_id': 'support_platform', 'resource_id': 'fx_row_1',
                'source_id': 'fx_file_ROWS', 'locator': 'L4', 'literal': f"'fx_row_1','{EMAIL}'", 'value': ''},
                s['case'])
            self.assertIsNotNone(fact, reason)
