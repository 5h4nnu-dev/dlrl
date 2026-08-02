#!/usr/bin/env python3
"""Browser Scanner — discovers API calls by driving a real browser.

Uses Playwright to open the target, monitor the network (fetch, XHR,
WebSockets, navigation), trigger interactions, and identify API endpoints
that static crawling would miss.
"""

import json
import re
import sys
import urllib.parse
from collections import OrderedDict
from urllib.parse import urlparse

from playwright.sync_api import sync_playwright

STATIC_EXTENSIONS = (
    ".js", ".css", ".png", ".jpg", ".jpeg", ".gif", ".svg", ".ico",
    ".woff", ".woff2", ".ttf", ".eot", ".webp", ".map", ".mp4", ".webm",
)

API_HINT = re.compile(r"/api/|/v\d+/|/rest|/graphql|/rpc|/admin|\.json$", re.I)


def classify(url: str, resource_type: str, mime: str) -> str:
    """Label a request as API, navigation, or static asset."""
    path = urlparse(url).path.lower()
    if resource_type in ("xhr", "fetch") or API_HINT.search(path):
        return "api"
    if resource_type == "document":
        return "nav"
    return "static"


def is_interesting_api(entry: dict) -> bool:
    url = entry["url"]
    path = urlparse(url).path
    if API_HINT.search(path):
        return True
    if entry.get("post_data"):
        return True
    if entry.get("response_content_type", ""):
        if entry.get("response_content_type", "").startswith("application/json"):
            return True
    if urlparse(url).hostname and urlparse(url).hostname != urlparse(url).netloc.split(":")[0]:
        return False
    return False


def scan(target: str, timeout_ms: int = 8000, interact: bool = True) -> dict:
    requests: OrderedDict[str, dict] = OrderedDict()
    js_files = set()

    def on_request(req):
        if req.resource_type == "document" or req.resource_type == "script":
            url = req.url
            if req.resource_type == "script" and not url.startswith("data:"):
                js_files.add(url)
        try:
            post_data = req.post_data
        except Exception:
            post_data = None
        key = f"{req.method} {req.url}"
        requests.setdefault(key, {
            "method": req.method,
            "url": req.url,
            "type": req.resource_type,
            "post_data": post_data,
            "status": None,
            "response_content_type": None,
        })

    def on_response(resp):
        key = f"{resp.request.method} {resp.request.url}"
        if key in requests:
            requests[key]["status"] = resp.status
            ct = resp.headers.get("content-type", "")
            requests[key]["response_content_type"] = ct

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=False)
        page = browser.new_page(viewport={"width": 1280, "height": 800})
        page.on("request", on_request)
        page.on("response", on_response)

        console_errors = []
        page.on("console", lambda msg: console_errors.append(msg.text)
                if msg.type == "error" else None)

        print(f"[1] Opening {target}...")
        try:
            page.goto(target, wait_until="load", timeout=timeout_ms)
            page.wait_for_timeout(2000)
            print(f"    Page title: {page.title()!r}")
        except Exception as e:
            print(f"    [!] Could not load page: {e}")

        if interact:
            print(f"[2] Interacting with page to trigger network calls...")
            _drive_page(page, timeout_ms=timeout_ms)

        browser.close()

    return {
        "requests": requests,
        "js_files": sorted(js_files),
        "console_errors": console_errors,
    }


def _drive_page(page, timeout_ms: int = 8000):
    actions = 0

    buttons = page.locator("button, input[type=submit], a[href], [role=button]")
    count = buttons.count()
    print(f"    Found {count} interactive elements")

    for i in range(count):
        el = buttons.nth(i)
        tag = el.evaluate("e => e.tagName.toLowerCase()")
        visible = el.is_visible()
        if not visible:
            continue
        try:
            text = (el.inner_text().strip().replace("\n", " ") or el.get_attribute("href") or el.get_attribute("value") or "")[:40]
            if tag in ("input", "button") and el.get_attribute("type") == "submit" and el.get_attribute("form"):
                pass
            el.click(timeout=3000)
            page.wait_for_timeout(800)
            actions += 1
            print(f"      clicked <{tag}> {text!r}")
        except Exception:
            continue

    for sel in ("input[type=text]", "input[type=search]", "input:not([type])", "textarea"):
        try:
            box = page.locator(sel).first
            if box.is_visible():
                box.fill("playwright test", timeout=3000)
                page.keyboard.press("Enter")
                page.wait_for_timeout(800)
                actions += 1
                print(f"      filled {sel!r} and pressed Enter")
        except Exception:
            continue

    print(f"    Performed {actions} actions")


def summarize(requests: dict) -> dict:
    entries = []
    for key, e in requests.items():
        label = classify(e["url"], e["type"], e.get("response_content_type") or "")
        entries.append({**e, "label": label})

    apis = [e for e in entries if e["label"] == "api"]
    interesting = [e for e in apis if is_interesting_api(e)]

    return {"all": entries, "apis": apis, "interesting": interesting}


def print_report(report: dict, raw: dict, summary: dict, target: str):
    print(f"[3] Network capture: {len(raw['requests'])} requests, "
          f"{len(summary['apis'])} API calls")

    print("\n[4] Identified API calls (fetch/XHR/JSON):")
    seen = set()
    for e in summary["interesting"]:
        if e["url"] in seen:
            continue
        seen.add(e["url"])
        status = e["status"] if e["status"] is not None else "?"
        print(f"    {e['method']:6s} {status!s:>4}  {e['url']}")
        if e.get("post_data"):
            print(f"            body: {e['post_data']}")

    print(f"\n[5] JS files loaded ({len(raw['js_files'])}):")
    for js in raw["js_files"][:10]:
        print(f"    {js}")
    if len(raw["js_files"]) > 10:
        print(f"    ... and {len(raw['js_files']) - 10} more")

    if raw["console_errors"]:
        print(f"\n[!] {len(raw['console_errors'])} console errors:")
        for err in raw["console_errors"][:5]:
            print(f"    {err}")


def main():
    if len(sys.argv) < 2:
        print("Usage: python scanner.py <url>")
        print("Example: python scanner.py http://127.0.0.1:8000")
        sys.exit(1)

    target = sys.argv[1]
    if not target.startswith("http"):
        target = "https://" + target

    print(f"[*] Browser scanner target: {target}\n")

    raw = scan(target)
    summary = summarize(raw["requests"])

    report = {
        "target": target,
        "method": "playwright-browser-network-monitoring",
        "total_requests": len(raw["requests"]),
        "api_calls": summary["apis"],
        "js_files": raw["js_files"],
        "console_errors": raw["console_errors"],
    }
    print_report(report, raw, summary, target)

    out = "browser_scan_report.json"
    with open(out, "w") as f:
        json.dump(report, f, indent=2)
    print(f"\n[*] Report saved to {out}")

    discovered = [e["url"] for e in summary["interesting"]]
    required = ["/api/chat", "/api/login", "/api/search"]
    print("\n[*] Verification against required endpoints:")
    for req in required:
        ok = any(req in u for u in discovered)
        print(f"    {'[OK]' if ok else '[--]'} {req}")


if __name__ == "__main__":
    main()
