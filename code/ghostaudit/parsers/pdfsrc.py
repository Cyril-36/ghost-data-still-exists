"""Native PDF text extraction (standard library only) and literal fact harvesting.

Extraction: walk Catalog -> Pages -> Kids in order, decode each page's content
streams (FlateDecode, ASCII85Decode), interpret text operators (Tj, TJ, ', ", Td,
TD, T*, Tm, cm, q/Q) and map glyph codes through each font's ToUnicode CMap.
Text is regrouped into lines by page and baseline, preserving page numbers.

Harvesting is literal and deterministic: identifiers, resource-like IDs, reference
labels and "retain until" dates found in the text, each with page and line. What
the document *is* (and therefore what its IDs mean) is decided separately.
If text cannot be extracted, the document is reported unreadable, never guessed.
"""
from __future__ import annotations

import base64
import re
import zlib
from datetime import datetime, timezone

_OBJ = re.compile(rb'(\d+)\s+0\s+obj(.*?)endobj', re.S)
_REF = re.compile(rb'(\d+)\s+0\s+R')
_TOKEN = re.compile(rb'\((?:\\.|[^\\)])*\)|<[0-9A-Fa-f\s]*>|\[|\]|/[^\s/\[\]()<>]+|[-+]?\d*\.?\d+|[A-Za-z\'"*]+')


class PdfError(ValueError):
    pass


def _objects(raw: bytes) -> dict:
    return {int(n): body for n, body in _OBJ.findall(raw)}


def _dict(body: bytes) -> bytes:
    return body[:body.find(b'stream')] if b'stream' in body else body


def _stream(body: bytes) -> bytes:
    head = _dict(body)
    start = body.index(b'stream') + len(b'stream')
    data = body[start:body.rindex(b'endstream')].lstrip(b'\r\n').rstrip(b'\r\n')
    if b'ASCII85Decode' in head:
        data = data.strip()
        if data.startswith(b'<~'):
            data = data[2:]
        if data.endswith(b'~>'):
            data = data[:-2]
        data = base64.a85decode(data)
    if b'FlateDecode' in head:
        data = zlib.decompress(data)
    return data


def _unescape(s: bytes) -> bytes:
    out, i = bytearray(), 0
    while i < len(s):
        c = s[i]
        if c == 0x5C and i + 1 < len(s):          # backslash
            n = s[i + 1]
            simple = {ord('n'): 10, ord('r'): 13, ord('t'): 9, ord('b'): 8, ord('f'): 12,
                      ord('('): 40, ord(')'): 41, ord('\\'): 92}
            if n in simple:
                out.append(simple[n])
                i += 2
                continue
            m = re.match(rb'[0-7]{1,3}', s[i + 1:i + 4])
            if m:
                out.append(int(m.group(0), 8) & 0xFF)
                i += 1 + len(m.group(0))
                continue
            i += 2
            continue
        out.append(c)
        i += 1
    return bytes(out)


def _cmap(data: bytes) -> dict:
    mapping = {}
    for block in re.findall(rb'beginbfchar(.*?)endbfchar', data, re.S):
        for src, dst in re.findall(rb'<([0-9A-Fa-f]+)>\s*<([0-9A-Fa-f]+)>', block):
            mapping[int(src, 16)] = bytes.fromhex(dst.decode()).decode('utf-16-be', errors='replace')
    for block in re.findall(rb'beginbfrange(.*?)endbfrange', data, re.S):
        for lo, hi, dst in re.findall(rb'<([0-9A-Fa-f]+)>\s*<([0-9A-Fa-f]+)>\s*<([0-9A-Fa-f]+)>', block):
            base = int(dst, 16)
            for k, code in enumerate(range(int(lo, 16), int(hi, 16) + 1)):
                mapping[code] = chr(base + k)
    return mapping


