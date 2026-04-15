#!/usr/bin/env bash
set -euo pipefail

echo "== Day 1 cutover v2 start =="

SIG_FILE="src/signal_engine.py"
RISK_FILE="src/risk_engine.py"

for f in "$SIG_FILE" "$RISK_FILE"; do
if [ ! -f "$f" ]; then
echo "Missing file: $f"
exit 1
fi
done

cp "$SIG_FILE" "${SIG_FILE}.bak"
cp "$RISK_FILE" "${RISK_FILE}.bak"

python3 <<'PY'
from pathlib import Path

def remove_ranges(text, ranges):
lines = text.splitlines()
keep = []
for idx, line in enumerate(lines, start=1):
drop = any(start <= idx <= end for start, end in ranges)
if not drop:
keep.append(line)
return "\n".join(keep) + "\n"

sig = Path("src/signal_engine.py")
risk = Path("src/risk_engine.py")

sig_text = sig.read_text()
risk_text = risk.read_text()

sig_new = remove_ranges(sig_text, [
(84, 87),
(102, 103),
(201, 205),
])

risk_new = remove_ranges(risk_text, [
(92, 107),
(132, 138),
])

sig.write_text(sig_new)
risk.write_text(risk_new)
PY

echo "== Syntax check =="
python3 -m py_compile src/signal_engine.py src/risk_engine.py

echo "== Preview diff =="
git diff -- src/signal_engine.py src/risk_engine.py || true

echo "== Done =="
echo "Backups saved:"
echo " src/signal_engine.py.bak"
echo " src/risk_engine.py.bak"
