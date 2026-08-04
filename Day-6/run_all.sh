#!/usr/bin/env bash
# Day 6 — end-to-end integration test.
# Starts every local app built so far, runs the integrated scanner against
# each, records false positives, and proves the pipeline learns from them.
set -u
cd "$(dirname "$0")"

echo "================================================================"
echo "  DAY 6 — INTEGRATE EVERYTHING"
echo "================================================================"
echo "  targets: Day-1 HTTP server (:8000)  plain REST app"
echo "           Day-5 normal_api   (:8002)  REST, no AI"
echo "           Day-5 mock AI API  (:8001)  OpenAI-style LLM"
echo "================================================================"

rm -f false_positives.json   # fresh registry every run

python3 ../Day-5/mock_ai_server.py > /tmp/day6_mock_ai.log 2>&1 &
MOCK_PID=$!
python3 ../Day-5/normal_api.py > /tmp/day6_normal_api.log 2>&1 &
NORM_PID=$!
python3 ../Day-1/server.py > /tmp/day6_day1.log 2>&1 &
DAY1_PID=$!
trap 'kill $MOCK_PID $NORM_PID $DAY1_PID 2>/dev/null' EXIT

sleep 1.5

echo
echo "================================================================"
echo "  SCAN 1: Normal REST API (:8002) — low threshold exposes FPs"
echo "================================================================"
python3 integrate.py http://127.0.0.1:8002 --threshold 20 --report report_normal.json

echo
echo "================================================================"
echo "  SCAN 2: AI-like API (:8001)"
echo "================================================================"
python3 integrate.py http://127.0.0.1:8001 --report report_ai.json

echo
echo "================================================================"
echo "  SCAN 3: Day-1 plain HTTP server (:8000) — no AI expected"
echo "================================================================"
python3 integrate.py http://127.0.0.1:8000 --report report_day1.json

echo
echo "================================================================"
echo "  FALSE POSITIVE REGISTRY — what the pipeline recorded"
echo "================================================================"
python3 record_fp.py list
python3 record_fp.py stats

echo
echo "================================================================"
echo "  HUMAN CONFIRMS THE FALSE POSITIVES (pipeline learns)"
echo "================================================================"
python3 record_fp.py confirm --all --target http://127.0.0.1:8002 \
    --reason "manual review: plain REST endpoints, not LLMs"

echo
echo "================================================================"
echo "  SCAN 4: re-scan normal API — confirmed FPs must be suppressed"
echo "================================================================"
python3 integrate.py http://127.0.0.1:8002 --threshold 20 --report report_normal_v2.json

echo
echo "================================================================"
echo "  FINAL VERDICT"
echo "================================================================"
python3 - <<'EOF'
import json

def load(p):
    with open(p) as f:
        return json.load(f)

normal = load("report_normal.json")
v2 = load("report_normal_v2.json")
ai = load("report_ai.json")

print(f"    {'endpoint':<24} {'scan1':>10} {'scan4 (after learning)':>24}")
for r in normal["ai_detection"]:
    r2 = next(x for x in v2["ai_detection"] if x["endpoint"] == r["endpoint"])
    print(f"    {r['endpoint']:<24} {r['verdict']:>10} {r2['verdict']:>24}")

print()
ai_flags = ai["summary"]["ai_flags_before_fp_filter"]
print(f"    AI-like API : {ai_flags} AI endpoint(s) found, "
      f"{ai['summary']['ai_flags_after_fp_filter']} after FP filter")
print(f"    Normal API  : scan1 flagged {normal['summary']['ai_flags_after_fp_filter']} AI endpoint(s), "
      f"scan4 flagged {v2['summary']['ai_flags_after_fp_filter']} after suppressing "
      f"{v2['summary']['false_positives_suppressed']} confirmed false positives")
EOF
