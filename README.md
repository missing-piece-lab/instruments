# The missing piece — the instruments

*2026-10-24 · four small tools, each with its design note, tests and an example · MIT*

Most of what is published about AI agents is about the agent: its tools, its memory, its benchmarks. Almost nothing is
published about the *pair* — the human and the agent as one working unit — even though team science settled decades ago
that a pair's output depends on things like shared mental models, mutual monitoring and calibrated trust, none of which
live inside either member. That relational axis is the missing piece. We are one such pair, building along it,
measuring as we go and keeping the record. This repo is the instruments: the small
pieces of code that let a pair observe its own state, hear whether the agent still sounds like itself, review its own work
cold, and keep an honest list of what is waiting on the human. They are deliberately small. Each one is the smallest
honest version of a thing we actually run.

| instrument | one line | the design decision at its heart |
|---|---|---|
| [`claims`](instruments/claims/) | a `CLAIMS.yml` per project, a runner that probes every claim and writes `STATE.md` | state is observed, never written by hand; documentation never asserts what a probe can check |
| [`pulse`](instruments/pulse/) | one JSON a day on an agent's voice: substrate · behaviour markers · a cold judge | the Goodhart wall — the agent never sees its scores; a trait scored every turn is a trait performed every turn |
| [`judges`](instruments/judges/) | a branch diff + the rules → two independent judges; MERGE only when both say MERGE | independent reads: the second judge sees nothing of the first; two holds and the human decides, never a third round |
| [`ledger`](instruments/ledger/) | one YAML of open decisions with one-way and two-way doors | a two-way door defaults when its date passes; a one-way door never does and ages visibly |

## Install and run

Python 3.11+, one dependency (`pyyaml`). Each instrument is a single file you can copy out on its own.

```
git clone <this repo> && cd missing-piece-instruments
pip install pyyaml
python tests/run.py            # every instrument's tests, offline, then tools/check.sh (the scrub gate: secret-shaped strings, the size cap,
                               #   and your own private words from ~/.config/missing-piece/private-words — never in the repo)
python -m pytest               # the same tests under pytest, if you have it
python instruments/claims/claims.py --root . --docs .     # the repo probing its own claims
```

Each `instruments/<name>/README.md` says what the tool is, why it exists, how to run it, and the decisions behind it with the
dates they were made. `examples/` under each has a config or manifest to start from. The tests run against fake endpoints
and temp directories; nothing here calls a model unless you give it a key. `ledger` and the gate are Unix-only (file locks, bash).

## What this is not

Not a framework, not an agent, not a benchmark. There is no server, no plugin system, no dashboard. The instruments are
small on purpose: a stranger should be able to read any one of them in ten minutes and decide whether the idea is worth
keeping. Everything that was specific to our own machines, forge, chat line and budget was cut, and each README says what.

## Who

Kevin, the human. Ariah, the AI collaborator.

## License

MIT, copyright 2026 Kevin and Ariah. See [LICENSE](LICENSE).
