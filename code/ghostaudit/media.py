"""PDF and screenshot interpretation -> validated observations.

PDFs: native text extraction (deterministic, offline) -> literal harvest -> ONE bounded
Jev choice for the document kind -> deterministic mapping. Without a gateway the text
is extracted but no state is derived (kind unknown). Identifiers and retention dates
belong to the record whose mention precedes them on the same page; a section that
names two people binds no identity at all.

Screenshots: one blind vision call per image (no case, subject or expected answer in the
prompt) -> strict JSON -> deterministic validation and mapping. A row yields:
  - identity tokens for its own record (ownership evidence), and
  - PRESENT state (+ visibly exposed identifier fields) only when the record's system is
    consistent with the page and with any structured evidence for the same ID.
No enabled/restore flags are inferred from badges such as "Healthy" or "Continuous".
The model never writes steps, statuses, evidence IDs or resource IDs that are not
literally present (malformed IDs are rejected; unreadable values are reported).
"""
from __future__ import annotations

import base64
import hashlib
import json
import re
from dataclasses import dataclass, field

from .intent_models import JEV_MODEL, PROMPTS, _strict_json, choice_problem
from .observations import PRESENT, STATE, Observation, SourceReport, classify_token, tokens_from
from .parsers import sniff
from .parsers.pdfsrc import PdfError, extract_pdf_text, harvest
from .providers import Gateway, ProviderError, fingerprint

VISION_MODEL = 'google/gemini-2.5-flash'
RESOURCE_ID = re.compile(r'^[a-z]+(?:_[a-z]+)*_\d+$')
PAGE_KINDS = {
    'ACCOUNT_PROFILE': ('account_service', 'USER_PROFILE'),
    'SEARCH_RESULTS': ('search_service', 'SEARCH_INDEX_ENTRY'),
    'DATA_FLOWS': ('integration_service', 'DATA_FLOW'),
    'ANALYTICS_PROFILE': ('analytics_platform', 'ANALYTICS_PROFILE'),
    'DATA_EXPORTS': ('export_service', 'USER_EXPORT'),
    'SUPPORT_TICKET': ('support_platform', 'SUPPORT_MESSAGE'),
    'BACKUP_ARCHIVES': ('backup_service', 'BACKUP_ARCHIVE'),
    'SECURITY_EVENTS': ('security_service', 'SECURITY_AUDIT_LOG'),
    'CUSTOMER_REFERENCE': ('identity_service', 'ACCOUNT_TOMBSTONE'),
    'MARKETING_PROFILE': (None, 'MARKETING_PROFILE'),
    'OBJECT_STORAGE': (None, None),
    'OTHER': (None, None),
}
EXPOSURE_CATEGORIES = {'ANALYTICS_PROFILE', 'SUPPORT_MESSAGE', 'ACCOUNT_TOMBSTONE', 'EMAIL_DELIVERY_LOG'}
DOC_KINDS = {'SUPPORT_ATTACHMENT': 'SUPPORT_ATTACHMENT', 'SECURITY_AUDIT_RECORD': 'SECURITY_AUDIT_LOG'}


@dataclass
class MediaResult:
    observations: list = field(default_factory=list)
    reports: dict = field(default_factory=dict)      # source_id -> SourceReport
    extractions: dict = field(default_factory=dict)  # source_id -> raw validated extraction (diagnostics)


# Enrichment from structured evidence (system, category, catalog label). A media observation
# is available only from the earliest time at which the structured evidence it relies on
# already gave the same answer, so a later source never makes an earlier capture look
# enriched before a cutoff.

def _settled_at(entry, used, decide):
    """Earliest availability, not before the capture, at which `used` already yields decide(used)."""
    final = decide(used)
    for t in sorted({entry.captured_at} | {o.available_at for o in used if o.available_at > entry.captured_at}):
        if decide([o for o in used if o.available_at <= t]) == final:
            return t


def _systems(observations):
    """Systems that state or catalog evidence places a record in. Operation claims are
    ignored: a claim's target is where work was attempted, not which system owns the record."""
    return {o.system_id for o in observations if o.system_id and o.kind in (STATE, 'LINK')}


def _catalog_links(corpus, label):
    """Catalog links carrying exactly this label, e.g. 'tombstone_x (user_x)'."""
    return [o for o in corpus.observations if o.kind == 'LINK' and o.resource_id and f'({label})' in o.excerpt]


