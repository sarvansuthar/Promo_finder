---
title: Promo Ranker
emoji: 🎬
colorFrom: indigo
colorTo: pink
sdk: docker
app_port: 7860
pinned: false
short_description: Find and rank promo clips in microdrama screenplays
---

# Promo Ranker — find the best promos in a microdrama screenplay

Reads a show's timestamped screenplays (one `.txt` per episode, or a `.zip` of them) and returns
ranked 20–60 s promos. Each promo comes with the episode and start/end timecodes of every
segment, the hook, the cliffhanger, why it works, and why it sits at that slot in the posting
order.

An **LLM does the editorial judgment**: it reads each episode, proposes promos, and ranks them
across the show. **Code checks every pick**: real timecodes, 20–60 s, never cutting mid-line, hook
inside 3 s. A keyword-rules mode stays as a free offline fallback and as a second opinion.

## Run

**Live app: https://promo-finder-1-1ksu.onrender.com** (how to use it: [RUN_GUIDE.md, section 0](RUN_GUIDE.md#0-use-the-deployed-app-nothing-to-install)).

**Step-by-step guide, deployment and troubleshooting: [RUN_GUIDE.md](RUN_GUIDE.md).**

```bash
pip3 install -r requirements.txt                     # app + Gemini/OpenAI/Claude clients
export GEMINI_API_KEY=...                            # free key: https://aistudio.google.com/apikey
python3 run.py --input "path/to/show-scripts.zip" --provider gemini --rpm 8
open outputs/promos.md
```

- **Input:** `--input` takes a folder of episode `.txt` files or a `.zip` of them.
- **Quick test:** `--episodes 1-3` runs on a few episodes only.
- **No LLM at all:** `--mode rules`.

## Web app

```bash
pip3 install -r requirements.txt
streamlit run app.py                     # opens http://localhost:8501
```

Upload the scripts (one `.zip` or several `.txt` files), pick a mode, choose the episode range,
and press **Find promos**. The page shows progress per episode, the ranked table, per-promo
details with transcripts, the quality report, and downloads (`.zip`, `promos.md`, `.json`,
`.csv`).

The three modes:
- **LLM:** the user pastes their **own** API key (Gemini, Claude or any OpenAI-compatible API).
  The key stays in memory for that run only and is never stored or logged. A one-call model
  check runs before the full run.
- **Rules only:** no key needed, and it takes seconds.
- **Demo:** runs the full LLM pipeline with a mock model, so anyone can see the flow without a
  key.

Uploaded scripts and LLM replies go to a temporary folder per session, never into a shared
cache.

**Deploy (Streamlit Community Cloud, free).** Push this repo to GitHub, without `outputs/` or
the input pack (`.gitignore` already excludes them). Then go to https://share.streamlit.io,
choose **Create app**, pick the repo, set the main file to `app.py`, and deploy. No secrets are
needed, because users bring their own key. Streamlit Cloud suits this better than serverless
hosts such as Vercel: a full-show LLM run takes several minutes in one request.

## Bring your own model

The pipeline never talks to a vendor. It calls one function, `complete(system, user) -> text`,
and everything else happens in `llm_client.py` the same way for every model:
- **JSON:** the request for JSON goes into the prompt, the JSON is pulled out of chatty or
  fenced replies, and it's checked against the schema. If the reply is unusable, one repair call
  tells the model exactly what was wrong.
- **Reliability:** retries with backoff, throttling, and a cache.

So any model works:

| Their model is… | Command |
|---|---|
| An OpenAI-style API (OpenAI, Azure proxy, Groq, OpenRouter, Together, vLLM, LM Studio, Ollama) | `--provider openai --base-url https://their-host/v1 --api-key-env THEIR_KEY --model their-model` |
| Gemini | `--provider gemini --model gemini-pro-latest` (`GEMINI_API_KEY`; `--list-models` shows what your key can use) |
| Claude | `--provider anthropic --model claude-haiku-5-5` (`ANTHROPIC_API_KEY`; `--base-url` for a proxy) |
| A command-line tool | `--provider command --provider-opt cmd="ollama run llama3"` (prompt on stdin, reply on stdout) |
| Anything else (internal API, other SDK) | Copy [examples/custom_provider.py](examples/custom_provider.py), edit about 5 lines, then `--provider my_provider.py:complete` |
| No model, offline demo | `--provider mock` (valid but non-editorial picks) or `--provider dryrun` (writes the prompts) |

