#!/usr/bin/env bash
# Run a pytest selection N times, SEQUENTIALLY, and tally each test's outcome across runs.
#
#   repeat_tests.sh <label> <N> <test file> <-k expression>
#
# Run from the maria-voice checkout whose code you want to test. Pass the environment the suite
# reads through EXTRA_ENV, e.g.
#   EXTRA_ENV="ACME_FLOW_ID=<clone> BACKEND_URL=http://localhost:3005" repeat_tests.sh v7 3 tests/…/test_acme_paths.py "decline"
# Output: $RUNS_DIR/<label>/run_<i>.log and summary.txt (RUNS_DIR defaults to ./runs).
# Sequential on purpose: a customer's test backend is small and parallel runs step on each other.
# Every run is a paid LLM conversation per test: select, do not sweep.
set -u
LABEL=$1; N=$2; FILE=$3; K=$4
DIR="${RUNS_DIR:-./runs}/$LABEL"
mkdir -p "$DIR"
for i in $(seq 1 "$N"); do
  env ${EXTRA_ENV:-} timeout "${RUN_TIMEOUT:-2400}" nice -n 5 \
    poetry run pytest -o addopts="" -o log_cli=false -p no:cacheprovider -rA -q "$FILE" -k "$K" \
    > "$DIR/run_$i.log" 2>&1
  echo "run $i: exit=$?" >> "$DIR/summary.txt"
done
python3 - "$DIR" "$N" <<'PY' >> "$DIR/summary.txt"
import collections, glob, re, sys
d, n = sys.argv[1], int(sys.argv[2])
res = collections.defaultdict(list)
pat = re.compile(r"^(PASSED|FAILED|ERROR|XFAIL|XPASS) tests/\S+::(\S+)|^SKIPPED \[\d+\] tests/\S+?:(\d+): (.{0,60})", re.M)
for f in sorted(glob.glob(f"{d}/run_*.log")):
    text = open(f, errors="ignore").read()
    if "short test summary info" in text:
        text = text[text.index("short test summary info"):]
    for m in pat.finditer(text):
        state, name, line, why = m.groups()
        if line:
            state, name = "SKIPPED", f"(line {line}) {why}"
        res[name].append(state)
print(f"\n=== summary ({n} runs) ===")
for t, states in sorted(res.items()):
    c = collections.Counter(states)
    print(f"  {t[:78]:78} " + "  ".join(f"{k}={v}" for k, v in sorted(c.items())))
PY
cat "$DIR/summary.txt"