def _unique_resource(links):
    hits = {o.resource_id for o in links}
    return hits.pop() if len(hits) == 1 else None


# --- PDFs -----------------------------------------------------------------------------------------

def _position(locator):
    _, page, _, line = locator.split()          # 'page P line L'
    return int(page), int(line)


def _owner_of(facts, record_ids):
    """Locator -> the record whose section it is in, or None when that is not established."""
    mentions = sorted((_position(loc), v) for v, loc in facts['resource_ids'] if v in record_ids)

    def owner(locator):
        here = _position(locator)
        before = [(pos, rid) for pos, rid in mentions if pos[0] == here[0] and pos <= here]
        if before:
            nearest = {rid for pos, rid in before if pos == before[-1][0]}
            return nearest.pop() if len(nearest) == 1 else None
        return record_ids[0] if len(record_ids) == 1 else None
    return owner


def _identity(values, report, what):
    """Identity tokens for one record section, or none when it could name more than one person:
    more than one user reference, email or phone (the text gives no link between them)."""
    tokens = tokens_from(*values)
    if any(len({t for t in tokens if t[0] == kind}) > 1 for kind in ('user', 'email', 'phone')):
        report.issues.append(f'NOTE: {what}: several people named; identity not attached')
        return frozenset()
    return tokens

def _doc_kind(gateway, text):
    spec = json.loads((PROMPTS / 'document_kind_jev_v1.json').read_text(encoding='utf-8'))
    version = f'{spec["version"]}:{fingerprint(spec)[:12]}'
    questions = spec['questions']

    payload = gateway.decide('document_kind', JEV_MODEL, spec['state_template'].format(text=text[:6000]),
                             questions, version, lambda p: choice_problem(p, 'kind', questions['kind']['criteria']))
    a = payload['answers']['kind']
    return a['choice'], float(a['probabilities'][a['choice']]), payload.get('model')


def interpret_pdf(entry, raw, corpus, gateway) -> tuple[list, SourceReport, dict]:
    report = SourceReport(entry.file_id, entry.file_path, 'pdf')
    try:
        pages = extract_pdf_text(raw)
    except (PdfError, ValueError, KeyError) as exc:
        report.status = 'UNSUPPORTED_MEDIA'
        report.issues.append(f'UNREADABLE_MEDIA: native text extraction failed ({exc}); needs OCR/vision')
        return [], report, {}
    facts = harvest(pages)
    text = '\n'.join('\n'.join(p) for p in pages)
    extraction = {'pages': len(pages), 'lines': sum(len(p) for p in pages), 'facts': {
        k: [str(v[0]) for v in vals] for k, vals in facts.items()}}
    if gateway is None:
        report.status = 'TEXT_EXTRACTED'
        report.issues.append('NOTE: text extracted; document kind not classified offline, so no state derived')
        return [], report, extraction
    try:
        kind, p, model = _doc_kind(gateway, text)
    except ProviderError as exc:
        report.status = 'TEXT_EXTRACTED'
        report.issues.append(f'PROVIDER_FAILURE: document kind: {exc}')
        return [], report, extraction
    extraction['kind'] = {'choice': kind, 'p': p, 'model': model}
    identity_facts = [*facts['emails'], *facts['phones'], *((v, loc) for v, loc in facts['resource_ids']
                                                            if classify_token(v))]
    record_ids = sorted({v for v, _ in facts['resource_ids'] if not classify_token(v)})
    owner = _owner_of(facts, record_ids)
    observations = []

    def obs(rid, system, category, locator, tokens, available_at, **kw):
        observations.append(Observation(
            source_id=entry.file_id, available_at=available_at, observed_at=entry.captured_at,
            locator=locator, excerpt=f'{kind}: {rid} ({locator})', kind=STATE, proof='DOCUMENT_TEXT',
            system_id=system, resource_id=rid, category=category, presence=PRESENT, tokens=tokens,
            attributes=(('document_kind', kind), ('kind_p', p), ('model', model)), **kw))

    if p < 0.6:
        report.issues.append(f'NOTE: document kind {kind} below probability threshold ({p:.2f}); no state derived')
    elif kind in DOC_KINDS:
        category = DOC_KINDS[kind]
        def system_of(observations):
            systems = _systems(observations)
            return next(iter(systems)) if len(systems) == 1 else (
                entry.origin if entry.origin in corpus.system_categories and not systems else None)
        for rid in record_ids:
            known = corpus.by_resource.get(rid, [])
            system = system_of(known)
            if system is None:
                report.issues.append(f'NOTE: {rid}: system not established; no state derived')
                continue
            locator = next(loc for v, loc in facts['resource_ids'] if v == rid)
            tokens = _identity([v for v, loc in identity_facts if owner(loc) == rid], report, rid)
            retention = sorted({d for d, loc in facts['retain_until'] if owner(loc) == rid})
            extra = {'retention_until': retention[0]} if category == 'SECURITY_AUDIT_LOG' and len(retention) == 1 else {}
            obs(rid, system, category, locator, tokens, _settled_at(entry, known, system_of), **extra)
    elif kind == 'CUSTOMER_STATEMENT_OR_EXPORT':
        # A document is the content of a catalogued resource only through an exact catalog label.
        labels = {v for v, _ in facts['labels']}
        tokens = _identity([v for v, _ in identity_facts], report, 'statement')
        for link in (o for o in corpus.observations if o.kind == 'LINK' and o.resource_id):
            m = re.search(r'\(([^)]+)\)', link.excerpt)
            if m and m.group(1) in labels:
                cats = corpus.system_categories.get(link.system_id, set())
                if len(cats) == 1:
                    obs(link.resource_id, link.system_id, next(iter(cats)), f'catalog label {m.group(1)}', tokens,
                        max(entry.captured_at, link.available_at))
    elif kind == 'LEGAL_RETENTION_REGISTER':
        rows = [loc for _, loc in facts['retain_until']]
        report.issues.append(f'NOTE: {len(rows)} retention rows keyed by order reference; no order reference '
                             'matches any structured record, so no resource is attached')
    report.status = 'INTERPRETED'
    report.observations = len(observations)
    return observations, report, extraction


