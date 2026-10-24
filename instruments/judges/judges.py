#!/usr/bin/env python3
"""judges — the cross-lab cold read. A branch, its diff and the project's rules go to two independent judges from two
labs. MERGE only when both say MERGE; a split is a HOLD with the split named; a judge that cannot answer leaves the
read on the other one, and the file says so.

  judges.py read   <branch> [--repo DIR] [--base main] [--rules FILE ...] [--out DIR] [--head SHA] [--dry-run] [--again]
  judges.py status <branch> [--repo DIR] [--out DIR]    the last read of this branch, and whether the branch still sits on the commit it read
  judges.py ledger [n] [--out DIR]                       the last n rows of the ledger

Env (read, never printed):
  JUDGES_FIRST_KIND=anthropic  JUDGES_FIRST_BASE_URL=https://api.anthropic.com   JUDGES_FIRST_MODEL=…  JUDGES_FIRST_API_KEY=…
  JUDGES_SECOND_KIND=openai    JUDGES_SECOND_BASE_URL=https://api.openai.com/v1  JUDGES_SECOND_MODEL=… JUDGES_SECOND_API_KEY=…
  An empty JUDGES_SECOND_MODEL = one judge only (agreement "single"). kind = anthropic (the Messages API) or openai
  (any /chat/completions-compatible door, which is how most other labs and routers are reached).

What a read does, in order: fetch nothing, trust the local repo (`git fetch` is the caller's job); refuse unread if
--head names a commit the branch is not on (exit 4); refuse if another read of this commit is in flight (a marker file
under 20 minutes old, exit 4); refuse after two rounds held since the last MERGE unless --again (exit 4); run the code
checks (files touched, conflict markers, head sha, merge base); build the brief; the first judge reads it with its own
ledger of past verdicts, the second reads it with the ledger cut out; combine; write <out>/<date>-<label>.md and one
ledger row; exit 0 MERGE, 2 HOLD, 1 could not read. Nothing here merges anything: `status` tells the merger whether the
read is of the commit it is about to merge.

The walls, in the code and not in a prompt: the second judge sees nothing of the first — not its verdict, not its
ledger; the first judge's ledger renders only its OWN verdicts, never the combined one, or the second's vote would reach
it on the next read. A round is a distinct commit: two HOLDs on one sha are one hold. Two holds and the next step is a
human's, never a third round.
"""
import json, os, re, subprocess, sys, time, urllib.error, urllib.request
from datetime import datetime, timezone

SYSTEM = """You are the reviewer on a review lane: a second mind, not the author. You have no memory of writing what you
are about to read and no stake in it landing, and that is the point: you read it cold, as a careful colleague would,
against the rules you are given and nothing else you assume. If a ledger of your own past verdicts is in the brief, use
it to notice when a change repeats a shape you held or passed before. Agreeing with the author is the normal outcome of
an honest read; you are not here to find things, you are here to answer one question: is this safe to merge as-is?
MERGE if the change does what its commit message says, breaks none of the rules, keeps meaning where it rewrites, and
leaves the project truer. HOLD only for something that makes it unsafe to land, and name that thing. Improvements you
would make to a change that is already safe go under "would change", never into the verdict. In `trace`, say briefly
what you read, in order — the trace is how the human knows a read happened without reading the diff themselves.
Answer with ONE JSON object and nothing else, matching this schema exactly:"""
SCHEMA = {"type": "object", "properties": {"analysis": {"type": "string", "maxLength": 3000}, "verdict": {"type": "string", "enum": ["MERGE", "HOLD"]},
                                           "review": {"type": "string", "maxLength": 3000}, "nit": {"type": "string", "maxLength": 400},
                                           "trace": {"type": "string", "maxLength": 800}},
          "required": ["analysis", "verdict", "review", "trace"], "additionalProperties": False}
