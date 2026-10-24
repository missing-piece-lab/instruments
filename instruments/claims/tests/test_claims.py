"""claims — probes of every kind, `every:` holds, the docs check, the nested guard, the CLI. Temp dirs, no network.
Run: python tests/test_claims.py (or pytest)."""
import importlib.util, json, os, subprocess, sys, tempfile
from datetime import datetime, timedelta, timezone

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
spec = importlib.util.spec_from_file_location("claims", os.path.join(ROOT, "claims.py"))
C = importlib.util.module_from_spec(spec); spec.loader.exec_module(C)


def world():
    tmp = tempfile.mkdtemp()
    a = os.path.join(tmp, "alpha"); b = os.path.join(tmp, "beta"); docs = os.path.join(tmp, "docs")
    for d in (a, b, docs, os.path.join(a, "sub")):
        os.makedirs(d, exist_ok=True)
    open(os.path.join(a, "sub", "marker"), "w").write("x\n")
    open(os.path.join(a, "CLAIMS.yml"), "w").write("""project: alpha
claims:
  - id: marker-present
    says: the marker file exists
    probe: test -f marker
    cwd: sub
    subject: marker
    contradicts: {false: [present, live]}
  - id: service-up
    says: the service answers
    probe: "false"
    subject: [service]
    contradicts: {false: [live, up], true: [down]}
  - id: version
    says: the version on disk
    probe: echo 1.4.2
    kind: value
  - id: rows
    says: rows in the table
    probe: echo "42 rows"
    kind: count
  - id: rows-expected
    says: the table holds 42 rows
    probe: echo 42
    kind: count
    expect: 42
  - id: slow
    says: a probe that hangs is unknown, not false
    probe: sleep 5
    timeout: 1
  - id: nowhere
    says: a missing cwd is unknown
    probe: "true"
    cwd: does-not-exist
  - id: nested
    says: a probe that shells back into the runner must not go red
    probe: python3 {claims} --root . --out state >/dev/null
    cwd: .
  - id: failed-value
    says: a value from a command that failed is unknown
    probe: echo oops; exit 3
    kind: value
  - id: expensive
    says: a probe held per day
    probe: date +%s%N
    kind: value
    every: daily
""".replace("{claims}", os.path.join(ROOT, "claims.py")))
    open(os.path.join(b, "CLAIMS.yml"), "w").write("project: beta\nclaims: not-a-list\n")
    os.makedirs(os.path.join(tmp, "gamma")); open(os.path.join(tmp, "gamma", "CLAIMS.yml"), "w").write("project: gamma\nclaims:\n  - id: x\n   says: [unclosed\n")
    open(os.path.join(docs, "notes.md"), "w").write("# notes\n\nThe service is live and up since Tuesday.\n\nThe marker is present.\n\nnothing about either here: live.\n\nThe gate is LIVE 9-19.\n")
    return tmp, a, b, docs


def by_id(state, project="alpha"):
    p = next(p for p in state["projects"] if p["project"] == project)
    return {c["id"]: c for c in p["claims"]}


def test_probes_of_every_kind():
    tmp, a, b, docs = world()
    st = C.evaluate(C.discover(tmp), os.path.join(tmp, "state"))
    assert [p["project"] for p in st["projects"]] == ["alpha", "beta", "gamma"]
    c = by_id(st)
    assert c["marker-present"]["value"] is True and c["service-up"]["value"] is False
    assert c["version"]["value"] == "1.4.2" and c["rows"]["value"] == 42
    assert c["rows-expected"]["ok"] is True and c["rows-expected"]["expect"] == 42
    assert c["slow"]["value"] is None and c["slow"]["note"] == "probe timed out"
    assert c["nowhere"]["value"] is None and "no such cwd" in c["nowhere"]["note"]
    assert c["nested"]["value"] is True, "the inner run exits 0 and prints that it skipped"
    assert c["marker-present"]["contradicts"] == {"false": ["present", "live"]}, "yaml booleans become the strings true/false"
    assert c["marker-present"]["subject"] == ["marker"], "a bare string subject is one subject, not its letters"
    assert c["failed-value"]["value"] is None and c["failed-value"]["note"] == "exit 3"
    beta = next(p for p in st["projects"] if p["project"] == "beta")
    assert beta["error"].startswith(beta["file"]) and "mapping" in beta["error"], beta
    gamma = next(p for p in st["projects"] if p["project"] == "gamma")
    assert "is not valid YAML at line 4" in gamma["error"] and gamma["error"].startswith(gamma["file"]), gamma["error"]