# --- screenshots ------------------------------------------------------------------------------------

def _vision_check(payload):
    try:
        data = _strict_json(payload['choices'][0]['message']['content'])
    except (KeyError, IndexError, TypeError, ValueError, AttributeError) as exc:
        return f'unparseable response: {exc}'
    if data.get('page_kind') not in PAGE_KINDS or not isinstance(data.get('entities'), list):
        return 'missing page_kind/entities'
    for e in data['entities']:
        if not isinstance(e, dict) or not isinstance(e.get('fields', {}), dict):
            return 'malformed entity'
    return None


def extract_screenshot(gateway, raw: bytes) -> dict:
    prompt = (PROMPTS / 'vision_extract_v1.md').read_text(encoding='utf-8')
    version = f'vision-extract-v1:{fingerprint(prompt)[:12]}:{hashlib.sha256(raw).hexdigest()[:16]}'
    image = base64.b64encode(raw).decode('ascii')
    messages = [{'role': 'system', 'content': prompt},
                {'role': 'user', 'content': [{'type': 'text', 'text': 'Extract this screenshot.'},
                                             {'type': 'image_url',
                                              'image_url': {'url': f'data:image/png;base64,{image}'}}]}]
    last = None
    for _attempt in range(2):
        try:
            payload = gateway.chat('vision_reader', VISION_MODEL, messages, version, _vision_check, max_tokens=12000)
            data = _strict_json(payload['choices'][0]['message']['content'])
            data['_model'] = payload.get('model')
            return data
        except ProviderError as exc:
            last = exc
            if 'unparseable' not in str(exc):
                break
    raise last


