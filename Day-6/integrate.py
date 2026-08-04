#!/usr/bin/env python3
"""Day 6 — INTEGRATE EVERYTHING.

Connects all five previous days into one pipeline:

    [1] Discovery   : Day-3 api_scanner (static crawl)  + Day-4 scanner (browser)
    [2] Detection   : Day-5 ai_detector probes every discovered endpoint
    [3] Headers     : Day-2 scanner security-header check
    [4] False pos.  : Day-6 registry — auto-record candidates, suppress confirmed

Everything rides on the Day-1 HTTP client/server fundamentals.

Usage:
    python integrate.py <url> [--browser] [--threshold 50] [--report out.json]
"""

import argparse
import contextlib
import importlib.util
import io
import json
import sys
import urllib.parse
from datetime import datetime, timezone
from pathlib import Path

import false_positives as fp

ROOT = Path(__file__).resolve().parents[1]

STATIC_EXTENSIONS = (
    ".js", ".css", ".png", ".jpg", ".jpeg", ".gif", ".svg", ".ico",
    ".woff", ".woff2", ".ttf", ".eot", ".webp", ".map", ".mp4", ".webm",
)
OPENAI_SHAPES = {"openai_chat_shape", "openai_text_shape", "openai_stream_shape"}


def load_module(name: str, rel_path: str):
    """Import a previous day's module directly by file path (no name clashes)."""
    path = ROOT / rel_path
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


day2_headers = load_module("day2_headers", "Day-2/scanner.py")
day3_api = load_module("day3_api", "Day-3/api_scanner.py")
day4_browser = load_module("day4_browser", "Day-4/scanner.py")
day5_ai = load_module("day5_ai", "Day-5/ai_detector.py")


# ── [1] Discovery ──────────────────────────────────────────────────────────

def discover(target: str, use_browser: bool) -> tuple[dict, list[dict]]:
    """Collect candidate endpoints from Day-3 static crawl (+ Day-4 browser)."""
    endpoints: dict[str, dict] = {}
    notes: list[dict] = []
    parsed = urllib.parse.urlparse(target)
    origin = f"{parsed.scheme}://{parsed.netloc}"

    def add(path: str, source: str, url: str):
        path = urllib.parse.urlparse(path).path or "/"
        if path != "/" and path.endswith(STATIC_EXTENSIONS):
            return
        if path not in endpoints:
            endpoints[path] = {"sources": set(), "url": url}
        endpoints[path]["sources"].add(source)

    # Day-3: probe common API paths
    print("[1] Discovery — Day-3 static crawl (api_scanner.py)")
    results = day3_api.try_endpoints(origin, day3_api.COMMON_PATHS)
    for r in results:
        if r["status"] != 404:
            add(r["endpoint"], "common-paths", r["endpoint"])
    notes.append({"phase": "common-paths", "probed": len(day3_api.COMMON_PATHS),
                  "live": len([r for r in results if r["status"] != 404])})

    # Day-3: mine the main page + JS files for API hints
    html, status = day3_api.fetch(target)
    if html:
        for u in day3_api.discover_from_html(html, target):
            add(u, "html", u)
        for js in day3_api.extract_js_references(html, target):
            for ep in day3_api.scan_js_for_endpoints(js):
                add(urllib.parse.urljoin(origin, ep), "js", js)
        notes.append({"phase": "html-js", "page_bytes": len(html)})

    # Day-5: the AI detector's own candidate paths — every one gets probed
    for path in day5_ai.CANDIDATES:
        add(path, "day5-candidates", urllib.parse.urljoin(origin, path))
    notes.append({"phase": "day5-candidates", "paths": len(day5_ai.CANDIDATES)})

    # Day-4: drive a real browser and capture network calls
    if use_browser:
        print("        + Day-4 browser network capture (scanner.py)")
        try:
            raw = day4_browser.scan(target)
            summary = day4_browser.summarize(raw["requests"])
            captured = 0
            for e in summary["interesting"]:
                u = urllib.parse.urlparse(e["url"])
                if u.hostname and u.hostname not in (parsed.hostname, None):
                    continue
                add(e["url"], "browser", e["url"])
                captured += 1
            notes.append({"phase": "browser", "requests": len(raw["requests"]),
                          "api_calls": captured})
            print(f"        Captured {captured} API calls from the browser")
        except Exception as exc:
            print(f"        [!] Browser phase skipped: {exc}")
            notes.append({"phase": "browser", "error": str(exc)})

    return endpoints, notes


