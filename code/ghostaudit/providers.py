"""Provider-neutral model gateway (plan section 21) with budget, telemetry and caching.

Two distinct API shapes, never mixed:
  decide(model, state, questions)  POST {base}/decisions        typed answers + probabilities
  chat(model, messages, ...)       POST {base}/chat/completions generative text (optional native tool calls)
  list_models()                    GET  {base}/models           read-only catalog: model IDs and kinds

Rules enforced here:
- Credentials come only from the environment (TOKENBAZAAR_API_KEY); never logged.
- Every call reserves its worst-case cost before it is sent, so concurrent calls cannot
  together exceed the ceiling; a live response without a cost header is charged that reserve.
- Redirects are never followed, so the bearer token cannot reach another origin.
- Every attempt is recorded: role, requested and returned model, tokens, latency,
  cost (gateway header, paise), retries, cache hit/miss, mode (live|replay).
- Only responses that pass the caller's validator are cached, and a cache hit is validated
  again before use (an invalid entry is discarded); the cache key is a fingerprint of
  endpoint, requested model, prompt/schema version and input.
- Authentication, payment and permission errors stop immediately; transient errors retry a
  bounded number of times with backoff, honouring a server-requested wait up to MAX_WAIT_S.
- Error messages carry the provider's explanation, redacted and truncated, never the request.
- Tool calls are validated (`tool_call_problem`), never executed: running a tool is the caller's job.
"""
from __future__ import annotations

import hashlib
import json
import math
import os
import re
import threading
import time
import urllib.error
import urllib.request
from dataclasses import asdict, dataclass
from pathlib import Path

DEFAULT_BASE_URL = 'https://tokenbazaar.in/api/public/v1'
ALLOWED_ORIGINS = ('https://tokenbazaar.in/',)
MAX_WAIT_S = 30.0                      # longest server-requested wait honoured before a retry
_WAIT_MESSAGE = re.compile(r'please wait\s+(\S+?)\s*s\b', re.IGNORECASE)
_HTTP_MEANING = {401: 'authentication failed', 402: 'payment required: account credit exhausted',
                 403: 'forbidden: key not permitted for this request or model', 429: 'rate limited',
                 503: 'service unavailable'}


class ProviderError(RuntimeError):
    """A provider call failed, was refused, or returned an invalid response."""


class BudgetExceeded(ProviderError):
    """The configured cost or call ceiling would be exceeded."""


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None            # urllib then raises HTTPError for the 3xx response


_OPENER = urllib.request.build_opener(_NoRedirect)


@dataclass(frozen=True)
class ProviderConfig:
    base_url: str = DEFAULT_BASE_URL
    api_key_env: str = 'TOKENBAZAAR_API_KEY'
    timeout_s: float = 90.0
    max_retries: int = 2
    max_rate_limit_retries: int = 5        # HTTP 429 gets a longer, still bounded, backoff
    max_cost_paise: float = 2000.0         # whole-run ceiling (Rs 20) unless raised explicitly
    max_calls: int = 3000
    cost_reserve_paise: float = 25.0       # assumed worst-case cost of one /decisions call
    chat_cost_reserve_paise: float = 120.0  # assumed worst-case cost of one chat call
    cache_dir: str | None = '.cache/ghostaudit'


@dataclass
class UsageRecord:
    role: str
    endpoint: str
    requested_model: str
    returned_model: str | None
    mode: str                      # live | replay | cache
    status: str                    # ok | invalid | error
    latency_s: float = 0.0
    input_tokens: int | None = None
    output_tokens: int | None = None
    reasoning_tokens: int | None = None
    cached_input_tokens: int | None = None
    cost_paise_header: float | None = None
    cost_usd_reported: float | None = None
    retries: int = 0
    request_id: str | None = None
    fingerprint: str = ''
    error: str = ''


def fingerprint(*parts) -> str:
    return hashlib.sha256(json.dumps(parts, sort_keys=True, ensure_ascii=False).encode('utf-8')).hexdigest()


def retry_delay(retry_after, message) -> float | None:
    """Server-requested wait in seconds: the Retry-After header, else 'Please wait 8s' in the body,
    capped at MAX_WAIT_S. None when neither is a finite non-negative number (caller backs off)."""
    found = _WAIT_MESSAGE.search(message or '')
    for raw in (retry_after, found.group(1) if found else None):
        try:
            value = float(str(raw).strip())
        except (TypeError, ValueError):
            continue
        if math.isfinite(value) and value >= 0:
            return min(MAX_WAIT_S, value)
    return None


