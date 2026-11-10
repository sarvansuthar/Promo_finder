"""Write the outputs an editor actually uses: ranked markdown, JSON, CSV, quality."""
from __future__ import annotations

import csv
import json
from pathlib import Path

from .parser import fmt
from .promo import Promo


def _q(s: str, n: int = 140) -> str:
    s = " ".join(s.split())
    return s if len(s) <= n else s[: n - 1] + "…"


def beat(e) -> str:
    if e is None:
        return ""
    return f'{e.speaker.title()}: "{_q(e.text, 110)}"' if e.kind == "dialogue" else _q(e.text, 120)


def to_dict(rank: int, p: Promo) -> dict:
    return {
        "rank": rank,
        "type": p.kind,
        "episodes": p.episodes,
        "duration_s": round(p.duration, 1),
        "segments": [s.to_dict() for s in p.segments],
        "hook": (p.llm or {}).get("hook") or beat(p.hook),
        "hook_at_s": round(p.hook_at, 2),
        "cliffhanger": (p.llm or {}).get("cliffhanger") or beat(p.cliff),
        "why_it_works": p.why,
        "post_reason": (p.llm or {}).get("post_reason", ""),
        "scores": p.scores,
        "flags": p.flags,
        "llm": p.llm,
        "transcript": [{"t": round(off, 2), "ep": e.ep, "src": fmt(e.start), "kind": e.kind,
                        "speaker": e.speaker, "text": e.text} for off, e in p.events],
    }


