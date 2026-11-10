"""Model-agnostic LLM client: complete_json(system, user, schema) -> dict | None.

Everything that must work the same for *any* model lives here, not in the providers:
  * the JSON-schema instruction (skipped when the provider enforces schemas natively)
  * pulling JSON out of chatty replies / ```json fences
  * checking the reply against the schema, and one repair round-trip telling the model
    exactly what was wrong (weaker or unfamiliar models usually fix it on the second try)
  * retries with backoff, requests-per-minute throttling, token accounting
  * an on-disk cache keyed by provider + model + prompt, so re-runs are free and identical
"""
from __future__ import annotations

import hashlib
import json
import re
import time
from pathlib import Path

from .providers import Completion, Provider, QuotaExhausted, RetryableError


def extract_json(text: str):
    t = re.sub(r"^\s*```(?:json)?\s*|\s*```\s*$", "", text.strip())
    try:
        return json.loads(t)
    except json.JSONDecodeError:
        a, b = t.find("{"), t.rfind("}")
        if a != -1 and b > a:
            return json.loads(t[a:b + 1])
        raise


def check(obj, schema: dict, path: str = "$") -> list[str]:
    """Small JSON-schema subset check (type / required / properties / items / enum).
    Coerces numeric strings to integers in place, since models often quote numbers."""
    errs: list[str] = []
    t = schema.get("type")
    if t == "object":
        if not isinstance(obj, dict):
            return [f"{path} must be an object"]
        for k in schema.get("required", []):
            if k not in obj:
                errs.append(f"{path}.{k} is missing")
        for k, sub in schema.get("properties", {}).items():
            if k in obj:
                if "enum" in sub and isinstance(obj[k], str):
                    norm = re.sub(r"[\s-]+", "_", obj[k].strip().lower())
                    if norm in sub["enum"]:
                        obj[k] = norm                      # "Shock action" -> "shock_action"
                if sub.get("type") == "integer" and isinstance(obj[k], (str, float)):
                    try:
                        obj[k] = int(float(obj[k]))
                    except ValueError:
                        pass
                errs += check(obj[k], sub, f"{path}.{k}")
    elif t == "array":
        if not isinstance(obj, list):
            return [f"{path} must be an array"]
        for i, item in enumerate(obj):
            errs += check(item, schema.get("items", {}), f"{path}[{i}]")
    elif t == "integer" and not (isinstance(obj, int) and not isinstance(obj, bool)):
        errs.append(f"{path} must be an integer")
    elif t == "string":
        if not isinstance(obj, str):
            errs.append(f"{path} must be a string")
        # an off-list label is kept as-is: labels are for auditing, the beat ids carry the pick
    return errs[:12]


class LLMClient:
    def __init__(self, provider: Provider, outdir: str | Path = "outputs",
                 cache_dir: str | Path = ".cache", rpm: float = 0, use_cache: bool = True):
        self.p = provider
        self.provider = getattr(provider, "name", "custom")
        self.model = getattr(provider, "model", "") or ""
        self.outdir, self.cache_dir = Path(outdir), Path(cache_dir)
        self.min_gap = 60.0 / rpm if rpm else 0.0
        self.use_cache = use_cache and self.provider not in ("mock", "dryrun")
        self._last = 0.0
        self.calls = self.cache_hits = self.repairs = 0
        self.in_tokens = self.out_tokens = 0
        self.errors: list[str] = []
        self._n_dry = 0

    # ------------------------------------------------------------------ public
    def complete_json(self, system: str, user: str, schema: dict, tag: str = "call") -> dict | None:
        if not getattr(self.p, "native_schema", False):
            system = (system + "\n\nReply with ONLY a JSON object (no prose, no code fences) that matches "
                      "this JSON Schema:\n" + json.dumps(schema))
        if self.provider == "dryrun":
            d = self.outdir / "llm_prompts"
            d.mkdir(parents=True, exist_ok=True)
            self._n_dry += 1
            (d / f"{self._n_dry:03d}_{tag}.txt").write_text(f"=== SYSTEM ===\n{system}\n\n=== USER ===\n{user}")
            return None
        key = hashlib.sha256(json.dumps([self.provider, self.model, system, user]).encode()).hexdigest()
        cache = self.cache_dir / f"{key}.json"
        if self.use_cache and cache.exists():
            self.cache_hits += 1
            return json.loads(cache.read_text())

        msg = user
        for attempt in range(2):                      # first answer + one repair round
            text = self._call(system, msg, schema, tag)
            if text is None:
                return None
            try:
                obj = extract_json(text)
                problems = check(obj, schema)
            except (json.JSONDecodeError, ValueError) as e:
                obj, problems = None, [f"not valid JSON ({e})"]
            if not problems:
                if self.use_cache:
                    self.cache_dir.mkdir(parents=True, exist_ok=True)
                    cache.write_text(json.dumps(obj))
                return obj
            if attempt == 0:
                self.repairs += 1
                msg = (user + "\n\n---\nYour previous reply could not be used:\n- " + "\n- ".join(problems)
                       + "\nReply again with ONLY the corrected JSON object.")
        self.errors.append(f"{tag}: unusable reply after repair: {problems[:3]}")
        return None

    def ping(self) -> tuple[bool, str]:
        """One tiny call to check the model is reachable and can follow a JSON schema."""
        schema = {"type": "object", "properties": {"ok": {"type": "string", "enum": ["yes"]},
                                                   "sum": {"type": "integer"}},
                  "required": ["ok", "sum"], "additionalProperties": False}
        saved, self.use_cache = self.use_cache, False
        t0 = time.time()
        res = self.complete_json("You are a test endpoint.", 'Return ok="yes" and sum = 17 + 25.', schema, "ping")
        self.use_cache = saved
        if res and res.get("sum") == 42:
            return True, f"OK in {time.time() - t0:.1f}s (repairs needed: {self.repairs})"
        return False, f"failed: {res} {self.errors[-1:] }"

    def summary(self) -> dict:
        return {"provider": self.provider, "model": self.model, "api_calls": self.calls,
                "cache_hits": self.cache_hits, "json_repairs": self.repairs,
                "input_tokens": self.in_tokens, "output_tokens": self.out_tokens,
                "errors": self.errors[:20]}

    # ----------------------------------------------------------------- private
    def _call(self, system: str, user: str, schema: dict, tag: str) -> str | None:
        for attempt in range(3):
            wait = self.min_gap - (time.time() - self._last)
            if wait > 0:
                time.sleep(wait)
            self._last = time.time()
            self.calls += 1
            try:
                r = self.p.complete(system, user, schema)
            except RetryableError as e:
                self.errors.append(f"{tag}: {e} (retry {attempt + 1})")
                wait = e.wait if e.wait else min(60, 10 * 2 ** attempt)
                if wait > 300:                      # server wants us gone for >5 min: treat as quota
                    raise QuotaExhausted(f"{e} (server asked to wait {wait:.0f}s)")
                print(f"    {tag}: {str(e)[:100]} - waiting {wait:.0f}s, retry {attempt + 1}/3")
                time.sleep(wait)
                continue
            except QuotaExhausted:
                raise
            except Exception as e:                         # auth, refusal, bad request: don't loop
                self.errors.append(f"{tag}: {type(e).__name__}: {str(e)[:200]}")
                return None
            if isinstance(r, Completion):
                self.in_tokens += r.input_tokens
                self.out_tokens += r.output_tokens
                return r.text
            return str(r)
        return None
