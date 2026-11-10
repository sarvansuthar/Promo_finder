"""LLM-first promo finding.

Stage 1  FIND   one call per episode: the model reads the whole episode as numbered,
                timestamped beats and proposes its best promos *by beat id*.
Stage 2  CHECK  code turns beat ids into real timecodes and enforces the hard rules
                (20-60 s, cut only where nobody is mid-line, hook inside 3 s). Proposals
                it can't repair are rejected and counted, never silently kept.
Stage 3  RANK   one show-level call sees every surviving candidate and decides the posting
                order: premise promos first, story-level duplicates removed.

The model never types timestamps, so it can't invent them; the code never judges drama,
which keyword rules do badly.
"""
from __future__ import annotations

from .llm_client import LLMClient
from .providers import QuotaExhausted
from .parser import Episode, Event, fmt
from .promo import Promo, Segment, legal_cuts, make_promo, nms, overlap

PROMPT_VERSION = "v2"

ROLE = """You are the senior promo editor for a vertical microdrama app (ReelShort / DramaBox style).
You cut 20-60 second promos from a show's own footage. They run on TikTok, Reels and Shorts to win NEW viewers.
Most moments are NOT promo-worthy. Be strict."""

# used when no playbook is given (--playbook none): the bare brief
BASE_RUBRIC = """A great promo:
1. HOOK: something in the first 3 seconds stops a scrolling stranger: a slap, a violent act, a reveal, a shocking or cruel line.
2. CLIFFHANGER: it cuts while something is unresolved (a question unanswered, a threat about to land, a blow about to fall,
   a proposal awaiting a reply, someone bursting in), so the viewer must know what happens next.
   Cutting AFTER the tension resolves (a hug, a relieved smile, the answer given) is a bad promo.
3. STANDALONE: a stranger who has never seen the show understands who is wronging whom and what is at stake.
   Avoid clips that depend on earlier episodes or on names never explained in the clip."""

HOOK_TYPES = ["shock_action", "reveal", "contradiction", "threat", "injustice", "curiosity_gap",
              "emotional_conflict", "pattern_interrupt"]
CLIFF_TYPES = ["question_unanswered", "threat_imminent", "blow_about_to_land", "reveal_interrupted",
               "arrival", "decision_pending", "reaction_hold"]


def build_system(playbook: str | None) -> str:
    """Role + editorial knowledge. The playbook is a plain markdown file editors can tune per genre."""
    return ROLE + "\n\n" + (playbook.strip() if playbook else BASE_RUBRIC)


FIND_TASK = """Below is one episode as numbered beats: [id] start-end KIND: text. Times are MM:SS.ss.
Propose up to {k} of the best promos in THIS episode (fewer if the episode has none worth posting).

Rules:
- Each promo has 1 or 2 segments, in the order they will play, from this episode only. Use 2 segments only to
  jump over slow material between a hook and a cliffhanger.
- Each segment is a beat range: start_beat .. end_beat (inclusive). Total length must be 20-60 seconds.
- hook_beat must start within the first 3 seconds of the promo. Start the promo ON or just before the hook.
- cliff_beat should be at, or within a beat or two of, the end of the last segment. End right on the unresolved moment.
- Scores are 0-10 and must be honest: 9-10 means a promo you would post today.

Show: {show}
Episode {ep} ({dur} long)

{beats}"""

FIND_SCHEMA = {
    "type": "object",
    "properties": {
        "promos": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "hook_type": {"type": "string", "enum": HOOK_TYPES},
                    "hook": {"type": "string", "description": "the hook, <= 15 words"},
                    "hook_beat": {"type": "integer"},
                    "cliffhanger_type": {"type": "string", "enum": CLIFF_TYPES},
                    "cliffhanger": {"type": "string", "description": "the unresolved moment it cuts on, <= 15 words"},
                    "cliff_beat": {"type": "integer"},
                    "stakes": {"type": "string", "description": "what someone is about to lose, <= 12 words"},
                    "segments": {"type": "array", "items": {
                        "type": "object",
                        "properties": {"start_beat": {"type": "integer"}, "end_beat": {"type": "integer"}},
                        "required": ["start_beat", "end_beat"], "additionalProperties": False}},
                    "why": {"type": "string", "description": "one line on why it works"},
                    "hook_score": {"type": "integer"},
                    "cliffhanger_score": {"type": "integer"},
                    "standalone_score": {"type": "integer"},
                },
                "required": ["hook_type", "hook", "hook_beat", "cliffhanger_type", "cliffhanger", "cliff_beat",
                             "stakes", "segments", "why", "hook_score", "cliffhanger_score", "standalone_score"],
                "additionalProperties": False,
            },
        }
    },
    "required": ["promos"],
    "additionalProperties": False,
}

RANK_TASK = """These are candidate promos for the show "{show}". Premise (from episode 1): {premise}

Decide the POSTING ORDER for the best {n}: which would you post first, second, and so on.
- Lead with promos that sell the show's core premise to a stranger. Then the strongest standalone hook+cliffhanger promos.
- Two candidates that tell the same story beat (even from different footage) are duplicates: keep only the better one.
- Vary hook types across the first posts so the feed does not repeat the same beat.
- Drop weak candidates; return fewer than {n} if needed.
- reason: one line on why it goes in that slot.

Candidates:
{cands}"""

