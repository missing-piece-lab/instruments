# judges — the cross-lab cold read

*2026-10-24*

**What it is.** A branch's diff, its commits, the project's rules and a few deterministic code checks go to two judges from
two labs, each in a fresh context with no memory of writing the change. MERGE only when both say MERGE; a split is a HOLD
with the split named; a judge that cannot answer leaves the read on the other and the file says so. Every read lands as a
markdown file plus one ledger row that names the exact commit it read.

**Why it exists.** On 2026-09-20 the human opened a fresh session to review a branch the agent had written and it caught
the one thing the author would have glossed over — a second mind with no stake in the change landing. Making that a lane
the agent can run on its own work turned the human from the approval step into the reader of a record.

## Run

```
source examples/judges.env.example                 # or export the same names; keys are read from the env, never printed
python judges.py read feature/x --repo ~/code/app --base main --rules REVIEW-RULES.md --out ~/code/app-reviews
python judges.py status feature/x --repo ~/code/app --out ~/code/app-reviews    # exit 0 = read MERGE at the branch's current commit
python judges.py ledger 20 --out ~/code/app-reviews
```

Exit codes: `0` MERGE · `2` HOLD · `1` could not read · `4` not read (head lag, a read in flight, or two holds already).
The first judge is the Anthropic Messages API by default; the second is any `/chat/completions`-compatible door (another
lab directly, or a router). `kind`, base URL, model and key are env per judge. Nothing here fetches or merges: fetch
before, and let your merge step call `status` so it merges only the commit that was read.

## Design decisions, dated

- **Code checks first, then the cold read** (2026-09-20). Files touched, conflict markers, the head sha and the merge base
  are computed and printed into the brief; the model is not asked to count.
- **The reviewer keeps a ledger of its own verdicts** (2026-10-01). The last fifteen render into its brief so it notices when
  a change repeats a shape it held or passed before. The file `LEDGER.md` is the human's view of the same rows.
- **Two holds and the next step is a human's, never a third round** (2026-09-22, after six rounds in one night: a reviewer
  never stops finding things). The third read is refused unread unless `--again`, which is the human's word that they looked.
- **The second judge is from another lab and sees nothing of the first** (2026-10-11). Not its verdict, not its ledger: the
  `## Your ledger` section is cut from the second brief, everything else byte-identical. Two correlated reads are one read.
- **The first judge's ledger shows only its own verdicts** (2026-10-11, a cold read of the lane itself caught it). The
  ledger row keeps `verdict` (the first judge's) apart from `final` (the combined one); only `verdict` renders into a brief,
  or the second judge's vote would reach the first on the next read.
- **A split is a HOLD, named** (2026-10-11). The human breaks the tie; the lane never asks a third model.
- **`--head`: refuse unread when the branch is not on the commit the caller expects** (2026-10-17). A pull ref lagged a push
  by seconds and a read landed on the wrong commit. Better exit 4 and no spend than a verdict about something else.
- **One read per commit at a time** (2026-10-24). Two callers read the same commit inside a minute, each HOLD counted, and
  the fix commit was refused as a "third round". Now a read leaves an `.inflight-<label>-<sha12>` marker (atomic create,
  stale after 20 minutes, cleared only by its owner), and **a round is a distinct commit**: two HOLDs on one sha are one.
- **Errors never carry the key or the response body** (2026-10-11). An error body can echo the request.

What was cut for this version: the forge API (open PRs, mergeability, merging through the forge), the budget guard that
backs the lane off when the account is near its cap, and the first judge running on a CLI subscription rather than the
API. Git and two HTTP doors are enough to show the shape.
