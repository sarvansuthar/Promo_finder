# How to run Promo Ranker

The screenplays (one `.txt` per episode, or a `.zip`) go in. Ranked 20–60 s promos come out,
with exact timecodes, hook, cliffhanger, why each works, and a quality report.

---

## 1. One-time setup

```bash
cd Promo_Ranker
pip3 install -r requirements.txt        # streamlit, pandas, openai, anthropic
```
You need Python 3.10 or later. The keyword-rules mode needs no packages beyond the standard
library.

## 2. Set an API key (only for LLM mode)

Paste the key at a hidden prompt, never into a file or chat:

```bash
read -s GEMINI_API_KEY                  # paste the key (nothing shows), then press Enter
export GEMINI_API_KEY
print -r -- "export GEMINI_API_KEY=${(q)GEMINI_API_KEY}" >> ~/.zshrc     # optional: remember it
```

| Provider | Variable | Where to get the key |
|---|---|---|
| Gemini | `GEMINI_API_KEY` | https://aistudio.google.com/apikey (free tier, about 20 requests a day) |
| Claude | `ANTHROPIC_API_KEY` | https://console.anthropic.com (starts `sk-ant-api`, needs credits) |
| OpenAI-compatible | any name, passed with `--api-key-env` | the provider's console |

## 3. Key steps: a full run

```bash
# 1. Is the model reachable? (1 tiny call)
python3 run.py --provider gemini --check-model

# 2. Quick test on 3 episodes
python3 run.py --input "path/to/show-scripts.zip" --provider gemini --rpm 8 --episodes 1-3 --out outputs/test

# 3. The whole show
python3 run.py --input "path/to/show-scripts.zip" --provider gemini --rpm 8 --out outputs/gemini

# 4. No key needed: keyword rules only (about 25 s)
python3 run.py --input "path/to/show-scripts.zip" --mode rules --out outputs/rules_only
```

**Reading the results** (in the `--out` folder):

| File | What's in it |
|---|---|
| `promos.md` | Ranked promos (rank = posting order): segments, hook, cliffhanger, stakes, why, ⚠ flags |
| `quality.md` | Hard checks, LLM accepted/repaired/rejected counts, tokens, "PARTIAL" if the quota ran out |
| `promos.json` | The same data plus each promo's transcript |
| `all_candidates.csv` | Every candidate, for picking beyond the top 25 |
| `rules_comparison/` | What the keyword rules alone would have picked |

**Useful flags**

| Flag | Meaning |
|---|---|
| `--episodes 16-30` | Run only these episodes |
| `--rpm 8` | Max requests per minute: 8 for free tiers, 30 or more for paid |
| `--top 25` | Number of promos in the final list |
| `--playbook none` | Run without the editorial playbook, for an A/B test |
| `--no-compare` | Skip the keyword-rules comparison |
| `--no-cache` | Ignore cached replies and call the model again |
| `--provider dryrun` | Write the exact prompts to `outputs/llm_prompts/`, with no API calls |
| `--provider mock` | Run the whole pipeline offline with a fake model (its picks are not real) |

**The cache:** every model reply is saved in `.cache/`. Re-running the same show with the same
model is instant and free, and a run stopped by the quota continues where it stopped. **Never
delete `.cache/` mid-project.**

## 4. Changing the model

```bash
python3 run.py --provider gemini --list-models                                   # which ids your key can use
python3 run.py --input show.zip --provider gemini --model gemini-3.5-flash       # Gemini (the default)
python3 run.py --input show.zip --provider anthropic --model claude-haiku-5-5    # Claude
python3 run.py --input show.zip --provider openai --base-url https://api.groq.com/openai/v1 \
               --api-key-env GROQ_API_KEY --model <model-id>                     # any OpenAI-style API
python3 run.py --input show.zip --provider command --provider-opt cmd="ollama run llama3"   # any CLI
python3 run.py --input show.zip --provider examples/custom_provider.py:complete  # your own function
```
Give each model its own `--out` folder so the results don't overwrite each other.

## 5. Web app (local)

