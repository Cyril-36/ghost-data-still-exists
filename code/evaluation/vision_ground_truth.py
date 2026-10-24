"""Manually checked literal fields for the 11 supplied screenshots (read by a person, not a model).

Per file: resource IDs visible on screen and the email/phone identifiers shown with them.
Used only to score the blind vision reader's literal extraction (IDs and identifiers);
it says nothing about deletion outcomes. Values were transcribed before scoring the reader.
"""
from __future__ import annotations

import re

GROUND_TRUTH = {
    'evidence_04': {'ids': {'account_5762', 'support_9538'},
                    'identifiers': {'haruka.s1838@zoho.com', '+81 90 7610 2507'}},
    'evidence_09': {'ids': {'search_428', 'search_1874'},
                    'identifiers': {'g.silva76@gmail.com', 'g.silva76.jr@gmail.com'}},
    'evidence_14': {'ids': {'data_flow_1121', 'data_flow_9846'},
                    'identifiers': {'jain.aditi@gmail.com', 'lucia-torres@gmail.com'}},
    'evidence_19': {'ids': {'analytics_7438'}, 'identifiers': {'darwish.omar@icloud.com'}},
    'evidence_23': {'ids': {'export_1258', 'export_1259', 'export_1260', 'export_1261'},
                    'identifiers': {'marcus.lee@northstar.shop', 'ingrid.dahl@outlook.com',
                                    'sara.kim@northstar.shop', 'daniel.park@northstar.shop'}},
    'evidence_28': {'ids': {'support_1034'}, 'identifiers': {'kagyeman8947@fastmail.com'}},
    'evidence_32': {'ids': {'backup_9286', 'backup_9287', 'backup_9288', 'backup_9291'},
                    'identifiers': {'bluepeakco@outlook.com', 'miller.logistics@company.com',
                                    'areed1433@gmail.com', 'summit-retail@company.com'}},
    'evidence_37': {'ids': {f'audit_record_00{i}' for i in range(1, 7)}, 'identifiers': set()},
    'evidence_42': {'ids': set(), 'identifiers': set()},          # only a user key; no record ID shown
    'evidence_46': {'ids': set(), 'identifiers': set()},          # file names only
    'evidence_60': {'ids': {'marketing_001'}, 'identifiers': {'sj.park@naver.com', '+82 10-5837-9451'}},
}
_ID = re.compile(r'^[a-z]+(?:_[a-z]+)*_\d+$')
_IDENT = re.compile(r'[\w.+-]+@[\w-]+(?:\.[\w-]+)+|\+\d[\d\s().-]{7,}\d')


def score_vision(extractions: dict) -> dict:
    """Literal ID/identifier precision and recall of the reader against the manual reading."""
    tp_ids = fp_ids = fn_ids = tp_idf = fp_idf = fn_idf = 0
    per_file = {}
    for fid, truth in GROUND_TRUTH.items():
        e = extractions.get(fid) or {}
        entities = e.get('entities') or {}
        # Raw reader IDs (before catalog-label resolution) that look like record IDs.
        got_ids = {rid for rid in entities if _ID.match(rid) and not rid.startswith('user_')}
        got_ids |= {v.split('·')[-1].strip() for f in entities.values() for v in map(str, f.values())
                    if _ID.match(v.split('·')[-1].strip()) and not v.split('·')[-1].strip().startswith(('user_', 'tombstone_'))}
        got_idf = {m for f in entities.values() for v in map(str, f.values()) for m in _IDENT.findall(v)}
        resolved = {rid for rid in entities if rid.startswith('tombstone_')}
        got_ids -= resolved
        tp_ids += len(got_ids & truth['ids'])
        fp_ids += len(got_ids - truth['ids'])
        fn_ids += len(truth['ids'] - got_ids)
        tp_idf += len(got_idf & truth['identifiers'])
        fp_idf += len(got_idf - truth['identifiers'])
        fn_idf += len(truth['identifiers'] - got_idf)
        per_file[fid] = {'missing_ids': sorted(truth['ids'] - got_ids), 'extra_ids': sorted(got_ids - truth['ids']),
                         'missing_identifiers': sorted(truth['identifiers'] - got_idf),
                         'extra_identifiers': sorted(got_idf - truth['identifiers'])}
    return {'id_precision': f'{tp_ids}/{tp_ids + fp_ids}', 'id_recall': f'{tp_ids}/{tp_ids + fn_ids}',
            'identifier_precision': f'{tp_idf}/{tp_idf + fp_idf}', 'identifier_recall': f'{tp_idf}/{tp_idf + fn_idf}',
            'per_file': per_file}
