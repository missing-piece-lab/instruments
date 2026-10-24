# The rules a cold read checks a change against (an example — write your project's own)

### state is observed
Documentation never asserts what a probe can check. A sentence like "the service is live" belongs in a claims file with
a probe, not in prose. A change that adds a state stamp (LIVE, DONE, ARMED) to a document is a HOLD until the stamp
becomes a claim.

### a merge is its own step
Nothing merges inside a chain of other commands. Merge, then verify (markers, tests, the probes), then push. A change
that wires a merge into a script's `&&` chain is unsafe to land.

### keep meaning where you rewrite
A rewrite of a documented rule or a recorded decision keeps its meaning and its date. Tightening the wording is fine;
changing what it says, silently, is not. If the meaning must change, the commit message says so and says why.

### no secrets, no topology
No tokens, keys, hostnames, internal addresses or private paths enter the repo. A change that adds one is a HOLD even
when the key is already rotated.
