"""Model providers. The pipeline depends only on this contract:

    provider.complete(system: str, user: str, schema: dict | None) -> str | Completion

Return the model's text reply. JSON extraction, checking, repair and retries happen in
LLMClient, so a provider can be a 5-line function around any API.

Built in:
  openai     any OpenAI-compatible endpoint: OpenAI, Azure-style proxies, Groq, OpenRouter,
             Together, vLLM, LM Studio, Ollama.  --base-url, --api-key-env
  gemini     preset of `openai` pointed at Google's endpoint (GEMINI_API_KEY)
  anthropic  Claude via the Anthropic SDK (ANTHROPIC_API_KEY); native JSON-schema output
  command    any CLI that reads the prompt on stdin and prints the reply:
             --provider command --provider-opt cmd="ollama run llama3"
  mock       offline stand-in that returns valid answers without a model (demos, tests)

Bring your own:
  --provider path/to/file.py:name    or    --provider package.module:name
where `name` is either
  * a function  complete(system, user) -> str            (simplest), or
  * a class     Name(model=..., **opts) with .complete(system, user, schema) -> str
"""
from __future__ import annotations

import importlib
import importlib.util
import inspect
import json
import os
import re
import shlex
import subprocess
from dataclasses import dataclass
from pathlib import Path


@dataclass
class Completion:
    text: str
    input_tokens: int = 0
    output_tokens: int = 0


class RetryableError(Exception):
    """Raise from a provider for rate limits / transient server errors; the client backs off.
    `wait` (seconds) is how long the server asked us to wait, if it said."""
    def __init__(self, msg, wait: float | None = None):
        super().__init__(msg)
        self.wait = wait


class QuotaExhausted(Exception):
    """The account's quota is used up (e.g. a free tier's daily cap). Retrying won't help:
    the pipeline stops calling, keeps what it has, and tells the user how to resume."""


def _api_message(body: str) -> str:
    m = re.search(r'"message":\s*"([^"]+)"', body)
    return (m.group(1) if m else body)[:160]


class Provider:
    name = "base"
    native_schema = False          # True if the API itself enforces the JSON schema

    def __init__(self, model: str | None = None, **opts):
        self.model = model or ""
        self.opts = opts

    def complete(self, system: str, user: str, schema: dict | None) -> str | Completion:
        raise NotImplementedError


# ------------------------------------------------------------------ OpenAI-compatible
class OpenAICompatible(Provider):
    name = "openai"
    default_model = "gpt-4o-mini"
    default_base_url = None
    default_key_env = "OPENAI_API_KEY"

    def __init__(self, model=None, base_url=None, api_key_env=None, json_mode="true", timeout="120",
                 api_key=None, **opts):
        super().__init__(model or self.default_model, **opts)
        try:
            from openai import OpenAI
            import openai
        except ImportError:
            raise SystemExit(f"{self.name} needs the openai package: pip3 install openai") from None
        self._err = openai
        key_env = api_key_env or self.default_key_env
        key = api_key or os.environ.get(key_env) or ("not-needed" if base_url or self.default_base_url else None)
        if not key:
            raise SystemExit(f"{self.name}: set {key_env} (or pass --api-key-env NAME)")
        # fail fast on a hung request (seen on Gemini's free tier) and let LLMClient do the
        # retrying, instead of the SDK's silent 10-minute timeout x 3 attempts
        self.client = OpenAI(api_key=key, base_url=base_url or os.environ.get("OPENAI_BASE_URL")
                             or self.default_base_url, timeout=float(timeout), max_retries=0)
        self.json_mode = str(json_mode).lower() not in ("0", "false", "no")

    def list_models(self) -> list[str]:
        return sorted(m.id.removeprefix("models/") for m in self.client.models.list())

    def complete(self, system, user, schema):
        kw = dict(model=self.model, messages=[{"role": "system", "content": system},
                                              {"role": "user", "content": user}])
        if self.json_mode:
            kw["response_format"] = {"type": "json_object"}
        try:
            r = self.client.chat.completions.create(**kw)
        except self._err.APIStatusError as e:
            # some servers (e.g. Gemini) return error bodies the SDK can't summarise: show the raw body
            body = (getattr(e, "response", None) and e.response.text or "")[:500]
            if e.status_code == 429:
                # out of money / daily cap: waiting won't help, so stop instead of retrying
                if re.search(r"per ?day|PerDay|daily|insufficient_quota|no credits|credit balance|billing|"
                             r"exceeded your current quota", body, re.I):
                    raise QuotaExhausted(_api_message(body)) from None
                m = re.search(r'"retryDelay":\s*"(\d+(?:\.\d+)?)s"', body)
                raise RetryableError(f"429 rate limit: {_api_message(body)}",
                                     wait=float(m.group(1)) + 1 if m else None) from None
            if e.status_code >= 500:
                raise RetryableError(f"{e.status_code}: {_api_message(body)}") from None
            if self.json_mode and e.status_code == 400 and ("response_format" in body or "json" in body.lower()):
                self.json_mode = False              # endpoint doesn't support JSON mode: fall back to text
                return self.complete(system, user, schema)
            hint = " (model name not available: run with --list-models)" if e.status_code == 404 else ""
            raise RuntimeError(f"HTTP {e.status_code} from {self.name}{hint}: "
                               f"{_api_message(body) if body else e}") from None
        except self._err.APIConnectionError as e:
            raise RetryableError(f"connection: {e}")
        u = r.usage
        return Completion(r.choices[0].message.content or "",
                          getattr(u, "prompt_tokens", 0) or 0, getattr(u, "completion_tokens", 0) or 0)


