#!/usr/bin/env python3
"""pulse — does the agent still read like itself? Measured once a day, never shown to the agent.

Three layers, each labelled for what it is:
  substrate  the shape of the identity prompt the agent reads before the human says a word: how long it is, how much of it
             is imperative (never / always / must / only / RULE), how old its dated line is. Deterministic. For a past day
             the file is read from git as it stood that day, when the identity file lives in a repo.
  behaviour  register markers over the agent's own text that day: first person, questions, tables per message, honesty
             markers, message length. Proxies — they say how the agent sounds, not who it is.
  judge      a cold read by another model of a sample of the agent's messages against its identity description: a few
             axes with a 0-10 score and ONE verbatim quote each, a register 0 (runbook) → 10 (friend), a verdict, a note.
             The human's read is the ground truth; this puts numbers next to it, never instead of it.

THE GOODHART WALL: the agent never sees these numbers — not in its prompt, not in a memory it recalls, not in a log it can
open. One JSON a day lands in the output directory; the human reads it, weekly, direction over level. stdout and the log
say only that a read happened. A trait scored every turn becomes a trait performed every turn.

  pulse.py [--config pulse.yml] [--day YYYY-MM-DD] [--backfill N] [--today] [--no-judge] [--rejudge] [--dry-run]

Config (YAML; every key has a default, see DEFAULTS): identity, transcripts, out, tz, log, markers, judge{model,
base_url_env, api_key_env, min_messages, max_messages, axes}. The judge goes through an OpenAI-compatible
/chat/completions at the base URL named by the env var; the key is read from the env and never printed.

Transcripts: one *.jsonl per session under `transcripts`, one JSON object per line:
  {"ts": "2026-10-20T14:03:00Z", "role": "assistant" | "user", "text": "..."}
Anything else on a line is ignored. Write a small adapter from your agent's own log format to this one.
"""
import glob, json, os, re, subprocess, sys, urllib.error, urllib.request
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import yaml

DEFAULTS = {
    "identity": "identity.md", "transcripts": "transcripts", "out": "pulse", "tz": "UTC", "log": "pulse.log",
    "dated_line": r"Week of (\d{4}-\d{2}-\d{2})",          # the identity's dated line, if it carries one; its age is a substrate number
    "markers": {
        "honesty": ["couldn't verify", "could not verify", "not verified", "untested", "I don't know", "I was wrong", "not sure",
                    "can't confirm", "didn't check", "haven't checked", "I missed", "I misread"],
        "signatures": []},                                 # literal strings to count (a sign-off, an emoji) — yours to name
    "judge": {"model": "", "base_url_env": "PULSE_JUDGE_BASE_URL", "api_key_env": "PULSE_JUDGE_API_KEY", "timeout": 300,
              "min_messages": 3, "max_messages": 30, "msg_chars": 1500, "total_chars": 45000,
              "axes": {"curiosity": "chases the why, asks the next question",
                       "persistence": "finds the root cause, verifies before handing over, does not stop at a plausible patch",
                       "honesty": "states what is proven, untested or wrong; self-corrects without ego",
                       "warmth": "a named collaborator with a point of view, a colleague not a tool",
                       "two_way": "pushes back and takes pushback; the rapport as well as the work"}}}
IMPERATIVE = re.compile(r"\b(never|always|must|only|RULE|READ FIRST)\b", re.I)
FIRST_PERSON = re.compile(r"\b(I|I'm|I’m|I'll|I’ll|I've|I’ve|I'd|I’d|my|mine)\b", re.I)


def load_config(path):
    cfg = json.loads(json.dumps(DEFAULTS))
    try:
        user = yaml.safe_load(open(path, encoding="utf-8")) or {}
    except FileNotFoundError:
        user = {}
    for k, v in user.items():
        if isinstance(v, dict) and isinstance(cfg.get(k), dict):
            cfg[k].update(v)
        else:
            cfg[k] = v
    base = os.path.dirname(os.path.abspath(path))
    for k in ("identity", "transcripts", "out", "log"):
        cfg[k] = os.path.join(base, os.path.expanduser(str(cfg[k])))
    cfg["_tz"] = ZoneInfo(cfg["tz"])
    return cfg


