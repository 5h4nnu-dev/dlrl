import requests
import sys

HEADERS_TO_CHECK = {
    "Content-Security-Policy": "CSP",
    "X-Frame-Options": "X-Frame",
    "X-Content-Type-Options": "X-Content-Type",
    "Strict-Transport-Security": "HSTS",
}


def scan(target):
    try:
        resp = requests.get(target, timeout=10)
    except requests.exceptions.RequestException as e:
        print(f"Error: {e}")
        sys.exit(1)

    print("Security Headers")
    print("----------------")
    for header, label in HEADERS_TO_CHECK.items():
        status = "Present" if header in resp.headers else "Missing"
        print(f"{label}: {status}")


if __name__ == "__main__":
    if len(sys.argv) != 2:
        print("Usage: python scanner.py <url>")
        sys.exit(1)
    scan(sys.argv[1])
