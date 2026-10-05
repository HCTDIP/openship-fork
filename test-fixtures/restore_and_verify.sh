#!/usr/bin/env bash
# Automated: backup -> inject -> reconcile -> restore -> SHA256 compare, for every test.
# Any restore SHA256 != original  => WHOLE TEST RUN VOID (exit 4).
# Run from repo root:  bash test-fixtures/restore_and_verify.sh
set -uo pipefail
cd "$(dirname "$0")"
FX=$(pwd); ROOT=$(cd .. && pwd)
LO=94853541
export RUN_ID=${RUN_ID:-local}
OUT=$FX/results; rm -rf "$OUT" dirty-ledger reconcile backup baseline; mkdir -p "$OUT" dirty-ledger reconcile backup baseline
LOG=$OUT/restore_and_verify.log
exec > >(tee -a "$LOG") 2>&1
echo "== restore_and_verify  run=$RUN_ID  $(date -u +%FT%TZ)"
echo "== jevaudit: $(pip show jevaudit 2>/dev/null | grep -i "^version")  ref=${JEVAUDIT_REF:-?}"
# 0. production ledger fingerprint (must never change)
( cd "$ROOT" && sha256sum raw/ledger/* ) > "$OUT/raw_ledger_sha_before.txt"
# 1. backup
cp -p ledger_original.jsonl.gz backup/ledger_original.jsonl.gz
ORIG_SHA=$(sha256sum ledger_original.jsonl.gz | cut -d' ' -f1)
echo "original ledger_original.jsonl.gz sha256=$ORIG_SHA"
python injector.py --orig backup/ledger_original.jsonl.gz --test NONE --out baseline --manifest baseline.manifest.json >/dev/null
( cd baseline && sha256sum chain_listener_*.jsonl.gz ) > "$OUT/baseline_partitions.sha"
cat "$OUT/baseline_partitions.sha"
VOID=0
: > "$OUT/results.jsonl"
for T in T1 T2 T3 T35 T36 T5; do
  FXID="FX-${T}-$(echo "$ORIG_SHA" | cut -c1-8)-${RUN_ID}"
  echo; echo "================ $T  fixture_id=$FXID"
  # 2. inject  (dirty-ledger/$T starts as a copy of the backup baseline, then the injector rewrites it)
  rm -rf "dirty-ledger/$T"; cp -rp baseline "dirty-ledger/$T"
  python injector.py --orig backup/ledger_original.jsonl.gz --test "$T" --out "dirty-ledger/$T.tmp" --manifest "dirty-ledger/$T.manifest.json" >/dev/null
  rm -f "dirty-ledger/$T"/*; cp -p "dirty-ledger/$T.tmp"/* "dirty-ledger/$T"/ 2>/dev/null; rm -rf "dirty-ledger/$T.tmp"
  ( cd "dirty-ledger/$T" && sha256sum chain_listener_*.jsonl.gz )
  MOD_SHA=$(jq -r .modified_content_sha256 "dirty-ledger/$T.manifest.json")
  # 3. reconcile (3 public RPC nodes, independent of the listener)
  python -m jevaudit.audit reconcile --ledger-glob "dirty-ledger/$T/chain_listener_*.jsonl*" \
     --wallets "$ROOT/listener/wallets.json" \
     --known-code-hash 68e29fd1e5973ded6efd7c8eab14501104450f81393521c9d273d816f7349b36 \
     --known-code-hash 0bd0618033e0ba07147406c3a4a63fff89999ca8bf47544aaeeb0bd88bd2eb99 \
     --from $LO --state fixture_state.json ${EXTRA_ARGS:-} --out-dir "reconcile/$T" > "reconcile/$T.stdout" 2>&1
  RC=$?; tail -30 "reconcile/$T.stdout"
  CHECK=$(python check_expect.py "$T" "reconcile/$T" "dirty-ledger/$T.manifest.json"); CRC=$?
  echo "$CHECK"
  # 4. restore from backup
  rm -rf "dirty-ledger/$T"; mkdir -p "dirty-ledger/$T"; cp -p baseline/* "dirty-ledger/$T"/
  ( cd "dirty-ledger/$T" && sha256sum chain_listener_*.jsonl.gz ) > "$OUT/$T.restored.sha"
  # 5. SHA256 compare: restored partitions == baseline, backup == original, original untouched
  R1=$(diff -q "$OUT/baseline_partitions.sha" "$OUT/$T.restored.sha" >/dev/null && echo 1 || echo 0)
  R2=$([ "$(sha256sum backup/ledger_original.jsonl.gz | cut -d' ' -f1)" = "$ORIG_SHA" ] && echo 1 || echo 0)
  R3=$([ "$(sha256sum ledger_original.jsonl.gz | cut -d' ' -f1)" = "$ORIG_SHA" ] && echo 1 || echo 0)
  RESTORED=$(cat "dirty-ledger/$T"/*.gz | gunzip | sha256sum | cut -d' ' -f1)
  R4=$([ "$RESTORED" = "$(jq -r .original_content_sha256 "dirty-ledger/$T.manifest.json")" ] && echo 1 || echo 0)
  if [ "$R1$R2$R3$R4" = "1111" ]; then RS=MATCH; else RS=MISMATCH; VOID=1; fi
  echo "restore: partitions==baseline:$R1 backup==orig:$R2 orig_untouched:$R3 content==orig:$R4 -> $RS (restored_content_sha256=$RESTORED)"
  jq -c --arg fx "$FXID" --arg o "$ORIG_SHA" --arg m "$MOD_SHA" --arg rs "$RS" --arg rc "$RC" --arg r "$RESTORED" \
     '. + {fixture_id:$fx, original_file_sha256:$o, modified_content_sha256:$m, restore:$rs, restored_content_sha256:$r, reconcile_exit:($rc|tonumber)}' <<<"$CHECK" >> "$OUT/results.jsonl"
done
echo; echo "================ T4 (mock RPC, listener v3)"
python t4_mock_rpc.py > "$OUT/T4_mock_rpc_output.txt" 2>&1; T4RC=$?
cat "$OUT/T4_mock_rpc_output.txt"
T4SHA=$(sha256sum t4_mock_rpc.py | cut -d' ' -f1)
jq -nc --arg p "$([ $T4RC = 0 ] && echo true || echo false)" --arg s "$T4SHA" --arg l "$(sha256sum "$ROOT/listener/listener.py"|cut -d' ' -f1)" \
  '{test:"T4", pass:($p=="true"), fixture_id:("FX-T4-mockrpc-"+env.RUN_ID), expected:"256 UNAVAILABLE, last_processed=255, pending_tip=256; round 2 writes 256, cursor 256", t4_script_sha256:$s, listener_sha256:$l, restore:"N/A (in-memory mock, temp dir)"}' >> "$OUT/results.jsonl"
( cd "$ROOT" && sha256sum raw/ledger/* ) > "$OUT/raw_ledger_sha_after.txt"
RAW_OK=$(diff -q "$OUT/raw_ledger_sha_before.txt" "$OUT/raw_ledger_sha_after.txt" >/dev/null && echo yes || echo NO)
echo; echo "================ SUMMARY"
jq -r '[.test, (if .pass then "PASS" else "FAIL" end), (.restore // "-")] | @tsv' "$OUT/results.jsonl"
NPASS=$(jq -s '[.[]|select(.pass)]|length' "$OUT/results.jsonl")
echo "raw/ledger unchanged: $RAW_OK"
if [ $VOID = 1 ]; then echo "RESTORE SHA256 MISMATCH -> WHOLE TEST RUN VOID"; exit 4; fi
echo "RESULT: $NPASS/7 passed; all restores SHA256-identical"
[ "$NPASS" = 7 ] && [ "$RAW_OK" = yes ]
