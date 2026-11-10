"""Template: plug ANY model into the promo finder without touching the pipeline.

Use it:
    python run.py --input show.zip --provider examples/custom_provider.py:complete
    python run.py --input show.zip --provider examples/custom_provider.py:MyProvider --model their-model-id

The contract is tiny: take the system prompt and the user prompt, return the model's reply as
text. The pipeline asks for JSON in the prompt, then extracts, checks and (once) repairs the
reply itself, so you don't need to handle any of that here. To signal a rate limit or a
transient error, raise promo_finder.providers.RetryableError and the client will back off.
"""
import json
import os
import urllib.request

# --------------------------------------------------------------------------------------------
# Option A: a plain function. Edit the URL, payload and response path for the API you're given.
# --------------------------------------------------------------------------------------------
def complete(system: str, user: str) -> str:
    url = os.environ["MY_LLM_URL"]                       # e.g. https://llm.internal/v1/generate
    headers = {"Content-Type": "application/json"}
    if os.environ.get("MY_LLM_KEY"):
        headers["Authorization"] = f"Bearer {os.environ['MY_LLM_KEY']}"
    payload = {"system": system, "prompt": user, "max_tokens": 8000}   # <- adapt to their API
    req = urllib.request.Request(url, json.dumps(payload).encode(), headers)
    with urllib.request.urlopen(req, timeout=600) as r:
        body = json.loads(r.read())
    return body["text"]                                  # <- adapt: where the reply text lives


# --------------------------------------------------------------------------------------------
# Option B: a class, when you want --model / --provider-opt values or a client kept across calls.
# --------------------------------------------------------------------------------------------
class MyProvider:
    name = "my-provider"
    native_schema = False        # set True only if the API itself enforces the JSON schema

    def __init__(self, model=None, **opts):
        self.model = model or "default-model"
        self.opts = opts         # anything passed as --provider-opt key=value

    def complete(self, system: str, user: str, schema: dict | None) -> str:
        return complete(system, user)