class Gemini(OpenAICompatible):
    name = "gemini"
    default_model = "gemini-3.5-flash"      # 3.8-flash timed out on the free tier; see --list-models
    default_base_url = "https://generativelanguage.googleapis.com/v1beta/openai/"
    default_key_env = "GEMINI_API_KEY"

    def __init__(self, model=None, base_url=None, api_key_env=None, api_key=None, **opts):
        if api_key:                                     # passed directly (web app): skip env checks
            super().__init__(model, base_url or self.default_base_url, api_key_env, api_key=api_key, **opts)
            return
        if not api_key_env and not os.environ.get("GEMINI_API_KEY") and os.environ.get("GOOGLE_API_KEY"):
            api_key_env = "GOOGLE_API_KEY"
        if not os.environ.get(api_key_env or "GEMINI_API_KEY"):
            raise SystemExit("gemini: set GEMINI_API_KEY (free key: https://aistudio.google.com/apikey)")
        key = os.environ.get(api_key_env or "GEMINI_API_KEY", "")
        has_junk = any(ch in key for ch in " \t\"'")
        if has_junk or len(key) < 30 or "your" in key.lower():
            junk = ", contains spaces/quotes" if has_junk else ""
            print(f"warning: GEMINI_API_KEY looks wrong ({len(key)} chars{junk}). "
                  "Copy it again from https://aistudio.google.com/apikey")
        super().__init__(model, base_url or self.default_base_url, api_key_env, **opts)


# ------------------------------------------------------------------ Anthropic
class Anthropic(Provider):
    name = "anthropic"
    native_schema = True

    def __init__(self, model=None, base_url=None, api_key_env=None, effort="medium", api_key=None, **opts):
        super().__init__(model or "claude-opus-5-5", **opts)
        try:
            import anthropic
        except ImportError:
            raise SystemExit("anthropic needs the anthropic package: pip3 install anthropic") from None
        self._sdk = anthropic
        kw = {}
        if base_url:
            kw["base_url"] = base_url
        if api_key:
            kw["api_key"] = api_key
        elif api_key_env:
            kw["api_key"] = os.environ.get(api_key_env)
        self.client = anthropic.Anthropic(**kw)
        self.effort = effort

    def complete(self, system, user, schema):
        a = self._sdk
        oc = {"effort": self.effort}
        if schema:
            oc["format"] = {"type": "json_schema", "schema": schema}
        try:
            with self.client.beta.messages.stream(
                model=self.model, max_tokens=32000, system=system,
                messages=[{"role": "user", "content": user}], output_config=oc,
                betas=["server-side-fallback-2026-07-01"], fallbacks="default",
            ) as stream:
                r = stream.get_final_message()
        except (a.RateLimitError, a.InternalServerError, a.APIConnectionError) as e:
            raise RetryableError(type(e).__name__)
        if r.stop_reason == "refusal":
            raise RuntimeError("model refused")
        if r.stop_reason == "max_tokens":
            raise RuntimeError("hit max_tokens")
        text = next((b.text for b in r.content if b.type == "text"), "")
        return Completion(text, r.usage.input_tokens, r.usage.output_tokens)


