"""Promo Ranker web app (Streamlit). Same pipeline as run.py, in a browser.

    streamlit run app.py

Users upload their own scripts and, for LLM mode, paste their OWN API key. The key lives only
in this browser session's memory for the run: it is never written to disk, logged, or cached.
Uploaded scripts go to a per-session temp folder. LLM replies share the CLI's .cache/ (keyed by the
exact prompt), so no run repeats work another run already paid for.
"""
from __future__ import annotations

import hmac
import io
import json
import os
import shutil
import tempfile
import zipfile
from pathlib import Path

import pandas as pd
import streamlit as st

from promo_finder.llm_client import LLMClient
from promo_finder.pipeline import PLAYBOOK, load, run_pipeline, show_name
from promo_finder.promo import DEFAULTS
from promo_finder.providers import load_provider

st.set_page_config(page_title="Promo Ranker", page_icon="🎬", layout="wide")

PROVIDERS = {
    "Gemini (Google)": {"spec": "gemini", "model": "gemini-3.5-flash", "rpm": 8, "base_url": False,
                        "help": "Free key: https://aistudio.google.com/apikey"},
    "Claude (Anthropic)": {"spec": "anthropic", "model": "claude-haiku-5-5", "rpm": 0, "base_url": False,
                           "help": "Key from https://console.anthropic.com"},
    "OpenAI-compatible API": {"spec": "openai", "model": "gpt-4o-mini", "rpm": 0, "base_url": True,
                              "help": "OpenAI, Groq, OpenRouter, Together, vLLM, Ollama… set the base URL"},
}

def secret(name: str, default=None):
    """Streamlit Cloud secret (or env var when running locally). Never shown in the UI."""
    try:
        if name in st.secrets:
            return st.secrets[name]
    except Exception:                       # no secrets file at all
        pass
    return os.environ.get(name, default)


# Optional "built-in model": the app owner's key, stored in Streamlit Cloud -> Settings -> Secrets
# (never in the repo). Guarded by a passcode and an episode cap so a public link can't drain it.
HOSTED = None
if secret("HOSTED_API_KEY"):
    HOSTED = {"provider": secret("HOSTED_PROVIDER", "gemini"), "model": secret("HOSTED_MODEL", "gemini-3.5-flash"),
              "key": secret("HOSTED_API_KEY"), "base_url": secret("HOSTED_BASE_URL"),
              "passcode": secret("HOSTED_PASSCODE"), "max_eps": int(secret("HOSTED_MAX_EPISODES", 10)),
              "rpm": float(secret("HOSTED_RPM", 8))}
# One cache for the CLI and the app: a reply is reused only for the identical prompt (same script,
# same model, same playbook), so sharing it never exposes one user's show to another, and it stops
# the app from spending quota on episodes the CLI (or another session) already ran.
SHARED_CACHE = Path(__file__).resolve().parent / ".cache"

ss = st.session_state
if "workdir" not in ss:
    ss.workdir = Path(tempfile.mkdtemp(prefix="promo_"))


