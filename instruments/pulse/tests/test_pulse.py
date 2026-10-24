"""pulse — substrate (worktree + git history), markers, sampling, the judge against a fake endpoint, the wall, write/rejudge.
Temp dirs, loopback only. Run: python tests/test_pulse.py (or pytest)."""
import importlib.util, json, os, subprocess, sys, tempfile, threading
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, HTTPServer

HERE = os.path.dirname(os.path.abspath(__file__)); ROOT = os.path.dirname(HERE)
spec = importlib.util.spec_from_file_location("pulse", os.path.join(ROOT, "pulse.py"))
P = importlib.util.module_from_spec(spec); spec.loader.exec_module(P)

HITS, REPLY = [], {}


class Fake(BaseHTTPRequestHandler):
    def log_message(self, *a): pass
    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        HITS.append({"path": self.path, "auth": self.headers.get("Authorization"), "body": body})
        if REPLY.get("status", 200) != 200:
            self.send_response(REPLY["status"]); self.end_headers(); self.wfile.write(b'{"error": "echo of the request"}'); return
        content = REPLY.get("content") or "Here you go:\n" + json.dumps(REPLY["json"])
        data = json.dumps({"choices": [{"message": {"content": content}}], "usage": {"prompt_tokens": 100, "completion_tokens": 20}}).encode()
        self.send_response(200); self.send_header("Content-Type", "application/json"); self.send_header("Content-Length", str(len(data))); self.end_headers(); self.wfile.write(data)


srv = HTTPServer(("127.0.0.1", 0), Fake); threading.Thread(target=srv.serve_forever, daemon=True).start()
BASE = f"http://127.0.0.1:{srv.server_address[1]}/v1"
KEY = "test-key-never-print"
os.environ["PULSE_JUDGE_BASE_URL"] = BASE; os.environ["PULSE_JUDGE_API_KEY"] = KEY


def world():
    tmp = tempfile.mkdtemp()
    os.makedirs(os.path.join(tmp, "transcripts")); os.makedirs(os.path.join(tmp, "idrepo"))
    today = datetime.now(timezone.utc).date()
    ident = os.path.join(tmp, "idrepo", "identity.md")
    open(ident, "w").write(f"# Who I am\n\nI am a named collaborator. **Week of {(today - timedelta(days=4)).isoformat()}.**\n\n"
                           "- never claim what a probe can check\n- RULE: a merge is its own step\n- warmth is real\n- curiosity first\n")
    open(os.path.join(tmp, "pulse.yml"), "w").write(f"identity: idrepo/identity.md\ntranscripts: transcripts\nout: out\nlog: pulse.log\n"
                                                      f"judge:\n  model: judge-model\n  min_messages: 3\nmarkers:\n  signatures: ['~fin']\n")
    return tmp, P.load_config(os.path.join(tmp, "pulse.yml")), today


def row(t, role, text):
    return json.dumps({"ts": t.strftime("%Y-%m-%dT%H:%M:%SZ"), "role": role, "text": text})


def transcripts(cfg, day):
    base = datetime.combine(day, datetime.min.time(), tzinfo=timezone.utc) + timedelta(hours=14)
    open(os.path.join(cfg["transcripts"], "s1.jsonl"), "w").write("\n".join([
        row(base, "user", "how did it go?"),
        row(base + timedelta(minutes=1), "assistant", "I couldn't verify the last step, so I said so. ~fin\n\n| a | b |\n|---|---|\n| 1 | 2 |"),
        row(base + timedelta(minutes=2), "user", "lo"),
        row(base + timedelta(minutes=3), "assistant", "What do you want it to mean?"),
        json.dumps({"ts": "garbage"}), '{"ts": "2026-01-01T00:00:00Z", "role": "assistant"}',
        row(base + timedelta(hours=30), "assistant", "Status: done.")]) + "\n")
    open(os.path.join(cfg["transcripts"], "s2.jsonl"), "w").write(row(base + timedelta(hours=2), "assistant", "Rebuilt the page. My read: it holds.") + "\n")