RANK_SCHEMA = {
    "type": "object",
    "properties": {"ranking": {"type": "array", "items": {
        "type": "object",
        "properties": {"id": {"type": "string"}, "reason": {"type": "string"}},
        "required": ["id", "reason"], "additionalProperties": False}}},
    "required": ["ranking"],
    "additionalProperties": False,
}


# ---------------------------------------------------------------- formatting
def beat_line(e: Event) -> str:
    who = f"{e.speaker} ({', '.join(e.tags)})" if e.kind == "dialogue" and e.tags else \
        (e.speaker if e.kind == "dialogue" else e.kind.upper())
    return f"[{e.idx}] {fmt(e.start)}-{fmt(e.end)} {who}: {e.text}"


def episode_prompt(ep: Episode, show: str, k: int) -> str:
    beats = "\n".join(beat_line(e) for e in ep.events if e.kind != "camera")
    return FIND_TASK.format(k=k, show=show, ep=ep.num, dur=fmt(ep.duration), beats=beats)


def compact_transcript(p: Promo, max_lines: int = 14) -> str:
    lines = []
    for off, e in p.events:
        if e.kind == "camera":
            continue
        who = e.speaker.title() if e.kind == "dialogue" else e.kind.upper()
        lines.append(f"  {off:4.1f}s {who}: {e.text[:140]}")
    if len(lines) > max_lines:
        lines = lines[: max_lines - 3] + ["  ..."] + lines[-2:]
    return "\n".join(lines)


# ---------------------------------------------------------------- validation
def _snap_start(cuts: list[float], t: float) -> float:
    ok = [c for c in cuts if c <= t + 0.05]
    return ok[-1] if ok else cuts[0]


def _snap_end(cuts: list[float], t: float) -> float:
    ok = [c for c in cuts if c >= t - 0.05]
    return ok[0] if ok else cuts[-1]


def validate(raw: dict, ep: Episode, eps: dict, cfg: dict) -> tuple[Promo | None, str]:
    """Turn one LLM proposal into a Promo that obeys the hard rules, or say why not."""
    by_id = {e.idx: e for e in ep.events}
    cuts = legal_cuts(ep)
    segs = []
    for s in raw.get("segments", [])[:2]:
        a, b = by_id.get(s.get("start_beat")), by_id.get(s.get("end_beat"))
        if a is None or b is None:
            return None, "unknown beat id"
        if b.end <= a.start:
            return None, "segment ends before it starts"
        segs.append(Segment(ep.num, _snap_start(cuts, a.start), _snap_end(cuts, b.end)))
    if not segs:
        return None, "no segments"
    if len(segs) == 2 and segs[1].start < segs[0].end:
        if segs[1].end <= segs[0].end:
            return None, "segments out of order"
        segs = [Segment(ep.num, segs[0].start, segs[1].end)]        # overlapping -> merge
    repairs = []

    # hook must land inside the first 3 s: move the start up to the hook if needed
    hook = by_id.get(raw.get("hook_beat"))
    if hook and segs[0].start - 0.05 <= hook.start <= segs[0].end:
        if hook.start - segs[0].start > cfg["hook_window"]:
            new = _snap_start(cuts, hook.start)
            if segs[0].end - new >= 3:
                segs[0] = Segment(ep.num, new, segs[0].end)
                repairs.append("moved start to hook")
    total = sum(s.dur for s in segs)

    # too short: extend the ending through non-dialogue beats (holding on a reaction keeps the cliff)
    if total < cfg["min_len"]:
        last = segs[-1]
        for c in [c for c in cuts if c > last.end]:
            spoken = [e for e in ep.events if e.kind == "dialogue" and last.end - 0.05 <= e.start < c]
            if spoken:
                break
            segs[-1] = Segment(ep.num, last.start, c)
            if sum(s.dur for s in segs) >= cfg["min_len"]:
                repairs.append("held the ending to reach 20 s")
                break
        total = sum(s.dur for s in segs)
    # too long: trim the opening of the first segment up to the hook
    if total > cfg["max_len"] and hook:
        excess = total - cfg["max_len"]
        new = _snap_end(cuts, segs[0].start + excess)
        if new <= hook.start + 0.05 and segs[0].end - new >= 3:
            segs[0] = Segment(ep.num, new, segs[0].end)
            repairs.append("trimmed the opening")
        total = sum(s.dur for s in segs)
    if not (cfg["min_len"] - 0.05 <= total <= cfg["max_len"] + 0.05):
        return None, (f"too long (>{cfg['max_len']:.0f}s)" if total > cfg["max_len"]
                      else f"too short (<{cfg['min_len']:.0f}s)")

    p = make_promo(segs, eps, cfg)              # also computes the rules scores, kept for comparison
    p.llm = {k: raw.get(k) for k in ("hook", "hook_type", "cliffhanger", "cliffhanger_type", "stakes", "why")}
    for k in ("hook_score", "cliffhanger_score", "standalone_score"):
        p.llm[k] = max(0, min(10, int(raw.get(k, 0) or 0)))
    p.llm["repairs"] = repairs
    # the LLM chose these beats; point the report at them instead of the rules' guesses
    if hook:
        p.hook, p.hook_at = hook, max(0.0, hook.start - segs[0].start)
    cb = by_id.get(raw.get("cliff_beat"))
    if cb:
        p.cliff = cb
    p.why = raw.get("why") or p.why
    llm_total = (0.4 * p.llm["hook_score"] + 0.35 * p.llm["cliffhanger_score"]
                 + 0.25 * p.llm["standalone_score"]) / 10
    p.scores["rules_total"] = p.scores["total"]
    p.scores["llm"] = round(llm_total, 3)
    p.scores["final"] = round(llm_total, 4)
    p.flags = [f for f in p.flags if f.startswith(("spans_", "many_speakers"))]   # rules' semantic guesses dropped
    if repairs:
        p.flags.append("auto-repaired: " + ", ".join(repairs))
    if p.hook_at > cfg["hook_window"]:
        p.flags.append(f"hook lands late ({p.hook_at:.1f}s)")
    return p, ""


