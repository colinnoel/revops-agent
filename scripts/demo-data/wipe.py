#!/usr/bin/env python3
"""
Delete every record tagged Data_Source__c = 'Demo Data' (Opportunities, Contacts, Accounts).

  python3 scripts/demo-data/wipe.py               # asks for confirmation
  python3 scripts/demo-data/wipe.py --yes         # no prompt
  python3 scripts/demo-data/wipe.py --target-org other-alias
"""
import argparse
import csv
import io
import json
import os
import subprocess
import sys
import tempfile

ORDER = ["Opportunity", "Contact", "Account"]  # children first


def query_ids(obj, org):
    res = subprocess.run(["sf", "data", "query", "--query",
                          f"SELECT Id FROM {obj} WHERE Data_Source__c = 'Demo Data'",
                          "--target-org", org, "--json"], text=True, capture_output=True)
    data = json.loads(res.stdout)
    if data.get("status") != 0:
        sys.exit(f"Query failed for {obj}: {data.get('message')}")
    return [r["Id"] for r in data["result"]["records"]]


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--target-org", default="revops-demo")
    p.add_argument("--yes", action="store_true")
    args = p.parse_args()

    found = {o: query_ids(o, args.target_org) for o in ORDER}
    print("Tagged records:", ", ".join(f"{o}={len(v)}" for o, v in found.items()))
    if not any(found.values()):
        print("Nothing to wipe.")
        return
    if not args.yes and input(f"Delete these from {args.target_org}? [y/N] ").lower() != "y":
        sys.exit("Aborted.")
    for obj in ORDER:
        ids = query_ids(obj, args.target_org)  # re-query: cascades may have removed some
        if not ids:
            continue
        with tempfile.NamedTemporaryFile("w", suffix=".csv", delete=False, newline="") as f:
            f.write("Id\n" + "\n".join(ids) + "\n")
        print(f"Deleting {len(ids)} {obj} records...", flush=True)
        res = subprocess.run(["sf", "data", "delete", "bulk", "--sobject", obj, "--file", f.name,
                              "--target-org", args.target_org, "--wait", "15"], text=True, capture_output=True)
        os.unlink(f.name)
        if res.returncode != 0:
            print(res.stdout, res.stderr)
            sys.exit(f"Delete failed for {obj}")
    print("Wipe complete.")


if __name__ == "__main__":
    main()