# ── [2] AI detection ───────────────────────────────────────────────────────

def detect(base: str, endpoints: dict, threshold: int) -> list[dict]:
    print(f"\n[2] AI detection — Day-5 ai_detector.py  (threshold = {threshold})")
    results = []
    for i, path in enumerate(sorted(endpoints), 1):
        r = day5_ai.score_endpoint(base, path)
        r["sources"] = sorted(endpoints[path]["sources"])
        r["verdict"] = "AI" if r["ai_confidence"] >= threshold else "Not AI"
        r["threshold"] = threshold
        r["suppressed"] = False
        r["suppressed_by"] = None
        results.append(r)
        print(f"    [{i}/{len(endpoints)}] {path:24s} "
              f"{r['ai_confidence']:>3}%  {r['verdict']}")
    return results


# ── [3] Security headers (Day 2) ───────────────────────────────────────────

def scan_headers(target: str) -> str:
    print("\n[3] Security headers — Day-2 scanner.py")
    buffer = io.StringIO()
    with contextlib.redirect_stdout(buffer):
        day2_headers.scan(target)
    output = buffer.getvalue().strip()
    for line in output.splitlines():
        print(f"    {line}")
    return output


# ── [4] False positives (Day 6) ────────────────────────────────────────────

def analyze_fp_risk(result: dict) -> list[str]:
    names = {s["signal"] for s in result["signals"]}
    response_evidence = bool(names & OPENAI_SHAPES) or (
        "ws_upgraded" in names and "ws_reply" in names)
    reasons = []
    if "echo" in names:
        reasons.append("echo reply: server parrots the probe back (rule-based, not an LLM)")
    if "deterministic" in names and not response_evidence:
        reasons.append("deterministic responses with no AI response evidence")
    if "stream_ignored" in names and not response_evidence:
        reasons.append("stream:true ignored — plain JSON, no AI response evidence")
    if result["verdict"] == "AI" and not response_evidence:
        reasons.append("flagged on request-side hints only, no response evidence")
    return reasons


def handle_false_positives(results: list[dict], target: str,
                           record: bool) -> tuple[list[str], list[str]]:
    print("\n[4] False positives — Day-6 registry (false_positives.json)")
    store = fp.load_store()
    recorded, suppressed = [], []

    for r in results:
        reasons = analyze_fp_risk(r)
        r["fp_risk"] = reasons
        names = {s["signal"] for s in r["signals"]}
        response_evidence = bool(names & OPENAI_SHAPES) or (
            "ws_upgraded" in names and "ws_reply" in names)
        heuristic_only = bool(reasons) and not response_evidence
        is_candidate = bool(reasons) and (r["verdict"] == "AI" or heuristic_only)

        # auto-record candidates (machine flags, human confirms later)
        if is_candidate and record:
            entry_id = fp.add_candidate(
                store, target, r["endpoint"], r["ai_confidence"],
                [s["signal"] for s in r["signals"]], reasons)
            if entry_id:
                recorded.append((entry_id, r["endpoint"], reasons))
                print(f"    [candidate] {entry_id} {r['endpoint']}")
                for reason in reasons:
                    print(f"               - {reason}")

        # confirmed entries suppress the AI verdict (the pipeline learns)
        confirmed = [e for e in fp.find(store, target, r["endpoint"])
                     if e["status"] == "confirmed"]
        if confirmed:
            entry = confirmed[0]
            r["suppressed"] = True
            r["suppressed_by"] = entry["id"]
            r["verdict"] = "Not AI"
            suppressed.append((entry["id"], r["endpoint"]))
            print(f"    [suppressed] {entry['id']} {r['endpoint']} "
                  f"(confirmed false positive — verdict forced to Not AI)")

    fp.save_store(store)
    if not recorded and not suppressed:
        print("    No false positives this run")
    return recorded, suppressed