Other settings:
- **`--provider-opt key=value`** passes provider-specific settings, for example
  `json_mode=false` for endpoints without JSON mode, or `effort=high` for Claude.
- **`PROMO_PROVIDER` / `PROMO_MODEL`** set defaults, so they can be exported once.

**In the interview, with a model you haven't seen:**
```bash
python3 run.py --provider openai --base-url $URL --api-key-env KEY --model $MODEL --check-model   # 1 tiny call
python3 run.py --input new_show.zip --provider openai --base-url $URL --api-key-env KEY --model $MODEL --episodes 1-3
python3 run.py --input new_show.zip --provider openai --base-url $URL --api-key-env KEY --model $MODEL
```
`--check-model` confirms that the model is reachable and can return the JSON the pipeline needs.
After a run, `quality.md` reports how many replies needed a JSON repair and how many proposals
broke the hard rules. Both are a direct measure of how well the new model follows instructions.

Replies are cached in `.cache/`, keyed by provider, model and prompt. A re-run is free and gives
identical output, and two models never share cached answers (`--no-cache` ignores the cache).

## How it works

1. **Parse** (`parser.py`): each script becomes numbered, timed beats (action, SFX, dialogue
   with speaker and emotion tags).
2. **Find** (`llm_finder.py`, one call per episode): the model reads the whole episode and
   proposes up to 3 promos. It answers **by beat id**, never by typing timestamps, so it can't
   invent times. It also gives the hook beat, the cliff beat, a one-line why, and 0–10 scores for
   hook, cliffhanger and standalone.
3. **Check** (code): beat ids become real timecodes. Cuts snap to moments where nobody is
   speaking.
   - Hook later than 3 s: the start moves up to the hook.
   - Slightly short: the ending is held on the reaction shot.
   - Too long: the opening is trimmed.
   - Anything still breaking a rule is **rejected and counted** in `quality.md`.
4. **Rank** (one show-level call): the model sees every surviving candidate and sets the posting
   order. Premise promos go first, and story-level duplicates are removed. Code then drops
   anything sharing footage and caps picks at 2 per episode.
5. **Quality** (`quality.py`):
   - Hard checks on every promo.
   - The LLM rejection and repair rate.
   - Agreement with the keyword-rules picks (written to `rules_comparison/`).
   - A cliffhanger benchmark against the writers' own episode endings.

Thresholds such as length and hook window live in `promo.DEFAULTS`; override them with
`--config my.json`.

## Editorial playbook (what the LLM is told about good promos)

[prompts/promo_playbook.md](prompts/promo_playbook.md) is a plain one-page markdown file that
goes into the system prompt of every call. It covers:
- **Hook psychology:** curiosity gap, pattern interrupt, threat, injustice, contradiction.
- **The promo's shape:** hook, then context, then escalation, then cliffhanger.
- **Cliffhanger mechanics:** cut before the payoff (Zeigarnik effect), the information gap, and
  loss aversion.
- **Good mystery versus confusion.**
- **Posting order.**

For every promo the model must name a `hook_type`, a `cliffhanger_type` and the `stakes`, so you
can audit whether it actually applied the playbook.

- **Tune it per genre** by editing the file, or point `--playbook` at another file. No code
  changes.
- **A/B test it:** run once with the playbook and once with `--playbook none`, which uses the
  bare brief:
  ```bash
  python3 run.py --input show.zip --provider gemini --rpm 8 --episodes 1-10 --out runs/playbook
  python3 run.py --input show.zip --provider gemini --rpm 8 --episodes 1-10 --out runs/bare --playbook none
  ```
  Then compare the two `promos.md` files and their LLM scores. The cache is keyed by prompt, so
  the two runs never mix.

## Outputs (`--out`, default `outputs/`)

| File | What it is |
|---|---|
| `promos.md` | Ranked promos (rank = posting order) with segments, hook, cliffhanger, why, LLM scores, ⚠ flags |
| `promos.json` | The same data, machine-readable, plus each promo's beat-by-beat transcript |
| `all_candidates.csv` | Every accepted candidate, for the editor to pick beyond the top 25 |
| `quality.md` / `.json` | Hard checks, LLM validation stats, token usage, rules-vs-LLM agreement |
| `rules_comparison/` | What the keyword rules alone would have picked |

## Data

The input pack is confidential. Keep it out of this repo; it is passed in with `--input`. The
outputs quote script lines, so share them only with the Kuku team. Sending the scripts to an LLM
API is allowed by the brief ("use it with AI tools").
