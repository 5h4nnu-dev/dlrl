#!/usr/bin/env python3
"""API Scanner — discovers API endpoints from a target URL."""

import sys
import urllib.parse
from urllib.request import urlopen, Request
from urllib.error import URLError, HTTPError
import json
import re
import os

COMMON_PATHS = [
    "/api",
    "/api/v1",
    "/api/chat",
    "/api/users",
    "/api/v2",
    "/v1",
    "/v2",
    "/swagger.json",
    "/api-docs",
    "/openapi.json",
    "/api/auth",
    "/api/login",
    "/api/logout",
    "/api/register",
    "/api/health",
    "/graphql",
    "/rest",
    "/api/admin",
    "/api/status",
]


def fetch(url: str, timeout: int = 10) -> tuple[str | None, int | None]:
    try:
        req = Request(url, headers={"User-Agent": "APIScanner/1.0"})
        with urlopen(req, timeout=timeout) as resp:
            body = resp.read().decode("utf-8", errors="replace")
            return body, resp.status
    except HTTPError as e:
        body = e.read().decode("utf-8", errors="replace") if e.fp else ""
        return body, e.code
    except Exception as e:
        print(f"  [!] Error fetching {url}: {e}")
        return None, None


def extract_links_from_html(html: str, base_url: str) -> set[str]:
    links = set()
    for m in re.finditer(
        r'(?:href|src|action)\s*=\s*["\']([^"\']+)["\']', html, re.I
    ):
        raw = m.group(1)
        resolved = urllib.parse.urljoin(base_url, raw)
        links.add(resolved)
    return links


def extract_js_references(html: str, base_url: str) -> set[str]:
    refs = set()
    for m in re.finditer(
        r'<script[^>]*src\s*=\s*["\']([^"\']+)["\']', html, re.I
    ):
        raw = m.group(1)
        resolved = urllib.parse.urljoin(base_url, raw)
        refs.add(resolved)
    return refs


def try_endpoints(base_url: str, paths: list[str]) -> list[dict]:
    results = []
    parsed = urllib.parse.urlparse(base_url)
    origin = f"{parsed.scheme}://{parsed.netloc}"

    for path in paths:
        url = urllib.parse.urljoin(origin, path)
        body, status = fetch(url, timeout=5)
        if status is not None:
            info = {
                "endpoint": url,
                "status": status,
                "note": "",
            }
            if status != 404:
                if body:
                    try:
                        json.loads(body)
                        info["note"] = "Responds with JSON"
                    except json.JSONDecodeError:
                        info["note"] = "Non-JSON response"
                if status < 300:
                    info["note"] = "Likely accessible"
            results.append(info)
            print(
                f"  [{'OK' if status != 404 else '--'}] {url:50s} {status}"
                + (f" — {info['note']}" if info["note"] else "")
            )
    return results


def discover_from_html(html: str, base_url: str) -> set[str]:
    all_urls = extract_links_from_html(html, base_url)
    all_urls |= extract_js_references(html, base_url)

    api_candidates = set()
    for u in all_urls:
        path = urllib.parse.urlparse(u).path.lower()
        if re.search(r"/api/|/v\d+/|/rest|/graphql|\.json$|/swagger", path):
            api_candidates.add(u)
    return api_candidates


def scan_js_for_endpoints(js_url: str) -> list[str]:
    body, _ = fetch(js_url, timeout=5)
    if not body:
        return []
    found = set()
    for m in re.finditer(r'["\'](/[a-zA-Z0-9_\-/.?&=]+)["\']', body):
        path = m.group(1)
        if re.search(r"/api/|/v\d+/|/rest|/graphql", path, re.I):
            found.add(path)
    return sorted(found)


def main():
    if len(sys.argv) < 2:
        print("Usage: python api_scanner.py <url>")
        print("Example: python api_scanner.py https://example.com")
        sys.exit(1)

    target = sys.argv[1]
    if not target.startswith("http"):
        target = "https://" + target

    print(f"[*] Target: {target}")
    print()

    parsed = urllib.parse.urlparse(target)
    origin = f"{parsed.scheme}://{parsed.netloc}"

    # ── Step 1: Fetch the main page ──
    print("[1] Fetching main page...")
    html, status = fetch(target)
    if html:
        print(f"    Status {status}, {len(html)} bytes")

        links = extract_links_from_html(html, target)
        js_refs = extract_js_references(html, target)
        print(f"    Found {len(links)} links, {len(js_refs)} JS files")
        print()

        # ── Step 2: Discover API hints in HTML ──
        print("[2] Discovering API hints in HTML/JS references...")
        candidates = discover_from_html(html, target)
        for c in sorted(candidates):
            print(f"    {c}")
        print()

        # ── Step 3: Scan JS files for embedded endpoints ──
        print("[3] Scanning JavaScript files for API paths...")
        for js in sorted(js_refs):
            endpoints = scan_js_for_endpoints(js)
            if endpoints:
                print(f"    {js}")
                for ep in endpoints[:10]:
                    print(f"      └ {ep}")
        print()
    else:
        print(f"    Could not reach {target}")
        print()

    # ── Step 4: Probe common API paths ──
    print("[4] Probing common API paths...")
    print()
    results = try_endpoints(target, COMMON_PATHS)

    # ── Summary ──
    live = [r for r in results if r["status"] not in (404,)]
    print()
    print(f"[+] Done — found {len(live)} live endpoints")
    for r in live:
        print(f"    {r['status']:3d} {r['endpoint']} {r['note']}")

    # Save report
    report = {"target": target, "endpoints": results}
    out = "api_scan_report.json"
    with open(out, "w") as f:
        json.dump(report, f, indent=2)
    print(f"\n[*] Report saved to {out}")


if __name__ == "__main__":
    main()