def provider_message(raw: bytes, secret: str | None = None, limit: int = 200) -> str:
    """The provider's explanation from an error body (JSON `error.message`/`message`/`detail`, else
    the text), whitespace-collapsed, with bearer tokens and the API key redacted, truncated."""
    text = raw.decode('utf-8', 'replace') if isinstance(raw, bytes) else ''
    try:
        data = json.loads(text)
    except ValueError:
        data = None
    if isinstance(data, dict):
        err = data.get('error')
        candidates = (err.get('message') if isinstance(err, dict) else err, data.get('message'), data.get('detail'))
        text = next((c for c in candidates if isinstance(c, str)), '')
    text = re.sub(r'(?i)bearer\s+\S+', 'Bearer [REDACTED]', ' '.join(text.split()))
    if secret:
        text = text.replace(secret, '[REDACTED]')
    return text[:limit]


def _http_error(endpoint, code, detail) -> str:
    meaning = _HTTP_MEANING.get(code)
    return f'{endpoint} HTTP {code}' + (f' ({meaning})' if meaning else '') + (f': {detail}' if detail else '')


def _checked(validate, payload) -> str | None:
    """Run a response validator; an exception is a validation problem, not a crash."""
    try:
        return validate(payload)
    except Exception as exc:
        return f'validator raised {type(exc).__name__}'


def _count(value) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) and value >= 0 else None


def cached_input_tokens(usage) -> int | None:
    """Prompt tokens served from the provider's cache (`prompt_tokens_details.cached_tokens`)."""
    for key in ('prompt_tokens_details', 'input_tokens_details'):
        details = usage.get(key) if isinstance(usage, dict) else None
        if isinstance(details, dict) and _count(details.get('cached_tokens')) is not None:
            return details['cached_tokens']
    return None


_JSON_TYPES = {'string': str, 'boolean': bool, 'object': dict, 'array': list, 'null': type(None)}
_SCHEMA_TYPES = frozenset(_JSON_TYPES) | {'integer', 'number'}
_SCHEMA_KEYS = frozenset({'type', 'enum', 'properties', 'required', 'additionalProperties', 'items',
                          'description', 'title'})


def strict_json(text: str):
    """json.loads that rejects NaN/Infinity, non-finite numbers (1e999) and duplicate object keys."""
    def no_constant(name):
        raise ValueError(f'unsupported JSON constant {name}')

    def finite(raw):
        value = float(raw)
        if not math.isfinite(value):
            raise ValueError(f'non-finite number {raw}')
        return value

    def unique(pairs):
        keys = [k for k, _ in pairs]
        if len(keys) != len(set(keys)):
            raise ValueError('duplicate object key')
        return dict(pairs)
    return json.loads(text, parse_constant=no_constant, parse_float=finite, object_pairs_hook=unique)


def schema_definition_problem(schema, where='parameters') -> str | None:
    """A developer-supplied parameter schema must use only the subset `_schema_problem` enforces, so
    nothing is silently unchecked: known keywords, known types, well-formed properties/required/items,
    and `additionalProperties` as a boolean."""
    if not isinstance(schema, dict):
        return f'{where}: schema is not an object'
    unknown = sorted(set(schema) - _SCHEMA_KEYS)
    if unknown:
        return f'{where}: unsupported schema keywords {unknown}'
    types = schema.get('type')
    types = [types] if isinstance(types, str) else types
    if 'type' in schema and (not isinstance(types, list) or not types or not set(types) <= _SCHEMA_TYPES):
        return f'{where}: unsupported type {schema.get("type")!r}'
    if 'enum' in schema and not isinstance(schema['enum'], list):
        return f'{where}: enum is not a list'
    if 'additionalProperties' in schema and not isinstance(schema['additionalProperties'], bool):
        return f'{where}: additionalProperties must be a boolean'
    props = schema.get('properties', {})
    if not isinstance(props, dict):
        return f'{where}: properties is not an object'
    required = schema.get('required', [])
    if not isinstance(required, list) or not all(isinstance(k, str) and k in props for k in required):
        return f'{where}: required must list declared properties'
    for name, sub in props.items():
        problem = schema_definition_problem(sub, f'{where}.{name}')
        if problem:
            return problem
    if 'items' in schema:
        return schema_definition_problem(schema['items'], f'{where}[]')
    return None


