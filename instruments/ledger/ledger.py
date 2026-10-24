#!/usr/bin/env python3
"""ledger — the decision ledger: the only list of what is waiting on the human, and whether one was missed.

One YAML file. Each open row:
  id · asked (date) · what · line (the one line they can run or say) · door (one-way | two-way) · by (date) · default
  (two-way only) · recommend (the agent's own read, dated by the row) · source · research (an open question on the row)

Two kinds of door. A two-way door can be walked back: when `by` passes, the default is owed, the row shows OVERDUE, and
whoever acts closes it as "defaulted: …" (`decide <id> default`). A one-way door cannot be walked back — anything that
touches identity, memory, money, another person, a box outside the pair — so it never defaults: `by` on it is advisory
and the row ages visibly until the human speaks. The ledger never acts; it notices and records.

  ledger.py [--file decisions.yml] --check      exit 0 = every open row well-formed and no two-way row past its date
  ledger.py [--file F] --summary                one line: "N open · k one-way · m two-way · o overdue"
  ledger.py [--file F] --lines                  the open rows as plain lines, one-way first, then two-way by date
  ledger.py [--file F] decide <id> yes [text]   closes: "yes — <date>: text"
  ledger.py [--file F] decide <id> no <why>     closes: "no — why" (the reason is required)
  ledger.py [--file F] decide <id> research <question>   keeps the row open with research: {question, status: pending}
  ledger.py [--file F] decide <id> answer <text>         the finding on a pending question; still open, the human decides
  ledger.py [--file F] decide <id> default      closes a two-way row past its date as "defaulted: <default>"
  ledger.py [--file F] close <id> "<outcome>"   closes with any outcome
"""
import fcntl, os, re, sys
from datetime import date, datetime, timezone

import yaml

DOORS = ("one-way", "two-way")
VERBS = ("yes", "no", "research", "answer", "default")
HEADER = """# The decision ledger — the only list of what is waiting on the human.
# open rows: id · asked · what · line · door (one-way | two-way) · by · default (two-way only) · recommend · source · research
# two-way: the default is owed when `by` passes (the row shows OVERDUE; `decide <id> default` records it). one-way: waits, never defaults.
# Partly machine-written: ledger.py rewrites this file on every decide/close, so comments below this header do not survive.
"""


def load(path):
    if not os.path.exists(path):
        return {"open": [], "closed": []}
    d = yaml.safe_load(open(path, encoding="utf-8")) or {}
    return {"open": d.get("open") or [], "closed": d.get("closed") or []}


def save(d, path):
    body = yaml.safe_dump({"open": d["open"], "closed": d["closed"]}, sort_keys=False, allow_unicode=True, width=200)
    tmp = path + ".tmp"; open(tmp, "w", encoding="utf-8").write(HEADER + body); os.replace(tmp, path)


def _d(v):
    if isinstance(v, date):
        return v
    try:
        return date.fromisoformat(str(v)[:10])
    except ValueError:
        return None


def _short(v):
    x = _d(v); return x.strftime("%-m-%d") if x else str(v or "")


def problems(d, today=None):
    today = today or datetime.now(timezone.utc).date()
    out, seen = [], set()
    for r in d["open"]:
        rid = str(r.get("id", "?"))
        if rid in seen:
            out.append(f"{rid}: duplicate id")
        seen.add(rid)
        for k in ("id", "asked", "what", "line", "door"):
            if not r.get(k):
                out.append(f"{rid}: missing {k}")
        if r.get("door") not in DOORS:
            out.append(f"{rid}: door must be one of {DOORS}")
        if r.get("door") == "two-way":
            if not r.get("default"):
                out.append(f"{rid}: a two-way door needs a default")
            by = _d(r.get("by"))
            if not by:
                out.append(f"{rid}: a two-way door needs a date (by)")
            elif by < today:
                out.append(f"{rid}: two-way, past its date {by} — the default is owed")
    return out


def overdue(d, today=None):
    today = today or datetime.now(timezone.utc).date()
    return [r for r in d["open"] if r.get("door") == "two-way" and _d(r.get("by")) and _d(r["by"]) < today]


def summary(d, today=None):
    o = d["open"]
    return (f"{len(o)} open · {sum(1 for r in o if r.get('door') == 'one-way')} one-way · "
            f"{sum(1 for r in o if r.get('door') == 'two-way')} two-way · {len(overdue(d, today))} overdue")


def lines(d, today=None):
    """The open rows as plain lines, one-way first (they only move by the human's hand), then two-way by date."""
    today = today or datetime.now(timezone.utc).date()
    def age(r):
        a = _d(r.get("asked"))
        if not a:
            return ""
        days = (today - a).days
        return f"asked {_short(a)}" + (f", {days} d" if days > 1 else "")
    def rec(r):
        return f" · I'd: {r['recommend']}" if r.get("recommend") else ""
    def research(r):
        rs = r.get("research") or {}
        return f" · research {rs['status']}" if rs.get("status") else ""
    out = []
    for r in [r for r in d["open"] if r.get("door") == "one-way"]:
        out.append(f"{r['what']} — one-way, {age(r)}" + (f", by {_short(r['by'])}" if _d(r.get("by")) else "") + f" · {r['line']}" + rec(r) + research(r))
    for r in sorted((r for r in d["open"] if r.get("door") == "two-way"), key=lambda r: str(r.get("by", ""))):
        late = " · OVERDUE" if _d(r.get("by")) and _d(r["by"]) < today else ""
        out.append(f"{r['what']} — two-way, by {_short(r.get('by'))}: default = {r.get('default')}{late} · {r['line']}" + rec(r) + research(r))
    return out


