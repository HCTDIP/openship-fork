#!/usr/bin/env bash
# backup -> inject -> reconcile -> restore -> sha256 compare. Any restore mismatch voids the whole run.
set -uo pipefail
cd "$(dirname "$0")"
PY=python3
mkdir -p reconcile fixtures
$PY injector.py setup > reconcile/setup.log || { echo "SETUP FAILED"; exit 1; }
for fid in T1 T2 T3 T3.5 T3.6 T5; do
  echo "--- $fid"
  $PY injector.py prepare "$fid"   || { echo "VOID: prepare $fid"; exit 1; }
  $PY injector.py inject "$fid"    || { echo "VOID: inject $fid"; exit 1; }
  $PY injector.py reconcile "$fid" || { echo "VOID: reconcile crashed $fid"; exit 1; }
  if ! $PY injector.py restore "$fid"; then echo "VOID: restored sha256 != original ($fid) -> whole test void"; exit 1; fi
done
echo "--- T4"
$PY t4_tip_race.py > reconcile/T4_stdout.txt 2>&1 || { echo "T4 crashed"; tail -20 reconcile/T4_stdout.txt; exit 1; }
tail -1 reconcile/T4_stdout.txt
echo "--- listener unit tests"
(cd ../listener && $PY -m unittest test_listener 2>&1 | tail -3) | tee reconcile/listener_unittest.txt
rm -f reconcile/case61.json
if $PY injector.py summary | grep -q false; then echo "NOT ALL PASS -> 61-fill case not run"; $PY injector.py summary | tee reconcile/summary.json; exit 1; fi
echo "--- case61 (only after all pass)"
$PY injector.py case61 | tee reconcile/case61.log
$PY injector.py summary | tee reconcile/summary.json
