#!/usr/bin/env bash
# The scrub gate: nothing private leaves in this repo. Zero hits allowed; part of every test run (tests/run.py).
# The list is rot13 (letters) + rot5 (digits) so the strings it hunts for never sit in the repo in the clear; a hit prints the decoded word.
set -u
cd "$(dirname "$0")/.."
ENC="nevnu-pber gnvy1796nn 655.6 cyrk-56 cbegnoyr ybmnab arb-nv855 fnz-fghqvb enira NRC Pbechf Sbetrwb cebgba.zr bc:// qvfpbeq zrzflf 6Cnffjbeq"
rc=0
for e in $ENC; do
  w=$(printf '%s' "$e" | tr 'A-Za-z0-9' 'N-ZA-Mn-za-m5-90-4')
  hits=$(grep -rIin --exclude-dir=.git --exclude-dir=__pycache__ --exclude-dir=.pytest_cache -F -- "$w" . || true)
  if [ -n "$hits" ]; then
    echo "check: '$w' found:"; printf '%s\n' "$hits" | sed 's/^/  /'; rc=1
  fi
done
# no secrets by shape either: API keys, bearer tokens, private key blocks
shape=$(grep -rIEn --exclude-dir=.git --exclude-dir=__pycache__ --exclude-dir=.pytest_cache -e 'sk-[A-Za-z0-9_-]{20,}' -e 'BEGIN (RSA |OPENSSH )?PRIVATE KEY' -e 'ghp_[A-Za-z0-9]{20,}' . || true)
if [ -n "$shape" ]; then echo "check: a secret-shaped string:"; printf '%s\n' "$shape" | sed 's/^/  /'; rc=1; fi
# the budget: the whole repo stays small on purpose
total=$(find . -path ./.git -prune -o -type f \( -name '*.py' -o -name '*.md' -o -name '*.yml' -o -name '*.sh' -o -name '*.toml' -o -name '*.jsonl' -o -name '*.example' -o -name 'LICENSE' \) -print0 | xargs -0 cat | wc -l)
if [ "$total" -gt 2500 ]; then echo "check: $total lines — the budget is 2500"; rc=1; fi
[ $rc -eq 0 ] && echo "check: clean ($total lines, nothing private, nothing secret-shaped)"
exit $rc
