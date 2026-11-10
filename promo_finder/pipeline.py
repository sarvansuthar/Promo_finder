"""The whole flow in one function, shared by the CLI (run.py) and the web app (app.py)."""
from __future__ import annotations

import re
import time
from datetime import datetime
from pathlib import Path

from . import quality, report, signals
from .parser import Episode, load_show
from .promo import DEFAULTS, build_pool, overlap, select

PLAYBOOK = Path(__file__).resolve().parent.parent / "prompts" / "promo_playbook.md"


def load(input_path: str | Path, episodes: str | None = None) -> tuple[list[Episode], str]:
    """Parse a folder/zip and optionally keep an episode range like '1-5'."""
    eps = load_show(input_path)
    if episodes:
        lo, hi = (int(x) for x in episodes.split("-"))
        eps = [e for e in eps if lo <= e.num <= hi]
    src = Path(input_path)
    show = re.sub(r"(-scripts)?(\s*\(\d+\))?$", "", src.name if src.is_dir() else src.stem)
    return eps, show


def run_pipeline(episodes: list[Episode], show: str, out: str | Path, *, mode: str = "llm",
                 client=None, playbook: str | None = None, playbook_name: str | None = None,
                 per_episode: int = 3, compare: bool = True, cfg: dict | None = None,
                 log=print) -> dict:
    """Run rules and/or LLM selection, quality checks, and write every output file to `out`.

    Returns {"final", "pool", "quality", "meta", "stopped"}; `client` is an LLMClient (llm mode).
    """
    cfg = {**DEFAULTS, **(cfg or {})}
    out = Path(out)
    t0 = time.time()
    if not episodes:
        raise ValueError("No parsable episodes found.")
    for e in episodes:
        signals.annotate(e.events)
    log(f"Parsed {len(episodes)} episodes, {sum(len(e.events) for e in episodes)} timed beats "
        f"({sum(e.skipped_blocks for e in episodes)} unparsable blocks skipped)")
    meta = {"show": show, "episodes": len(episodes), "mode": mode, "provider": None, "model": None,
            "generated": datetime.now().isoformat(timespec="seconds"), "config": cfg}

    rules_pool = rules_final = None
    if mode == "rules" or compare:
        rules_pool = build_pool(episodes, cfg)
        rules_final = select(rules_pool, cfg)
        log(f"Rules: scored {len(rules_pool)} candidates in {time.time() - t0:.1f}s")

    llm_stats = None
    if mode == "rules":
        final, pool = rules_final, rules_pool
        meta["provider"] = "rules"
    else:
        from . import llm_finder
        system = llm_finder.build_system(playbook)
        meta.update(provider=client.provider, model=client.model,
                    playbook=playbook_name if playbook else None, prompt_version=llm_finder.PROMPT_VERSION)
        log(f"Playbook: {meta['playbook'] or 'none (bare brief)'}")
        log(f"LLM find: {client.provider} / {client.model}, one call per episode ...")
        pool, llm_stats = llm_finder.find(episodes, client, show, cfg, per_episode, log=log, system=system)
        if client.provider == "dryrun":
            stand_in = rules_pool or build_pool(episodes, cfg)     # so the rank prompt can be inspected
            for p in stand_in:
                p.llm = {"hook": "(rules) " + (p.hook.text[:60] if p.hook else ""),
                         "cliffhanger": "(rules) " + (p.cliff.text[:60] if p.cliff else "")}
                p.scores["final"] = p.total
            llm_finder.rank(stand_in, client, show, episodes[0].logline, cfg, system=system)
            log(f"Dry run: prompts written to {out / 'llm_prompts'}")
            return {"final": [], "pool": [], "quality": {}, "meta": meta, "stopped": None}
        log(f"LLM proposed {llm_stats['proposed']}, accepted {llm_stats['accepted']} "
            f"({llm_stats['repaired']} auto-repaired), rejected {llm_stats['rejected']}")
        final = llm_finder.rank(pool, client, show, episodes[0].logline, cfg, system=system) if pool else []
        meta["llm"] = client.summary()
        log(f"LLM rank: {len(final)} promos. Calls {client.calls}, cache hits {client.cache_hits}, "
            f"tokens in/out {client.in_tokens}/{client.out_tokens}, errors {len(client.errors)}")

    q = quality.run(final, pool, episodes, cfg)
    if llm_stats:
        q["llm_validation"] = dict(llm_stats)
    if rules_final is not None and mode == "llm":
        agree = sum(any(overlap(p, r) > 0.3 for r in rules_final) for p in final)
        q["rules_vs_llm"] = {"llm_picks_also_found_by_rules": agree, "of": len(final)}
        report.write_all(out / "rules_comparison", rules_final, rules_pool,
                         quality.run(rules_final, rules_pool, episodes, cfg),
                         {**meta, "provider": "rules", "model": None})
    meta["runtime_s"] = round(time.time() - t0, 1)
    report.write_all(out, final, pool, q, meta)
    return {"final": final, "pool": pool, "quality": q, "meta": meta,
            "stopped": (llm_stats or {}).get("stopped")}
