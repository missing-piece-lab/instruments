#!/usr/bin/env python3
"""claims — state is observed, never written by hand.

A project carries a CLAIMS.yml at its root. Each claim is one sentence about the project and the probe that checks it:

    project: web-service
    claims:
      - id: service-answers
        says: the service answers on its port
        probe: curl -s -o /dev/null -w '%{http_code}' -m 5 http://127.0.0.1:8080/health | grep -q 200
        kind: bool                        # exit 0 = true (the default); value = first line of stdout; count = the first integer
        cwd: .                            # where the probe runs, relative to the manifest
        subject: [service, api]           # the words a document uses when it talks about this claim (else the project name)
        contradicts: {false: [live, up]}  # words a document cannot use about the subject if the claim fails (true: the other way)
        every: daily                      # probe once per UTC day (or "6h"); other runs hold the last observation
        timeout: 20

    claims.py [--root DIR] [--out DIR] [--docs DIR] [--fresh] [--only ID] [--project NAME] [--print]

Every manifest under --root (its own CLAIMS.yml and one directory down) is probed; the result is written to
<out>/STATE.json and <out>/STATE.md, machine-written, never edited. --docs DIR scans the markdown there: a line about
a claim's subject that uses a word the observed value contradicts is drift, listed under "docs check". --only and
--project print one result and write nothing. --fresh ignores `every:`.

Plans say what we intend. History says what happened. This says what is. A probe returns a boolean, a short value or a
count — never contents. A probe that cannot run is "unknown", never a failure. Nothing here changes anything. A probe
must never run a build or a test suite that could call this runner again (CLAIMS_RUNNING guards the nested case).
"""
import glob, json, os, re, subprocess, sys, time
from datetime import datetime, timezone

import yaml

# state stamps are the uppercase ones ("LIVE", "DONE 9-19"); lowercase "live code" is prose
STATE_WORDS = re.compile(r"\b(LIVE|DONE|BUILT|ARMED|UNARMED|PENDING|PARKED|BLOCKED|STAGED|INSTALLED|IN PROGRESS|NOT STARTED|COMPLETE|SHIPPED|RETIRED|SUPERSEDED)\b")


def discover(root):
    """Every CLAIMS.yml at the root or one directory down → a list of manifests (an unreadable one is kept, with its error)."""
    files = sorted(set(glob.glob(os.path.join(root, "CLAIMS.yml")) + glob.glob(os.path.join(root, "*", "CLAIMS.yml"))))
    out = []
    for f in files:
        name = os.path.basename(os.path.dirname(os.path.abspath(f)))
        try:
            d = yaml.safe_load(open(f, encoding="utf-8")) or {}
            if not isinstance(d, dict) or not isinstance(d.get("claims", []), list):
                raise ValueError("expected a mapping with a `claims:` list")
            d.setdefault("project", name); d.setdefault("claims", []); d["_file"] = f
            out.append(d)
        except yaml.YAMLError as e:
            mark = getattr(e, "problem_mark", None)
            where = f" at line {mark.line + 1}" if mark is not None else ""
            out.append({"project": name, "_file": f, "_error": f"{f} is not valid YAML{where}: {getattr(e, 'problem', None) or 'parse error'}", "claims": []})
        except Exception as e:
            out.append({"project": name, "_file": f, "_error": f"{f}: {str(e)[:100]}", "claims": []})
    return out


def run_probe(c, manifest_dir):
    """→ (value, note). value: True/False for bool claims, a short string for value claims, an int for counts; None = unknown."""
    kind = c.get("kind", "bool")
    cwd = os.path.normpath(os.path.join(manifest_dir, os.path.expanduser(str(c.get("cwd", ".")))))
    if not os.path.isdir(cwd):
        return None, f"no such cwd: {c.get('cwd')}"
    env = dict(os.environ, CLAIMS_RUNNING="1")       # a probe that shells back into this runner must not start a nested run
    try:
        r = subprocess.run(str(c["probe"]), shell=True, cwd=cwd, env=env, capture_output=True, text=True, timeout=int(c.get("timeout", 20)))
    except subprocess.TimeoutExpired:
        return None, "probe timed out"
    except Exception as e:
        return None, str(e)[:80]
    if kind in ("value", "count") and r.returncode != 0:
        return None, f"exit {r.returncode}"                    # a value from a failed command is not a value
    if kind == "value":
        lines = (r.stdout or "").strip().splitlines()
        return (lines[0][:60] if lines else ""), None
    if kind == "count":
        try:
            return int((r.stdout or "").split()[0]), None
        except (IndexError, ValueError):
            return None, "no count"
    return r.returncode == 0, None


