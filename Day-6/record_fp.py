#!/usr/bin/env python3
"""Day 6 — False Positive CLI.

Manage the false-positive registry recorded by integrate.py:

    python record_fp.py list [--status confirmed|candidate]
    python record_fp.py add --target <url> --endpoint <path> --reason "<why>"
                            [--status confirmed]
    python record_fp.py confirm <id> [--reason "<why>"]
    python record_fp.py confirm --all --target <url> [--reason "<why>"]
    python record_fp.py remove <id>
    python record_fp.py stats
"""

import argparse
import sys

import false_positives as fp


def main():
    parser = argparse.ArgumentParser(description="Manage the false-positive registry")
    sub = parser.add_subparsers(dest="command", required=True)

    p_list = sub.add_parser("list", help="list recorded false positives")
    p_list.add_argument("--status", choices=["candidate", "confirmed"])

    p_add = sub.add_parser("add", help="manually record a false positive")
    p_add.add_argument("--target", required=True)
    p_add.add_argument("--endpoint", required=True)
    p_add.add_argument("--reason", required=True)
    p_add.add_argument("--status", choices=["candidate", "confirmed"],
                       default="confirmed")

    p_confirm = sub.add_parser("confirm", help="confirm candidate(s) as false positives")
    p_confirm.add_argument("ids", nargs="*")
    p_confirm.add_argument("--all", action="store_true")
    p_confirm.add_argument("--target")
    p_confirm.add_argument("--reason")

    p_rm = sub.add_parser("remove", help="remove an entry")
    p_rm.add_argument("id")

    sub.add_parser("stats", help="registry summary")

    args = parser.parse_args()
    entries = fp.load_store()

    if args.command == "list":
        print(f"False positive registry: {fp.STORE_PATH}\n")
        fp.print_entries(entries, args.status)

    elif args.command == "add":
        entry_id = fp.add_candidate(
            entries, args.target, args.endpoint, 0, [],
            [args.reason], extra={"status": args.status,
                                  "reason": args.reason})
        if entry_id is None:
            print(f"[!] Endpoint already covered by a confirmed entry — skipped")
            sys.exit(1)
        fp.save_store(entries)
        print(f"[+] Recorded {args.endpoint} on {args.target} as "
              f"{args.status} ({entry_id})")

    elif args.command == "confirm":
        if args.all:
            if not args.target:
                print("[!] --all requires --target")
                sys.exit(1)
            ids = [e["id"] for e in entries
                   if e["status"] == "candidate"
                   and e.get("target") == args.target]
        else:
            ids = args.ids
        if not ids:
            print("[!] Nothing to confirm")
            sys.exit(1)
        for entry_id in ids:
            if fp.confirm(entries, entry_id, args.reason):
                print(f"[+] Confirmed {entry_id}")
            else:
                print(f"[!] Unknown id: {entry_id}")
        fp.save_store(entries)

    elif args.command == "remove":
        if fp.remove(entries, args.id):
            fp.save_store(entries)
            print(f"[+] Removed {args.id}")
        else:
            print(f"[!] Unknown id: {args.id}")
            sys.exit(1)

    elif args.command == "stats":
        s = fp.stats(entries)
        print(f"False positive registry: {fp.STORE_PATH}")
        for k, v in s.items():
            print(f"  {k:<12} {v}")


if __name__ == "__main__":
    main()
