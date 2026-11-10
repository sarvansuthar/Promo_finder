"""Per-event signals: how hard does this beat hook, does it leave things open,
does it resolve tension, does it lean on context a new viewer lacks.

These are transparent keyword/tag rules on purpose: they are free, instant,
explainable ("scored high because of 'strikes' + SFX 'whip crack'"), and give
the optional LLM judge a short, good candidate list instead of 50 episodes.
"""
from __future__ import annotations

import re
from .parser import Event

def _rx(words):
    return re.compile(r"\b(" + "|".join(words) + r")\b", re.I)

# --- hook vocab -----------------------------------------------------------
# tier A: unmistakable scroll-stoppers; tier B: physical but milder
PHYSICAL_A = _rx([
    r"slap\w*", r"strikes?", r"struck", r"striking", r"punch\w*", r"backhand\w*", r"whip\w*",
    r"crop", r"blade", r"sword", r"dagger", r"knife", r"blood\w*", r"bleed\w*", r"ablaze",
    r"inferno", r"flames?", r"explo\w*", r"shatter\w*", r"red-hot", r"brand\w*", r"chok\w*",
    r"one knee", r"hurl\w*", r"slams? (?:her|him)", r"lacerat\w*", r"briars?", r"thorns?",
    r"noose", r"gallows", r"chained", r"shackled", r"faint\w*", r"collaps\w*",
])
PHYSICAL_B = _rx([
    r"shov\w*", r"slam\w*", r"throws?", r"threw", r"seiz\w*", r"yank\w*", r"drag\w*",
    r"lung\w*", r"fire", r"chains?", r"kiss\w*", r"rip\w*", r"stomp\w*", r"trampl\w*",
    r"wound\w*", r"screams?", r"smash\w*", r"tears? (?:off|open)", r"pins?", r"restrain\w*",
])
PHYSICAL = re.compile(PHYSICAL_A.pattern + "|" + PHYSICAL_B.pattern, re.I)
REVEAL = _rx([
    r"reveal\w*", r"truth", r"identity", r"crest", r"signet", r"ring", r"poison\w*",
    r"arsenic", r"alive", r"dead", r"duke", r"duchess", r"heir", r"betray\w*", r"secret\w*",
    r"witch\w*", r"execut\w*", r"gallows", r"hang\w*", r"confess\w*", r"framed?", r"proof",
    r"disown\w*", r"inherit\w*", r"pregnan\w*", r"marry", r"married", r"wedding", r"invalid",
    r"traitor", r"treason", r"liar", r"mastermind",
])
SHOCK_LINE = _rx([
    r"kill", r"die", r"dead", r"hang", r"burn", r"witch", r"poison", r"rot", r"never",
    r"not anymore", r"worthless", r"plain\w*", r"disgrace", r"filth", r"beg", r"kneel",
    r"marry me", r"how dare", r"you will", r"suffer", r"divorce", r"nothing", r"mine",
])
INTENSE_TAGS = {
    "screaming", "shouting", "furious", "venomous", "cruel", "threatening", "chilling",
    "sinister", "mocking", "gloating", "cold", "scornful", "spiteful", "commanding",
    "harsh", "taunting", "vicious", "enraged", "menacing", "blunt", "unforgiving",
    "dismissive", "arrogant", "smug", "accusatory", "contemptuous", "icy",
}
SFX_HIT = _rx([r"crack\w*", r"slam\w*", r"thud\w*", r"explo\w*", r"shatter\w*", r"sudden\w*",
               r"violent\w*", r"scream\w*", r"gasp\w*", r"smash\w*", r"boom\w*", r"splinter\w*"])

# --- cliffhanger vocab ----------------------------------------------------
OPEN_SFX = _rx([r"ominous", r"cliffhanger", r"swell\w*", r"drone", r"sting\w*", r"crescendo",
                r"creak\w*", r"footsteps", r"latch", r"knock\w*", r"heartbeat", r"tension",
                r"suspense\w*", r"silence"])
OPEN_ACTION = _rx([r"arriv\w*", r"enters?", r"burst\w* in", r"bursts", r"appears?", r"raises?",
                   r"lung\w*", r"reach\w* for", r"door\w* (?:open|swing|burst)\w*", r"silhouett\w*",
                   r"figure", r"freez\w*", r"froze", r"turns? (?:slowly|sharply)", r"stunned",
                   r"horror", r"shock\w*", r"widen\w*", r"draws?", r"aim\w*"])
THREAT = _rx([r"or (?:else|watch|you|he|she)", r"unless", r"will hang", r"you will", r"tomorrow",
              r"before dawn", r"at dawn", r"by sunrise", r"execution", r"die", r"choose",
              r"if you", r"one chance", r"last chance", r"ten days", r"pay for", r"regret",
              r"who are you", r"what have you done", r"it's you", r"you're alive",
              # pending-answer lines: a proposal/offer/demand nobody has answered yet
              r"accept my hand", r"marry me", r"will you", r"be my wife", r"say yes",
              r"answer me", r"tell me the truth", r"swear it"])