def previous(out_dir):
    """{(project, id): entry} from the last STATE.json — what `every:` claims hold between probes."""
    try:
        old = json.load(open(os.path.join(out_dir, "STATE.json"), encoding="utf-8"))
        return {(p["project"], c["id"]): c for p in old.get("projects", []) for c in p.get("claims", [])}
    except Exception:
        return {}


def held(c, prev, now):
    """The last observation of an `every:` claim if it is still current, else None. `daily` = one probe per UTC day;
    `6h` = hold six hours. A held unknown is never reused."""
    every = str(c.get("every", "")).strip().lower()
    if not every or not prev or prev.get("value") is None or not prev.get("observed"):
        return None
    try:
        then = datetime.fromisoformat(prev["observed"])
    except ValueError:
        return None
    if every == "daily":
        return prev if then.date() == now.date() else None
    m = re.fullmatch(r"(\d+)h", every)
    return prev if m and (now - then).total_seconds() < int(m.group(1)) * 3600 else None


def evaluate(manifests, out_dir, only=None, project=None, fresh=False):
    now = datetime.now(timezone.utc)
    result = {"generated": now.isoformat(timespec="seconds"), "projects": []}
    prev = {} if (fresh or only or project) else previous(out_dir)
    for m in manifests:
        if project and m["project"] != project:
            continue
        p = {"project": m["project"], "file": m["_file"], "claims": []}
        if m.get("_error"):
            p["error"] = m["_error"]
        for c in m["claims"]:
            if not isinstance(c, dict) or not c.get("id") or not c.get("probe") or (only and c["id"] != only):
                continue
            t0 = time.time()
            h = held(c, prev.get((m["project"], c["id"])), now)
            if h:
                v, note, observed = h["value"], f"held from {h['observed'][11:16]}Z, probed {c['every']}", h["observed"]
            else:
                v, note = run_probe(c, os.path.dirname(os.path.abspath(m["_file"])))
                observed = now.isoformat(timespec="seconds")
            entry = {"id": c["id"], "says": c.get("says", ""), "kind": c.get("kind", "bool"), "value": v, "note": note,
                     "observed": observed, "ms": int((time.time() - t0) * 1000)}
            if h:
                entry["held"] = True
            if c.get("contradicts"):
                entry["contradicts"] = {str(k).lower(): list(v) for k, v in c["contradicts"].items()}   # yaml reads true:/false: as booleans
            if c.get("subject"):
                entry["subject"] = [c["subject"]] if isinstance(c["subject"], str) else list(c["subject"])
            if c.get("expect") is not None:
                entry["expect"] = c["expect"]; entry["ok"] = (v == c["expect"]) if v is not None else None
            p["claims"].append(entry)
        result["projects"].append(p)
    return result


def doc_lines(docs_dir):
    """(file, line number, text) for every line of every markdown file under docs_dir."""
    for f in sorted(glob.glob(os.path.join(docs_dir, "**", "*.md"), recursive=True)):
        try:
            for i, line in enumerate(open(f, encoding="utf-8"), 1):
                yield os.path.relpath(f, docs_dir), i, line.rstrip("\n")
        except OSError:
            continue


def check_docs(state, docs_dir):
    """A documentation line that uses a word a probe contradicts = drift. A line about the subject is one that mentions
    one of the claim's subject words (else the project name) — matched as substrings, so pick subjects that are not
    prefixes of other words. Also counted: lines carrying a state stamp at all (a state word in prose is a sentence
    pretending to be an observation)."""
    drift, stamped = [], 0
    lines = list(doc_lines(docs_dir))
    for _, _, text in lines:
        if STATE_WORDS.search(text):
            stamped += 1
    for p in state["projects"]:
        for c in p["claims"]:
            con, v = c.get("contradicts"), c.get("value")
            if not con or v is None or c["kind"] != "bool":
                continue
            bad = [w.lower() for w in con.get("true" if v else "false", [])]
            subj = [s.lower() for s in (c.get("subject") or [p["project"]])]
            for f, n, text in lines:
                low = text.lower()
                if not any(s in low for s in subj):
                    continue
                hits = [w for w in bad if re.search(r"\b" + re.escape(w) + r"\b", low)]
                if hits:
                    drift.append({"file": f, "line": n, "project": p["project"], "claim": c["id"], "observed": v, "says": c["says"], "words": hits})
    state["docs_check"] = {"drift": drift, "lines": len(lines), "stamped_lines": stamped}
    return state