def interpret_screenshot(entry, raw, corpus, gateway) -> tuple[list, SourceReport, dict]:
    report = SourceReport(entry.file_id, entry.file_path, 'image')
    if gateway is None:
        report.status = 'UNSUPPORTED_MEDIA'
        report.issues.append('UNREADABLE_MEDIA: vision reader not configured (offline run)')
        return [], report, {}
    try:
        data = extract_screenshot(gateway, raw)
    except ProviderError as exc:
        report.status = 'FAILED'
        report.issues.append(f'PROVIDER_FAILURE: vision extraction: {exc}')
        return [], report, {}
    page_system, page_category = PAGE_KINDS[data['page_kind']]
    merged, rejected = {}, []
    for e in data['entities']:
        rid = e.get('resource_id')
        values = [str(v) for v in (e.get('fields') or {}).values()] + ([str(e['label'])] if e.get('label') else [])
        if rid is not None and not (isinstance(rid, str) and RESOURCE_ID.match(rid)):
            rejected.append(rid)
            continue
        if rid is None:
            continue
        if classify_token(rid):
            # A user key shown as a page title is not a record ID; it names a record only
            # through an exact catalog label (or it is skipped).
            links = _catalog_links(corpus, rid)
            resolved = _unique_resource(links)
            if resolved is None:
                continue
            values.append(rid)
            rid, resolved_at = resolved, _settled_at(entry, links, _unique_resource)
        else:
            resolved_at = entry.captured_at
        slot = merged.setdefault(rid, {'fields': {}, 'values': [], 'resolved_at': entry.captured_at})
        slot['fields'].update(e.get('fields') or {})
        slot['values'] += values
        slot['resolved_at'] = max(slot['resolved_at'], resolved_at)

    def row_decision(observations):
        """(system, consistent with the page and the structured categories) for one row."""
        systems = _systems(observations)
        structured = {o.category for o in observations if o.category}
        system = next(iter(systems)) if len(systems) == 1 else (page_system if not systems else None)
        consistent = system is not None and page_category is not None and (
            not structured or structured == {page_category}) and (page_system is None or system == page_system)
        return system, consistent
    observations = []
    for rid, slot in sorted(merged.items()):
        tokens = tokens_from(*slot['values'])
        known = corpus.by_resource.get(rid, [])
        (system, consistent), category = row_decision(known), page_category
        available_at = max(slot['resolved_at'], _settled_at(entry, known, row_decision))
        base = dict(source_id=entry.file_id, available_at=available_at, observed_at=entry.captured_at,
                    locator=f'screenshot entity {rid}', excerpt=json.dumps(slot['fields'], ensure_ascii=False)[:400],
                    system_id=system, resource_id=rid, tokens=tokens,
                    attributes=(('page_kind', data['page_kind']), ('model', data.get('_model')),
                                ('corroborated', bool(corpus.by_resource.get(rid)))))
        if consistent:
            exposed = None
            if category in EXPOSURE_CATEGORIES:
                shown = tuple(sorted(label for label, value in slot['fields'].items()
                                     if classify_token(str(value)) and classify_token(str(value))[0] != 'user'))
                exposed = shown or None        # not showing a field does not prove it is empty
            observations.append(Observation(kind=STATE, proof='SCREENSHOT_ROW', category=category,
                                            presence=PRESENT, exposed=exposed, **base))
        elif system is not None and tokens:
            observations.append(Observation(kind='LINK', proof='SCREENSHOT_ROW', **base))
            report.issues.append(f'NOTE: {rid}: page/system/category not consistent; identity link only')
    if rejected:
        report.issues.append(f'UNSUPPORTED_MODEL_FACT: rejected malformed resource IDs {rejected}')
    if data.get('unreadable'):
        report.issues.append(f'UNREADABLE_MEDIA: reader flagged {data["unreadable"]}')
    # Read, but with rejected entities or unreadable fields: some of its facts are missing.
    report.status = 'PARTIAL' if rejected or data.get('unreadable') else 'INTERPRETED'
    report.observations = len(observations)
    return observations, report, {'page_kind': data['page_kind'], 'page_title': data.get('page_title'),
                                  'entities': {rid: slot['fields'] for rid, slot in merged.items()},
                                  'rejected_ids': rejected, 'unreadable': data.get('unreadable'),
                                  'model': data.get('_model')}


def interpret_media(context, corpus, gateway: Gateway | None) -> MediaResult:
    result = MediaResult()
    for entry in context.manifest:
        raw = (context.root / entry.file_path).read_bytes()
        kind = sniff(raw)
        if kind == 'pdf':
            obs, report, extraction = interpret_pdf(entry, raw, corpus, gateway)
        elif kind == 'image':
            obs, report, extraction = interpret_screenshot(entry, raw, corpus, gateway)
        else:
            continue
        result.observations += obs
        result.reports[entry.file_id] = report
        result.extractions[entry.file_id] = extraction
    return result

