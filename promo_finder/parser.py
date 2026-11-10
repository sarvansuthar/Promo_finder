"""Parse the timestamped microdrama screenplay format into structured events.

Format (one block per beat):

    SCENE 01
    Scene heading: INT. CATHEDRAL – DAY
    Scene start: 00:00.00
    Scene end: 02:18.00
    **CAMERA:**                     <- kind = camera / action / sound fx
    Start: (00:00.00) | End: (00:04.80)
    text...
    **MATRIARCH (Cold, Commanding):**   <- kind = dialogue, speaker + emotion tags
    Start: (00:11.30) | End: (00:13.10)
    "You should turn around, Juliana."

Pure-stdlib, deterministic. Anything it cannot parse is skipped and counted, so a
new show with slightly different formatting degrades instead of crashing.
"""
from __future__ import annotations

import re
import zipfile
from dataclasses import dataclass, field
from pathlib import Path

TS = r"(\d+):(\d+(?:\.\d+)?)"
RE_TIME = re.compile(rf"Start:\s*\(?{TS}\)?\s*\|\s*End:\s*\(?{TS}\)?")
RE_HEAD = re.compile(r"^\*\*(.+?):\*\*\s*$")
RE_SCENE = re.compile(r"^SCENE\s+(\d+)", re.I)
RE_EPNUM = re.compile(r"EPISODE NUMBER:\s*Episode\s*(\d+)", re.I)
NON_DIALOGUE = {"CAMERA": "camera", "ACTION": "action", "SOUND FX": "sfx", "SFX": "sfx"}


def to_sec(m: str, s: str) -> float:
    return int(m) * 60 + float(s)


def fmt(t: float) -> str:
    m, s = divmod(max(t, 0.0), 60)
    return f"{int(m):02d}:{s:05.2f}"


@dataclass
class Event:
    ep: int
    idx: int
    scene: int
    heading: str
    kind: str              # camera | action | sfx | dialogue
    start: float
    end: float
    text: str
    speaker: str = ""
    tags: list[str] = field(default_factory=list)   # emotion / delivery tags

    def label(self) -> str:
        if self.kind == "dialogue":
            return f'{self.speaker}: {self.text}'
        return f"[{self.kind.upper()}] {self.text}"


@dataclass
class Episode:
    num: int
    logline: str
    events: list[Event]
    duration: float
    scene_ends: list[float]
    source: str
    skipped_blocks: int = 0


def parse_text(raw: str, source: str = "", fallback_num: int = 0) -> Episode:
    m = RE_EPNUM.search(raw)
    num = int(m.group(1)) if m else fallback_num
    logline = ""
    for line in raw.splitlines():
        if line.startswith("EPISODE LOGLINE:"):
            logline = line.split(":", 1)[1].strip()
            break

    body = raw.split("--- SCRIPT START ---", 1)[-1].split("--- SCRIPT END ---", 1)[0]
    lines = body.splitlines()
    events: list[Event] = []
    scene, heading, scene_ends, skipped = 0, "", [], 0
    i = 0
    while i < len(lines):
        line = lines[i].strip()
        sm = RE_SCENE.match(line)
        if sm:
            scene = int(sm.group(1))
        elif line.startswith("Scene heading:"):
            heading = line.split(":", 1)[1].strip()
        elif line.startswith("Scene end:"):
            tm = re.search(TS, line)
            if tm:
                scene_ends.append(to_sec(*tm.groups()))
        hm = RE_HEAD.match(line)
        if hm:
            head = hm.group(1).strip()
            # timing line is the next non-empty line
            j = i + 1
            while j < len(lines) and not lines[j].strip():
                j += 1
            tm = RE_TIME.search(lines[j]) if j < len(lines) else None
            if not tm:
                skipped += 1
                i += 1
                continue
            start, end = to_sec(*tm.groups()[:2]), to_sec(*tm.groups()[2:])
            # text = following lines until blank line / next header
            k, buf = j + 1, []
            while k < len(lines) and lines[k].strip() and not RE_HEAD.match(lines[k].strip()) \
                    and not RE_SCENE.match(lines[k].strip()):
                buf.append(lines[k].strip())
                k += 1
            text = " ".join(buf).strip()
            kind, speaker, tags = NON_DIALOGUE.get(head.upper(), "dialogue"), "", []
            if kind == "dialogue":
                pm = re.match(r"^(.*?)\s*\((.*)\)\s*$", head)
                speaker = (pm.group(1) if pm else head).strip()
                if pm:
                    tags = [t.strip() for t in re.split(r"[,/]", pm.group(2)) if t.strip()]
                text = text.strip('"“” ')
            events.append(Event(num, len(events), scene, heading, kind, start, end, text, speaker, tags))
            i = k
            continue
        i += 1

    events.sort(key=lambda e: (e.start, e.end))
    for n, e in enumerate(events):
        e.idx = n
    duration = max([e.end for e in events] + scene_ends + [0.0])
    return Episode(num, logline, events, duration, sorted(scene_ends), source, skipped)


def load_show(path: str | Path) -> list[Episode]:
    """Load every episode from a folder of .txt files or a .zip of them."""
    path = Path(path)
    items: list[tuple[str, str]] = []
    if path.is_file() and path.suffix == ".zip":
        with zipfile.ZipFile(path) as z:
            for n in sorted(z.namelist()):
                if n.endswith(".txt") and not n.startswith("__MACOSX"):
                    items.append((n, z.read(n).decode("utf-8", "replace")))
    elif path.is_dir():
        for p in sorted(path.rglob("*.txt")):
            items.append((p.name, p.read_text("utf-8", "replace")))
    else:
        raise FileNotFoundError(f"No screenplay folder or zip at {path}")
    eps = [parse_text(raw, name, n + 1) for n, (name, raw) in enumerate(items)]
    eps = [e for e in eps if e.events]
    eps.sort(key=lambda e: e.num)
    return eps