INSTRUCTION = "\n\nReview it and answer in the schema."
LABS = (("claude", "anthropic"), ("anthropic", "anthropic"), ("opus", "anthropic"), ("sonnet", "anthropic"), ("haiku", "anthropic"),
        ("gpt", "openai"), ("openai", "openai"), ("gemini", "google"), ("google", "google"), ("deepseek", "deepseek"),
        ("mistral", "mistral"), ("mixtral", "mistral"), ("llama", "meta"), ("meta", "meta"), ("qwen", "alibaba"), ("grok", "xai"))
INFLIGHT_MIN = 20              # a marker older than this is a read that died: stale, replaced
DIFF_CHARS = 60000
TIMEOUT = int(os.environ.get("JUDGES_TIMEOUT", "300"))


class NotRead(Exception): pass


def sh(args, cwd=None, check=True):
    r = subprocess.run(args, cwd=cwd, capture_output=True, text=True)
    if check and r.returncode:
        raise RuntimeError(f"{' '.join(args[:3])}… failed: {(r.stderr or r.stdout).strip()[:300]}")
    return r.stdout


def lab_of(model):
    """The lab behind a model name (the ledger records judges per lab): a router alias like openai/o3 counts by its prefix."""
    m = (model or "").lower()
    for k, lab in LABS:
        if k in m:
            return lab
    return "openai" if re.match(r"o\d", m.rsplit("/", 1)[-1]) else "other"


def judge_spec(which):
    """The first or second judge from the env → {kind, base, model, key} (model "" = this judge is off)."""
    p = f"JUDGES_{which.upper()}_"
    kind = (os.environ.get(p + "KIND") or ("anthropic" if which == "first" else "openai")).lower()
    base = (os.environ.get(p + "BASE_URL") or ("https://api.anthropic.com" if kind == "anthropic" else "https://api.openai.com/v1")).rstrip("/")
    model = (os.environ.get(p + "MODEL") or "").strip()
    return {"kind": kind, "base": base, "model": "" if model.lower() in ("off", "none") else model, "key": os.environ.get(p + "API_KEY", "")}