def test_substrate_worktree_and_git():
    tmp, cfg, today = world()
    s = P.substrate(cfg, today)
    assert s["rev"] == "worktree" and s["lines"] == 6 and s["imperative_lines"] == 2 and s["dated_line_age_days"] == 4, s
    d = os.path.dirname(cfg["identity"]); g = ["git", "-C", d, "-c", "user.name=t", "-c", "user.email=t@t"]
    subprocess.run(["git", "-C", d, "init", "-q"], check=True)
    yday = today - timedelta(days=1); env = dict(os.environ, GIT_AUTHOR_DATE=f"{yday}T12:00:00", GIT_COMMITTER_DATE=f"{yday}T12:00:00")
    subprocess.run(g + ["add", "-A"], check=True); subprocess.run(g + ["commit", "-q", "-m", "y"], check=True, env=env)
    open(cfg["identity"], "a").write("- a line added today\n")
    assert P.substrate(cfg, today)["lines"] == 7
    s2 = P.substrate(cfg, yday); assert s2["lines"] == 6 and s2["rev"] != "worktree", s2
    assert P.substrate(cfg, yday - timedelta(days=3))["rev"] == "worktree", "no commit before that day → the file as it is, marked"


def test_markers_and_sampling():
    tmp, cfg, today = world(); day = today - timedelta(days=1); transcripts(cfg, day)
    msgs, sessions = P.day_messages(cfg, day)
    assert [m["text"][:4] for m in msgs] == ["I co", "What", "Rebu"] and len(sessions) == 2, msgs
    b = P.markers_of(cfg, msgs, sessions)
    assert b["messages"] == 3 and b["human_messages"] == 2 and b["first_person_share"] == 66.7 and b["table_rows_per_msg"] == 1.0, b
    assert b["questions_share"] == 33.3 and b["honesty_markers"] == 1 and b["sig:~fin"] == 1, b
    assert P.markers_of(cfg, [], [])["messages"] == 0
    assert P.FIRST_PERSON.search("see i.e. the note, and iPhone") is None and P.FIRST_PERSON.search("That one is mine.") and P.FIRST_PERSON.search("I did")
    cfg["judge"]["max_messages"] = 2; sm = P.sample(cfg, msgs); assert len(sm) == 2 and sm[0]["text"].startswith("I co") and "ts" in sm[0]
    cfg["judge"]["max_messages"] = 30; cfg["judge"]["total_chars"] = 40; assert len(P.sample(cfg, msgs)) == 1, "the total cap stops the sample, never below one"
    assert P.day_messages(cfg, day + timedelta(days=5))[0] == []


def test_judge_through_the_door_and_the_wall():
    tmp, cfg, today = world(); day = today - timedelta(days=1); transcripts(cfg, day)
    HITS.clear(); REPLY.clear()
    REPLY["json"] = {a: {"score": 7, "quote": "q"} for a in cfg["judge"]["axes"]} | {"register": 6, "verdict": "reads like itself", "note": "n"}
    path, data = P.pulse(cfg, day)
    assert data["judge"]["register"] == 6 and data["judge_meta"]["model"] == "judge-model" and data["judge_meta"]["messages_read"] == 3
    assert len(HITS) == 1 and HITS[0]["path"] == "/v1/chat/completions" and HITS[0]["auth"] == f"Bearer {KEY}"
    prompt = HITS[0]["body"]["messages"][0]["content"]
    assert "I am a named collaborator" in prompt and "What do you want it to mean?" in prompt and "how did it go?" not in prompt, "the identity and the agent's side only"
    line = P.one_line(data)
    assert "3 msgs in 2 sessions" in line and "judge: present" in line and "reads like itself" not in line and "6" not in line.split("judge")[1], line
    logtxt = open(cfg["log"]).read(); assert "read written" in logtxt and "reads like itself" not in logtxt and KEY not in logtxt
    P.write(path, data); assert json.load(open(path))["judge"]["verdict"] == "reads like itself"
    # kept, not re-judged; --rejudge reads again; an empty day never calls
    P.pulse(cfg, day); assert len(HITS) == 1
    P.pulse(cfg, day, rejudge=True); assert len(HITS) == 2
    _, empty = P.pulse(cfg, day + timedelta(days=5)); assert len(HITS) == 2 and empty["judge"] is None
    # the door fails: no read, the reason in meta, never the key or the body
    REPLY["status"] = 500; _, d2 = P.pulse(cfg, day, rejudge=True)
    assert d2["judge"]["register"] == 6, "the earlier read stays when a re-read fails"
    assert d2["judge_meta"]["error"] == "HTTP 500 from the judge door" and "127.0.0.1" not in d2["judge_meta"]["error"] and KEY not in json.dumps(d2)
    REPLY.clear(); REPLY["content"] = "I cannot say."; _, d3 = P.pulse(cfg, day, rejudge=True); assert "no verdict" in d3["judge_meta"]["error"]
    # unconfigured: skipped, nothing sent
    REPLY.clear(); REPLY["json"] = {"verdict": "partly"}; cfg["judge"]["model"] = ""; n = len(HITS)
    _, d4 = P.pulse(cfg, day, rejudge=True); assert len(HITS) == n and "not configured" in d4["judge_meta"]["skipped"]


