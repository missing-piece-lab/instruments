# pulse — does the agent still read like itself?

*2026-10-24*

**What it is.** One JSON a day about an AI agent's voice, in three layers: **substrate** (the shape of the identity prompt
it read — length, imperative density, the age of its dated line), **behaviour** (register markers over its own messages
that day — first person, questions, tables, honesty markers, length), and a **judge** (a cold read by another model of a
sample of those messages against the identity description: a few axes with a 0–10 score and one verbatim quote each, a
register from runbook to friend, a verdict, a note).

**Why it exists.** A persona that is described rather than enforced has no checklist to pass, so the only way to know it is
still there is to measure it from outside and let the human's ear be the ground truth. And the measurement must never
reach the agent: a trait scored every turn becomes a trait performed every turn.

## Run

```
cp examples/pulse.yml . && edit it                 # identity file, transcript dir, output dir, judge
export PULSE_JUDGE_BASE_URL=https://…/v1 PULSE_JUDGE_API_KEY=…
python pulse.py --day 2026-10-20                   # one day → pulse-out/2026-10-20.json
python pulse.py                                    # yesterday (the human's day, in the configured tz)
python pulse.py --backfill 7 --no-judge            # the last 7 days, the two deterministic layers only
python pulse.py --today --dry-run                  # print, write nothing; the running day is judged only with --judge
```

Transcripts are JSONL, one file per session, one `{"ts", "role", "text"}` object per line — write a ten-line adapter from
your agent's own log format. The judge is any OpenAI-compatible `/chat/completions` door; the model name is config, the
base URL and key come from the env and are never printed. `examples/` has a config, an identity file and a readable day.

## The Goodhart wall

The output directory is the one place the read lands. The agent's prompt, memory and recall never include it. stdout and
the log (not written on --dry-run) carry the deterministic numbers and the words `judge: present` — never the verdict, never a score. The human
reads the directory weekly, direction over level, and says what they hear. This is the whole instrument; the code is the
smaller part.

## Design decisions, dated

- **Three layers, each labelled for what it is** (2026-09-23). The substrate is what the agent was given; the markers are
  how it sounded; the judge is what a stranger heard. None of them is "the persona". Together, over weeks, they say whether
  it is drifting.
- **The judge scores what is on the page and must quote it** (2026-09-23). A score with no quote is an opinion; the quote is
  how the human checks the judge.
- **The log says a read happened, nothing else** (2026-09-26, after a leak on 2026-10-02 when a one-line summary with the
  verdict landed in a log the agent reads). `one_line()` and the log are written to be safe to stumble on.
- **A day with fewer than `min_messages` is not judged** (2026-09-24). Three messages do not have a register.
- **Days are the human's days** (2026-09-23). `tz` is config (default UTC); a day runs midnight to midnight in that zone.
- **The identity is read as it stood that day** (2026-09-24). If the identity file is tracked in git, a past day's substrate
  comes from the last commit on or before it, so a backfill measures what was actually in front of the agent.
- **The agent's side only** (2026-09-23). The human's messages are counted, never sent to the judge.
- **`I` is case-sensitive** (2026-10-24). A lone lowercase `i` ("i.e.") counted as first person; now the `I` forms match
  by case and `my`/`mine` either way. `first_person_share` is not comparable across this date.

What was cut for this version: per-lane measurement (the interactive seat, the unattended jobs, the always-on process each
measured apart), the per-model split within a lane, and reading the agent runtime's own transcript format directly.
Those are the shape of one deployment; the three layers and the wall are the instrument.
