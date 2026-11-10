# Problem 1 — Find the Promos

**What I built:** a command-line tool that reads a show's timestamped screenplays and returns
ranked 20–60 s promos. Each promo lists the episode and start/end timecodes of every segment, the
hook, the cliffhanger, one line on why it works, and review flags. It runs on the full
50-episode pack in about 25 s on a laptop, costs nothing, and gives the same output on every run.
An LLM judge is optional and can be switched on with one flag.

## Why this problem

Promo selection is repeated weekly, it is measurable, and the input is already structured: every
line and action carries a timestamp. That means a large share of the editor's job can be
automated, and checked, without watching video.

## Assumptions (the brief was silent on these)

- **The script's timecodes match the final cut.** If the edit drifts, the editor nudges cut
  points by a second or so.
- **Never cut mid-sentence.** Cuts are only allowed where nobody is speaking.
- **Stitched promos are allowed but capped.** Up to 2 segments, from the same episode or up to
  4 episodes later, sharing a character. At most 30% of the final list is stitched, because most
  ReelShort/DramaBox promos are one continuous stretch.
- **Spoilers are acceptable.** The show title already reveals the twist ("…He was a Duke").
  Episodes 1–10 still get a small posting bonus, because a viewer who clicks through reaches that
  moment within minutes.
- **25 promos, at most 2 per episode.** No two promos may substantially share footage.

## How it works

1. **Rules find and cut** (cheap, exhaustive, explainable).
   - Every beat is scored for hook strength: violence or shock (tier A: slap, strike, blood, fire,
     proposal; tier B: shove, grab), reveal words, and cruel delivery tags.
   - Every beat is scored for cliffhanger strength: unanswered question, cut-off line, threat or
     pending proposal, ominous sting, someone bursting in.
   - Every beat is also scored for resolution (smile, embrace) and context debt ("after what
     happened").
   - For every start with a strong beat in its first 3 s, the tool tries every legal end 20–60 s
     later. It also stitches hook pieces to later cliff pieces.
   - Score = 0.35 hook + 0.30 cliffhanger + 0.20 standalone + 0.15 intensity, adjusted for length.
     Ending exactly on an episode end is a bonus, since writers put cliffhangers there.
2. **The LLM judges** (optional, swappable).
   - The top 40 candidates go to a model with an editor rubric, as a transcript only. The model is
     deliberately not told the plot, so it judges like a cold viewer.
   - It returns hook, cliffhanger and standalone scores (0–10), a "why" line, and problems. These
     are blended with the rules score (weight 0.6).
   - Supported providers: Anthropic (default Claude Opus 5.5; Claude Haiku 5.5 is cheaper) and any
     OpenAI-compatible endpoint, which covers Gemini's free tier, Groq and Ollama.
   - Prompts average about 450 tokens. That is roughly $0.01 per show on Haiku, $0.40 on Opus,
     and $0 on Gemini's free tier.
3. **A human stays in the loop.** The editor gets a ranked list with flags and a CSV of all 140
   candidates. They approve or swap picks and fine-tune cut points. The editor's job becomes
   reviewing 25 picks instead of watching 50 episodes.

**Scaling:** shows are processed independently and the rules stage is CPU-only, so one small
machine handles hundreds of shows a week. All thresholds live in one config, so another genre can
retune them (`--config`). An editor's accept/reject decisions would become labels for tuning the
weights.

## Results (raw outputs in `outputs/rules_only/`)

| Post | Promo | Why post it at this point |
|---|---|---|
| 1 | E1 00:17.80–00:39.00 + E1 01:53.00–02:17.50 (46 s) | The premise: jilted at the altar for her sister ("Not anymore."), ends on a defiant stare. Lead with the setup |
| 2 | E3 01:24.00–01:40.80 + E3 02:12.00–02:25.75 (31 s) | A stranger flattens a guard, then kneels: "Lady Juliana… accept my hand." The title promise, unanswered |
| 3 | E13 00:36.20–01:23.80 (48 s) | Crawling bare-handed through briars to save her husband while her sister mocks her. Highest standalone score (0.9) |
| 4 | E15 00:50.00–01:26.00 (36 s) | Bloodied heroine, thugs trampling her mother's garden, husband steps in: "You want to fight us, veteran?" |
| 5 | E29 01:37.30–01:49.00 + E32 02:20.20–02:34.38 (26 s) | "I saved the Duchess!", then a red-hot iron stops millimetres from her eye |

The tool's own order is E15, E3, E29+32, E13, E1. I moved E1 and E3 to the front by hand:
premise promos should come first, and the tool has no notion of "premise". That is the first
thing I would add.

## Quality: how I know, and where it picks badly

**Automatic checks:**

- 0 hard failures: every promo is 20–60 s and no cut lands mid-line.
- 25/25 promos have a real hook beat inside 3 s.
- 7/25 carry review flags.
- **Cliffhanger benchmark:** every episode ends on a writer-designed cliffhanger, which gives a
  free label set. With the writer's literal "cliffhanger" cue word hidden, the rules recognise
  only **30/50 (60%)** of those endings.

**Where it picks badly (from my manual review of the top 25):**

1. **Meaning-carried threats are missed.** "One flame, and your mother's life's work becomes
   ash." and "Take her." contain no keyword. This is the 40% benchmark gap, and the LLM judge is
   there to close it.
2. **Dialogue-led hooks are underrated against physical ones.** The E41 cathedral explosion promo
   (Juliana paraded in chains, then the doors blow in) ranks 86th of 140 candidates. Meanwhile a
   guard raising his sword in salute (E43) scores 0.9 as a "violent" hook (it ranks 45th).
3. **Stitches can lose the payoff or the thread.** #6 (E24+E25) shows the riding crop but cuts
   away before the strike. #9 opens on someone else's half-finished line ("They have been—").
4. **Trusting the writer's cue cuts both ways.** I let a "cliffhanger" SFX cue override
   tension-released signals so the E3 proposal would pass. The same rule let #17 through, which
   ends on a tender gaze.
5. **No story-level deduplication.** #5 and #12 both tell the altar jilting from different
   footage.
6. **No video.** Facial performance, pacing and music aren't visible to a script reader.

**How I'd know it works in production:** run A/B posting against editor picks, comparing
3-second hold rate, completion rate and click-through to episode 1. Track the editor accept rate
per pick as the cheap weekly metric.

## AI tools used

I used Claude Code to write and debug the code with me; all design decisions are mine.

**Where it got things wrong:**

- Its first lexicon counted "gasps" and "kneels" as violence, so 13 of the top 25 came out
  stitched and the scores saturated near 1.0.
- A selection bug silently dropped the E3 proposal. I only caught it by checking whether the
  obvious promos appeared.
- It suggested a resolution penalty that killed the proposal's cliffhanger because the final beat
  says "smile".