# ------------------------------------------------------------------ sidebar: settings
with st.sidebar:
    st.header("Settings")
    modes = (["LLM (built-in model)"] if HOSTED else []) + \
        ["LLM (your API key)", "Rules only (no key)", "Demo (mock model, no key)"]
    mode = st.radio("Mode", modes,
                    help="Rules = keyword scoring, instant and free. Demo = runs the full LLM pipeline "
                         "with a fake model so you can see the flow; its picks are not real judgments.")
    provider_label, api_key, model, base_url, rpm, passcode = None, "", "", "", 0.0, ""
    if mode == "LLM (built-in model)":
        st.caption(f"Model: **{HOSTED['provider']} / {HOSTED['model']}**, provided by the app owner. "
                   f"Up to {HOSTED['max_eps']} episodes per run.")
        if HOSTED["passcode"]:
            passcode = st.text_input("Passcode", type="password", help="Ask the app owner.")
        rpm = HOSTED["rpm"]
        use_playbook = st.checkbox("Use editorial playbook", value=True)
        per_episode = st.slider("Promos proposed per episode", 1, 5, 3)
    elif mode.startswith("LLM"):
        provider_label = st.selectbox("Model provider", list(PROVIDERS))
        pv = PROVIDERS[provider_label]
        api_key = st.text_input("API key", type="password", help=pv["help"] + ". Used only for this run; "
                                "never stored.")
        model = st.text_input("Model id", value=pv["model"])
        if pv["base_url"]:
            base_url = st.text_input("Base URL", value="https://api.openai.com/v1",
                                     help="OpenAI: https://api.openai.com/v1 · Groq: https://api.groq.com/openai/v1 · "
                                          "OpenRouter: https://openrouter.ai/api/v1 · Ollama: http://localhost:11434/v1")
        rpm = st.number_input("Max requests / minute (0 = no limit)", 0.0, 600.0, float(pv["rpm"]),
                              help="Free tiers need ~8.")
        use_playbook = st.checkbox("Use editorial playbook", value=True,
                                   help="Hook psychology + cliffhanger rules in prompts/promo_playbook.md")
        per_episode = st.slider("Promos proposed per episode", 1, 5, 3)
    else:
        use_playbook, per_episode = True, 3
    top_n = st.slider("Promos in the final list", 5, 50, DEFAULTS["top_n"])
    compare = st.checkbox("Also show keyword-rules picks for comparison", value=mode.startswith("LLM"))


# ------------------------------------------------------------------ main: upload
st.title("🎬 Promo Ranker")
st.caption("Upload a microdrama's timestamped screenplays. Get ranked 20–60 s promos with exact timecodes, "
           "hook, cliffhanger and why each works.")

files = st.file_uploader("Episode scripts: one .zip, or several .txt files", type=["zip", "txt"],
                         accept_multiple_files=True)
if not files:
    st.info("Upload the scripts to begin. Each file is one episode in the timestamped screenplay format "
            "(`**CHARACTER (Emotion):**` / `Start: (00:11.30) | End: (00:13.10)`).")
    st.stop()

# save uploads into this session's folder
sig = tuple((f.name, f.size) for f in files)
if ss.get("upload_sig") != sig:
    up = ss.workdir / "input"
    if up.exists():
        for p in up.rglob("*"):
            if p.is_file():
                p.unlink()
    up.mkdir(parents=True, exist_ok=True)
    for f in files:
        data = f.getvalue()
        if f.name.lower().endswith(".zip"):
            with zipfile.ZipFile(io.BytesIO(data)) as z:
                for n in z.namelist():
                    if n.lower().endswith(".txt") and not n.startswith("__MACOSX") and ".." not in n:
                        (up / Path(n).name).write_bytes(z.read(n))
        else:
            (up / Path(f.name).name).write_bytes(data)
    ss.upload_sig, ss.result = sig, None
    ss.show = show_name(files[0].name) if len(files) == 1 else "Uploaded show"

try:
    all_eps, _ = load(ss.workdir / "input")
except Exception as e:
    st.error(f"Could not read the scripts: {e}")
    st.stop()
if not all_eps:
    st.error("No parsable episodes found in the upload.")
    st.stop()
nums = [e.num for e in all_eps]
st.success(f"Loaded **{len(all_eps)} episodes** ({min(nums)}–{max(nums)}), "
           f"{sum(len(e.events) for e in all_eps)} timed beats.")

c1, c2 = st.columns([3, 1])
with c1:
    lo, hi = st.select_slider("Episodes to process", options=nums, value=(min(nums), max(nums)))
with c2:
    st.write("")
    run = st.button("▶ Find promos", type="primary", width="stretch")

n_sel = sum(lo <= n <= hi for n in nums)
st.caption(f"{n_sel} episodes selected."
           + (f" LLM mode makes about {n_sel + 1} API calls (one per episode + one ranking call)."
              if mode.startswith("LLM") else ""))