def call(spec, brief):
    """One cold read through a door → (text | None, usage, error). Nothing returned ever carries the key or a response body."""
    system = SYSTEM + "\n" + json.dumps(SCHEMA); user = brief + INSTRUCTION
    if spec["kind"] == "anthropic":
        url = f"{spec['base']}/v1/messages"
        headers = {"x-api-key": spec["key"], "anthropic-version": "2023-06-01", "content-type": "application/json"}
        body = {"model": spec["model"], "max_tokens": 4000, "system": system, "messages": [{"role": "user", "content": user}]}
    else:
        url = f"{spec['base']}/chat/completions"
        headers = {"Authorization": f"Bearer {spec['key']}", "Content-Type": "application/json"}
        body = {"model": spec["model"], "response_format": {"type": "json_object"},
                "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}]}
    if not spec["key"]:
        return None, {}, f"no API key for the {spec['kind']} judge"
    req = urllib.request.Request(url, data=json.dumps(body).encode("utf-8"), method="POST", headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
            out = json.loads(r.read().decode("utf-8", "replace"))
    except urllib.error.HTTPError as e:
        return None, {}, f"HTTP {e.code} from the judge door"
    except Exception as e:
        return None, {}, f"unreachable: {str(getattr(e, 'reason', None) or e)[:120]}"
    try:
        if spec["kind"] == "anthropic":
            text = "".join(c.get("text", "") for c in out["content"] if isinstance(c, dict)); u = out.get("usage") or {}
            usage = {"input_tokens": u.get("input_tokens"), "output_tokens": u.get("output_tokens")}
        else:
            text = out["choices"][0]["message"]["content"]; u = out.get("usage") or {}
            usage = {"input_tokens": u.get("prompt_tokens"), "output_tokens": u.get("completion_tokens")}
    except (KeyError, IndexError, TypeError, ValueError):
        return None, {}, f"bad JSON from {spec['model']}"
    return text, usage, None


def parse_verdict(content):
    """Lenient: the first {...} object in the answer (a model that wraps its JSON in prose still counts); None when there is no verdict."""
    i = (content or "").find("{")
    if i < 0:
        return None
    try:
        d, _ = json.JSONDecoder().raw_decode(content[i:])
    except ValueError:
        return None
    return d if isinstance(d, dict) and d.get("verdict") in ("MERGE", "HOLD") else None


def read_one(spec, brief):
    j = {"model": spec["model"], "lab": lab_of(spec["model"]), "verdict": None, "d": None, "usage": {}, "error": None}
    text, j["usage"], j["error"] = call(spec, brief)
    if j["error"]:
        return j
    d = parse_verdict(text)
    if d is None:
        j["error"] = f"no verdict in the answer from {spec['model']}"; return j
    j["d"], j["verdict"] = d, d["verdict"]
    return j


def strip_ledger(brief):
    """The second judge's brief: the first judge's '## Your ledger' section cut, everything else byte-identical."""
    return re.sub(r"\n\n## Your ledger[^\n]*\n.*?(?=\n\n## )", "", brief, count=1, flags=re.S)


def combine(first, second):
    """→ (final verdict, agreement, note). One judge → its verdict, "single". Both answered: MERGE only when both say MERGE,
    any split = HOLD with the split named. One could not answer → the other's verdict stands, "unavailable", the reason noted."""
    if first["error"] and not second:
        raise RuntimeError(f"the only judge could not answer — {first['error']}")
    if first["error"] and second["error"]:
        raise RuntimeError(f"neither judge answered — first: {first['error']}; second: {second['error']}")
    if first["error"]:
        return second["verdict"], "unavailable", f"first judge unavailable — {first['error']}"
    if not second:
        return first["verdict"], "single", None
    if second["error"]:
        return first["verdict"], "unavailable", f"second judge unavailable — {second['error']}"
    if first["verdict"] == second["verdict"]:
        return first["verdict"], "full", None
    return "HOLD", "split", f"judges split: {first['lab']} {first['verdict']} / {second['lab']} {second['verdict']}"


# ---------- the ledger: the reviewer's own history ----------
def ledger_path(out): return os.path.join(out, "ledger", "ledger.jsonl")


def ledger_rows(out):
    rows = []
    try:
        for l in open(ledger_path(out), encoding="utf-8"):
            try:
                r = json.loads(l)
            except json.JSONDecodeError:
                continue
            if isinstance(r, dict) and r.get("ts") and r.get("final"):
                rows.append(r)
    except FileNotFoundError:
        pass
    return rows


def ledger_tail(out, n=15):
    """The FIRST judge's own past verdicts, for its brief: date · ref · its verdict · its one line. THE WALL: never `final`,
    never `judges` or `agreement` — the second judge's vote must not reach the first through its ledger."""
    rows = ledger_rows(out)[-n:]
    if not rows:
        return "(no ledger yet — this is the first read on this lane)"
    return "\n".join(f"- {r['ts'][:10]} {r['ref']} → {r['verdict'] or '(you could not answer)'}" + (f" — {r['note']}" if r.get("note") else "") for r in rows)


def ledger_append(out, row):
    os.makedirs(os.path.dirname(ledger_path(out)), exist_ok=True)
    with open(ledger_path(out), "a", encoding="utf-8") as f:
        f.write(json.dumps(row, ensure_ascii=False) + "\n")
    rows = ledger_rows(out)
    md = ["# The judges' ledger", f"*{len(rows)} read(s) · {sum(1 for r in rows if r['final'] == 'HOLD')} held · machine-written after every cold read*", "",
          "| date | ref · head | first judge | final | the one line | read, in order |", "|---|---|---|---|---|---|"]
    for r in reversed(rows[-60:]):
        md.append(f"| {r['ts'][:16]}Z | {r['ref']} · {r['head'][:12]} | {r['verdict'] or '—'} | {r['final']} ({r['agreement']}) | "
                  f"{(r.get('note') or '').replace('|', '/')[:160]} | {(r.get('trace') or '').replace('|', '/')[:200]} |")
    open(os.path.join(out, "ledger", "LEDGER.md"), "w", encoding="utf-8").write("\n".join(md) + "\n")


def first_line(text, verdict):
    """The ledger's one line: on HOLD the reason it held; on MERGE the first thing it would change."""
    t = re.sub(r"\s+", " ", (text or "").strip())
    if verdict == "HOLD":
        return re.sub(r"^(hold|held)[.:,\s-]*", "", t, flags=re.I).split(". ")[0].rstrip(".")[:200]
    m = re.search(r"Would change[^:]*:\s*(.*)", t, flags=re.I)
    return (m.group(1) if m else t).split(". ")[0].rstrip(".")[:200]


def count_holds(reads):
    """`reads` = (final verdict, commit) oldest first → rounds held since the last MERGE; a round is a distinct commit."""
    rounds = set()
    for verdict, commit in reversed(reads):
        if verdict == "MERGE":
            break
        if verdict == "HOLD":
            rounds.add(commit)
    return len(rounds)


def hold_rounds(out, label):
    return count_holds([(r["final"], r["head"]) for r in ledger_rows(out) if r.get("label") == label])


# ---------- a read in flight ----------
def inflight(out, label, sha): return os.path.join(out, f".inflight-{label}-{sha[:12]}")


def inflight_mark(path):
    """Take the marker atomically (O_CREAT|O_EXCL). One already there and younger than INFLIGHT_MIN minutes raises NotRead;
    a stale or unreadable one is replaced."""
    os.makedirs(os.path.dirname(path), exist_ok=True)
    for attempt in (1, 2):
        try:
            fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o644)
        except FileExistsError:
            try:
                age = time.time() - os.stat(path).st_mtime; body = json.load(open(path, encoding="utf-8"))
            except (OSError, ValueError):
                age, body = INFLIGHT_MIN * 60 + 1, {}
            if attempt == 2 or age <= INFLIGHT_MIN * 60:
                raise NotRead(f"a read of this commit is in flight (started {body.get('at')} by pid {body.get('pid')}) — {os.path.basename(path)}")
            try: os.remove(path)
            except FileNotFoundError: pass
            continue
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump({"pid": os.getpid(), "at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")}, f)
        return


