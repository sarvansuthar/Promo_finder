# How to run Promo Ranker

## 1. Quick start (web app)
```bash
cd Promo_Ranker
pip3 install -r requirements.txt     # installs the app plus the Gemini, OpenAI and Claude clients
streamlit run app.py                 # opens http://localhost:8501
```
In the sidebar, choose **LLM (your API key)**, pick **Gemini / Claude / OpenAI-compatible**,
paste your key, check the model id, then upload the scripts and click **▶ Find promos**.

Where to get a key:
- **Gemini:** https://aistudio.google.com/apikey (free tier).
- **Claude:** https://console.anthropic.com.
- **OpenAI:** https://platform.openai.com.

For the command line, set the key in the terminal instead:
`read -s GEMINI_API_KEY && export GEMINI_API_KEY` (paste the key, then press Enter).

## 2. Command line
```bash
python3 run.py --provider gemini --check-model                                   # test the key (1 call)
python3 run.py --input show.zip --provider gemini --rpm 8 --episodes 1-3 --out outputs/test
python3 run.py --input show.zip --provider gemini --rpm 8 --out outputs/gemini   # full show
python3 run.py --input show.zip --mode rules --out outputs/rules_only           # no key needed
```
The results are in the `--out` folder: `promos.md` (ranked promos) and `quality.md` (checks).
Replies are cached in `.cache/`, so a re-run, or a run stopped by the quota, continues for free.
Don't delete `.cache/`.

## 3. Web app (local)
```bash
streamlit run app.py        # opens http://localhost:8501; Ctrl+C stops it
```
1. Upload the `.zip`.
2. Pick a **Mode** in the sidebar:
   - **LLM:** paste a key.
   - **Rules only:** no key needed.
   - **Demo:** a fake model, to show the flow.
3. Choose the episodes, then click **▶ Find promos**.
4. Download the results. They're temporary otherwise.

## 4. Change the model
```bash
python3 run.py --provider gemini --list-models                         # available Gemini models
python3 run.py --input show.zip --provider anthropic --model claude-haiku-5-5
python3 run.py --input show.zip --provider openai --base-url <URL> --api-key-env <KEY_VAR> --model <id>
```
`openai` covers any OpenAI-style API (OpenAI, Groq, OpenRouter, Ollama, or the interviewer's
own). In the app, the same choices are in the sidebar.

## 5. Deploy (Streamlit Community Cloud, free)
1. Push to GitHub: `git add -A && git commit -m "update" && git push`.
2. At https://share.streamlit.io, choose **Create app**, pick this repo, branch `main` and file
   `app.py`, set Python **3.12**, then **Deploy**.
3. Optional: add your key under **⋮ → Settings → Secrets** so visitors can use it (passcode-protected):
   ```toml
   HOSTED_API_KEY = "your-key"
   HOSTED_PASSCODE = "choose-one"
   HOSTED_MAX_EPISODES = "10"
   ```

## 6. Common problems
| Error | Fix |
|---|---|
| `400 … valid API key` / `401` | Wrong key: set it again (step 1) |
| `404 … model not available` | Run `--list-models` and pass a current `--model` |
| `QUOTA EXHAUSTED` / `no credits` | Free limit reached: re-run after the reset (cached episodes are kept), or add billing |
| `ModuleNotFoundError` | `pip3 install -r requirements.txt` |
| `streamlit: command not found` | `python3 -m streamlit run app.py` |
| The cloud app loads forever | **Manage app → logs**, set Python 3.12, then **Reboot app** |

**Never commit** the scripts, `outputs/`, `.cache/`, `.env` or `.streamlit/secrets.toml`; all of
these are already gitignored.