def _fonts(objects, page_body) -> dict:
    """Font resource name -> decoder (ToUnicode map, else WinAnsi/latin-1)."""
    res = re.search(rb'/Resources\s*(\d+\s+0\s+R|<<.*?>>\s*>>)', page_body, re.S)
    if not res:
        return {}
    res_body = res.group(1)
    if _REF.fullmatch(res_body.strip()):
        res_body = _dict(objects[int(_REF.match(res_body.strip()).group(1))])
    m = re.search(rb'/Font\s*(\d+)\s+0\s+R', res_body)
    font_dict = _dict(objects[int(m.group(1))]) if m else res_body
    fonts = {}
    for name, ref in re.findall(rb'/([^\s/<>\[\]]+)\s+(\d+)\s+0\s+R', font_dict):
        if int(ref) not in objects:
            continue
        fbody = _dict(objects[int(ref)])
        tu = re.search(rb'/ToUnicode\s+(\d+)\s+0\s+R', fbody)
        fonts[name.decode()] = _cmap(_stream(objects[int(tu.group(1))])) if tu else None
    return fonts


def _decode(raw: bytes, cmap) -> str:
    if cmap:
        return ''.join(cmap.get(b, chr(b)) for b in raw)
    return raw.decode('cp1252', errors='replace')


def _page_refs(objects, node_num, seen=None):
    seen = seen or set()
    if node_num in seen or node_num not in objects:
        return []
    seen.add(node_num)
    body = _dict(objects[node_num])
    if b'/Type /Pages' in body or b'/Type/Pages' in body:
        kids = re.search(rb'/Kids\s*\[(.*?)\]', body, re.S)
        out = []
        for ref in _REF.findall(kids.group(1) if kids else b''):
            out += _page_refs(objects, int(ref), seen)
        return out
    return [node_num]


def _run_content(data: bytes, fonts: dict):
    """Minimal text-state interpreter -> [(y, x, text)]."""
    segments, stack = [], []
    ctm = [1, 0, 0, 1, 0, 0]
    tm = [1, 0, 0, 1, 0, 0]
    tlm = list(tm)
    leading, font = 0.0, None
    operands = []

    def mul(a, b):
        return [a[0] * b[0] + a[1] * b[2], a[0] * b[1] + a[1] * b[3], a[2] * b[0] + a[3] * b[2],
                a[2] * b[1] + a[3] * b[3], a[4] * b[0] + a[5] * b[2] + b[4], a[4] * b[1] + a[5] * b[3] + b[5]]

    def emit(raw):
        m = mul(tm, ctm)
        segments.append((round(m[5], 1), round(m[4], 1), _decode(raw, fonts.get(font))))

    for tok in _TOKEN.findall(data):
        if tok[:1] in (b'(', b'<', b'[', b']', b'/') or re.fullmatch(rb'[-+]?\d*\.?\d+', tok):
            operands.append(tok)
            continue
        op = tok.decode('latin-1')
        nums = []
        for o in operands:
            try:
                nums.append(float(o))
            except ValueError:
                pass
        if op == 'q':
            stack.append(list(ctm))
        elif op == 'Q' and stack:
            ctm = stack.pop()
        elif op == 'cm' and len(nums) >= 6:
            ctm = mul(nums[-6:], ctm)
        elif op == 'BT':
            tm, tlm = [1, 0, 0, 1, 0, 0], [1, 0, 0, 1, 0, 0]
        elif op == 'Tf' and operands:
            font = next((o[1:].decode() for o in operands if o.startswith(b'/')), font)
        elif op == 'TL' and nums:
            leading = nums[-1]
        elif op == 'Tm' and len(nums) >= 6:
            tm, tlm = nums[-6:], nums[-6:]
        elif op in ('Td', 'TD') and len(nums) >= 2:
            if op == 'TD':
                leading = -nums[-1]
            tlm = mul([1, 0, 0, 1, nums[-2], nums[-1]], tlm)
            tm = list(tlm)
        elif op == 'T*':
            tlm = mul([1, 0, 0, 1, 0, -leading], tlm)
            tm = list(tlm)
        elif op in ('Tj', "'", '"'):
            if op in ("'", '"'):
                tlm = mul([1, 0, 0, 1, 0, -leading], tlm)
                tm = list(tlm)
            strings = [o for o in operands if o.startswith((b'(', b'<'))]
            if strings:
                s = strings[-1]
                emit(_unescape(s[1:-1]) if s.startswith(b'(') else bytes.fromhex(s[1:-1].decode()))
        elif op == 'TJ':
            parts = [(_unescape(o[1:-1]) if o.startswith(b'(') else bytes.fromhex(o[1:-1].decode()))
                     for o in operands if o.startswith((b'(', b'<'))]
            if parts:
                emit(b''.join(parts))
        operands = []
    return segments