# ------------------------------------------------------------------ run
def build_client(outdir: Path):
    if mode.startswith("Demo"):
        return LLMClient(load_provider("mock"), outdir, cache_dir=ss.workdir / "cache")
    if mode == "LLM (built-in model)":
        opts = {"api_key": HOSTED["key"]}
        if HOSTED["base_url"]:
            opts["base_url"] = HOSTED["base_url"]
        # shared cache: re-running a show someone already ran costs no quota
        return LLMClient(load_provider(HOSTED["provider"], HOSTED["model"], **opts), outdir,
                         cache_dir=SHARED_CACHE, rpm=HOSTED["rpm"])
    pv = PROVIDERS[provider_label]
    opts = {"api_key": api_key.strip()}
    if base_url:
        opts["base_url"] = base_url.strip()
    return LLMClient(load_provider(pv["spec"], model.strip() or None, **opts), outdir,
                     cache_dir=SHARED_CACHE, rpm=rpm)


if run:
    if mode == "LLM (built-in model)":
        if HOSTED["passcode"] and not hmac.compare_digest(passcode.strip(), str(HOSTED["passcode"])):
            st.error("Wrong passcode for the built-in model. Use your own key, Rules or Demo mode instead.")
            st.stop()
        if n_sel > HOSTED["max_eps"]:
            st.error(f"The built-in model is limited to {HOSTED['max_eps']} episodes per run "
                     f"(you selected {n_sel}). Narrow the episode range, or use your own key.")
            st.stop()
    elif mode.startswith("LLM") and not api_key.strip():
        st.error("Paste an API key in the sidebar, or switch to Rules / Demo mode.")
        st.stop()
    if mode == "LLM (your API key)" and PROVIDERS[provider_label]["base_url"] and not base_url.strip():
        st.error("Set the Base URL for the OpenAI-compatible provider (e.g. https://api.openai.com/v1, "
                 "https://api.groq.com/openai/v1). For Gemini, pick 'Gemini (Google)' as the provider instead.")
        st.stop()
    episodes = [e for e in load(ss.workdir / "input")[0] if lo <= e.num <= hi]
    outdir = ss.workdir / "output"
    shutil.rmtree(outdir, ignore_errors=True)          # no stale files from a previous run
    llm_mode = not mode.startswith("Rules")
    total = len(episodes)
    bar = st.progress(0.0, text="Starting…")
    with st.status("Running…", expanded=True) as status:
        def log(msg: str):
            msg = msg.strip()
            if not msg:
                return
            st.write(msg)
            if msg.startswith("episode "):
                try:
                    done = int(msg.split()[1].rstrip(":"))
                    k = sum(e.num <= done for e in episodes)
                    bar.progress(k / total, text=f"Episode {done} done ({k}/{total})")
                except ValueError:
                    pass
        try:
            client = build_client(outdir) if llm_mode else None
            if client is not None and mode == "LLM (your API key)":   # built-in: save quota, skip ping
                ok, msg = client.ping()
                if not ok:
                    status.update(label="Model check failed", state="error")
                    st.error(f"The model didn't answer correctly: {msg}")
                    st.stop()
                st.write(f"Model check: {msg}")
            playbook = PLAYBOOK.read_text() if (llm_mode and use_playbook) else None
            res = run_pipeline(episodes, ss.show, outdir, mode="llm" if llm_mode else "rules",
                               client=client, playbook=playbook, playbook_name=PLAYBOOK.name,
                               per_episode=per_episode, compare=compare and llm_mode,
                               cfg={"top_n": top_n}, log=log)
        except SystemExit as e:                     # provider config errors raise SystemExit with a message
            status.update(label="Setup error", state="error")
            st.error(str(e))
            st.stop()
        except Exception as e:
            status.update(label="Failed", state="error")
            st.exception(e)
            st.stop()
        bar.progress(1.0, text="Done")
        status.update(label=f"Done in {res['meta'].get('runtime_s', 0)} s", state="complete", expanded=False)
    ss.result = {"outdir": str(outdir), "stopped": res["stopped"], "quality": res["quality"],
                 "meta": res["meta"]}


