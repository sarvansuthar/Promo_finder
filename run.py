#!/usr/bin/env python3
"""Find and rank promos in a microdrama screenplay pack.

LLM mode (default): the model reads every episode, proposes promos, and ranks them;
code validates every timestamp and hard rule. The model is a plug-in:

  --provider gemini     | anthropic | openai | command | mock | dryrun
  --provider my_model.py:complete          (your own function: complete(system, user) -> str)

  python run.py --input show.zip --provider gemini --rpm 8                      # GEMINI_API_KEY
  python run.py --input show.zip --provider anthropic --model claude-haiku-5-5   # ANTHROPIC_API_KEY
  python run.py --input show.zip --provider openai --base-url http://host/v1 --model their-model \
                --api-key-env THEIR_KEY                                          # any OpenAI-style API
  python run.py --input show.zip --provider command --provider-opt cmd="ollama run llama3"
  python run.py --input show.zip --provider examples/custom_provider.py:complete
  python run.py --provider gemini --list-models                                  # which model ids exist
  python run.py --provider openai --base-url ... --check-model                   # 1 test call, then exit

Rules mode (no LLM, free, offline fallback):
  python run.py --input show.zip --mode rules
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

from promo_finder.promo import DEFAULTS
from promo_finder.pipeline import PLAYBOOK, load, run_pipeline


def make_client(a, out: Path):
    """Build the model plug-in from CLI flags. Nothing else in the pipeline knows which model it is."""
    from promo_finder.providers import load_provider
    from promo_finder.llm_client import LLMClient
    opts = dict(kv.split("=", 1) for kv in a.provider_opt)
    if a.base_url:
        opts.setdefault("base_url", a.base_url)
    if a.api_key_env:
        opts.setdefault("api_key_env", a.api_key_env)
    if a.provider == "anthropic":
        opts.setdefault("effort", a.effort)
    provider = load_provider(a.provider, a.model, **opts)
    return LLMClient(provider, out, cache_dir=Path(__file__).parent / ".cache", rpm=a.rpm,
                     use_cache=not a.no_cache)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--input", help="folder of episode .txt files, or a .zip of them")
    ap.add_argument("--out", default="outputs", help="output folder")
    ap.add_argument("--mode", default="llm", choices=["llm", "rules"])
    ap.add_argument("--provider", default=os.environ.get("PROMO_PROVIDER", "gemini"),
                    help="gemini|anthropic|openai|command|mock|dryrun, or path/file.py:function (env PROMO_PROVIDER)")
    ap.add_argument("--model", default=os.environ.get("PROMO_MODEL"), help="model id (env PROMO_MODEL)")
    ap.add_argument("--base-url", help="API base URL for OpenAI-compatible / Anthropic-compatible endpoints")
    ap.add_argument("--api-key-env", help="name of the environment variable holding the API key")
    ap.add_argument("--provider-opt", action="append", default=[], metavar="KEY=VALUE",
                    help="extra provider setting, repeatable (e.g. cmd=..., json_mode=false, effort=high)")
    ap.add_argument("--check-model", action="store_true", help="make one tiny test call and exit")
    ap.add_argument("--list-models", action="store_true", help="list the model ids this provider/key can use")
    ap.add_argument("--no-cache", action="store_true", help="ignore cached LLM replies")
    ap.add_argument("--rpm", type=float, default=0, help="max requests per minute (free tiers: ~8)")
    ap.add_argument("--effort", default="medium", help="Anthropic effort: low|medium|high")
    ap.add_argument("--playbook", default=str(PLAYBOOK),
                    help="markdown file of editorial guidance for the LLM, or 'none' for the bare brief")
    ap.add_argument("--per-episode", type=int, default=3, help="max promos the LLM proposes per episode")
    ap.add_argument("--top", type=int, default=DEFAULTS["top_n"], help="number of promos to return")
    ap.add_argument("--episodes", help="limit to a range, e.g. 1-5 (for quick tests)")
    ap.add_argument("--no-compare", action="store_true", help="skip the rules-only comparison run")
    ap.add_argument("--config", help="JSON file overriding values in promo.DEFAULTS")
    a = ap.parse_args()

    if a.list_models:
        client = make_client(a, Path(a.out))
        if not hasattr(client.p, "list_models"):
            raise SystemExit(f"{client.provider} can't list models")
        print("\n".join(client.p.list_models()))
        raise SystemExit(0)
    if a.check_model:
        client = make_client(a, Path(a.out))
        ok, msg = client.ping()
        print(f"{client.provider} / {client.model}: {msg}")
        raise SystemExit(0 if ok else 1)

    if not a.input:
        ap.error("--input is required (folder of episode .txt files, or a .zip)")
    cfg = {**DEFAULTS, "top_n": a.top}
    if a.config:
        cfg.update(json.loads(Path(a.config).read_text()))
    out = Path(a.out)
    episodes, show = load(a.input, a.episodes)
    if not episodes:
        raise SystemExit("No parsable episodes found.")
    client = playbook = None
    if a.mode == "llm":
        client = make_client(a, out)
        playbook = None if a.playbook == "none" else Path(a.playbook).read_text()
    r = run_pipeline(episodes, show, out, mode=a.mode, client=client, playbook=playbook,
                     playbook_name=Path(a.playbook).name, per_episode=a.per_episode,
                     compare=not a.no_compare, cfg=cfg)
    if client is not None and client.provider == "dryrun":
        return
    final, q = r["final"], r["quality"]
    print(f"\nTop {min(10, len(final))} (full list in {out / 'promos.md'}):")
    for i, p in enumerate(final[:10], 1):
        segs = " + ".join(f"E{s.ep} {s.start:.1f}-{s.end:.1f}s" for s in p.segments)
        print(f"{i:2d}. {segs} ({p.duration:.0f}s)  {p.why[:90]}")
    if r["stopped"]:
        st = r["stopped"]
        print(f"\n*** PARTIAL RESULT: quota ran out at episode {st['at_episode']}; "
              f"{len(st['missing'])} episodes not processed. Re-run the same command when the quota resets "
              "(finished episodes come from cache), or use another key / --model.")
    print(f"\nQuality: {q['hard_failures']} hard failures, {q['hook_within_3s']}/{q['promos']} hooks in 3s -> "
          f"{out / 'quality.md'}")
    if q.get("hook_types"):
        print(f"Hook types in the final list: {q['hook_types']}")


if __name__ == "__main__":
    main()