def write_all(outdir: Path, final: list[Promo], pool: list[Promo], quality: dict, meta: dict) -> None:
    outdir.mkdir(parents=True, exist_ok=True)
    data = [to_dict(i, p) for i, p in enumerate(final, 1)]
    (outdir / "promos.json").write_text(json.dumps({"meta": meta, "promos": data}, indent=2))
    (outdir / "quality.json").write_text(json.dumps(quality, indent=2))

    with open(outdir / "all_candidates.csv", "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["rank_score", "total", "hook", "cliffhanger", "standalone", "intensity", "type",
                    "segments", "duration_s", "hook_beat", "cliff_beat", "flags"])
        for p in sorted(pool, key=lambda p: -p.scores.get("rank", p.total)):
            w.writerow([p.scores.get("rank"), p.total, p.scores["hook"], p.scores["cliffhanger"],
                        p.scores["standalone"], p.scores["intensity"], p.kind,
                        " + ".join(f"E{s.ep} {fmt(s.start)}-{fmt(s.end)}" for s in p.segments),
                        round(p.duration, 1), beat(p.hook), beat(p.cliff), "; ".join(p.flags)])

    md = [f"# Promo picks — {meta['show']}", "",
          f"{len(final)} promos from {meta['episodes']} episodes · mode: `{meta.get('mode', 'rules')}` · scorer: `{meta['provider']}`"
          f"{' / ' + meta['model'] if meta.get('model') else ''} · generated {meta['generated']}", "",
          "Timestamps are source timecodes inside each episode (MM:SS.ss). Rank = posting order.", "",
          "| # | Segments (episode: start → end) | Len | Hook (first 3 s) | Cliffhanger | Score |",
          "|---|---|---|---|---|---|"]
    for d in data:
        segs = "<br>".join(f"E{s['episode']}: {s['start']} → {s['end']}" for s in d["segments"])
        md.append(f"| {d['rank']} | {segs} | {d['duration_s']}s | {_q(d['hook'], 90)} | "
                  f"{_q(d['cliffhanger'], 90)} | {d['scores'].get('rank')} |")
    md += ["", "## Details", ""]
    for d in data:
        md.append(f"### {d['rank']}. Episode{'s' if len(d['episodes']) > 1 else ''} "
                  f"{', '.join(map(str, d['episodes']))} — {d['duration_s']}s {d['type']}")
        for s in d["segments"]:
            md.append(f"- **Segment:** Episode {s['episode']}, {s['start']} → {s['end']} ({s['duration_s']}s)")
        L = d["llm"] or {}
        md.append(f"- **Hook** (at {d['hook_at_s']}s{', ' + L['hook_type'] if L.get('hook_type') else ''}): {d['hook']}")
        md.append(f"- **Cliffhanger**{' (' + L['cliffhanger_type'] + ')' if L.get('cliffhanger_type') else ''}: "
                  f"{d['cliffhanger']}")
        if L.get("stakes"):
            md.append(f"- **Stakes:** {L['stakes']}")
        md.append(f"- **Why it works:** {d['why_it_works']}")
        if d["post_reason"]:
            md.append(f"- **Why post it here:** {d['post_reason']}")
        sc = d["scores"]
        if d["llm"] and "hook_score" in d["llm"]:
            L = d["llm"]
            md.append(f"- LLM scores (0-10): hook {L['hook_score']} · cliffhanger {L['cliffhanger_score']}"
                      f" · standalone {L['standalone_score']}  |  rules total {sc.get('rules_total')}")
        else:
            md.append(f"- Scores: hook {sc['hook']} · cliff {sc['cliffhanger']} · standalone {sc['standalone']}"
                      f" · intensity {sc['intensity']}")
        if d["flags"]:
            md.append(f"- ⚠ Review: {'; '.join(d['flags'])}")
        md.append("")
    (outdir / "promos.md").write_text("\n".join(md))

    b = quality["cliff_detector_benchmark"]
    q = ["# Automatic quality report", "",
         f"- Hard-rule failures (length, cut mid-line, out of bounds): **{quality['hard_failures']}**",
         f"- Promos with a real hook inside 3 s: **{quality['hook_within_3s']}/{quality['promos']}**",
         f"- Promos carrying at least one review flag: **{quality['promos_with_flags']}/{quality['promos']}**",
         f"- Single vs stitched: {quality['single_vs_multi']}",
         f"- Episodes covered: {len(quality['episodes_covered'])} of {meta['episodes']} "
         f"({', '.join(map(str, quality['episodes_covered']))})",
         f"- Candidates scored: {quality['candidates_scored']}", ""]
    if quality.get("hook_types"):
        q += [f"- Hook types: {quality['hook_types']}",
              f"- Cliffhanger types: {quality['cliffhanger_types']}", ""]
    if meta.get("mode") == "llm":
        q += [f"- Playbook: {meta.get('playbook') or 'none (bare brief)'} · prompt {meta.get('prompt_version')}", ""]
    if "llm_validation" in quality:
        v = quality["llm_validation"]
        q += ["## LLM proposals vs hard rules", "",
              f"- Proposed {v['proposed']}, accepted {v['accepted']} ({v['repaired']} auto-repaired), "
              f"rejected {sum(v['rejected'].values())}: {v['rejected'] or 'none'}",
              f"- Episodes where the LLM call failed: {v['episodes_failed'] or 'none'}"]
        if v.get("stopped"):
            q += [f"- **PARTIAL: quota exhausted at episode {v['stopped']['at_episode']}**; not processed: "
                  f"{v['stopped']['missing']}"]
        q += [""]
    if "llm" in meta:
        u = meta["llm"]
        q += [f"- Model `{u['model']}`: {u['api_calls']} API calls, {u['cache_hits']} cache hits, "
              f"{u['input_tokens']} input / {u['output_tokens']} output tokens, {len(u['errors'])} errors", ""]
    if "rules_vs_llm" in quality:
        r = quality["rules_vs_llm"]
        q += [f"- LLM picks that the keyword rules also found: {r['llm_picks_also_found_by_rules']}/{r['of']} "
              "(rules-only list in `rules_comparison/`)", ""]
    q += [
         "## Cliffhanger-detector benchmark", "",
         "Every episode ends on a writer-designed cliffhanger. With the literal word 'cliffhanger' hidden, "
         f"the rule rates the final 6 s as open in **{b['detected']}/{b['episodes']} episodes "
         f"(recall {b['recall']})**. Misses (episode, score, last line):", ""]
    q += [f"- E{n} ({s}): \"{t}\"" for n, s, t in b["misses"]]
    q += ["", "## Per-promo flags", ""]
    for x in quality["per_promo"]:
        if x["hard_failures"] or x["flags"]:
            q.append(f"- #{x['rank']}: " + "; ".join(x["hard_failures"] + x["flags"]))
    (outdir / "quality.md").write_text("\n".join(q))
