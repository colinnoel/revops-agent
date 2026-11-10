"""Checks the live org against the demo-data answer key.

Needs the demo data loaded with:  python3 scripts/demo-data/generate.py --as-of 2026-11-10
The answer key is regenerated here (deterministic, nothing is loaded) so it always matches the generator.
"""
import csv
import datetime as dt
import os
import subprocess
import sys

import pytest

import risk
from salesforce import Salesforce

AS_OF = dt.date(2026, 11, 10)
ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))


@pytest.fixture(scope="module")
def key():
    # The generator runs on the system Python (3.9+) and needs no extra packages
    subprocess.run(["python3", os.path.join(ROOT, "scripts/demo-data/generate.py"), "--no-load",
                    "--as-of", AS_OF.isoformat()], check=True, capture_output=True)
    with open(os.path.join(ROOT, "scripts/demo-data/out/answer_key.csv")) as f:
        return {r["renewal_id"]: r for r in csv.DictReader(f)}


@pytest.fixture(scope="module")
def sf():
    return Salesforce(os.environ.get("REVOPS_ORG", "revops-demo"))


@pytest.fixture(scope="module")
def all_open(sf):
    return {r["demo_id"]: r for r in risk.assess_renewals(sf, "all_open", AS_OF)["renewals"]}


def test_same_renewals(key, all_open):
    assert set(all_open) == set(key)


def test_signals_scores_and_bands_match(key, all_open):
    wrong = []
    for rid, k in key.items():
        got = all_open[rid]
        got_codes = {s["code"] for s in got["signals"]}
        want_codes = set(filter(None, k["signals"].split(";")))
        if got_codes != want_codes or got["score"] != int(k["score"]) or got["band"] != k["band"]:
            wrong.append((rid, k["account"], sorted(want_codes), sorted(got_codes)))
    assert not wrong, wrong


def test_this_quarter_scope_and_arr(sf, key):
    res = risk.assess_renewals(sf, "this_quarter", AS_OF)
    want = {rid for rid, k in key.items() if k["in_scope_this_quarter"] == "yes"}
    assert {r["demo_id"] for r in res["renewals"]} == want
    want_at_risk_arr = sum(float(key[r]["prior_arr"]) for r in want if key[r]["band"] == "At Risk")
    assert res["summary"]["at_risk"]["prior_arr"] == pytest.approx(want_at_risk_arr)


def test_rep_vs_evidence_flags(sf, key):
    res = risk.assess_renewals(sf, "all_open", AS_OF)
    for r in res["renewals"]:
        scenario = key[r["demo_id"]]["scenario"]
        if scenario == "hidden":
            assert r["rep_vs_evidence"] and r["rep_vs_evidence"].startswith("hidden_risk"), r["account"]
        if scenario == "false_alarm" and r["band"] == "Healthy":
            assert r["rep_vs_evidence"] and r["rep_vs_evidence"].startswith("unsupported_flag"), r["account"]


def test_detail_lookup_by_account_name(sf):
    d = risk.renewal_detail(sf, "Orchard", AS_OF)
    assert d.get("band") == "At Risk", d
    assert d["evidence"]["open_cases"], "expected open cases as evidence"
