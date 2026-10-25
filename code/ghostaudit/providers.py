"""Provider-neutral model gateway (plan section 21) with budget, telemetry and caching.

Two distinct API shapes, never mixed:
  decide(model, state, questions)  POST {base}/decisions        typed answers + probabilities
  chat(model, messages, ...)       POST {base}/chat/completions generative text

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
- Authentication errors stop immediately; transient errors retry a bounded number
  of times with backoff.
"""
from __future__ import annotations

import hashlib
import json
import os
import threading
import time
import urllib.error
import urllib.request
from dataclasses import asdict, dataclass
from pathlib import Path

DEFAULT_BASE_URL = 'https://tokenbazaar.in/api/public/v1'
ALLOWED_ORIGINS = ('https://tokenbazaar.in/',)


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
    cost_paise_header: float | None = None
    cost_usd_reported: float | None = None
    retries: int = 0
    request_id: str | None = None
    fingerprint: str = ''
    error: str = ''


def fingerprint(*parts) -> str:
    return hashlib.sha256(json.dumps(parts, sort_keys=True, ensure_ascii=False).encode('utf-8')).hexdigest()


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
        key = os.environ.get(self.config.api_key_env)
        if not key:
            raise ProviderError(f'{self.config.api_key_env} is not set')
        request = urllib.request.Request(
            f'{self.config.base_url.rstrip("/")}/{path}', data=json.dumps(body).encode('utf-8'), method='POST',
            headers={'Authorization': f'Bearer {key}', 'Content-Type': 'application/json'})
        with _OPENER.open(request, timeout=self.config.timeout_s) as response:
            cost = response.headers.get('x-tokenbaazar-cost-paise')
            return json.loads(response.read().decode('utf-8')), (float(cost) if cost else None)

    def _call(self, role, endpoint, requested_model, body, fp, validate, extract_usage):
        cached = self._cache_get(fp)
        if cached is not None:
            problem = ('cache entry metadata mismatch' if cached.get('requested_model') != requested_model
                       or not isinstance(cached.get('response'), dict) else validate(cached['response']))
            self._record(UsageRecord(role, endpoint, requested_model, cached.get('returned_model'), 'cache',
                                     'invalid' if problem else 'ok', fingerprint=fp, error=problem or ''))
            if not problem:
                return cached['response']
            self._cache_path(fp).unlink()          # invalid entry: discard it and ask again
        reserve = self.config.cost_reserve_paise if endpoint == 'decisions' else self.config.chat_cost_reserve_paise
        self._reserve(reserve)
        payload, cost, retries, latency = self._send(role, endpoint, requested_model, body, fp, reserve)
        rec = UsageRecord(role, endpoint, requested_model, payload.get('model'), self.mode, 'ok', latency,
                          retries=retries, request_id=payload.get('id'), cost_paise_header=cost, fingerprint=fp,
                          **extract_usage(payload))
        problem = validate(payload)
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
                limit = self.config.max_rate_limit_retries if exc.code == 429 else self.config.max_retries
                if exc.code in (401, 403) or retries >= limit or exc.code not in (408, 429, 500, 502, 503, 504):
                    self._record(UsageRecord(role, endpoint, requested_model, None, self.mode, 'error',
                                             time.time() - started, retries=retries, fingerprint=fp,
                                             error=f'HTTP {exc.code}'), reserve)
                    raise ProviderError(f'{endpoint} HTTP {exc.code}') from exc
                retry_after = exc.headers.get('Retry-After') if exc.headers else None
                if retry_after and retry_after.strip().isdigit():
                    wait = min(30.0, float(retry_after))
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

    # --- public API ---------------------------------------------------------------------
    def decide(self, role, model, state, questions, version, validate):
        """Typed decision call. `questions` maps question names to {type, instructions, criteria}."""
        body = {'model': model, 'state': state, 'questions': questions}
        fp = fingerprint('decisions', model, version, questions, state)

        def usage(p):
            u = p.get('usage') or {}
            return {'input_tokens': u.get('input_tokens'), 'output_tokens': u.get('output_tokens'),
                    'cost_usd_reported': u.get('cost')}
        return self._call(role, 'decisions', model, body, fp, validate, usage)

    def chat(self, role, model, messages, version, validate, max_tokens=4000):
        body = {'model': model, 'messages': messages, 'max_tokens': max_tokens, 'temperature': 0}
        fp = fingerprint('chat/completions', model, version, messages, max_tokens)

        def usage(p):
            u = p.get('usage') or {}
            return {'input_tokens': u.get('prompt_tokens'), 'output_tokens': u.get('completion_tokens'),
                    'reasoning_tokens': (u.get('completion_tokens_details') or {}).get('reasoning_tokens')}
        return self._call(role, 'chat/completions', model, body, fp, validate, usage)

    def usage_summary(self) -> dict:
        out = {}
        for r in self.usage:
            key = f'{r.role}|{r.requested_model}->{r.returned_model}|{r.mode}|{r.status}'
            s = out.setdefault(key, {'calls': 0, 'input_tokens': 0, 'output_tokens': 0, 'reasoning_tokens': 0,
                                     'cost_paise_header': 0.0, 'latency_s': 0.0, 'retries': 0})
            s['calls'] += 1
            for f in ('input_tokens', 'output_tokens', 'reasoning_tokens', 'retries'):
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