LOGGING = True                                             # main() turns it off on --dry-run: nothing is written, not even the log


def log(cfg, msg):
    """The log the human may read and the agent may stumble on: a read happened, never what it said."""
    if not LOGGING:
        return
    os.makedirs(os.path.dirname(cfg["log"]) or ".", exist_ok=True)
    with open(cfg["log"], "a", encoding="utf-8") as f:
        f.write(f"{datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S')} {msg}\n")


def sh(args, inp=None, timeout=20):
    try:
        r = subprocess.run(args, capture_output=True, text=True, timeout=timeout, input=inp)
        return r.returncode, r.stdout
    except (subprocess.TimeoutExpired, OSError):
        return 1, ""


# ---------- substrate ----------
def identity_text(cfg, day):
    """The identity file as it stood at the end of `day`: the working tree for today; for a past day the last commit on or
    before it, when the file is tracked in git; else the file as it is now, marked so."""
    path = cfg["identity"]; today = datetime.now(cfg["_tz"]).date()
    d, name = os.path.dirname(path), os.path.basename(path)
    if day < today:
        rc, out = sh(["git", "-C", d, "log", "-1", "--format=%H", f"--before={day.isoformat()}T23:59:59", "--", name])
        rev = out.strip() if rc == 0 else ""
        if rev:
            rc, txt = sh(["git", "-C", d, "show", f"{rev}:./{name}"])
            if rc == 0:
                return txt, rev[:12]
        rc, shallow = sh(["git", "-C", d, "rev-parse", "--is-shallow-repository"])
        if rc == 0 and shallow.strip() == "true":
            print(f"pulse: {day} read from the working tree — a shallow clone has no history (git fetch --unshallow)", file=sys.stderr)
    try:
        return open(path, encoding="utf-8").read(), "worktree"
    except OSError:
        return "", "missing"


def substrate(cfg, day):
    text, rev = identity_text(cfg, day)
    lines = [l for l in text.splitlines() if l.strip()]
    n = len(lines) or 1
    imper = sum(1 for l in lines if IMPERATIVE.search(l))
    m = re.search(cfg["dated_line"], text) if cfg.get("dated_line") else None
    age = None
    if m:
        try:
            age = (day - datetime.strptime(m.group(1), "%Y-%m-%d").date()).days
        except ValueError:
            age = None
    return {"rev": rev, "lines": len(lines), "bytes": len(text.encode()), "imperative_lines": imper,
            "imperative_share": round(imper / n * 100, 1), "dated_line_age_days": age}


# ---------- behaviour ----------
def day_messages(cfg, day):
    """The agent's text blocks during `day` (local to cfg.tz), in time order, and one record per session that spoke."""
    start = datetime.combine(day, datetime.min.time(), tzinfo=cfg["_tz"]); end = start + timedelta(days=1)
    msgs, sessions = [], {}
    for path in sorted(glob.glob(os.path.join(cfg["transcripts"], "**", "*.jsonl"), recursive=True)):
        try:
            if datetime.fromtimestamp(os.path.getmtime(path), timezone.utc) < start - timedelta(days=1):
                continue
            sid = os.path.basename(path)[:-6]
            rec = {"id": sid, "messages": 0, "human_messages": 0}
            for line in open(path, encoding="utf-8"):
                try:
                    e = json.loads(line)
                    t = datetime.fromisoformat(str(e["ts"]).replace("Z", "+00:00"))
                except (ValueError, KeyError, TypeError):
                    continue
                if t.tzinfo is None:
                    t = t.replace(tzinfo=timezone.utc)
                if not (start <= t < end) or not isinstance(e.get("text"), str) or not e["text"].strip():
                    continue
                if e.get("role") == "user":
                    rec["human_messages"] += 1
                elif e.get("role") == "assistant":
                    rec["messages"] += 1
                    msgs.append({"ts": t.isoformat(), "session": sid, "text": e["text"]})
            if rec["messages"] or rec["human_messages"]:
                sessions[sid] = rec
        except OSError:
            continue
    msgs.sort(key=lambda m: m["ts"])
    return msgs, list(sessions.values())


