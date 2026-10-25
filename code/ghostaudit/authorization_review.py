"""Independent request-only confirmation; never sees or writes the primary agent's plan."""
import hashlib
import json
import math

from .intent_models import PROMPTS
from .providers import ProviderError, strict_json


class AuthorizationReview:
    def __init__(self, gateway, model):
        self.gateway, self.model, self.results = gateway, model, {}
        self.prompt = (PROMPTS / 'authorization_review_v1.md').read_text(encoding='utf-8')
        self.version = 'authorization-review-v1:' + hashlib.sha256(self.prompt.encode()).hexdigest()[:16]

    def __call__(self, text):
        if text in self.results:
            return self.results[text]

        def validate(payload):
            try:
                r = strict_json(payload['choices'][0]['message']['content'])
                if set(r) != {'decision', 'quote', 'confidence', 'conflicting'}:
                    return 'authorization review fields differ'
                if r['decision'] not in ('DELETE', 'NON_DELETION', 'UNRESOLVED') or r['quote'] != text:
                    return 'authorization review decision or full request quote invalid'
                if type(r['confidence']) not in (int, float) or not math.isfinite(r['confidence']) \
                        or not 0 <= r['confidence'] <= 1 or type(r['conflicting']) is not bool:
                    return 'authorization review probability/flag invalid'
            except (KeyError, TypeError, ValueError):
                return 'malformed authorization review'
            return None
        try:
            messages = [{'role': 'system', 'content': self.prompt},
                        {'role': 'user', 'content': json.dumps({'request': text}, ensure_ascii=False)}]
            if getattr(self, 'admit', None) and not self.admit(self.model, messages, 1000):
                return None
            payload = self.gateway.chat('authorization_review', self.model,
                messages,
                self.version, validate, max_tokens=1000)
            result = strict_json(payload['choices'][0]['message']['content'])
        except ProviderError:
            result = None
        self.results[text] = result  # no repeated review spending during feedback for the same request
        return result
