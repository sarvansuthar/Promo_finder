"""Automatic quality checks on the tool's own picks.

1. Hard checks per promo (must never fail): length 20-60 s, segments inside the
   episode, no cut in the middle of a spoken line.
2. Soft checks per promo (flag for the editor): hook later than 3 s, weak hook,
   tension released at the end, weak cliffhanger, context debt, too many
   speakers, stitched across distant episodes, overlapping another pick.
3. Detector benchmark: every episode in this format ends on a writer-designed
   cliffhanger, so "does the cliff detector (with the writer's literal
   'cliffhanger' cue word hidden) rate each episode's final beats as open?" is a
   free, label-less recall test of the cliffhanger rule.
"""
from __future__ import annotations

import re
from copy import copy

from .parser import Episode
from .promo import Promo, overlap
from . import signals as S


def hard_checks(p: Promo, eps: dict[int, Episode], cfg: dict) -> list[str]:
    errs = []
    if not (cfg["min_len"] - 0.05 <= p.duration <= cfg["max_len"] + 0.05):
        errs.append(f"length {p.duration:.1f}s outside {cfg['min_len']:.0f}-{cfg['max_len']:.0f}s")
    for s in p.segments:
        ep = eps[s.ep]
        if s.start < 0 or s.end > ep.duration + 0.05 or s.end <= s.start:
            errs.append(f"segment ep{s.ep} {s.start}-{s.end} outside episode")
        for e in ep.events:
            if e.kind != "dialogue":
                continue
            for t in (s.start, s.end):
                if e.start + 0.05 < t < e.end - 0.05:
                    errs.append(f"cuts mid-line at ep{s.ep} {t:.2f}s: '{e.text[:40]}'")
    return errs


def soft_checks(p: Promo, others: list[Promo]) -> list[str]:
    out = list(p.flags)
    if p.hook_at > 3.0:
        out.append(f"hook lands late ({p.hook_at:.1f}s)")
    for o in others:
        if o is not p and overlap(p, o) > 0.2:
            out.append("shares footage with another pick")
            break
    return out


def cliff_detector_recall(episodes: list[Episode], tail_s: float = 6.0) -> dict:
    hits, total, misses = 0, 0, []
    for ep in episodes:
        tail = [e for e in ep.events if e.end >= ep.duration - tail_s]
        if not tail:
            continue
        total += 1
        best = 0.0
        for e in tail:
            e2 = copy(e)
            e2.text = re.sub(r"cliffhanger", "", e.text, flags=re.I)
            best = max(best, S.cliff_strength(e2))
        if best >= 0.5:
            hits += 1
        else:
            last_line = next((e.text for e in reversed(ep.events) if e.kind == "dialogue"), "")
            misses.append((ep.num, round(best, 2), last_line[:70]))
    return {"episodes": total, "detected": hits, "recall": round(hits / max(total, 1), 3), "misses": misses}


def run(final: list[Promo], pool: list[Promo], episodes: list[Episode], cfg: dict) -> dict:
    eps = {e.num: e for e in episodes}
    per = []
    for i, p in enumerate(final, 1):
        per.append({"rank": i, "hard_failures": hard_checks(p, eps, cfg), "flags": soft_checks(p, final)})
    covered = sorted({e for p in final for e in p.episodes})
    def tally(key):
        out: dict[str, int] = {}
        for p in final:
            v = (p.llm or {}).get(key)
            if v:
                out[v] = out.get(v, 0) + 1
        return dict(sorted(out.items(), key=lambda kv: -kv[1]))
    return {
        "promos": len(final), "candidates_scored": len(pool),
        "hard_failures": sum(len(x["hard_failures"]) for x in per),
        "promos_with_flags": sum(bool(x["flags"]) for x in per),
        "single_vs_multi": {"single": sum(p.kind == "single" for p in final),
                            "multi": sum(p.kind == "multi" for p in final)},
        "episodes_covered": covered,
        "hook_within_3s": sum(p.hook_at <= 3.0 and (p.llm["hook_score"] >= 6 if p.llm and "hook_score" in p.llm
                                                     else p.scores["hook"] >= 0.5) for p in final),
        "cliff_detector_benchmark": cliff_detector_recall(episodes),
        "hook_types": tally("hook_type"),
        "cliffhanger_types": tally("cliffhanger_type"),
        "per_promo": per,
    }
