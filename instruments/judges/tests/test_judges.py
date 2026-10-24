"""judges — two fake doors (an Anthropic-shaped one and an OpenAI-shaped one, http.server on loopback), a temp git repo,
every outcome of the pair, the ledger, holds per round, the in-flight marker, head lag, the walls, the CLI.
Run: python tests/test_judges.py (or pytest)."""
import importlib.util, json, os, subprocess, sys, tempfile, threading, time
from http.server import BaseHTTPRequestHandler, HTTPServer

HERE = os.path.dirname(os.path.abspath(__file__)); ROOT = os.path.dirname(HERE)
spec = importlib.util.spec_from_file_location("judges", os.path.join(ROOT, "judges.py"))
J = importlib.util.module_from_spec(spec); spec.loader.exec_module(J)

HITS, REPLY = [], {"first": "MERGE", "second": "MERGE", "first_status": 200, "second_status": 200}
KEY1, KEY2 = "first-key-never-print", "second-key-never-print"


class Door(BaseHTTPRequestHandler):
    def log_message(self, *a): pass
    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        which = "first" if self.path.endswith("/v1/messages") else "second"
        HITS.append({"which": which, "path": self.path, "headers": {k.lower(): v for k, v in self.headers.items()}, "body": body})
        if REPLY[f"{which}_status"] != 200:
            self.send_response(REPLY[f"{which}_status"]); self.end_headers(); self.wfile.write(b'{"error": "echo of the request body"}'); return
        v = REPLY[which]
        ans = "garbage, no verdict" if v == "garbage" else "Verdict follows:\n" + json.dumps(
            {"analysis": "read it", "verdict": v, "review": ("Held. The rewrite drops the why" if v == "HOLD" else "Safe. Would change: the title") + f" ({which}).", "trace": "commits, checks, diff", "nit": "n"})
        if which == "first":
            out = {"content": [{"type": "text", "text": ans}], "usage": {"input_tokens": 100, "output_tokens": 20}}
        else:
            out = {"choices": [{"message": {"content": ans}}], "usage": {"prompt_tokens": 100, "completion_tokens": 20}}
        data = json.dumps(out).encode(); self.send_response(200); self.send_header("Content-Type", "application/json"); self.send_header("Content-Length", str(len(data))); self.end_headers(); self.wfile.write(data)


srv = HTTPServer(("127.0.0.1", 0), Door); threading.Thread(target=srv.serve_forever, daemon=True).start()
PORT = srv.server_address[1]
os.environ.update({"JUDGES_FIRST_KIND": "anthropic", "JUDGES_FIRST_BASE_URL": f"http://127.0.0.1:{PORT}", "JUDGES_FIRST_MODEL": "claude-test", "JUDGES_FIRST_API_KEY": KEY1,
                   "JUDGES_SECOND_KIND": "openai", "JUDGES_SECOND_BASE_URL": f"http://127.0.0.1:{PORT}/v1", "JUDGES_SECOND_MODEL": "gpt-test", "JUDGES_SECOND_API_KEY": KEY2})


def repo():
    tmp = tempfile.mkdtemp(); r = os.path.join(tmp, "repo"); os.makedirs(r)
    g = ["git", "-C", r, "-c", "user.name=t", "-c", "user.email=t@t"]
    subprocess.run(["git", "-C", r, "init", "-q", "-b", "main"], check=True)
    open(os.path.join(r, "a.py"), "w").write("x = 1\n"); subprocess.run(g + ["add", "-A"], check=True); subprocess.run(g + ["commit", "-q", "-m", "base"], check=True)
    subprocess.run(["git", "-C", r, "checkout", "-q", "-b", "feature"], check=True)
    open(os.path.join(r, "a.py"), "w").write("x = 2\n"); subprocess.run(g + ["commit", "-q", "-am", "feature: x to 2"], check=True)
    return r, os.path.join(tmp, "reviews"), g


def head(r, ref="feature"): return subprocess.run(["git", "-C", r, "rev-parse", ref], capture_output=True, text=True).stdout.strip()