def inflight_clear(path):
    """Release the marker only when the pid inside is this process's own."""
    try:
        if json.load(open(path, encoding="utf-8")).get("pid") == os.getpid():
            os.remove(path); return True
    except (OSError, ValueError):
        pass
    return False


# ---------- the review file ----------
def read_review(path):
    """A review file's verdict (its first line) and the commit it read (the `- head <sha>` line)."""
    text = open(path, encoding="utf-8").read()
    v = re.search(r"·\s*(MERGE|HOLD)\s*$", text.split("\n", 1)[0]); h = re.search(r"^- head ([0-9a-f]{40})\s*$", text, re.M)
    return {"verdict": v.group(1) if v else None, "head": h.group(1) if h else None, "file": path}


def last_read(out, label):
    import glob
    files = sorted(glob.glob(os.path.join(out, f"????-??-??-??????-{label}-" + "?" * 12 + "*.md")),
                   key=lambda f: (os.path.basename(f)[:17], os.path.getmtime(f)))        # the date-time in the name, then mtime inside one second
    return read_review(files[-1]) if files else None


def code_checks(repo, head, base):
    out = []
    files = sh(["git", "diff", "--name-only", f"{base}...{head}"], cwd=repo).split()
    out.append(f"files touched: {', '.join(files) or 'none'}")
    for f in files:
        if re.search(r"^(<<<<<<<|>>>>>>>)", sh(["git", "show", f"{head}:{f}"], cwd=repo, check=False), re.M):
            out.append(f"CONFLICT MARKERS in {f}")
    out.append(f"head {sh(['git', 'rev-parse', head], cwd=repo).strip()}")     # the commit this read is of — merge only that one
    out.append(f"merge base {sh(['git', 'merge-base', base, head], cwd=repo).strip()[:7]}; base is "
               f"{sh(['git', 'rev-list', '--count', f'{head}..{base}'], cwd=repo).strip()} commit(s) ahead of the branch point")
    return out