def _schema_problem(value, schema, where) -> str | None:
    """A small strict JSON-Schema subset: type, enum, properties, required, additionalProperties
    (unknown keys rejected unless it is true), items."""
    if not isinstance(schema, dict):
        return f'{where}: schema is not an object'
    types = schema.get('type')
    types = [types] if isinstance(types, str) else types
    if types:
        def fits(t):
            if t == 'integer':
                return isinstance(value, int) and not isinstance(value, bool)
            if t == 'number':
                return isinstance(value, (int, float)) and not isinstance(value, bool)
            return t in _JSON_TYPES and isinstance(value, _JSON_TYPES[t])
        if not isinstance(types, list) or not any(fits(t) for t in types):
            return f'{where}: expected {schema.get("type")}'
    if 'enum' in schema and value not in schema['enum']:
        return f'{where}: value not in enum'
    if isinstance(value, dict):
        props = schema.get('properties') or {}
        if not isinstance(props, dict):
            return f'{where}: properties is not an object'
        missing = [k for k in schema.get('required') or () if k not in value]
        if missing:
            return f'{where}: missing required {missing}'
        for k, v in value.items():
            if k in props:
                problem = _schema_problem(v, props[k], f'{where}.{k}')
                if problem:
                    return problem
            elif schema.get('additionalProperties') is not True:
                return f'{where}: unsupported argument {k!r}'
    if isinstance(value, list) and 'items' in schema:
        for i, v in enumerate(value):
            problem = _schema_problem(v, schema['items'], f'{where}[{i}]')
            if problem:
                return problem
    return None


def _tool_specs(tools, tool_choice):
    """-> ({function name: parameter schema}, forced function name or None, problem or None)."""
    specs = {}
    for tool in tools if isinstance(tools, list) else ():
        fn = tool.get('function') if isinstance(tool, dict) and tool.get('type') == 'function' else None
        if not isinstance(fn, dict) or not isinstance(fn.get('name'), str) or not fn['name'] or fn['name'] in specs:
            return {}, None, 'malformed tool definition'
        params = fn.get('parameters', {'type': 'object', 'properties': {}})
        problem = schema_definition_problem(params, f'{fn["name"]}.parameters')
        if problem or params.get('type') != 'object':
            return {}, None, problem or f'{fn["name"]}.parameters: top-level type must be object'
        specs[fn['name']] = params
    if not specs:
        return {}, None, 'no tool definitions'
    forced = None
    if isinstance(tool_choice, dict):
        fn = tool_choice.get('function')
        forced = fn.get('name') if isinstance(fn, dict) else None
        if forced not in specs:
            return {}, None, 'tool_choice names no supplied tool'
    return specs, forced, None


def tool_call_problem(payload, tools, tool_choice=None) -> str | None:
    """Strict check of the tool calls in a chat completion against the supplied `tools`.
    None when every call is well formed, names a supplied function, and its JSON arguments satisfy
    that function's parameter schema (and `tool_choice` is respected); otherwise the first problem."""
    specs, forced, problem = _tool_specs(tools, tool_choice)
    if problem:
        return problem
    choices = payload.get('choices') if isinstance(payload, dict) else None
    if not isinstance(choices, list) or not choices:
        return 'no choices'
    for choice in choices:
        message = choice.get('message') if isinstance(choice, dict) else None
        if not isinstance(message, dict):
            return 'message is not an object'
        calls = message.get('tool_calls')
        if calls is None:
            if tool_choice == 'required' or forced:
                return 'tool call required but none returned'
            continue
        if not isinstance(calls, list) or not calls:
            return 'tool_calls is not a non-empty list'
        if tool_choice == 'none':
            return 'tool call returned although tool_choice is none'
        ids = set()
        for call in calls:
            fn = call.get('function') if isinstance(call, dict) else None
            if not isinstance(fn, dict) or call.get('type') != 'function':
                return 'malformed tool call'
            if not isinstance(call.get('id'), str) or not call['id'] or call['id'] in ids:
                return 'tool call id missing or duplicated'
            ids.add(call['id'])
            name = fn.get('name')
            if name not in specs or (forced and name != forced):
                return f'unknown tool {name!r}' if name not in specs else f'tool {name!r} not the forced tool'
            if not isinstance(fn.get('arguments'), str):
                return f'{name}: arguments are not a JSON string'
            try:
                args = strict_json(fn['arguments'])
            except (ValueError, RecursionError):
                return f'{name}: arguments are not strict JSON'
            if not isinstance(args, dict):
                return f'{name}: arguments are not a JSON object'
            problem = _schema_problem(args, specs[name], name)
            if problem:
                return problem
    return None


