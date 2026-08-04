#!/usr/bin/env bash
# Day 5 — test the AI Detector against two local APIs:
#   mock_ai_server.py (AI-like) on :8001
#   normal_api.py     (plain REST) on :8002
# The detector must distinguish them.
set -u
cd "$(dirname "$0")"

python3 mock_ai_server.py > /tmp/day5_mock_ai.log 2>&1 &
MOCK_PID=$!
python3 normal_api.py > /tmp/day5_normal_api.log 2>&1 &
NORM_PID=$!
trap 'kill $MOCK_PID $NORM_PID 2>/dev/null' EXIT

sleep 1

echo "================================================================"
echo "  TEST 1: AI-like API   -> http://127.0.0.1:8001"
echo "================================================================"
python3 ai_detector.py http://127.0.0.1:8001 ai_mock_report.json

echo
echo "================================================================"
echo "  TEST 2: Normal API    -> http://127.0.0.1:8002"
echo "================================================================"
python3 ai_detector.py http://127.0.0.1:8002 normal_report.json

echo
echo "================================================================"
echo "  FINAL VERDICT"
echo "================================================================"
python3 - <<'EOF'
import json

def load(p):
    with open(p) as f:
        return json.load(f)

mock = load("ai_mock_report.json")
normal = load("normal_report.json")

rows = []
for r in mock["endpoints"]:
    rows.append((r["endpoint"], "AI-like", f"{r['ai_confidence']}%", r["verdict"]))
for r in normal["endpoints"]:
    rows.append((r["endpoint"], "Normal", f"{r['ai_confidence']}%", r["verdict"]))

print(f"    {'Endpoint':<24} {'Source':<10} {'Score':>6}  Verdict")
for ep, src, score, verdict in rows:
    print(f"    {ep:<24} {src:<10} {score:>6}  {verdict}")

mock_ai = [r for r in mock["endpoints"] if r["verdict"] == "AI"]
normal_ai = [r for r in normal["endpoints"] if r["verdict"] == "AI"]
print()
print(f"    AI-like API: {len(mock_ai)}/{len(mock['endpoints'])} endpoints detected as AI")
print(f"    Normal API : {len(normal_ai)}/{len(normal['endpoints'])} endpoints detected as AI (false positives)")
EOF