def reset(**kw):
    HITS.clear(); REPLY.update({"first": "MERGE", "second": "MERGE", "first_status": 200, "second_status": 200}); REPLY.update(kw)


def test_pure_parts():
    assert J.lab_of("claude-opus-4-1") == "anthropic" and J.lab_of("gpt-5") == "openai" and J.lab_of("openai/o3") == "openai" and J.lab_of("gemini-2.5-pro") == "google"
    assert J.lab_of("deepseek-r1") == "deepseek" and J.lab_of("meta-llama/llama-4") == "meta" and J.lab_of("qwen3") == "alibaba" and J.lab_of("mystery") == "other"
    assert J.parse_verdict('prose {"verdict": "HOLD", "review": "x"} more')["verdict"] == "HOLD" and J.parse_verdict("no json") is None and J.parse_verdict('{"verdict": "MAYBE"}') is None
    brief = "# b\n\n## Commits\nc\n\n## Code checks\n- x\n\n## Your ledger — the last verdicts\n- 2026-10-01 pr-9 → HOLD\n\n## The rules\nr\n\n## The diff\nd\n"
    cut = J.strip_ledger(brief); assert "Your ledger" not in cut and "pr-9" not in cut and cut.count("## ") == brief.count("## ") - 1, cut
    ok = lambda v, lab: {"verdict": v, "lab": lab, "error": None}
    assert J.combine(ok("MERGE", "a"), None) == ("MERGE", "single", None)
    assert J.combine(ok("MERGE", "a"), ok("MERGE", "b")) == ("MERGE", "full", None)
    assert J.combine(ok("HOLD", "a"), ok("HOLD", "b")) == ("HOLD", "full", None)
    assert J.combine(ok("MERGE", "anthropic"), ok("HOLD", "openai")) == ("HOLD", "split", "judges split: anthropic MERGE / openai HOLD")
    assert J.combine(ok("MERGE", "a"), {"verdict": None, "lab": "b", "error": "HTTP 500"})[1:] == ("unavailable", "second judge unavailable — HTTP 500")
    assert J.combine({"verdict": None, "lab": "a", "error": "down"}, ok("HOLD", "b"))[0:2] == ("HOLD", "unavailable")
    assert J.count_holds([("HOLD", "a"), ("HOLD", "a")]) == 1 and J.count_holds([("HOLD", "a"), ("HOLD", "b")]) == 2
    assert J.count_holds([("HOLD", "a"), ("MERGE", "b"), ("HOLD", "c")]) == 1 and J.count_holds([("MERGE", "a")]) == 0
    assert J.first_line("Held. The rewrite drops the why. More.", "HOLD") == "The rewrite drops the why" and J.first_line("Safe. Would change: the title. Also x.", "MERGE") == "the title"


def test_both_merge_writes_file_and_ledger():
    r, out, g = repo(); reset()
    rules = os.path.join(ROOT, "examples", "REVIEW-RULES.md")
    assert J.review("feature", r, "main", [rules], out) == "MERGE"
    assert [h["which"] for h in HITS] == ["first", "second"]
    assert HITS[0]["headers"]["x-api-key"] == KEY1 and HITS[1]["headers"]["authorization"] == f"Bearer {KEY2}"
    first_user = HITS[0]["body"]["messages"][0]["content"]; second_user = HITS[1]["body"]["messages"][1]["content"]
    assert "## Your ledger" in first_user and "no ledger yet" in first_user and "## Your ledger" not in second_user, "the second judge never sees the first's ledger"
    assert "state is observed" in first_user and "+x = 2" in first_user and f"head {head(r)}" in first_user and second_user.count("## ") == first_user.count("## ") - 1
    assert HITS[1]["body"]["response_format"] == {"type": "json_object"} and "verdict" in HITS[1]["body"]["messages"][0]["content"]
    files = [f for f in os.listdir(out) if f.endswith("-feature.md")]; assert len(files) == 1
    text = open(os.path.join(out, files[0])).read()
    assert text.startswith("# Cold read — `feature` → `main` · MERGE") and f"- head {head(r)}" in text and "agreement: full" in text
    assert "claude-test (anthropic) → MERGE" in text and "gpt-test (openai) → MERGE" in text and "## Second judge" in text
    rows = J.ledger_rows(out); assert len(rows) == 1 and rows[0]["head"] == head(r) and rows[0]["final"] == "MERGE" and rows[0]["verdict"] == "MERGE" and rows[0]["note"] == "the title (first)"
    assert os.path.exists(os.path.join(out, "ledger", "LEDGER.md")) and KEY1 not in text and KEY2 not in open(os.path.join(out, "ledger", "ledger.jsonl")).read()
    rv = J.last_read(out, "feature"); assert rv["verdict"] == "MERGE" and rv["head"] == head(r)
    assert not [f for f in os.listdir(out) if f.startswith(".inflight")], "the marker is cleared after the read"


