# ledger — the decision ledger

*2026-10-24*

**What it is.** One YAML file of the decisions waiting on the human, each with the one line they can run or say, a door
(`one-way` | `two-way`), a date, a default where one is allowed, and the agent's own recommendation beside the ask. A
two-way row past its date shows OVERDUE and its default is owed; a one-way row never defaults and ages visibly.
`decide <id> yes|no|research|answer|default` is how a word becomes a record.

**Why it exists.** On 2026-10-01 the human's question was "what is waiting on me, and did I miss one?" — the asks were
spread across chat, commit messages and a board, and the answer took a search. One list, machine-checked, with the
irreversible decisions marked as such, is the answer that does not need a search.

## Run

```
cp examples/decisions.yml . && edit it
python ledger.py --check          # exit 0 = every open row well-formed, no two-way row past its date (a fine claims probe)
python ledger.py --lines          # the open rows as plain lines: one-way first, then two-way by date, OVERDUE marked
python ledger.py decide pr-9 yes "merge it tonight"
python ledger.py decide gate-2 research "what does the freeze still protect?"
python ledger.py decide gate-2 answer "two files, both under review"      # the finding; the row stays open for a yes/no
python ledger.py decide pr-9 default                                        # a two-way row past its date → "defaulted: …"
```

A row: `id · asked · what · line · door · by · default · recommend · source`, plus `research` once a question is on it.
Writes take a file lock, so two doors (a chat command and a web page, say) can share the file.

## Design decisions, dated

- **Two kinds of door** (2026-10-01). A two-way decision can be walked back, so silence may pick the default once the date
  passes. A one-way decision — anything touching identity, memory, money, another person, a machine outside the pair —
  cannot, so it never defaults no matter how old; its `by` is advisory and the age shows. The agent proposes; the human
  rules; the file knows which kind it is holding.
- **The ledger never acts** (2026-10-01). It notices a default is owed and records a default once someone made it happen.
  The action itself belongs to whoever has hands for it.
- **The recommendation rides beside the ask, dated by the row** (2026-10-01). The human asked to see the agent's read next
  to the question, not instead of it, and not scored afterwards.
- **A `no` needs a reason; `research` needs a question** (2026-10-02). A bare no teaches nothing; a bare "look into it" never
  closes.
- **Asking again keeps the earlier finding** (2026-10-03). A second `research` on an answered row moves the first answer to
  `history` rather than overwriting it.
- **`--check` is a probe** (2026-10-01). It exits 0 only when every open row is well-formed and nothing two-way is past its
  date, so the claims runner can carry "no decision is overdue" as an observed claim.

What was cut for this version: the daily tick that messaged the human once per overdue row, the hand-off of research
questions to a separate notes file, and committing the ledger to git on every write. Those are wiring; the two doors are
the idea.