```bash
streamlit run app.py                    # opens http://localhost:8501
```
1. Upload the `.zip` (or several `.txt` files).
2. In the sidebar, pick a **Mode**:
   - **LLM (your API key):** pick a provider, paste the key, and check the model id.
   - **Rules only:** no key needed.
   - **Demo:** a fake model, which shows the flow without a key.
3. Choose the episode range, then click **▶ Find promos**.
4. Read the table, details, quality report and comparison, and use **Download** for the files.

The app and the CLI share `.cache/`, so neither pays twice for the same episode.

## 6. Deploy (Streamlit Community Cloud, free)

1. **Push the code to a public GitHub repo.** `.gitignore` already excludes scripts, outputs,
   `.cache/` and secrets.
   ```bash
   git add -A && git status && git commit -m "update" && git push
   ```
2. **Create the app:** go to https://share.streamlit.io, choose **Create app**, then pick the
   repo, branch `main` and file `app.py`. Under **Advanced settings**, set Python to **3.12**.
   Then click **Deploy**.
3. **Optional, to let visitors use your key:** go to **⋮ → Settings → Secrets** and add:
   ```toml
   HOSTED_API_KEY = "your-key"
   HOSTED_PROVIDER = "gemini"
   HOSTED_MODEL = "gemini-3.5-flash"
   HOSTED_PASSCODE = "choose-one"
   HOSTED_MAX_EPISODES = "10"
   HOSTED_RPM = "30"
   ```
   The sidebar then shows **LLM (built-in model)**, guarded by the passcode and the episode cap.
   The key is never in the repo or on the page.
4. **Updating:** push to GitHub and the app redeploys by itself. If it doesn't, use
   **⋮ → Reboot app**.

The cloud app starts with an empty cache, so its first runs make real API calls.

## 7. In the interview: new show, new model

```bash
python3 run.py --provider openai --base-url $URL --api-key-env THEIR_KEY --model $MODEL --check-model
python3 run.py --input new_show.zip --provider openai --base-url $URL --api-key-env THEIR_KEY --model $MODEL --episodes 1-3
python3 run.py --input new_show.zip --provider openai --base-url $URL --api-key-env THEIR_KEY --model $MODEL
```
Then open `promos.md` for the picks and `quality.md` for how well the new model followed
instructions (JSON repairs, rejected proposals).

## 8. Troubleshooting

| You see | Cause | Fix |
|---|---|---|
| `HTTP 400 … Please pass a valid API key` | The key is wrong, or placeholder text was saved | Set the key again (step 2). It should start with `AIza` (Gemini) or `sk-ant-api` (Claude) |
| `401 … Invalid bearer token` (Claude) | Not a Console API key, or a revoked one | Create a new key at console.anthropic.com |
| `404 … model … no longer available` | Google retired that model name | `--list-models`, then pass a current one with `--model` |
| `QUOTA EXHAUSTED at episode N` | The free daily cap was hit | Re-run the same command after the reset (about 1:30 PM IST for Gemini), or turn on billing. Finished episodes are cached |
| `out of quota / credits` | The account has no credits (OpenAI, Claude) | Add credits, or use another provider |
| `429 rate limit … waiting Ns` | The per-minute limit was hit | Nothing: it waits and retries by itself. Lower `--rpm` |
| A run stuck on one episode | A request hung on the provider's side | It times out after 120 s and retries. Ctrl+C, then re-run, resumes from the cache |
| `Set the Base URL…` (app) | OpenAI-compatible was chosen with no URL | Enter the URL, or choose **Gemini (Google)** |
| `ModuleNotFoundError: anthropic` / `openai` | The package isn't installed | `pip3 install -r requirements.txt` |
| `streamlit: command not found` | Not on your PATH | `python3 -m streamlit run app.py` |
| The cloud app loads forever | Still building, or a crash | **Manage app → logs**. Set Python to 3.12, then **Reboot app** |
| The app re-runs episodes the CLI already did | An old app version | Pull or push the latest code, then restart the app |

## 9. Data rules

The input pack is confidential. Never commit the scripts, `outputs/`, `.cache/`, `.env` or
`.streamlit/secrets.toml`; all of these are gitignored. Share raw outputs only through a private
link (for example, Drive set to "anyone with the link").