def close(d, rid, outcome, today=None):
    today = today or datetime.now(timezone.utc).date()
    for i, r in enumerate(d["open"]):
        if str(r.get("id")) == rid:
            r = dict(r); r["decided"] = today.isoformat(); r["outcome"] = outcome
            d["open"].pop(i); d["closed"].insert(0, r)
            return r
    raise ValueError(f"no open decision '{rid}'")


def decide(d, rid, verb, text="", today=None):
    """The human's word on one open row (or the finding on its question). yes/no/default close it; research keeps it open
    with a pending question; answer stores the finding and keeps it open. Raises ValueError with a plain message."""
    today = today or datetime.now(timezone.utc).date()
    rid, verb, text = str(rid).strip(), str(verb).strip().lower(), " ".join(str(text or "").split())
    if verb not in VERBS:
        raise ValueError(f"verb must be one of {', '.join(VERBS)}, not '{verb}'")
    row = next((r for r in d["open"] if str(r.get("id")) == rid), None)
    if row is None:
        if any(str(r.get("id")) == rid for r in d["closed"]):
            raise ValueError(f"{rid} is already closed — nothing to decide")
        raise ValueError(f"no open decision '{rid}'")
    if verb == "no" and not text:
        raise ValueError("a no needs a reason — say why in a few words")
    if verb == "research" and not text:
        raise ValueError("research needs the question — what should be found out?")
    if verb == "answer":
        rs = row.get("research") or {}
        if rs.get("status") != "pending":
            raise ValueError(f"{rid} has no pending research — nothing to answer")
        if not text:
            raise ValueError("an answer needs the text — one paragraph, sourced")
        row["research"] = {**rs, "status": "answered", "answer": text, "answered": today.isoformat()}
        return row
    if verb == "research":
        prev = row.get("research") or {}
        hist = list(prev.get("history") or [])
        if prev.get("status") == "answered":                              # asking again keeps the earlier finding, in history
            hist.append({k: prev[k] for k in ("asked", "question", "answer", "answered") if k in prev})
        row["research"] = {"asked": today.isoformat(), "question": text, "status": "pending", **({"history": hist} if hist else {})}
        return row
    if verb == "default":
        if row.get("door") != "two-way":
            raise ValueError(f"{rid} is a one-way door — it never defaults; the human decides")
        by = _d(row.get("by"))
        if not by or by >= today:
            raise ValueError(f"{rid} is not past its date ({row.get('by')}) — the default is not owed yet")
        return close(d, rid, f"defaulted: {row.get('default')}", today)
    if verb == "yes":
        return close(d, rid, f"yes — {_short(today)}" + (f": {text}" if text else ""), today)
    return close(d, rid, f"no — {text}", today)


def locked(path, fn):
    """load → fn(d) → save, under a file lock: two doors (a chat command, a web page) may write the same file. Unix-only (fcntl)."""
    with open(path + ".lock", "w") as lk:
        fcntl.flock(lk, fcntl.LOCK_EX)
        d = load(path)
        r = fn(d)
        save(d, path)
    return r


def apply(path, verb, rid, text="", today=None):
    """decide under the lock; returns the one-line result to show."""
    row = locked(path, lambda d: decide(d, rid, verb, text, today))
    verb = verb.strip().lower()
    if verb == "research":
        return f"{rid}: still open — research asked: {row['research']['question']}"
    if verb == "answer":
        return f"{rid}: answered — still open, a yes or no is wanted"
    return f"{rid}: closed — {row['outcome']}"


def main(argv):
    if not argv or argv[0] in ("-h", "--help"):
        print(__doc__); return 0
    path = "decisions.yml"
    if "--file" in argv:
        i = argv.index("--file"); path = argv[i + 1]; argv = argv[:i] + argv[i + 2:]
    if not argv:
        print(__doc__); return 2
    d = load(path)
    if argv[0] == "--check":
        p = problems(d)
        for x in p:
            print(x)
        return 1 if p else 0
    if argv[0] == "--summary":
        print(summary(d)); return 0
    if argv[0] == "--lines":
        for l in lines(d):
            print(l)
        return 0
    try:
        if argv[0] == "close" and len(argv) >= 3:
            r = locked(path, lambda d: close(d, argv[1], argv[2])); print(f"closed {r['id']}: {r['outcome']}"); return 0
        if argv[0] == "decide" and len(argv) >= 3:
            print(apply(path, argv[2], argv[1], " ".join(argv[3:]))); return 0
    except ValueError as e:
        print(f"{argv[0]}: {e}"); return 1
    print(__doc__); return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
