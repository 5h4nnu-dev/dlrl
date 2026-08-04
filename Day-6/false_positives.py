#!/usr/bin/env python3
"""Day 6 — False Positive Registry.

Endpoints the AI detector flagged as AI, but a human confirmed are NOT AI.

Two-stage lifecycle:
    candidate  -> auto-recorded by the pipeline when it finds suspicious flags
    confirmed  -> promoted by a human (record_fp.py confirm) — future scans
                  suppress the AI verdict for this endpoint (the pipeline learns)

Store file: false_positives.json (next to this module)
"""

import json
import re
import urllib.parse
from datetime import datetime, timezone
from pathlib import Path

STORE_PATH = Path(__file__).resolve().parent / "false_positives.json"


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def load_store() -> list[dict]:
    if not STORE_PATH.exists():
        return []
    try:
        entries = json.loads(STORE_PATH.read_text())
        return entries if isinstance(entries, list) else []
    except json.JSONDecodeError:
        return []


def save_store(entries: list[dict]) -> None:
    STORE_PATH.write_text(json.dumps(entries, indent=2) + "\n")


def next_id(entries: list[dict]) -> str:
    nums = [int(e["id"].split("-")[1]) for e in entries
            if re.fullmatch(r"fp-\d+", e.get("id", ""))]
    return f"fp-{max(nums, default=0) + 1:03d}"


def host_of(url: str) -> str | None:
    """Normalize a URL to host[:port], or None for wildcard/empty."""
    if not url or url == "*":
        return None
    parsed = urllib.parse.urlparse(url if "://" in url else "http://" + url)
    if not parsed.hostname:
        return None
    return f"{parsed.hostname}:{parsed.port}" if parsed.port else parsed.hostname


def find(entries: list[dict], target: str, endpoint: str) -> list[dict]:
    """All entries for a host+endpoint pair (host '*' acts as a wildcard)."""
    host = host_of(target)
    return [e for e in entries
            if e.get("endpoint") == endpoint
            and host_of(e.get("target", "")) in (host, None)]


def add_candidate(entries: list[dict], target: str, endpoint: str,
                  confidence: int, signals: list[str], reasons: list[str],
                  extra: dict | None = None) -> str | None:
    """Auto-record (or refresh) a candidate FP. Returns id, or None if a
    confirmed entry already covers this endpoint (never demote a confirmed FP)."""
    if any(e["status"] == "confirmed" for e in find(entries, target, endpoint)):
        return None

    for e in entries:
        if (e.get("status") == "candidate"
                and e.get("target") == target
                and e.get("endpoint") == endpoint):
            e["confidence"] = confidence
            e["signals"] = signals
            e["reasons"] = reasons
            e["reason"] = "; ".join(reasons)
            e["last_seen"] = now_iso()
            e.update(extra or {})
            return e["id"]

    entry = {
        "id": next_id(entries),
        "target": target,
        "endpoint": endpoint,
        "status": "candidate",
        "reason": "; ".join(reasons),
        "reasons": reasons,
        "confidence": confidence,
        "signals": signals,
        "created": now_iso(),
        "last_seen": now_iso(),
        "confirmed_at": None,
    }
    entry.update(extra or {})
    entries.append(entry)
    return entry["id"]


def confirm(entries: list[dict], entry_id: str, reason: str | None = None) -> bool:
    for e in entries:
        if e.get("id") == entry_id:
            e["status"] = "confirmed"
            if reason:
                e["reason"] = reason
            e["confirmed_at"] = now_iso()
            return True
    return False


def remove(entries: list[dict], entry_id: str) -> bool:
    before = len(entries)
    entries[:] = [e for e in entries if e.get("id") != entry_id]
    return len(entries) != before


def stats(entries: list[dict]) -> dict:
    return {
        "total": len(entries),
        "confirmed": sum(1 for e in entries if e["status"] == "confirmed"),
        "candidates": sum(1 for e in entries if e["status"] == "candidate"),
        "hosts": sorted({host_of(e.get("target", "")) or "*"
                         for e in entries}),
    }


def print_entries(entries: list[dict], status: str | None = None) -> None:
    rows = [e for e in entries if status is None or e["status"] == status]
    if not rows:
        print("  (none)")
        return
    print(f"  {'ID':<7} {'STATUS':<10} {'TARGET':<24} {'ENDPOINT':<22} {'CONF':>5}  REASON")
    for e in rows:
        print(f"  {e['id']:<7} {e['status']:<10} "
              f"{host_of(e.get('target', '')) or '*':<24} {e['endpoint']:<22} "
              f"{e.get('confidence', 0):>4}%  {e.get('reason', '')[:60]}")