def test_every_holds_between_runs():
    tmp, a, b, docs = world()
    out = os.path.join(tmp, "state")
    s1 = C.evaluate(C.discover(tmp), out); C.write_state(s1, out)
    v1 = by_id(s1)["expensive"]["value"]
    s2 = C.evaluate(C.discover(tmp), out)
    e2 = by_id(s2)["expensive"]
    assert e2["value"] == v1 and e2.get("held") and "held from" in e2["note"], e2
    s3 = C.evaluate(C.discover(tmp), out, fresh=True)
    assert by_id(s3)["expensive"]["value"] != v1 and not by_id(s3)["expensive"].get("held")
    # a held value from yesterday is re-probed today
    prev = json.load(open(os.path.join(out, "STATE.json")))
    yday = (datetime.now(timezone.utc) - timedelta(days=1)).isoformat(timespec="seconds")
    for c in prev["projects"][0]["claims"]:
        c["observed"] = yday
    json.dump(prev, open(os.path.join(out, "STATE.json"), "w"))
    assert not by_id(C.evaluate(C.discover(tmp), out))["expensive"].get("held")
    # a held unknown is never reused
    assert C.held({"every": "daily"}, {"value": None, "observed": yday}, datetime.now(timezone.utc)) is None
    now = datetime.now(timezone.utc)
    assert C.held({"every": "6h"}, {"value": 1, "observed": (now - timedelta(hours=5)).isoformat()}, now)
    assert C.held({"every": "6h"}, {"value": 1, "observed": (now - timedelta(hours=7)).isoformat()}, now) is None


def test_docs_check_names_drift():
    tmp, a, b, docs = world()
    st = C.check_docs(C.evaluate(C.discover(tmp), os.path.join(tmp, "state")), docs)
    d = st["docs_check"]
    assert d["lines"] > 0 and d["stamped_lines"] == 1
    drift = {(x["claim"], x["line"]): x["words"] for x in d["drift"]}
    assert drift == {("service-up", 3): ["live", "up"]}, drift      # line 5 says "live" but names no subject; the marker line agrees with its probe
    md = C.render(st)
    assert "notes.md:3" in md and "service-up" in md and "1 line(s) carry a state stamp" in md
    assert "✅ **marker-present**" in md and "❌ **service-up**" in md and "？ **slow**" in md and "**version** — the version on disk = **1.4.2**" in md


def test_cli_writes_state_and_guards_nesting():
    tmp, a, b, docs = world()
    out = os.path.join(tmp, "st")
    env = {k: v for k, v in os.environ.items() if k != "CLAIMS_RUNNING"}
    r = subprocess.run([sys.executable, os.path.join(ROOT, "claims.py"), "--root", tmp, "--out", out, "--docs", docs], capture_output=True, text=True, env=env)
    assert r.returncode == 0 and "10 probed" in r.stdout and "docs drift 1" in r.stdout, r.stdout + r.stderr
    assert r.stderr.count("claims: ") == 2 and "not valid YAML" in r.stderr, r.stderr
    assert os.path.exists(os.path.join(out, "STATE.json")) and open(os.path.join(out, "STATE.md")).read().startswith("# STATE — observed")
    r = subprocess.run([sys.executable, os.path.join(ROOT, "claims.py"), "--root", tmp, "--only", "version"], capture_output=True, text=True, env=env)
    one = json.loads(r.stdout)
    assert [c["id"] for p in one["projects"] for c in p["claims"]] == ["version"] and not os.path.exists(os.path.join(tmp, "state")), "--only prints and writes nothing"
    r = subprocess.run([sys.executable, os.path.join(ROOT, "claims.py"), "--root", tmp, "--out", os.path.join(tmp, "never")], capture_output=True, text=True, env=dict(env, CLAIMS_RUNNING="1"))
    assert r.returncode == 0 and "nested run skipped" in r.stdout and not os.path.exists(os.path.join(tmp, "never"))


def test_examples_parse():
    ex = os.path.join(ROOT, "examples")
    ms = C.discover(ex)
    assert len(ms) == 3 and all(not m.get("_error") and m["claims"] for m in ms), [(m["project"], m.get("_error")) for m in ms]
    for m in ms:
        for c in m["claims"]:
            assert c.get("id") and c.get("says") and c.get("probe"), c


if __name__ == "__main__":
    for name, fn in [(k, v) for k, v in list(globals().items()) if k.startswith("test_")]:
        fn(); print(f"  ok {name}")
    print("claims: all green")