def render(state):
    lines = [f"# STATE — observed {state['generated'][:16]}Z by claims.py (machine-written; do not edit)", "",
             "Plans say what we intend. History says what happened. This says what is: every line below is a probe that ran, not a sentence someone wrote.", ""]
    for p in state["projects"]:
        lines.append(f"## {p['project']}  ·  `{p['file']}`")
        if p.get("error"):
            lines.append(f"- ⚠ manifest error: {p['error']}")
        for c in p["claims"]:
            v = c["value"]
            if v is None: mark = "？"
            elif c["kind"] == "bool": mark = "✅" if v else "❌"
            elif "ok" in c: mark = "✅" if c["ok"] else "❌"
            else: mark = "·"
            val = "" if c["kind"] == "bool" else f" = **{v}**"
            note = f" _({c['note']})_" if c.get("note") else ""
            lines.append(f"- {mark} **{c['id']}** — {c['says']}{val}{note}")
        lines.append("")
    dc = state.get("docs_check")
    if dc:
        lines.append("## docs check")
        for d in dc["drift"]:
            lines.append(f"- ❌ `{d['file']}:{d['line']}` says **{', '.join(d['words'])}** but `{d['claim']}` observed **{d['observed']}** ({d['says']})")
        if not dc["drift"]:
            lines.append(f"- ✅ no documentation line contradicts a probe ({dc['lines']} lines read)")
        if dc["stamped_lines"]:
            lines.append(f"- {dc['stamped_lines']} line(s) carry a state stamp (LIVE, DONE, …) — state belongs here, not in prose")
        lines.append("")
    return "\n".join(lines)


def write_state(state, out_dir):
    os.makedirs(out_dir, exist_ok=True)
    tmp = os.path.join(out_dir, "STATE.json.tmp")
    json.dump(state, open(tmp, "w", encoding="utf-8"), indent=1, ensure_ascii=False)
    os.replace(tmp, os.path.join(out_dir, "STATE.json"))
    open(os.path.join(out_dir, "STATE.md"), "w", encoding="utf-8").write(render(state))


def main(argv):
    if os.environ.get("CLAIMS_RUNNING"):
        # a probe ran a build whose lint re-ran this runner, which ran the probe again … (2026-09-28: ~200 orphaned
        # processes on a one-CPU box). A nested run is never useful: the outer run is about to write STATE.json anyway.
        # Exits 0 on purpose, so a probe that only shells out here would read green unconditionally — probe STATE.json instead.
        print("claims: nested run skipped (called from inside a probe)"); return 0
    if "--help" in argv or "-h" in argv:
        print(__doc__); return 0
    def opt(k, d=None):
        return argv[argv.index(k) + 1] if k in argv and argv.index(k) + 1 < len(argv) else d
    root = opt("--root", "."); out_dir = opt("--out", os.path.join(root, "state"))
    only, project, fresh = opt("--only"), opt("--project"), "--fresh" in argv
    manifests = discover(root)
    for m in manifests:
        if m.get("_error"):
            print(f"claims: {m['_error']}", file=sys.stderr)
    state = evaluate(manifests, out_dir, only=only, project=project, fresh=fresh)
    if opt("--docs"):
        check_docs(state, opt("--docs"))
    if only or project or "--print" in argv:
        print(json.dumps(state, indent=1, ensure_ascii=False)); return 0
    write_state(state, out_dir)
    cs = [c for p in state["projects"] for c in p["claims"]]
    t = sum(1 for c in cs if c["kind"] == "bool" and c["value"] is True)
    f = sum(1 for c in cs if c["kind"] == "bool" and c["value"] is False)
    u = sum(1 for c in cs if c["value"] is None)
    d = len(state.get("docs_check", {}).get("drift", []))
    print(f"claims: {len(cs)} probed · {t} true · {f} false · {u} unknown" + (f" · docs drift {d}" if "docs_check" in state else "") + f" → {out_dir}/STATE.md")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