def test_split_is_hold_and_the_wall_holds():
    r, out, g = repo(); reset(second="HOLD")
    assert J.review("feature", r, "main", [], out) == "HOLD"
    text = open(J.last_read(out, "feature")["file"]).read()
    assert "judges split: anthropic MERGE / openai HOLD" in text and text.startswith("# Cold read — `feature` → `main` · HOLD")
    row = J.ledger_rows(out)[-1]; assert row["verdict"] == "MERGE" and row["final"] == "HOLD" and row["agreement"] == "split"
    tail = J.ledger_tail(out); assert "→ MERGE" in tail and "HOLD" not in tail and "split" not in tail, "the first judge's ledger shows only its own verdict"
    # the second judge down: the first's verdict stands, the file says so
    reset(second_status=500); subprocess.run(g + ["commit", "-q", "--allow-empty", "-m", "nudge"], check=True)
    assert J.review("feature", r, "main", [], out) == "MERGE"
    text = open(J.last_read(out, "feature")["file"]).read()
    assert "agreement: unavailable — second judge unavailable" in text and "HTTP 500" in text and "echo of the request" not in text
    reset(second="garbage"); subprocess.run(g + ["commit", "-q", "--allow-empty", "-m", "nudge2"], check=True)
    assert J.review("feature", r, "main", [], out) == "MERGE" and "no verdict in the answer" in open(J.last_read(out, "feature")["file"]).read()
    # the first judge down and the second answers: its verdict stands; both down: could not read
    reset(first_status=500, second="HOLD"); subprocess.run(g + ["commit", "-q", "--allow-empty", "-m", "nudge3"], check=True)
    assert J.review("feature", r, "main", [], out) == "HOLD"
    reset(first_status=500, second_status=503); subprocess.run(g + ["commit", "-q", "--allow-empty", "-m", "nudge4"], check=True)
    try: J.review("feature", r, "main", [], out); raise AssertionError("read with no judge")
    except RuntimeError as e: assert "neither judge answered" in str(e) and KEY1 not in str(e)
    # one judge only
    os.environ["JUDGES_SECOND_MODEL"] = ""; reset(); subprocess.run(g + ["commit", "-q", "--allow-empty", "-m", "nudge5"], check=True)
    assert J.review("feature", r, "main", [], out) == "MERGE" and [h["which"] for h in HITS] == ["first"] and J.ledger_rows(out)[-1]["agreement"] == "single"
    os.environ["JUDGES_SECOND_MODEL"] = "gpt-test"


def test_holds_per_round_and_the_brake():
    r, out, g = repo(); reset(first="HOLD", second="HOLD")
    assert J.review("feature", r, "main", [], out) == "HOLD"
    # the same commit read twice (the marker was cleared between): still one round
    assert J.review("feature", r, "main", [], out) == "HOLD" and J.hold_rounds(out, "feature") == 1
    subprocess.run(g + ["commit", "-q", "--allow-empty", "-m", "fix attempt"], check=True)
    assert J.review("feature", r, "main", [], out) == "HOLD" and J.hold_rounds(out, "feature") == 2
    subprocess.run(g + ["commit", "-q", "--allow-empty", "-m", "third"], check=True); n = len(HITS)
    try: J.review("feature", r, "main", [], out); raise AssertionError("a third round ran")
    except J.NotRead as e: assert "two rounds already held" in str(e) and len(HITS) == n, "refused before any spend"
    reset(); assert J.review("feature", r, "main", [], out, again=True) == "MERGE" and J.hold_rounds(out, "feature") == 0


