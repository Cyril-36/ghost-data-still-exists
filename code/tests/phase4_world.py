"""A small synthetic world for Phase 4 tests (fx_ identifiers, example.test emails)."""
import csv
from pathlib import Path

import helpers  # noqa: F401
from evaluation.fixtures import HEADERS, _identity, _lookups, _manifest, _request, _rules
from test_media import make_pdf

EMAIL, OTHER = 'person.p@example.test', 'person.q@example.test'
T_CAPTURE, T_AUDIT = '2026-03-01T10:00:00Z', '2026-03-02T12:00:00Z'
PDF_LINES = [['SUPPORT CASE CS-1 / fx_attach_P', 'Detail  Fixture Person P', f'Detail  {EMAIL}',
              'Detail  uploaded attachment identity_scan.pdf stored for ticket CS-1'],
             ['Continued - Attachment properties', 'Detail  checksum fx']]


def world(tmp, *, requests=None, lookup_rows=(), files=None, activities=(), extra_identities=()):
    """Write a dataset directory; returns its path. `files`: {relative path: (file_id, captured_at, origin, bytes)}."""
    root = Path(tmp) / 'input'
    rows = [('2026-03-01T09:00:00Z', 'account_service', 'fx_account_P', 'USER_PROFILE', 'FOUND', EMAIL)]
    rows += list(lookup_rows)
    files = {'evidence/records/lookups.json': ('fx_file_L', T_CAPTURE, 'fixture', _lookups(T_CAPTURE, *rows)),
             'evidence/documents/attachment.pdf': ('fx_file_PDF', T_CAPTURE, 'support_platform', make_pdf(PDF_LINES)),
             **(files or {})}
    tables = {
        'deletion_requests.csv': requests or [_request('fx_case_P', EMAIL, T_AUDIT,
                                                       text='Please delete my account and everything you hold.')],
        'user_identities.csv': [_identity('fx_user_P', 'Fixture Person P', EMAIL, '+12025550151'),
                                _identity('fx_user_Q', 'Fixture Person Q', OTHER, '+12025550152'), *extra_identities],
        'deletion_rules.csv': _rules(),
        'evidence_files.csv': [_manifest(fid, rel, at, origin) for rel, (fid, at, origin, _) in files.items()],
        'system_activity.csv': [dict(zip(HEADERS['system_activity.csv'], a)) for a in activities],
    }
    for name, header in HEADERS.items():
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, 'w', encoding='utf-8', newline='') as f:
            w = csv.DictWriter(f, fieldnames=header, lineterminator='\n')
            w.writeheader()
            w.writerows(tables.get(name, []))
    for rel, (_, _, _, content) in files.items():
        (root / rel).parent.mkdir(parents=True, exist_ok=True)
        (root / rel).write_bytes(content)
    return root