CUT_CAM = _rx([r"cut to black", r"smash cut", r"freeze", r"hard cut", r"fade to black"])
CLIFF_TAGS = {"stunned", "shocked", "incredulous", "alarmed", "gasping", "breathless",
              "horrified", "chilling", "threatening", "sinister", "ominous", "urgent",
              "desperate", "panicked", "terrified", "disbelieving", "unbelieving", "final"}

# --- resolution (bad place to cut) ---------------------------------------
RESOLVE = _rx([r"embrac\w*", r"hug\w*", r"smil\w*", r"relie\w*", r"peace\w*", r"tender\w*",
               r"warm\w*", r"laugh\w* (?:softly|warmly|together)", r"sleep\w*", r"rest\w*",
               r"thank you", r"safe now", r"it's over", r"together", r"content\w*", r"serene",
               r"fade\w* out", r"sunlight", r"cozy", r"gently"])
RESOLVE_TAGS = {"reassuring", "tender", "gentle", "soft", "calm", "warm", "relieved",
                "content", "loving", "affectionate", "grateful", "peaceful", "soothing"}

# --- standalone (context debt) -------------------------------------------
BACKREF = _rx([r"remember", r"you promised", r"as I (?:said|told)", r"earlier", r"last night",
               r"yesterday", r"after what", r"what happened", r"that day", r"like I said",
               r"you knew", r"all along", r"back then"])
ROLE_WORDS = _rx([r"sister", r"husband", r"wife", r"fianc\w*", r"mother", r"stepmother", r"father",
                  r"duke", r"duchess", r"maid", r"bride", r"groom", r"wedding", r"married", r"son",
                  r"daughter", r"family", r"lady", r"lord", r"soldier", r"prisoner", r"witch"])
CONTINUATION = re.compile(r"^(and|but|so|then|yes|no|because|or|well)\b", re.I)


def tagset(e: Event) -> set[str]:
    out = set()
    for t in e.tags:
        out.update(w.lower() for w in re.findall(r"[A-Za-z]+", t))
    return out


def hook_strength(e: Event) -> float:
    """0..1 — how likely this beat stops a scroll."""
    t, tags, s = e.text, tagset(e), 0.0
    if e.kind in ("action", "camera"):
        s = 0.9 if PHYSICAL_A.search(t) else 0.5 if PHYSICAL_B.search(t) else 0.0
        if REVEAL.search(t):
            s = max(s, 0.4)
        if e.kind == "camera":
            s *= 0.6                    # camera notes describe, actions happen
    elif e.kind == "sfx":
        s = 0.5 * bool(SFX_HIT.search(t))
    else:
        s = 0.15
        if tags & INTENSE_TAGS:
            s += 0.2
        if SHOCK_LINE.search(t):
            s += 0.25
        if REVEAL.search(t):
            s += 0.15
        if t.endswith("!"):
            s += 0.1
        if len(t.split()) <= 7:     # short punchy lines land inside 3 s
            s += 0.1
    return min(s, 1.0)


def cliff_strength(e: Event) -> float:
    """0..1 — does this beat leave a question hanging."""
    t, tags, s = e.text, tagset(e), 0.0
    if e.kind == "sfx":
        s = 0.5 * bool(OPEN_SFX.search(t)) + (0.4 if "cliffhanger" in t.lower() else 0)
    elif e.kind == "camera":
        s = 0.3 * bool(CUT_CAM.search(t)) + 0.35 * bool(OPEN_ACTION.search(t))
    elif e.kind == "action":
        s = 0.55 * bool(OPEN_ACTION.search(t)) + 0.3 * bool(PHYSICAL.search(t))
    if e.kind != "dialogue" and "cliffhanger" in t.lower():
        s = max(s, 0.8)                  # the writer marked this beat as the cliff
    else:
        if t.rstrip("!").endswith("?"):
            s += 0.5
        if t.endswith(("...", "…", "—", "-")):
            s += 0.5                      # interrupted / trailing line
        if THREAT.search(t):
            s += 0.45
        if tags & CLIFF_TAGS:
            s += 0.25
        if REVEAL.search(t):
            s += 0.2
    return min(s, 1.0)


def resolution(e: Event) -> float:
    """0..1 — tension released (a bad place to end a promo)."""
    s = 0.6 * bool(RESOLVE.search(e.text))
    if e.kind == "dialogue" and tagset(e) & RESOLVE_TAGS:
        s += 0.4
    return min(s, 1.0)


def annotate(events: list[Event]) -> None:
    for e in events:
        e.hook = hook_strength(e)          # type: ignore[attr-defined]
        e.cliff = cliff_strength(e)        # type: ignore[attr-defined]
        e.resolve = resolution(e)          # type: ignore[attr-defined]