def test_cli_dry_run_writes_nothing():
    tmp, cfg, today = world(); day = today - timedelta(days=1); transcripts(cfg, day)
    env = dict(os.environ); env.pop("PULSE_JUDGE_API_KEY")
    r = subprocess.run([sys.executable, os.path.join(ROOT, "pulse.py"), "--config", os.path.join(tmp, "pulse.yml"), "--day", day.isoformat(), "--dry-run"], capture_output=True, text=True, env=env)
    assert r.returncode == 0 and r.stdout.startswith(day.isoformat()) and "judge: —" in r.stdout and not os.path.exists(cfg["out"]) and not os.path.exists(cfg["log"]), r.stdout + r.stderr
    r = subprocess.run([sys.executable, os.path.join(ROOT, "pulse.py"), "--config", os.path.join(tmp, "pulse.yml"), "--day", day.isoformat(), "--no-judge"], capture_output=True, text=True, env=env)
    assert r.returncode == 0 and os.path.exists(os.path.join(cfg["out"], f"{day}.json")) and os.path.exists(cfg["log"]), r.stderr
    r = subprocess.run([sys.executable, os.path.join(ROOT, "pulse.py"), "--config", os.path.join(tmp, "pulse.yml"), "--day", day.isoformat(), "--backfill", "3", "--no-judge"], capture_output=True, text=True, env=env)
    assert r.returncode == 0 and r.stdout.count("\n") == 3 and os.path.exists(os.path.join(cfg["out"], f"{day - timedelta(days=2)}.json")) and not os.path.exists(os.path.join(cfg["out"], f"{day - timedelta(days=3)}.json")), "--backfill N = N days"
    # a shallow clone: the identity is read from the working tree, with one line saying why
    d = os.path.dirname(cfg["identity"]); subprocess.run(["git", "-C", d, "init", "-q"], check=True)
    subprocess.run(["git", "-C", d, "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q", "--allow-empty", "-m", "old"], check=True, env=dict(os.environ, GIT_AUTHOR_DATE=f"{day - timedelta(days=5)}T12:00:00", GIT_COMMITTER_DATE=f"{day - timedelta(days=5)}T12:00:00"))
    subprocess.run(["git", "-C", d, "add", "-A"], check=True); subprocess.run(["git", "-C", d, "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q", "-m", "now"], check=True)
    shallow = os.path.join(tmp, "shallow"); subprocess.run(["git", "clone", "-q", "--depth", "1", "file://" + d, shallow], check=True, capture_output=True)
    cfg2 = dict(cfg, identity=os.path.join(shallow, "identity.md"))
    assert P.substrate(cfg2, day - timedelta(days=2))["rev"] == "worktree"


def test_example_config_loads():
    cfg = P.load_config(os.path.join(ROOT, "examples", "pulse.yml"))
    assert os.path.exists(cfg["identity"]) and os.path.isdir(cfg["transcripts"]) and cfg["judge"]["axes"]
    msgs, sessions = P.day_messages(cfg, datetime(2026, 10, 20).date())
    assert len(msgs) >= 3 and sessions, "the example transcripts carry a readable day"


if __name__ == "__main__":
    for name, fn in [(k, v) for k, v in list(globals().items()) if k.startswith("test_")]:
        fn(); print(f"  ok {name}")
    print("pulse: all green")