# ── Report / summary ───────────────────────────────────────────────────────

def build_report(target, endpoints, notes, results, headers_out,
                 recorded, suppressed, threshold) -> dict:
    ai_before = sum(1 for r in results
                    if r["ai_confidence"] >= threshold)
    ai_final = sum(1 for r in results if r["verdict"] == "AI")
    live = sum(1 for r in results if r["alive"])
    return {
        "day": 6,
        "target": target,
        "generated": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "pipeline": [
            "Day-1 HTTP fundamentals (transport)",
            "Day-3 api_scanner (static discovery)",
            "Day-4 scanner (browser network capture)",
            "Day-5 ai_detector (AI classification)",
            "Day-2 scanner (security headers)",
            "Day-6 false positive registry",
        ] if any(n.get("phase") == "browser" for n in notes) else [
            "Day-1 HTTP fundamentals (transport)",
            "Day-3 api_scanner (static discovery)",
            "Day-5 ai_detector (AI classification)",
            "Day-2 scanner (security headers)",
            "Day-6 false positive registry",
        ],
        "threshold": threshold,
        "discovery": {
            "endpoints": sorted(endpoints),
            "notes": notes,
        },
        "ai_detection": [
            {k: r[k] for k in (
                "endpoint", "status", "alive", "ai_confidence", "type",
                "streaming", "verdict", "sources", "fp_risk",
                "suppressed", "suppressed_by", "signals", "evidence")}
            for r in results
        ],
        "headers": headers_out,
        "false_positives": {
            "recorded_candidates": [
                {"id": i, "endpoint": e, "reasons": r}
                for i, e, r in recorded
            ],
            "suppressed": [
                {"id": i, "endpoint": e} for i, e in suppressed
            ],
            "registry_path": str(fp.STORE_PATH),
        },
        "summary": {
            "paths_probed": len(endpoints),
            "endpoints_found": live,
            "ai_flags_before_fp_filter": ai_before,
            "false_positives_suppressed": len(suppressed),
            "ai_flags_after_fp_filter": ai_final,
        },
    }


def main():
    parser = argparse.ArgumentParser(description="Day 6 — integrated AI-API scanner")
    parser.add_argument("url")
    parser.add_argument("--browser", action="store_true",
                        help="include Day-4 browser network capture")
    parser.add_argument("--threshold", type=int, default=50,
                        help="AI confidence threshold (default 50)")
    parser.add_argument("--report", default="integrated_report.json")
    parser.add_argument("--no-record", action="store_true",
                        help="don't auto-record FP candidates")
    args = parser.parse_args()

    target = args.url if args.url.startswith("http") else "https://" + args.url
    base = target.rstrip("/")

    print("=" * 64)
    print("  DAY 6 — INTEGRATED AI-API SCANNER")
    print("  pipeline: discovery -> detection -> headers -> false positives")
    print("  transport: HTTP/1.1 (Day 1 fundamentals)")
    print("=" * 64)
    print(f"[*] Target: {target}")

    endpoints, notes = discover(target, args.browser)
    print(f"    {len(endpoints)} unique endpoint(s) to probe")

    results = detect(base, endpoints, args.threshold)

    headers_out = scan_headers(target)

    recorded, suppressed = handle_false_positives(
        results, target, record=not args.no_record)

    report = build_report(target, endpoints, notes, results, headers_out,
                          recorded, suppressed, args.threshold)
    with open(args.report, "w") as f:
        json.dump(report, f, indent=2)

    s = report["summary"]
    print("\n" + "-" * 64)
    print("  SUMMARY")
    print(f"    paths probed             : {s['paths_probed']}")
    print(f"    live endpoints found     : {s['endpoints_found']}")
    print(f"    AI flags (raw detection) : {s['ai_flags_before_fp_filter']}")
    print(f"    suppressed false positives: {s['false_positives_suppressed']}")
    print(f"    AI endpoints (final)     : {s['ai_flags_after_fp_filter']}")
    print(f"    candidates recorded       : {len(recorded)} "
          f"(confirm with: python record_fp.py confirm --all --target {target})")
    print(f"[*] Report saved to {args.report}")


if __name__ == "__main__":
    main()