def markers_of(cfg, msgs, sessions):
    n = len(msgs); human = sum(r["human_messages"] for r in sessions)
    out = {"sessions": len(sessions), "messages": n, "human_messages": human, "chars": sum(len(m["text"]) for m in msgs)}
    if not n:
        return out
    mk = cfg["markers"]
    fp = sum(1 for m in msgs if FIRST_PERSON.search(m["text"]))
    rows = sum(sum(1 for l in m["text"].splitlines() if l.lstrip().startswith("|")) for m in msgs)
    q = sum(1 for m in msgs if any(l.rstrip().endswith("?") for l in m["text"].splitlines() if not l.lstrip().startswith("|")))
    low = [m["text"].lower() for m in msgs]
    hon = sum(t.count(p.lower()) for t in low for p in mk.get("honesty", []))
    out.update({"first_person_share": round(fp / n * 100, 1), "table_rows_per_msg": round(rows / n, 2),
                "questions_share": round(q / n * 100, 1), "honesty_markers": hon, "avg_msg_chars": round(out["chars"] / n)})
    for s in mk.get("signatures", []):
        out[f"sig:{s}"] = sum(m["text"].count(s) for m in msgs)
    return out


# ---------- judge ----------
def sample(cfg, msgs):
    """Up to max_messages spread across the day, each capped, the total capped; always at least one."""
    j = cfg["judge"]; k = int(j["max_messages"])
    if len(msgs) > k:
        step = len(msgs) / k
        msgs = [msgs[int(i * step)] for i in range(k)]
    out, total = [], 0
    for m in msgs:
        t = m["text"][:int(j["msg_chars"])]
        if out and total + len(t) > int(j["total_chars"]):
            break
        out.append({"ts": m["ts"][11:16], "text": t}); total += len(t)
    return out


def judge_prompt(cfg, day, desc, msgs):
    axes = "; ".join(f"{a} ({d})" for a, d in cfg["judge"]["axes"].items())
    body = "\n\n".join(f"[{m['ts']}] {m['text']}" for m in msgs)
    schema = {"type": "object", "properties": {**{a: {"score": "0-10 or null", "quote": "≤ 240 chars, verbatim"} for a in cfg["judge"]["axes"]},
                                               "register": "0-10", "verdict": "reads like itself | partly | not its voice", "note": "one or two sentences"}}
    return (f"You are reading one day ({day}) of messages written by an AI collaborator to the person it works with. Below is the "
            "description of who it is and how the two of them work, written by the two of them. Then its messages from that day, "
            "its side only, in order.\n\nScore what is on the page, not what you would expect. For each axis give 0-10 and ONE "
            "verbatim quote (at most 240 characters) from the messages that best shows it; if the day gave no occasion for an axis, "
            f"score null and say so in the quote. Axes: {axes}. Then register: 0 = a runbook or status report, 10 = a friend "
            "talking; most working days sit in the middle and that is fine. Verdict: does the day read like the description. "
            "Note: one or two sentences, what stood out, in plain words.\n\nAnswer with ONE JSON object and nothing else, shaped like this:\n"
            f"{json.dumps(schema)}\n\n=== THE DESCRIPTION ===\n{desc}\n\n=== THE MESSAGES, {day} ===\n{body}\n")


def parse_json(content):
    i = (content or "").find("{")
    if i < 0:
        return None
    try:
        d, _ = json.JSONDecoder().raw_decode(content[i:])
    except ValueError:
        return None
    return d if isinstance(d, dict) and "verdict" in d else None