def load_env_file(path: Path) -> list[str]:
    """Explicit development helper: export KEY=VALUE lines from a local .env (values never returned)."""
    names = []
    for line in Path(path).read_text(encoding='utf-8').splitlines():
        if '=' in line and not line.lstrip().startswith('#'):
            name, value = line.split('=', 1)
            os.environ.setdefault(name.strip(), value.strip().strip('"').strip("'"))
            names.append(name.strip())
    return names


class Gateway:
    """Live HTTPS gateway. Thread-safe accounting; callers bound their own concurrency."""

    mode = 'live'

    def __init__(self, config: ProviderConfig | None = None):
        config = config or ProviderConfig()
        if not config.base_url.startswith(ALLOWED_ORIGINS):
            raise ProviderError(f'base URL outside the allowlist: {config.base_url}')
        self.config = config
        self.usage: list[UsageRecord] = []
        self._lock = threading.Lock()
        self._spent = 0.0
        self._reserved = 0.0
        self._calls = 0

    # --- accounting ---------------------------------------------------------------------
    @property
    def spent_paise(self) -> float:
        return self._spent

    def _reserve(self, reserve):
        with self._lock:
            if self._calls + 1 > self.config.max_calls:
                raise BudgetExceeded(f'call ceiling {self.config.max_calls} reached')
            if self._spent + self._reserved + reserve > self.config.max_cost_paise:
                raise BudgetExceeded(f'cost ceiling {self.config.max_cost_paise} paise would be exceeded '
                                     f'(spent {self._spent:.1f}, in flight {self._reserved:.1f})')
            self._calls += 1
            self._reserved += reserve

    def _record(self, rec: UsageRecord, reserve=0.0):
        """Append `rec`; for a reserved call, release its reservation and charge its cost in the
        same critical section, so no call is admitted against a released but uncharged reserve."""
        with self._lock:
            self.usage.append(rec)
            self._reserved -= reserve
            if reserve and rec.mode == 'live' and rec.status != 'error':
                self._spent += reserve if rec.cost_paise_header is None else rec.cost_paise_header

    # --- cache --------------------------------------------------------------------------
    def _cache_path(self, fp):
        if not self.config.cache_dir:
            return None
        return Path(self.config.cache_dir) / fp[:2] / f'{fp}.json'

    def _cache_get(self, fp):
        path = self._cache_path(fp)
        if path and path.exists():
            return json.loads(path.read_text(encoding='utf-8'))
        return None

    def _cache_put(self, fp, entry):
        path = self._cache_path(fp)
        if path is None:
            return
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix('.tmp')
        tmp.write_text(json.dumps(entry, ensure_ascii=False), encoding='utf-8')
        os.replace(tmp, path)

    # --- transport ----------------------------------------------------------------------
    def _post(self, path, body):
        return self._request('POST', path, body)

    def _get(self, path):
        return self._request('GET', path)[0]

    def _request(self, method, path, body=None):
        key = os.environ.get(self.config.api_key_env)
        if not key:
            raise ProviderError(f'{self.config.api_key_env} is not set')
        headers = {'Authorization': f'Bearer {key}'}
        if body is not None:
            headers['Content-Type'] = 'application/json'
        request = urllib.request.Request(
            f'{self.config.base_url.rstrip("/")}/{path}', method=method, headers=headers,
            data=None if body is None else json.dumps(body).encode('utf-8'))
        with _OPENER.open(request, timeout=self.config.timeout_s) as response:
            cost = response.headers.get('x-tokenbaazar-cost-paise')
            return json.loads(response.read().decode('utf-8')), (float(cost) if cost else None)

    def _call(self, role, endpoint, requested_model, body, fp, validate, extract_usage):
        cached = self._cache_get(fp)
        if cached is not None:
            problem = ('cache entry metadata mismatch' if cached.get('requested_model') != requested_model
                       or not isinstance(cached.get('response'), dict) else _checked(validate, cached['response']))
            self._record(UsageRecord(role, endpoint, requested_model, cached.get('returned_model'), 'cache',
                                     'invalid' if problem else 'ok', fingerprint=fp, error=problem or ''))
            if not problem:
                return cached['response']
            self._cache_path(fp).unlink()          # invalid entry: discard it and ask again
        reserve = self.config.cost_reserve_paise if endpoint == 'decisions' else self.config.chat_cost_reserve_paise
        self._reserve(reserve)
        payload, cost, retries, latency = self._send(role, endpoint, requested_model, body, fp, reserve)
        rec = UsageRecord(role, endpoint, requested_model, payload.get('model'), self.mode, 'ok', latency,
                          retries=retries, request_id=payload.get('id'), cost_paise_header=cost, fingerprint=fp)
        try:                                       # a raising extractor or validator still settles the reserve
            for k, v in extract_usage(payload).items():
                setattr(rec, k, v)
            problem = _checked(validate, payload)
        except Exception as exc:
            problem = f'usage extraction raised {type(exc).__name__}'
        if problem:
            rec.status, rec.error = 'invalid', problem
            self._record(rec, reserve)
            raise ProviderError(f'invalid {endpoint} response: {problem}')
        self._record(rec, reserve)
        self._cache_put(fp, {'response': payload, 'returned_model': payload.get('model'),
                             'requested_model': requested_model, 'fingerprint_parts': endpoint})
        return payload

    def _send(self, role, endpoint, requested_model, body, fp, reserve):
        """POST with bounded retries -> (JSON object, cost header, retries, latency). A failure
        settles the call's reservation here; a success leaves it for the caller to settle."""
        retries, started = 0, time.time()
        while True:
            wait = None
            try:
                payload, cost = self._post(endpoint, body)
                if not isinstance(payload, dict):
                    raise ValueError('response is not a JSON object')
                return payload, cost, retries, round(time.time() - started, 3)
            except urllib.error.HTTPError as exc:
                detail = self._error_detail(exc)
                limit = self.config.max_rate_limit_retries if exc.code == 429 else self.config.max_retries
                if retries >= limit or exc.code not in (408, 429, 500, 502, 503, 504):
                    error = _http_error(endpoint, exc.code, detail)
                    self._record(UsageRecord(role, endpoint, requested_model, None, self.mode, 'error',
                                             time.time() - started, retries=retries, fingerprint=fp,
                                             error=error), reserve)
                    raise ProviderError(error) from exc
                wait = retry_delay(exc.headers.get('Retry-After') if exc.headers else None, detail)
            except (urllib.error.URLError, TimeoutError) as exc:
                if retries >= self.config.max_retries:
                    self._record(UsageRecord(role, endpoint, requested_model, None, self.mode, 'error',
                                             time.time() - started, retries=retries, fingerprint=fp,
                                             error=type(exc).__name__), reserve)
                    raise ProviderError(f'{endpoint} transport failure') from exc
            except ValueError as exc:                  # body is not a JSON object (may still have been billed)
                self._record(UsageRecord(role, endpoint, requested_model, None, self.mode, 'invalid',
                                         time.time() - started, retries=retries, fingerprint=fp, error=str(exc)),
                             reserve)
                raise ProviderError(f'{endpoint} returned a non-JSON body') from exc
            retries += 1
            time.sleep(wait if wait is not None else min(20.0, 1.5 * 2 ** retries))

    def _error_detail(self, exc) -> str:
        try:
            raw = exc.read()
        except Exception:                          # an unreadable body only loses the explanation
            raw = b''
        return provider_message(raw, os.environ.get(self.config.api_key_env))

    # --- public API ---------------------------------------------------------------------
    def decide(self, role, model, state, questions, version, validate):
        """Typed decision call. `questions` maps question names to {type, instructions, criteria}."""
        body = {'model': model, 'state': state, 'questions': questions}
        fp = fingerprint('decisions', model, version, questions, state)

        def usage(p):
            u = p.get('usage') or {}
            return {'input_tokens': u.get('input_tokens'), 'output_tokens': u.get('output_tokens'),
                    'cached_input_tokens': cached_input_tokens(u), 'cost_usd_reported': u.get('cost')}
        return self._call(role, 'decisions', model, body, fp, validate, usage)

    def chat(self, role, model, messages, version, validate, max_tokens=4000, *,
             max_completion_tokens=None, tools=None, tool_choice=None):
        """Chat completion. The output limit is sent as the documented `max_completion_tokens`
        (`max_tokens` stays the Python keyword; the alias wins when both are given). Optional
        `tools`/`tool_choice` are passed through; the response's tool calls must then pass
        `tool_call_problem` before the caller's validator. Without tools the cache key is unchanged."""
        limit = max_tokens if max_completion_tokens is None else max_completion_tokens
        body = {'model': model, 'messages': messages, 'max_completion_tokens': limit}
        parts = ['chat/completions', model, version, messages, limit]
        if tools is not None or tool_choice is not None:
            problem = _tool_specs(tools, tool_choice)[2]
            if problem:
                raise ProviderError(f'chat tools rejected before sending: {problem}')
            body.update({k: v for k, v in (('tools', tools), ('tool_choice', tool_choice)) if v is not None})
            parts.append({'tools': tools, 'tool_choice': tool_choice})
            caller_validate = validate

            def validate(payload):
                return tool_call_problem(payload, tools, tool_choice) or caller_validate(payload)
        fp = fingerprint(*parts)

        def usage(p):
            u = p.get('usage') or {}
            return {'input_tokens': u.get('prompt_tokens'), 'output_tokens': u.get('completion_tokens'),
                    'reasoning_tokens': (u.get('completion_tokens_details') or {}).get('reasoning_tokens'),
                    'cached_input_tokens': cached_input_tokens(u)}
        return self._call(role, 'chat/completions', model, body, fp, validate, usage)

    def list_models(self) -> list[dict]:
        """Read-only GET {base}/models -> [{'id', 'kind'}] for entries with a usable string ID.
        `kind` is the entry's own `kind` (or `type`) string, else None; no capability is inferred."""
        try:
            payload = self._get('models')
        except urllib.error.HTTPError as exc:
            raise ProviderError(_http_error('models', exc.code, self._error_detail(exc))) from exc
        except (urllib.error.URLError, TimeoutError) as exc:
            raise ProviderError('models transport failure') from exc
        except ValueError as exc:
            raise ProviderError('models returned a non-JSON body') from exc
        entries = payload.get('data', payload.get('models')) if isinstance(payload, dict) else None
        if not isinstance(entries, list):
            raise ProviderError('models response has no model list')
        out = []
        for entry in entries:
            model_id = entry.get('id') if isinstance(entry, dict) else None
            if isinstance(model_id, str) and model_id.strip():
                kind = next((entry[k] for k in ('kind', 'type') if isinstance(entry.get(k), str)), None)
                out.append({'id': model_id, 'kind': kind})
        return out

    def usage_summary(self) -> dict:
        out = {}
        for r in self.usage:
            key = f'{r.role}|{r.requested_model}->{r.returned_model}|{r.mode}|{r.status}'
            s = out.setdefault(key, {'calls': 0, 'input_tokens': 0, 'output_tokens': 0, 'reasoning_tokens': 0,
                                     'cached_input_tokens': 0, 'cost_paise_header': 0.0, 'latency_s': 0.0,
                                     'retries': 0})
            s['calls'] += 1
            for f in ('input_tokens', 'output_tokens', 'reasoning_tokens', 'cached_input_tokens', 'retries'):
                s[f] += getattr(r, f) or 0
            s['cost_paise_header'] += r.cost_paise_header or 0
            s['latency_s'] += r.latency_s
        return out

    def write_usage(self, path: Path):
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, 'w', encoding='utf-8') as f:
            for r in self.usage:
                f.write(json.dumps(asdict(r)) + '\n')


class ReplayGateway(Gateway):
    """Offline test double: serves recorded responses by request content. Never 'live'."""

    mode = 'replay'

    def __init__(self, responder, config: ProviderConfig | None = None):
        super().__init__(config or ProviderConfig(cache_dir=None))
        self.responder = responder           # callable(endpoint, body) -> response dict

    def _post(self, path, body):
        return self.responder(path, body), 0.0

    def _get(self, path):
        return self.responder(path, None)