# ---------------------------------------------------------------- pipeline
def find(episodes: list[Episode], client: LLMClient, show: str, cfg: dict, per_episode: int = 3,
         log=print, system: str | None = None) -> tuple[list[Promo], dict]:
    system = system or build_system(None)
    eps = {e.num: e for e in episodes}
    pool, stats = [], {"proposed": 0, "accepted": 0, "repaired": 0, "rejected": {}, "episodes_failed": []}
    for ep in episodes:
        try:
            res = client.complete_json(system, episode_prompt(ep, show, per_episode), FIND_SCHEMA,
                                       tag=f"find_e{ep.num:02d}")
        except QuotaExhausted as e:
            done = [x.num for x in episodes if x.num < ep.num]
            stats["stopped"] = {"at_episode": ep.num, "reason": str(e),
                                "missing": [x.num for x in episodes if x.num >= ep.num]}
            log(f"\n  QUOTA EXHAUSTED at episode {ep.num}: {e}\n  Kept {len(done)} finished episodes. "
                "Re-run the same command later (or with another key/model): finished episodes are cached.")
            break
        if res is None:
            if client.provider != "dryrun":
                stats["episodes_failed"].append(ep.num)
            continue
        got = 0
        for raw in res.get("promos", [])[:per_episode]:
            stats["proposed"] += 1
            p, why = validate(raw, ep, eps, cfg)
            if p is None:
                stats["rejected"][why] = stats["rejected"].get(why, 0) + 1
                continue
            stats["accepted"] += 1
            stats["repaired"] += bool(p.llm["repairs"])
            pool.append(p)
            got += 1
        log(f"  episode {ep.num:2d}: {got} promo(s)")
    return pool, stats


def rank(pool: list[Promo], client: LLMClient, show: str, premise: str, cfg: dict,
         system: str | None = None) -> list[Promo]:
    """Show-level posting order from the LLM, then hard de-duplication of shared footage."""
    system = system or build_system(None)
    # one call sees everything; keep the list to the best 120 by stage-1 score to bound the prompt
    shortlist = sorted(pool, key=lambda p: -p.scores["final"])[:120]
    ids = {f"P{i + 1}": p for i, p in enumerate(shortlist)}
    cands = "\n\n".join(
        f"{cid} | " + " + ".join(f"E{s.ep} {fmt(s.start)}-{fmt(s.end)}" for s in p.segments)
        + f" | {p.duration:.0f}s | hook ({p.llm.get('hook_type', '?')}): {p.llm['hook']}"
        + f" | cliff ({p.llm.get('cliffhanger_type', '?')}): {p.llm['cliffhanger']}"
        + (f" | stakes: {p.llm['stakes']}" if p.llm.get("stakes") else "") + f"\n{compact_transcript(p)}"
        for cid, p in ids.items())
    try:
        res = client.complete_json(system, RANK_TASK.format(show=show, premise=premise, n=cfg["top_n"],
                                                            cands=cands), RANK_SCHEMA, tag="rank")
    except QuotaExhausted as e:
        print(f"  Ranking call hit the quota ({e}); falling back to the per-episode LLM scores.")
        res = None
    order: list[Promo] = []
    if res:
        for item in res.get("ranking", []):
            p = ids.get(str(item.get("id", "")).strip())
            if p is not None and p not in order:
                p.llm["post_reason"] = item.get("reason", "")
                order.append(p)
    # anything the ranker skipped (or a failed rank call) falls back to stage-1 score order
    order += [p for p in shortlist if p not in order and not res]
    n = len(order)
    for i, p in enumerate(order):
        p.scores["rank"] = round(1.0 - i / max(n, 1), 4)       # preserve the LLM's order through nms()
    return nms(order, cfg["nms_overlap"], keep=cfg["top_n"], per_ep=cfg["max_per_episode_final"])