def rules_text(files):
    parts = []
    for p in files:
        try:
            body = re.sub(r"^---\n.*?\n---\n", "", open(p, encoding="utf-8").read(), count=1, flags=re.S)
            parts.append(f"### {os.path.basename(p)}\n{body.strip()[:3500]}")
        except OSError:
            parts.append(f"### {os.path.basename(p)}\n(unreadable)")
    return "\n\n".join(parts) or "(no rules file given — read against the commit messages and common sense)"


def review(ref, repo=".", base="main", rules=(), out="reviews", expect_head=None, dry=False, again=False, read_fn=read_one):
    label = ref.replace("/", "-")
    sha = sh(["git", "rev-parse", ref], cwd=repo).strip()
    if expect_head and not sha.startswith(expect_head):
        raise NotRead(f"{ref} is at {sha[:12]}, not {expect_head[:12]} — not read (fetch, then try again)")
    first, second = judge_spec("first"), judge_spec("second")
    if not first["model"]:
        raise RuntimeError("JUDGES_FIRST_MODEL is not set")
    log = sh(["git", "log", "--format=%h %an: %s", f"{base}..{ref}"], cwd=repo)
    diff = sh(["git", "diff", f"{base}...{ref}"], cwd=repo)
    checks = code_checks(repo, ref, base)
    brief = (f"# Review brief — `{ref}` against `{base}`\n\n## Commits\n{log or '(none)'}\n\n## Code checks (already run, deterministic)\n"
             + "\n".join(f"- {c}" for c in checks) + f"\n\n## Your ledger — the last verdicts you gave on this lane\n{ledger_tail(out)}"
             f"\n\n## The rules that govern this change\n{rules_text(rules)}\n\n## The diff\n```diff\n{diff[:DIFF_CHARS]}\n```\n"
             + ("(diff truncated)\n" if len(diff) > DIFF_CHARS else ""))
    if dry:
        print(brief[:3000] + f"\n… ({len(brief)} chars)\nfirst judge: {first['model']} ({lab_of(first['model'])}, {first['kind']})"
              f"\nsecond judge: {(second['model'] + ' (' + lab_of(second['model']) + ', ' + second['kind'] + '), brief without the ledger') if second['model'] else 'off'}")
        return None
    held = hold_rounds(out, label)
    if held >= 2 and not again:
        raise NotRead(f"two rounds already held on {ref} — the next step is a human's, not a third round (--again once they have looked)")
    mark = inflight(out, label, sha); inflight_mark(mark)
    try:
        return _read(ref, base, label, sha, brief, checks, first, second, out, read_fn)
    finally:
        inflight_clear(mark)


def _read(ref, base, label, sha, brief, checks, first, second, out, read_fn):
    a = read_fn(first, brief)
    b = read_fn(second, strip_ledger(brief)) if second["model"] else None     # new context, nothing of the first judge in it
    final, agreement, note = combine(a, b)
    now = datetime.now(timezone.utc); os.makedirs(out, exist_ok=True)
    stem = os.path.join(out, f"{now.strftime('%Y-%m-%d-%H%M%S')}-{label}-{sha[:12]}")   # date, time and sha: no two reads share a name
    path, n = stem + ".md", 1
    while os.path.exists(path):
        n += 1; path = f"{stem}-{n}.md"
    judges = [{"model": j["model"], "lab": j["lab"], "verdict": j["verdict"], "error": j["error"]} for j in (a, b) if j]
    def section(j, title):
        if not j:
            return ""
        if j["error"]:
            return f"\n## {title} — {j['model']} ({j['lab']}) · no read\n{j['error']}\n"
        d = j["d"]
        return (f"\n## {title} — {j['model']} ({j['lab']}) · {j['verdict']}\n{d.get('review', '')}\n\n"
                + (f"**Nit:** {d['nit']}\n\n" if d.get("nit") else "") + f"### What it read, in order\n{d.get('trace', '')}\n\n### Analysis\n{d.get('analysis', '')}\n")
    text = (f"# Cold read — `{ref}` → `{base}` · {final}\n\n*{now.strftime('%Y-%m-%d %H:%M UTC')} · two judges from two labs, each in a fresh context · code checks first*\n\n"
            + (f"**{note}** — a split is a HOLD: the human breaks the tie, never a third round.\n\n" if agreement == "split" else "")
            + "## Judges\n" + "\n".join(f"- {j['model']} ({j['lab']}) → {j['verdict'] or j['error']}" for j in judges)
            + f"\n- agreement: {agreement}" + (f" — {note}" if note and agreement != "split" else "") + "\n\n## Code checks\n" + "\n".join(f"- {c}" for c in checks) + "\n"
            + section(a, "First judge") + section(b, "Second judge (the same brief without the ledger)"))
    open(path, "w", encoding="utf-8").write(text)
    ad = a["d"] or {}                                                        # the ledger's `verdict`/`note` are the first judge's own (None when it could not answer); `final` is the combined one
    ledger_append(out, {"ts": now.strftime("%Y-%m-%dT%H:%M"), "ref": ref, "label": label, "head": sha, "verdict": a["verdict"], "model": a["model"],
                        "note": first_line(ad.get("review"), a["verdict"]) if a["verdict"] else (a["error"] or ""), "trace": ad.get("trace", ""), "usage": a["usage"],
                        "second_verdict": b["verdict"] if b else None, "file": path, "final": final, "judges": judges, "agreement": agreement})
    print(f"{final} — {ref}" + (f"\n\n{note}" if note else "") + "\n\n" + "\n".join(f"{j['model']} ({j['lab']}) → {j['verdict'] or j['error']}" for j in judges)
          + (f"\n\n{a['d']['review']}" if a["d"] else "") + f"\n\n→ {path}")
    return final