def extract_pdf_text(raw: bytes) -> list[list[str]]:
    """Pages -> lines (top to bottom, segments on one baseline joined left to right)."""
    if not raw.startswith(b'%PDF-'):
        raise PdfError('not a PDF')
    objects = _objects(raw)
    catalog = next((n for n, b in objects.items() if b'/Type /Catalog' in b or b'/Type/Catalog' in b), None)
    if catalog is None:
        raise PdfError('no catalog')
    pages_ref = re.search(rb'/Pages\s+(\d+)\s+0\s+R', _dict(objects[catalog]))
    pages = []
    for page in _page_refs(objects, int(pages_ref.group(1))):
        body = _dict(objects[page])
        fonts = _fonts(objects, body)
        contents = re.search(rb'/Contents\s*(\[[^\]]*\]|\d+\s+0\s+R)', body)
        data = b''
        for ref in _REF.findall(contents.group(1) if contents else b''):
            data += _stream(objects[int(ref)]) + b'\n'
        rows = {}
        for y, x, text in _run_content(data, fonts):
            rows.setdefault(y, []).append((x, text))
        lines = []
        for y in sorted(rows, reverse=True):
            line = '  '.join(t.strip() for _, t in sorted(rows[y]) if t.strip())
            if line:
                lines.append(line)
        pages.append(lines)
    if not any(pages):
        raise PdfError('no extractable text (would need OCR/vision)')
    return pages


# --- literal harvesting -----------------------------------------------------------------------

RESOURCE_ID = re.compile(r'\b[a-z]+(?:_[a-z]+)*_\d+\b')
USER_REF = re.compile(r'\b(?:subject reference|customer reference|user)\s*[:\s]\s*(\S+)', re.I)
RETAIN_UNTIL = re.compile(r'retain(?:ed)? until\s+(\d{1,2}\s+[A-Z][a-z]{2}\s+\d{4})(?:\s+(\d{2})\s*:?\s*(\d{2})\s*UTC)?', re.I)
LABEL = re.compile(r'\b(?:[A-Z]{2,5}-\d{4}-\d{4}-\d{3,5}|[A-Z]{2,4}-\d{3,8}|ORD-\d{6,}|[\w.-]+\.pdf)\b')


def parse_date(day_month_year: str, hh=None, mm=None):
    when = datetime.strptime(day_month_year, '%d %b %Y').replace(tzinfo=timezone.utc)
    if hh is not None:
        when = when.replace(hour=int(hh), minute=int(mm))
    return when


def harvest(pages: list[list[str]]) -> dict:
    """Literal facts with their page/line locators; nothing here is interpreted."""
    from ..observations import EMAIL_IN_TEXT, PHONE_IN_TEXT
    facts = {'resource_ids': [], 'emails': [], 'phones': [], 'labels': [], 'retain_until': [], 'user_refs': []}
    for p, lines in enumerate(pages, start=1):
        for n, line in enumerate(lines, start=1):
            loc = f'page {p} line {n}'
            facts['resource_ids'] += [(r, loc) for r in RESOURCE_ID.findall(line)]
            facts['emails'] += [(e, loc) for e in EMAIL_IN_TEXT.findall(line)]
            facts['phones'] += [(ph.strip(), loc) for ph in PHONE_IN_TEXT.findall(line)]
            facts['labels'] += [(lab, loc) for lab in LABEL.findall(line)]
            facts['retain_until'] += [(parse_date(*m.groups()), loc) for m in RETAIN_UNTIL.finditer(line)]
    return facts
