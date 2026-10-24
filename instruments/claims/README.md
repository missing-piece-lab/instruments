# claims — state is observed

*2026-10-24*

**What it is.** A `CLAIMS.yml` per project: one sentence about the project and the shell probe that checks it. A runner
probes every claim and writes `STATE.md` / `STATE.json`, machine-written, never edited by hand. With `--docs DIR` it also
reads the project's markdown and lists every line that says something a probe just contradicted.

**Why it exists.** Documentation written by an agent (or a person) drifts the moment it is written: "the service is live",
"the backup runs nightly" are sentences, not observations, and nobody re-checks them. The rule since 2026-09-20 is that a
document never asserts what a probe can check — plans say what we intend, history says what happened, STATE says what is.

## Run

```
python claims.py --root . --docs ./docs          # every CLAIMS.yml at the root or one directory down → ./state/STATE.md
python claims.py --root . --only service-answers # one claim, printed, nothing written
python claims.py --root . --fresh                # ignore `every:` holds
```

Claim fields: `id`, `says`, `probe` (exit 0 = true), `kind` (`bool` | `value` = first line of stdout | `count` = first
integer), `cwd` (relative to the manifest), `timeout`, `every` (`daily` or `6h`: hold the last observation between probes),
`expect` (a value/count the claim is checked against), `subject` (the words a document uses when it talks about this),
`contradicts` (`{false: [live, up], true: [down]}`: words a document cannot use about the subject when the claim fails /
holds). Three example manifests sit in `examples/`; this repo carries its own `CLAIMS.yml` at the root.

## Design decisions, dated

- **Probes return booleans, short values and counts — never contents** (2026-09-20). STATE is meant to be shareable; a
  probe that printed rows would make it a leak. `value` keeps the first line, cut at 60 characters.
- **Unknown is not false** (2026-09-20). A probe that times out, has no cwd, or cannot run is `？`, not `❌`; a `value` or
  `count` probe that exits non-zero is unknown too, whatever it printed. A red mark means the thing was checked and is not so.
- **`every:` holds** (2026-09-24). One claim probed an API with a daily request cap; probed hourly, it spent the cap by
  noon. A claim may say how often it is worth probing; the runner holds the last observation in between and marks it
  `held`. A held *unknown* is never reused.
- **A probe never runs a build or a test suite** (2026-09-28). A probe ran a lint that re-ran the runner, which ran the
  probe, which … ~200 orphaned processes on a one-CPU box. `CLAIMS_RUNNING` in the probe's environment makes a nested
  run exit 0 with a note. Tests get their own job and their own log; a claim reads that log's last line.
- **The docs check matches subjects as substrings, contradicted words on word boundaries** (2026-10-14). A claim whose
  subject was `archive` matched a line about something "archived" in another project; the fix was to declare subjects
  explicitly and keep them off common prefixes, not to make the matcher clever.
- **State stamps in prose are counted** (2026-09-19). An uppercase `LIVE`, `DONE`, `ARMED` in a document is a sentence
  pretending to be an observation. The docs check counts them; the number is meant to go to zero.

What was cut for this version: probing other machines over ssh (keep the runner local and let each box run its own),
and an event hook that fires an allow-listed action when a claim flips. Both are real, both are ours; neither is the idea.