def status(ref, repo=".", out="reviews"):
    """Exit 0 = the branch's current commit was read and said MERGE · 2 = read here, said HOLD · 4 = unread here (no read, or the branch moved)."""
    rv = last_read(out, ref.replace("/", "-"))
    if not rv:
        print(f"{ref}: no read yet"); return 4
    sha = sh(["git", "rev-parse", ref], cwd=repo).strip()
    cur = rv["head"] == sha
    print(f"{ref}: last read {rv['verdict']} at {(rv['head'] or 'no commit')[:12]} ({os.path.basename(rv['file'])}) — "
          + ("the branch still sits on that commit" if cur else f"the branch moved to {sha[:12]}: unread, read it again"))
    return (0 if rv["verdict"] == "MERGE" else 2) if cur else 4


def main(argv):
    if not argv or argv[0] in ("-h", "--help"):
        print(__doc__); return 0
    def opt(k, d=None):
        return argv[argv.index(k) + 1] if k in argv and argv.index(k) + 1 < len(argv) else d
    repo, out = opt("--repo", "."), opt("--out", "reviews")
    if argv[0] == "ledger":
        for r in ledger_rows(out)[-(int(argv[1]) if len(argv) > 1 and argv[1].isdigit() else 20):]:
            print(f"{r['ts']}  {r['ref']:30} {r['head'][:12]}  {(r['verdict'] or '—'):5} → {r['final']} ({r['agreement']})" + (f" — {r['note']}" if r.get("note") else ""))
        return 0
    if len(argv) < 2:
        print(__doc__); return 1
    if argv[0] == "status":
        return status(argv[1], repo, out)
    if argv[0] != "read":
        print(__doc__); return 1
    rules = []
    if "--rules" in argv:                                   # every path after --rules up to the next option
        for x in argv[argv.index("--rules") + 1:]:
            if x.startswith("--"):
                break
            rules.append(x)
    try:
        v = review(argv[1], repo, opt("--base", "main"), rules, out, expect_head=opt("--head"), dry="--dry-run" in argv, again="--again" in argv)
    except NotRead as e:
        print(f"NOT READ: {e}"); return 4
    except RuntimeError as e:
        hint = " — a shallow or partial clone cannot see the base: git fetch --unshallow, and make sure the base branch exists locally" \
            if re.search(r"merge-base|unknown revision|bad revision|ambiguous argument|shallow", str(e), re.I) else ""
        print(f"could not read: {e}{hint}"); return 1
    return 0 if v in (None, "MERGE") else 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