def test_inflight_and_head_lag():
    r, out, g = repo(); reset()
    mark = J.inflight(out, "feature", head(r)); os.makedirs(out); open(mark, "w").write(json.dumps({"pid": 4242, "at": "2026-10-24T02:38:39Z"}))
    try: J.review("feature", r, "main", [], out); raise AssertionError("read under a fresh marker")
    except J.NotRead as e: assert "in flight (started 2026-10-24T02:38:39Z by pid 4242)" in str(e) and HITS == []
    os.utime(mark, (time.time() - 21 * 60, time.time() - 21 * 60))
    assert J.review("feature", r, "main", [], out) == "MERGE" and not os.path.exists(mark), "a stale marker is replaced, then cleared"
    def dying(spec, brief): raise RuntimeError("the door hung")
    try: J.review("feature", r, "main", [], out, read_fn=dying)
    except RuntimeError: pass
    assert not os.path.exists(mark), "cleared when the read dies too"
    try: J.review("feature", r, "main", [], out, expect_head="0" * 40); raise AssertionError("read the wrong commit")
    except J.NotRead as e: assert "not read" in str(e)
    assert J.review("feature", r, "main", [], out, expect_head=head(r)[:12]) == "MERGE"


def test_cli_and_status():
    r, out, g = repo(); reset()
    py = [sys.executable, os.path.join(ROOT, "judges.py")]
    s = subprocess.run(py + ["status", "feature", "--repo", r, "--out", out], capture_output=True, text=True); assert s.returncode == 2 and "no read yet" in s.stdout
    d = subprocess.run(py + ["read", "feature", "--repo", r, "--out", out, "--rules", os.path.join(ROOT, "examples", "REVIEW-RULES.md"), "--base", "main", "--dry-run"], capture_output=True, text=True)
    assert d.returncode == 0 and "second judge: gpt-test (openai, openai), brief without the ledger" in d.stdout and "REVIEW-RULES.md" in d.stdout and HITS == [], d.stdout + d.stderr
    x = subprocess.run(py + ["read", "feature", "--repo", r, "--out", out, "--base", "main"], capture_output=True, text=True)
    assert x.returncode == 0 and x.stdout.startswith("MERGE — feature") and KEY1 not in x.stdout, x.stdout + x.stderr
    s = subprocess.run(py + ["status", "feature", "--repo", r, "--out", out], capture_output=True, text=True); assert s.returncode == 0 and "still sits on that commit" in s.stdout
    subprocess.run(g + ["commit", "-q", "--allow-empty", "-m", "moved"], check=True)
    s = subprocess.run(py + ["status", "feature", "--repo", r, "--out", out], capture_output=True, text=True); assert s.returncode == 2 and "moved to" in s.stdout
    reset(first="HOLD"); x = subprocess.run(py + ["read", "feature", "--repo", r, "--out", out, "--base", "main"], capture_output=True, text=True)
    assert x.returncode == 2 and x.stdout.startswith("HOLD — feature") and "judges split" in x.stdout
    x = subprocess.run(py + ["read", "feature", "--repo", r, "--out", out, "--base", "main", "--head", "0" * 40], capture_output=True, text=True); assert x.returncode == 4 and "NOT READ" in x.stdout
    l = subprocess.run(py + ["ledger", "--out", out], capture_output=True, text=True); assert l.stdout.count("\n") == 2 and "→ HOLD (split)" in l.stdout
    env = {k: v for k, v in os.environ.items() if k != "JUDGES_FIRST_MODEL"}
    x = subprocess.run(py + ["read", "feature", "--repo", r, "--out", out], capture_output=True, text=True, env=env); assert x.returncode == 1 and "JUDGES_FIRST_MODEL" in x.stdout


if __name__ == "__main__":
    for name, fn in [(k, v) for k, v in list(globals().items()) if k.startswith("test_")]:
        fn(); print(f"  ok {name}")
    print("judges: all green")