# ------------------------------------------------------------------ results
r = ss.get("result")
if not r:
    st.stop()
outdir = Path(r["outdir"])
data = json.loads((outdir / "promos.json").read_text())
promos, q, meta = data["promos"], r["quality"], r["meta"]

if r["stopped"]:
    st.warning(f"Partial result: the API quota ran out at episode {r['stopped']['at_episode']}. "
               f"Not processed: {r['stopped']['missing']}. Try again later, or use another key/model.")

st.subheader(f"Ranked promos ({len(promos)})")
m = st.columns(4)
m[0].metric("Hard-rule failures", q.get("hard_failures", 0))
m[1].metric("Hooks inside 3 s", f"{q.get('hook_within_3s', 0)}/{q.get('promos', 0)}")
if "llm_validation" in q:
    v = q["llm_validation"]
    m[2].metric("LLM proposals accepted", f"{v['accepted']}/{v['proposed']}")
if "rules_vs_llm" in q:
    rv = q["rules_vs_llm"]
    m[3].metric("Also found by keyword rules", f"{rv['llm_picks_also_found_by_rules']}/{rv['of']}")

rows = []
for d in promos:
    L = d.get("llm") or {}
    rows.append({
        "#": d["rank"],
        "Segments": " + ".join(f"E{s['episode']} {s['start']}→{s['end']}" for s in d["segments"]),
        "Length (s)": d["duration_s"],
        "Hook type": L.get("hook_type", ""),
        "Hook": d["hook"],
        "Cliffhanger": d["cliffhanger"],
        "Why it works": d["why_it_works"],
        "Why post it here": d.get("post_reason", ""),
        "Flags": "; ".join(d["flags"]),
    })
st.dataframe(pd.DataFrame(rows), hide_index=True, width="stretch")

with st.expander("Details for each promo (segments, stakes, transcript)"):
    for d in promos:
        L = d.get("llm") or {}
        st.markdown(f"**{d['rank']}. " + " + ".join(
            f"Episode {s['episode']} {s['start']} → {s['end']}" for s in d["segments"]) + f"** ({d['duration_s']} s)")
        st.markdown(f"- Hook ({L.get('hook_type', '')}, at {d['hook_at_s']} s): {d['hook']}\n"
                    f"- Cliffhanger ({L.get('cliffhanger_type', '')}): {d['cliffhanger']}\n"
                    + (f"- Stakes: {L['stakes']}\n" if L.get("stakes") else "")
                    + f"- Why: {d['why_it_works']}")
        st.code("\n".join(f"{b['t']:5.1f}s  E{b['ep']} {b['src']}  "
                          f"{(b['speaker'] or b['kind'].upper())}: {b['text'][:110]}"
                          for b in d["transcript"] if b["kind"] != "camera"), language=None)

with st.expander("Quality report"):
    st.markdown((outdir / "quality.md").read_text())

if (outdir / "rules_comparison" / "promos.md").exists():
    with st.expander("Keyword-rules picks (for comparison)"):
        st.markdown((outdir / "rules_comparison" / "promos.md").read_text())

st.subheader("Download")
buf = io.BytesIO()
with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
    for p in outdir.rglob("*"):
        if p.is_file():
            z.write(p, p.relative_to(outdir))
d1, d2, d3, d4 = st.columns(4)
d1.download_button("All outputs (.zip)", buf.getvalue(), "promo_outputs.zip", "application/zip")
d2.download_button("promos.md", (outdir / "promos.md").read_bytes(), "promos.md", "text/markdown")
d3.download_button("promos.json", (outdir / "promos.json").read_bytes(), "promos.json", "application/json")
d4.download_button("all_candidates.csv", (outdir / "all_candidates.csv").read_bytes(), "all_candidates.csv",
                   "text/csv")
st.caption(f"Model: {meta.get('provider')} {meta.get('model') or ''} · playbook: {meta.get('playbook') or 'none'} "
           f"· {meta.get('episodes')} episodes")