# ------------------------------------------------------------------ any CLI
class Command(Provider):
    """Pipe the prompt into a command and read the reply from stdout."""
    name = "command"

    def __init__(self, model=None, cmd=None, timeout="600", **opts):
        super().__init__(model, **opts)
        if not cmd:
            raise SystemExit('command provider needs --provider-opt cmd="your-cli --flags"')
        self.argv = shlex.split(cmd.replace("{model}", self.model))
        self.timeout = float(timeout)

    def complete(self, system, user, schema):
        r = subprocess.run(self.argv, input=f"{system}\n\n{user}", capture_output=True, text=True,
                           timeout=self.timeout)
        if r.returncode != 0:
            raise RuntimeError(f"command exited {r.returncode}: {r.stderr[:200]}")
        return r.stdout


# ------------------------------------------------------------------ offline mock
class Mock(Provider):
    """Returns structurally valid answers without any model, by reading the beat list in the
    prompt. Lets the whole LLM pipeline run offline for demos and tests. Its picks are NOT
    editorial judgments."""
    name = "mock"
    BEAT = re.compile(r"^\[(\d+)\] (\d+):(\d+\.\d+)-(\d+):(\d+\.\d+) ([^:]+): (.*)$", re.M)

    def __init__(self, model=None, **opts):
        super().__init__(model or "mock", **opts)

    def complete(self, system, user, schema):
        if "17 + 25" in user:                                   # --check-model ping
            return json.dumps({"ok": "yes", "sum": 42})
        if "POSTING ORDER" in user:
            ids = re.findall(r"^(P\d+) \|", user, re.M)
            return json.dumps({"ranking": [{"id": i, "reason": "mock order"} for i in ids]})
        beats = [(int(i), int(m1) * 60 + float(s1), int(m2) * 60 + float(s2), who, txt)
                 for i, m1, s1, m2, s2, who, txt in self.BEAT.findall(user)]
        promos = []
        loud = [b for b in beats if "!" in b[4] or "?" in b[4]]
        for start in loud[::max(1, len(loud) // 3)][:3]:
            ends = [b for b in beats if 25 <= b[2] - start[1] <= 45]
            if not ends:
                continue
            end = ends[-1]
            promos.append({"hook_type": "threat", "hook": start[4][:60], "hook_beat": start[0],
                           "cliffhanger_type": "question_unanswered", "cliffhanger": end[4][:60],
                           "cliff_beat": end[0], "stakes": "mock",
                           "segments": [{"start_beat": start[0], "end_beat": end[0]}],
                           "why": "mock pick (no model)", "hook_score": 5, "cliffhanger_score": 5,
                           "standalone_score": 5})
        return json.dumps({"promos": promos})


class DryRun(Provider):
    name = "dryrun"


BUILTIN = {c.name: c for c in (OpenAICompatible, Gemini, Anthropic, Command, Mock, DryRun)}


class _FunctionProvider(Provider):
    def __init__(self, fn, model=None, **opts):
        super().__init__(model, **opts)
        self.fn = fn
        self.name = getattr(fn, "__name__", "custom")

    def complete(self, system, user, schema):
        return self.fn(system, user)


def load_provider(spec: str, model: str | None = None, **opts) -> Provider:
    """Built-in name, or 'file.py:attr' / 'package.module:attr' for a custom provider."""
    if spec in BUILTIN:
        return BUILTIN[spec](model=model, **opts)
    if ":" not in spec:
        raise SystemExit(f"Unknown provider {spec!r}. Built-ins: {', '.join(BUILTIN)}; "
                         "or use path/to/file.py:function_or_class")
    target, attr = spec.rsplit(":", 1)
    if target.endswith(".py"):
        path = Path(target).resolve()
        if not path.exists():
            raise SystemExit(f"Provider file not found: {path}")
        mod_spec = importlib.util.spec_from_file_location(path.stem, path)
        mod = importlib.util.module_from_spec(mod_spec)
        mod_spec.loader.exec_module(mod)
    else:
        mod = importlib.import_module(target)
    obj = getattr(mod, attr, None)
    if obj is None:
        raise SystemExit(f"{target} has no attribute {attr!r}")
    if inspect.isclass(obj):
        inst = obj(model=model, **opts)
        if not hasattr(inst, "native_schema"):
            inst.native_schema = False
        if not getattr(inst, "model", None):
            inst.model = model or attr
        inst.name = getattr(inst, "name", attr)
        return inst
    if callable(obj):
        return _FunctionProvider(obj, model or attr, **opts)
    raise SystemExit(f"{spec} is neither a function nor a class")