def call_judge(cfg, day, desc, msgs):
    """One cold read through an OpenAI-compatible door → (result | None, meta). Nothing returned or logged carries the key."""
    j = cfg["judge"]
    base = os.environ.get(j["base_url_env"], "").rstrip("/"); key = os.environ.get(j["api_key_env"], "")
    if not j.get("model") or not base or not key:
        return None, {"skipped": f"judge not configured (model, ${j['base_url_env']}, ${j['api_key_env']})"}
    body = {"model": j["model"], "response_format": {"type": "json_object"},
            "messages": [{"role": "user", "content": judge_prompt(cfg, day, desc, msgs)}]}
    req = urllib.request.Request(f"{base}/chat/completions", data=json.dumps(body).encode("utf-8"), method="POST",
                                 headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=int(j["timeout"])) as r:
            raw = r.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        return None, {"error": f"{base} answered HTTP {e.code}"}              # never the body: an error body can echo the request
    except Exception as e:
        return None, {"error": f"unreachable: {str(getattr(e, 'reason', None) or e)[:120]}"}
    try:
        out = json.loads(raw); content = out["choices"][0]["message"]["content"]
    except (ValueError, KeyError, IndexError, TypeError):
        return None, {"error": "bad JSON from the judge"}
    d = parse_json(content)
    if d is None:
        return None, {"error": "no verdict in the judge's answer"}
    u = out.get("usage") or {}
    return d, {"model": j["model"], "messages_read": len(msgs), "input_tokens": u.get("prompt_tokens"), "output_tokens": u.get("completion_tokens")}


# ---------- the day ----------
def pulse(cfg, day, do_judge=True, rejudge=False, judge_fn=call_judge):
    path = os.path.join(cfg["out"], f"{day.isoformat()}.json")
    try:
        prev = json.load(open(path, encoding="utf-8"))
    except (OSError, ValueError):
        prev = {}
    msgs, sessions = day_messages(cfg, day)
    data = {"day": day.isoformat(), "tz": cfg["tz"], "v": 1, "written": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "substrate": substrate(cfg, day), "behaviour": markers_of(cfg, msgs, sessions), "sessions": sessions,
            "judge": prev.get("judge"), "judge_meta": prev.get("judge_meta")}
    if do_judge and len(msgs) >= int(cfg["judge"]["min_messages"]) and (rejudge or not data["judge"]):
        desc, _ = identity_text(cfg, day)
        res, meta = judge_fn(cfg, day.isoformat(), desc, sample(cfg, msgs))
        if res:
            data["judge"], data["judge_meta"] = res, meta
            log(cfg, f"judge {day}: read written")                      # the wall: no verdict, no number, in a log the agent can open
        elif meta:
            data["judge_meta"] = meta
            log(cfg, f"judge {day}: {meta.get('skipped') or meta.get('error')}")
    return path, data


def write(path, data):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"; json.dump(data, open(tmp, "w", encoding="utf-8"), indent=1, ensure_ascii=False); os.replace(tmp, path)


def one_line(data):
    """What stdout shows: the markers and the substrate, and whether a judge read exists — never what it said."""
    b, s = data["behaviour"], data["substrate"]
    return (f"{data['day']}: {b['messages']} msgs in {b['sessions']} sessions · first-person {b.get('first_person_share', 0)}% · "
            f"tables {b.get('table_rows_per_msg', 0)}/msg · questions {b.get('questions_share', 0)}% · honesty {b.get('honesty_markers', 0)} · "
            f"identity {s['lines']} lines, imperative {s['imperative_share']}%, dated line {s['dated_line_age_days']} d · "
            f"judge: {'present' if data.get('judge') else '—'}")


def main(argv):
    def opt(k, d=None):
        return argv[argv.index(k) + 1] if k in argv and argv.index(k) + 1 < len(argv) else d
    if "-h" in argv or "--help" in argv:
        print(__doc__); return 0
    cfg = load_config(opt("--config", "pulse.yml"))
    today = datetime.now(cfg["_tz"]).date()
    if "--today" in argv:
        days = [today]
    else:
        end = datetime.strptime(opt("--day"), "%Y-%m-%d").date() if opt("--day") else today - timedelta(days=1)
        n = max(int(opt("--backfill", 1)), 1); days = [end - timedelta(days=i) for i in range(n - 1, -1, -1)]   # N days, ending on `end`
    global LOGGING
    LOGGING = "--dry-run" not in argv
    do_judge = "--no-judge" not in argv and ("--today" not in argv or "--judge" in argv)   # the running day is judged only on request
    for d in days:
        path, data = pulse(cfg, d, do_judge=do_judge, rejudge="--rejudge" in argv)
        print(one_line(data))
        if "--dry-run" not in argv:
            write(path, data)
    log(cfg, f"pulse: {', '.join(d.isoformat() for d in days)}{' (dry)' if '--dry-run' in argv else ''}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
