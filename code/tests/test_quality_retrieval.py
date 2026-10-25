"""Coverage under crowded evidence search, with no promotion of hidden text."""
import tempfile
import unittest

import helpers  # noqa: F401
from phase4_world import EMAIL
from test_phase4_casefile import setup_case
from ghostaudit.tools import ToolError


class DiverseSearch(unittest.TestCase):
    def setup(self, tmp):
        lines = '\n'.join(f'fx_duplicate_{n} {EMAIL} present' for n in range(40))
        return setup_case(tmp, files={'evidence/logs/crowded.log': (
            'fx_file_A', '2026-03-01T10:00:00Z', 'support_platform', lines.encode())})

    def test_repeated_hits_do_not_hide_the_document_source(self):
        with tempfile.TemporaryDirectory() as tmp:
            s = self.setup(tmp)
            result = s['tools'].call('search_identifiers', {'values': [EMAIL]})
            self.assertIn('fx_file_PDF', {m['source_id'] for m in result['matches']})
            self.assertTrue(result['truncated'])

    def test_pagination_retrieves_every_match_once_without_granting_hidden_handles(self):
        with tempfile.TemporaryDirectory() as tmp:
            s = self.setup(tmp)
            seen, offset = set(), 0
            while True:
                result = s['tools'].call('search_identifiers', {'values': [EMAIL], 'start_match': offset})
                keys = {(m['source_id'], m['locator']) for m in result['matches']}
                self.assertFalse(seen & keys)
                seen |= keys
                if result['next_match'] is None:
                    break
                self.assertGreater(result['next_match'], offset)
                if offset == 0:
                    self.assertNotIn('fx_duplicate_39', s['case'].handles)
                offset = result['next_match']
            self.assertEqual(len(seen), result['total_matches'])
            self.assertIn('fx_duplicate_39', s['case'].handles)

    def test_invalid_page_offset_is_refused(self):
        with tempfile.TemporaryDirectory() as tmp:
            s = self.setup(tmp)
            with self.assertRaises(ToolError):
                s['tools'].call('search_identifiers', {'values': [EMAIL], 'start_match': -1})

    def test_response_clipping_does_not_skip_matches_between_pages(self):
        with tempfile.TemporaryDirectory() as tmp:
            long = '\n'.join(f'fx_row_{n} {EMAIL} ' + '"' * 180 for n in range(40))
            s = setup_case(tmp, files={'evidence/logs/long.log': (
                'fx_file_LONG', '2026-03-01T10:00:00Z', 'support_platform', long.encode())})
            seen, offset = set(), 0
            while True:
                result = s['tools'].call('search_identifiers', {'values': [EMAIL], 'start_match': offset})
                keys = {(m['source_id'], m['locator']) for m in result['matches']}
                self.assertFalse(seen & keys)
                seen |= keys
                if offset == 0:
                    self.assertLess(len(keys), 25)
                if result['next_match'] is None:
                    break
                self.assertEqual(result['next_match'], offset + len(keys))
                offset = result['next_match']
            self.assertEqual(len(seen), result['total_matches'])
