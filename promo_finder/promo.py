"""Candidate generation + scoring.

Pipeline (all deterministic):
  1. cut points  — a promo may only start/end where no line of dialogue is
                   being spoken (never cut a sentence in half).
  2. singles     — for every strong-hook start, try every legal end 20–60 s later,
                   keep the best-scoring end.
  3. multis      — stitch a short "hook piece" to a later "cliff piece" (same or
                   up to N episodes later, sharing a character).
  4. dedupe      — temporal non-max suppression + max promos per episode.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

from .parser import Episode, Event, fmt
from . import signals as S

DEFAULTS = {
    "min_len": 20.0, "max_len": 60.0, "sweet_min": 25.0, "sweet_max": 50.0,
    "hook_window": 3.0, "cliff_window": 5.0,
    "w_hook": 0.35, "w_cliff": 0.30, "w_standalone": 0.20, "w_intensity": 0.15,
    "min_start_hook": 0.45,
    "scene_end_bonus": 0.10, "episode_end_bonus": 0.20,
    "multi_max_episode_gap": 4, "multi_segment_penalty": 0.12, "max_multi_share": 0.3,
    "per_episode_keep": 3, "nms_overlap": 0.2, "max_per_episode_final": 2,
    "top_n": 25, "early_episode_max": 10, "early_episode_bonus": 0.03,
}


@dataclass
class Segment:
    ep: int
    start: float
    end: float

    @property
    def dur(self) -> float:
        return self.end - self.start

    def to_dict(self):
        return {"episode": self.ep, "start": fmt(self.start), "end": fmt(self.end),
                "start_s": round(self.start, 2), "end_s": round(self.end, 2),
                "duration_s": round(self.dur, 1)}


@dataclass
class Promo:
    segments: list[Segment]
    events: list[tuple[float, Event]]          # (offset within promo, event)
    scores: dict = field(default_factory=dict)
    hook: Event | None = None
    hook_at: float = 0.0
    cliff: Event | None = None
    flags: list[str] = field(default_factory=list)
    why: str = ""
    llm: dict | None = None

    @property
    def duration(self) -> float:
        return sum(s.dur for s in self.segments)

    @property
    def kind(self) -> str:
        return "single" if len(self.segments) == 1 else "multi"

    @property
    def episodes(self) -> list[int]:
        return sorted({s.ep for s in self.segments})

    @property
    def total(self) -> float:
        return self.scores.get("final", self.scores.get("total", 0.0))


# ---------------------------------------------------------------- helpers
def legal_cuts(ep: Episode) -> list[float]:
    """Timestamps where no dialogue line is mid-delivery."""
    dlg = [(e.start, e.end) for e in ep.events if e.kind == "dialogue"]
    pts = sorted({round(e.start, 2) for e in ep.events} | {round(e.end, 2) for e in ep.events}
                 | {round(t, 2) for t in ep.scene_ends})
    return [t for t in pts if not any(a + 0.05 < t < b - 0.05 for a, b in dlg)]


def slice_events(ep: Episode, a: float, b: float) -> list[Event]:
    return [e for e in ep.events if e.start >= a - 0.01 and e.start < b - 0.01]


def norm_speaker(s: str) -> str:
    s = re.sub(r"^(LADY|LORD|THE|SIR)\s+", "", s.upper()).strip()
    return s.split("'")[0].split()[0] if s else s


# ---------------------------------------------------------------- scoring
def score(promo: Promo, eps: dict[int, Episode], cfg: dict) -> Promo:
    ev = promo.events
    dur = promo.duration
    # hook: strongest beat that lands inside the first N seconds
    hook_val, promo.hook, promo.hook_at = 0.0, None, 0.0
    for off, e in ev:
        if off > cfg["hook_window"]:
            break
        v = e.hook * (1 - 0.1 * off / cfg["hook_window"])
        if v > hook_val:
            hook_val, promo.hook, promo.hook_at = v, e, off
    # cliff: strongest open beat in the last N seconds, minus any release
    tail = [(off, e) for off, e in ev if off + (e.end - e.start) >= dur - cfg["cliff_window"]]
    cliff_val, promo.cliff = 0.0, None
    for off, e in tail:
        v = e.cliff * (0.8 + 0.2 * min(1.0, (off + e.end - e.start) / max(dur, 1)))
        if v > cliff_val:
            cliff_val, promo.cliff = v, e
    if promo.cliff is None:
        dl = [e for _, e in tail if e.kind == "dialogue"] or [e for _, e in tail]
        promo.cliff = dl[-1] if dl else (ev[-1][1] if ev else None)
    release = max([e.resolve for _, e in tail] + [0.0])
    if any("cliffhanger" in e.text.lower() for _, e in tail):
        release *= 0.4      # a smile under a held "cliffhanger" cue is still unresolved
    last = promo.segments[-1]
    ep_last = eps[last.ep]
    bonus = 0.0
    if abs(last.end - ep_last.duration) < 1.0:
        bonus = cfg["episode_end_bonus"]        # writers end episodes on cliffhangers
    elif any(abs(last.end - t) < 0.3 for t in ep_last.scene_ends):
        bonus = cfg["scene_end_bonus"]
    cliff_score = max(0.0, min(1.0, cliff_val + bonus) - 0.6 * release)

    # standalone: penalise context debt, reward self-explaining setup
    dlg = [e for _, e in ev if e.kind == "dialogue"]
    text = " ".join(e.text for _, e in ev)
    st = 0.7
    flags = []
    first_line = next(((off, e) for off, e in ev if e.kind == "dialogue"), None)
    if first_line and first_line[0] < 1.5 and S.CONTINUATION.match(first_line[1].text):
        st -= 0.2; flags.append("starts_mid_conversation")
    nback = len(S.BACKREF.findall(" ".join(e.text for e in dlg)))
    if nback:
        st -= min(0.3, 0.1 * nback); flags.append(f"context_debt({nback} back-references)")
    speakers = {norm_speaker(e.speaker) for e in dlg}
    if len(speakers) > 4:
        st -= 0.15; flags.append(f"many_speakers({len(speakers)})")
    roles = {m.lower() for m in S.ROLE_WORDS.findall(text)}
    st += min(0.25, 0.05 * len(roles))
    if len(dlg) < 2:
        st -= 0.3; flags.append("almost_no_dialogue")
    n = len(promo.segments)
    if n > 1:
        st -= cfg["multi_segment_penalty"] * (n - 1)
        gap = max(promo.episodes) - min(promo.episodes)
        if gap:
            st -= 0.03 * gap; flags.append(f"spans_episodes({gap} apart)")
    st = max(0.0, min(1.0, st))

    # intensity: conflict density across the body
    # = share of story beats that are themselves hook- or cliff-grade (>= 0.5)
    body = [max(e.hook, e.cliff) for _, e in ev if e.kind in ("dialogue", "action")]
    inten = sum(v >= 0.5 for v in body) / len(body) if body else 0.0

    # duration fit
    if cfg["sweet_min"] <= dur <= cfg["sweet_max"]:
        dfit = 1.0
    elif dur < cfg["sweet_min"]:
        dfit = 0.75 + 0.25 * (dur - cfg["min_len"]) / (cfg["sweet_min"] - cfg["min_len"])
    else:
        dfit = 0.75 + 0.25 * (cfg["max_len"] - dur) / (cfg["max_len"] - cfg["sweet_max"])
    dfit = max(0.5, min(1.0, dfit))

    total = (cfg["w_hook"] * hook_val + cfg["w_cliff"] * cliff_score
             + cfg["w_standalone"] * st + cfg["w_intensity"] * inten) * dfit

    if hook_val < 0.5:
        flags.append("weak_hook")
    if release >= 0.4:
        flags.append("tension_released_at_end")
    if cliff_score < 0.35:
        flags.append("weak_cliffhanger")
    promo.flags = flags
    promo.scores = {"hook": round(hook_val, 3), "cliffhanger": round(cliff_score, 3),
                    "standalone": round(st, 3), "intensity": round(inten, 3),
                    "duration_fit": round(dfit, 3), "total": round(total, 4)}
    promo.why = explain(promo, bonus, roles)
    return promo


def _hook_kind(e: Event | None) -> str:
    if e is None:
        return "no clear hook"
    if e.kind in ("action", "camera") and S.PHYSICAL.search(e.text):
        m = S.PHYSICAL.search(e.text).group(0).lower()
        return f"physical shock ('{m}')"
    if e.kind == "sfx":
        return "a sound hit"
    if S.REVEAL.search(e.text):
        return f"a reveal line ('{S.REVEAL.search(e.text).group(0).lower()}')"
    return "a charged line" if e.kind == "dialogue" else "a visual beat"


def _cliff_kind(e: Event | None) -> str:
    if e is None:
        return "an unclear ending"
    t = e.text
    if e.kind == "dialogue":
        if t.endswith("?"):
            return "an unanswered question"
        if t.endswith(("...", "…", "—", "-")):
            return "a line cut off mid-sentence"
        if S.THREAT.search(t):
            return "a threat/ultimatum"
        return "a charged line"
    if e.kind == "sfx":
        return "an ominous sting"
    if S.OPEN_ACTION.search(t):
        return f"an interrupted action ('{S.OPEN_ACTION.search(t).group(0).lower()}')"
    return "a held reaction shot"


def explain(p: Promo, bonus: float, roles: set[str]) -> str:
    parts = [f"Opens on {_hook_kind(p.hook)} at {p.hook_at:.1f}s",
             f"cuts on {_cliff_kind(p.cliff)}"]
    if bonus >= 0.2:
        parts[-1] += " exactly where the episode ends"
    if roles:
        parts.append("setup words (" + ", ".join(sorted(roles)[:4]) + ") let a cold viewer follow it")
    return "; ".join(parts) + "."


# ---------------------------------------------------------------- build
def make_promo(segs: list[Segment], eps: dict[int, Episode], cfg: dict) -> Promo:
    evs, off = [], 0.0
    for s in segs:
        for e in slice_events(eps[s.ep], s.start, s.end):
            evs.append((off + e.start - s.start, e))
        off += s.dur
    return score(Promo(segs, evs), eps, cfg)


def overlap(a: Promo, b: Promo) -> float:
    """Max fraction of shared screen time (by episode/time) relative to shorter promo."""
    inter = 0.0
    for x in a.segments:
        for y in b.segments:
            if x.ep == y.ep:
                inter += max(0.0, min(x.end, y.end) - max(x.start, y.start))
    return inter / max(1e-6, min(a.duration, b.duration))


def nms(cands: list[Promo], thr: float, keep: int | None = None,
        per_ep: int | None = None, max_multi: int | None = None) -> list[Promo]:
    out: list[Promo] = []
    ep_count: dict[int, int] = {}
    for c in sorted(cands, key=lambda p: -p.scores.get("rank", p.total)):
        if any(overlap(c, o) > thr for o in out):
            continue
        if per_ep and any(ep_count.get(e, 0) >= per_ep for e in c.episodes):
            continue
        if max_multi is not None and c.kind == "multi" and \
                sum(o.kind == "multi" for o in out) >= max_multi:
            continue
        out.append(c)
        for e in c.episodes:
            ep_count[e] = ep_count.get(e, 0) + 1
        if keep and len(out) >= keep:
            break
    return out


def single_candidates(ep: Episode, eps, cfg) -> list[Promo]:
    cuts = legal_cuts(ep)
    starts = []
    for s in cuts:
        window = [e.hook for e in ep.events if s - 0.01 <= e.start <= s + cfg["hook_window"]]
        if window and max(window) >= cfg["min_start_hook"]:
            starts.append(s)
    out = []
    for s in starts:
        best = None
        for t in cuts:
            if t - s < cfg["min_len"] or t - s > cfg["max_len"]:
                continue
            p = make_promo([Segment(ep.num, s, t)], eps, cfg)
            if best is None or p.total > best.total:
                best = p
        if best:
            out.append(best)
    return nms(out, cfg["nms_overlap"], keep=cfg["per_episode_keep"])


def pieces(ep: Episode, eps, cfg, mode: str) -> list[Segment]:
    """Short building blocks for stitched promos."""
    cuts = legal_cuts(ep)
    scored = []
    for a in cuts:
        for b in cuts:
            d = b - a
            if not (6.0 <= d <= 25.0):
                continue
            p = make_promo([Segment(ep.num, a, b)], eps, {**cfg, "min_len": 6.0, "sweet_min": 8.0,
                                                          "sweet_max": 25.0, "max_len": 30.0})
            key = p.scores["hook"] + 0.3 * p.scores["intensity"] if mode == "hook" \
                else p.scores["cliffhanger"] + 0.3 * p.scores["intensity"]
            p.scores["rank"] = key          # order the NMS by this piece's role
            scored.append(p)
    chosen = nms(scored, 0.2, keep=3)
    return [c.segments[0] for c in chosen]


def multi_candidates(episodes: list[Episode], eps, cfg) -> list[Promo]:
    hooks = {e.num: pieces(e, eps, cfg, "hook") for e in episodes}
    cliffs = {e.num: pieces(e, eps, cfg, "cliff") for e in episodes}
    def speakers(seg):
        return {norm_speaker(e.speaker) for e in slice_events(eps[seg.ep], seg.start, seg.end)
                if e.kind == "dialogue"}
    out = []
    for i, hs in hooks.items():
        for j in range(i, i + cfg["multi_max_episode_gap"] + 1):
            for h in hs:
                for c in cliffs.get(j, []):
                    if j == i and c.start < h.end + 2.0:      # must move forward in time
                        continue
                    if not (cfg["min_len"] <= h.dur + c.dur <= cfg["max_len"]):
                        continue
                    if not (speakers(h) & speakers(c)):
                        continue
                    out.append(make_promo([h, c], eps, cfg))
    # refine the best stitches: pieces were picked at a fixed size, so let the hook
    # piece run longer / the cliff piece start earlier when that reads better
    out.sort(key=lambda p: -p.total)
    refined = []
    for p in out[:150]:
        h, c = p.segments
        h_cuts = [t for t in legal_cuts(eps[h.ep]) if h.end <= t <= h.start + 25]
        c_cuts = [t for t in legal_cuts(eps[c.ep]) if c.end - 25 <= t <= c.start]
        best = p
        for he in h_cuts[::2] + [h.end]:
            for cs in c_cuts[::2] + [c.start]:
                if h.ep == c.ep and cs < he + 2.0:
                    continue
                if not (cfg["min_len"] <= (he - h.start) + (c.end - cs) <= cfg["max_len"]):
                    continue
                q = make_promo([Segment(h.ep, h.start, he), Segment(c.ep, cs, c.end)], eps, cfg)
                if q.total > best.total:
                    best = q
        refined.append(best)
    return refined + out[150:]


def build_pool(episodes: list[Episode], cfg: dict) -> list[Promo]:
    """Every scored candidate (singles: best few per episode; multis: best 40)."""
    eps = {e.num: e for e in episodes}
    for e in episodes:
        S.annotate(e.events)
    singles = [p for e in episodes for p in single_candidates(e, eps, cfg)]
    multis = nms(multi_candidates(episodes, eps, cfg), 0.3, keep=40)
    return singles + multis


def select(pool: list[Promo], cfg: dict) -> list[Promo]:
    """Final ranked list: no near-duplicates, spread across episodes, few stitched promos.

    Posting priority = quality score + a small bonus when every segment comes from the
    first episodes: a viewer who clicks through reaches that moment within minutes
    of starting the show, and it spoils the least.
    """
    for p in pool:
        early = max(p.episodes) <= cfg["early_episode_max"]
        p.scores["rank"] = round(p.total + (cfg["early_episode_bonus"] if early else 0.0), 4)
    return nms(pool, cfg["nms_overlap"], keep=cfg["top_n"], per_ep=cfg["max_per_episode_final"],
               max_multi=int(cfg["top_n"] * cfg["max_multi_share"]))


def find_promos(episodes: list[Episode], cfg: dict | None = None) -> tuple[list[Promo], list[Promo]]:
    cfg = {**DEFAULTS, **(cfg or {})}
    pool = build_pool(episodes, cfg)
    return select(pool, cfg), pool
