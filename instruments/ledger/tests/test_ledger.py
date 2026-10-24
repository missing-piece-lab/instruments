"""ledger — well-formed rows, the two doors, overdue, lines, every decide verb, research history, the lock, the CLI.
A temp file. Run: python tests/test_ledger.py (or pytest)."""
import importlib.util, os, subprocess, sys, tempfile
from datetime import date

HERE = os.path.dirname(os.path.abspath(__file__)); ROOT = os.path.dirname(HERE)
spec = importlib.util.spec_from_file_location("ledger", os.path.join(ROOT, "ledger.py"))
L = importlib.util.module_from_spec(spec); spec.loader.exec_module(L)
TODAY, LATER = date(2026, 10, 3), date(2026, 10, 9)


def rows():
    return {"open": [
        {"id": "gate-2", "asked": "2026-10-01", "what": "lift the freeze on the identity files", "line": "say lift", "door": "one-way", "by": "2026-10-05", "source": "t"},
        {"id": "pr-9", "asked": "2026-10-01", "what": "merge the lock fix", "line": "say merge, or merge it yourself", "door": "two-way", "by": "2026-10-08", "default": "merge through the lane"}],
        "closed": []}


def test_well_formed_and_faults():
    d = L.load(os.path.join(tempfile.mkdtemp(), "none.yml"))
    assert d == {"open": [], "closed": []} and L.problems(d, TODAY) == [] and L.summary(d, TODAY) == "0 open · 0 one-way · 0 two-way · 0 overdue"
    d = rows(); assert L.problems(d, TODAY) == []
    bad = {"open": d["open"] + [{"id": "x", "asked": "2026-10-01", "what": "w", "line": "l", "door": "two-way"}, {"id": "x", "what": "w", "line": "l", "door": "sideways"}], "closed": []}
    p = L.problems(bad, TODAY)
    for frag in ("needs a default", "needs a date", "door must be", "duplicate id", "missing asked"):
        assert any(frag in x for x in p), (frag, p)


def test_two_doors_age_differently():
    d = rows()
    p = L.problems(d, LATER); assert len(p) == 1 and "pr-9" in p[0] and "owed" in p[0], p       # the one-way row is also past its date: not a problem, it ages
    assert [r["id"] for r in L.overdue(d, LATER)] == ["pr-9"] and L.summary(d, LATER) == "2 open · 1 one-way · 1 two-way · 1 overdue"
    ls = L.lines(d, LATER)
    assert ls[0].startswith("lift the freeze on the identity files — one-way, asked 10-01, 8 d, by 10-05 · say lift") and "OVERDUE" in ls[1] and "default = merge through the lane" in ls[1], ls
    d["open"][0]["recommend"] = "hold the rule"; ls = L.lines(d, LATER); assert ls[0].endswith(" · I'd: hold the rule") and "I'd:" not in ls[1]


def test_decide_every_verb():
    d = rows()
    for verb, text, frag in (("maybe", "", "verb must be"), ("no", "", "needs a reason"), ("research", "", "needs the question"), ("answer", "x", "no pending research"), ("yes", "", "no open decision")):
        try: L.decide(d, "nope" if frag == "no open decision" else "gate-2", verb, text, TODAY); raise AssertionError(verb)
        except ValueError as e: assert frag in str(e), (verb, e)
    # a one-way door never defaults; a two-way one only once its date has passed
    try: L.decide(d, "gate-2", "default", "", LATER); raise AssertionError("one-way defaulted")
    except ValueError as e: assert "never defaults" in str(e)
    try: L.decide(d, "pr-9", "default", "", TODAY); raise AssertionError("defaulted early")
    except ValueError as e: assert "not owed yet" in str(e)
    r = L.decide(d, "pr-9", "default", "", LATER); assert r["outcome"] == "defaulted: merge through the lane" and r["decided"] == "2026-10-09" and d["closed"][0]["id"] == "pr-9"
    # research keeps the row open; answer stores the finding; asking again keeps the history
    r = L.decide(d, "gate-2", "research", "what does the freeze still protect?", TODAY)
    assert r["research"] == {"asked": "2026-10-03", "question": "what does the freeze still protect?", "status": "pending"} and len(d["open"]) == 1
    assert L.lines(d, TODAY)[0].endswith(" · research pending")
    r = L.decide(d, "gate-2", "answer", "two files, both under review", LATER); assert r["research"]["status"] == "answered" and r["research"]["answered"] == "2026-10-09"
    r = L.decide(d, "gate-2", "research", "and after the review?", LATER)
    assert r["research"]["status"] == "pending" and r["research"]["history"][0]["answer"] == "two files, both under review"
    r = L.decide(d, "gate-2", "yes", "lift it Monday", LATER); assert r["outcome"] == "yes — 10-09: lift it Monday" and d["open"] == []
    try: L.decide(d, "gate-2", "yes", "", LATER); raise AssertionError("decided twice")
    except ValueError as e: assert "already closed" in str(e)
    d = rows(); r = L.decide(d, "pr-9", "no", "not this week", TODAY); assert r["outcome"] == "no — not this week"


def test_file_roundtrip_and_cli():
    tmp = tempfile.mkdtemp(); path = os.path.join(tmp, "decisions.yml")
    L.save(rows(), path); raw = open(path).read(); assert raw.startswith("# The decision ledger")
    assert L.load(path)["open"][1]["default"] == "merge through the lane"
    py = [sys.executable, os.path.join(ROOT, "ledger.py"), "--file", path]
    r = subprocess.run(py + ["--summary"], capture_output=True, text=True); assert r.stdout.strip().startswith("2 open · 1 one-way · 1 two-way"), r.stdout
    r = subprocess.run(py + ["--check"], capture_output=True, text=True); assert r.returncode == 1 and "pr-9" in r.stdout and "owed" in r.stdout, "today is past 2026-10-08"
    r = subprocess.run(py + ["decide", "pr-9", "default"], capture_output=True, text=True); assert r.returncode == 0 and "defaulted: merge through the lane" in r.stdout, r.stdout
    r = subprocess.run(py + ["--check"], capture_output=True, text=True); assert r.returncode == 0 and r.stdout == ""
    r = subprocess.run(py + ["decide", "gate-2", "research", "what", "does", "it", "protect?"], capture_output=True, text=True); assert "research asked: what does it protect?" in r.stdout
    r = subprocess.run(py + ["decide", "gate-2", "no"], capture_output=True, text=True); assert r.returncode == 1 and "needs a reason" in r.stdout
    r = subprocess.run(py + ["--lines"], capture_output=True, text=True); assert r.stdout.count("\n") == 1 and "research pending" in r.stdout
    r = subprocess.run(py + ["close", "gate-2", "lifted by hand"], capture_output=True, text=True); assert "closed gate-2: lifted by hand" in r.stdout
    d = L.load(path); assert d["open"] == [] and [x["id"] for x in d["closed"]] == ["gate-2", "pr-9"] and os.path.exists(path + ".lock")


def test_example_ledger_is_well_formed():
    d = L.load(os.path.join(ROOT, "examples", "decisions.yml"))
    assert len(d["open"]) >= 2 and L.problems(d, date(2027, 1, 10)) == [] and L.lines(d, date(2027, 1, 10))   # the example dates are placeholders: check it on a day inside them


if __name__ == "__main__":
    for name, fn in [(k, v) for k, v in list(globals().items()) if k.startswith("test_")]:
        fn(); print(f"  ok {name}")
    print("ledger: all green")
