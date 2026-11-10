#!/usr/bin/env python3
"""
Generate and load a realistic B2B SaaS demo dataset into a Salesforce org.

  python3 scripts/demo-data/generate.py                 # wipe tagged data, regenerate, load
  python3 scripts/demo-data/generate.py --no-load       # only write CSVs
  python3 scripts/demo-data/generate.py --seed 7        # different (still deterministic) data
  python3 scripts/demo-data/generate.py --target-org other-alias
  python3 scripts/demo-data/generate.py --as-of 2026-11-10

Every record gets Data_Source__c = "Demo Data" and a stable Demo_External_Id__c
(DEMO-ACC-001, DEMO-CON-001-1, DEMO-OPP-0001, DEMO-REN-0001 ...). Loads are
upserts on that external id. Use wipe.py to delete everything tagged.

Requires the metadata in force-app/ (deploy with `sf project deploy start`)
and the RevOps_Demo_Data permission set assigned to the loading user.
"""
import argparse
import csv
import json
import datetime as dt
import os
import random
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, "out")
TAG = "Demo Data"

SEGMENTS = {
    # name: (share of accounts, ARR range, employees range, contacts range, opps/account weight)
    "SMB":         (0.40, (8_000, 40_000),    (20, 200),        (2, 3), 1.6),
    "Mid-Market":  (0.36, (40_000, 160_000),  (200, 2_000),     (3, 4), 2.5),
    "Enterprise":  (0.24, (160_000, 650_000), (2_000, 40_000),  (4, 6), 3.4),
}
OPEN_STAGES = [  # (stage, weight)
    ("Prospecting", 6), ("Qualification", 7), ("Needs Analysis", 6), ("Value Proposition", 5),
    ("Id. Decision Makers", 4), ("Perception Analysis", 3), ("Proposal/Price Quote", 5),
    ("Negotiation/Review", 4),
]
STAGE_ORDER = [s for s, _ in OPEN_STAGES]

NAME_A = ["Aster", "Bluefin", "Cobalt", "Driftwood", "Ember", "Fernhill", "Granite", "Harbor", "Ironleaf",
          "Juniper", "Kestrel", "Lumen", "Meridian", "Northpass", "Oakridge", "Pinecrest", "Quarry", "Redwater",
          "Saltmarsh", "Tidewell", "Upland", "Vantage", "Willowby", "Yellowpine", "Zephyr", "Brightpath",
          "Clearwater", "Duskline", "Evergale", "Foxglove", "Goldcrest", "Highmark", "Ivory Gate", "Lakeshore",
          "Moonridge", "Nettlefield", "Orchard", "Pebblebrook", "Riverbend", "Stonebridge", "Thornbury",
          "Silverline", "Copperfield", "Waypoint", "Halcyon", "Summit Ash", "Bramble", "Cinder", "Larkspur", "Mistral"]
NAME_B = {
    "Technology": ["Software", "Systems", "Labs", "Cloud"], "Healthcare": ["Health", "Medical Group", "Care Partners"],
    "Finance": ["Capital", "Financial", "Lending"], "Manufacturing": ["Manufacturing", "Industries", "Fabrication"],
    "Retail": ["Retail Co", "Outfitters", "Goods"], "Transportation": ["Logistics", "Freight", "Transit"],
    "Education": ["Learning", "Academy"], "Insurance": ["Insurance", "Mutual"], "Media": ["Media", "Studios"],
    "Energy": ["Energy", "Power"], "Telecommunications": ["Networks", "Telecom"], "Consulting": ["Advisors", "Consulting"],
}
INDUSTRY_W = {"Technology": 9, "Healthcare": 5, "Finance": 5, "Manufacturing": 5, "Retail": 4, "Transportation": 3,
              "Education": 2, "Insurance": 3, "Media": 2, "Energy": 2, "Telecommunications": 2, "Consulting": 3}
GEOS = [("San Francisco", "CA", "US"), ("Austin", "TX", "US"), ("New York", "NY", "US"), ("Chicago", "IL", "US"),
        ("Boston", "MA", "US"), ("Seattle", "WA", "US"), ("Denver", "CO", "US"), ("Atlanta", "GA", "US"),
        ("Toronto", "ON", "CA"), ("London", "", "GB"), ("Dublin", "", "IE"), ("Raleigh", "NC", "US")]
FIRST = ["Ava", "Ben", "Carmen", "Dev", "Elena", "Femi", "Grace", "Hiro", "Isla", "Jamal", "Kira", "Liam", "Maya",
         "Noah", "Olivia", "Priya", "Quinn", "Rafael", "Sofia", "Theo", "Uma", "Victor", "Wen", "Ximena", "Yusuf",
         "Zoe", "Aaron", "Bianca", "Caleb", "Dana", "Ethan", "Farah", "Gabe", "Hannah", "Ivan", "Jade", "Kofi", "Lena"]
LAST = ["Alvarez", "Brooks", "Chen", "Dubois", "Ellis", "Fischer", "Garcia", "Hughes", "Ibrahim", "Jensen", "Kim",
        "Lopez", "Morales", "Nakamura", "Okafor", "Patel", "Reyes", "Schmidt", "Takahashi", "Underwood", "Varga",
        "Walsh", "Xu", "Young", "Zimmerman", "Bennett", "Castillo", "Duarte", "Novak", "Osei", "Price", "Rossi"]
TITLES = [("VP of Sales", "Economic Buyer"), ("Chief Revenue Officer", "Economic Buyer"),
          ("Director of Revenue Operations", "Champion"), ("Sales Operations Manager", "Champion"),
          ("CFO", "Economic Buyer"), ("IT Director", "Technical Evaluator"), ("Head of Procurement", "Procurement"),
          ("Senior Sales Ops Analyst", "End User"), ("VP of Customer Success", "Influencer"),
          ("Security Architect", "Technical Evaluator")]
PRODUCTS = ["Platform", "Forecasting Add-on", "Analytics Suite", "Data Connectors", "Enterprise Bundle"]
COMPETITORS = ["Incumbent spreadsheet process", "Competitor A", "Competitor B", "In-house build", "Competitor C"]
LOSS_REASONS = ["Lost to competitor on price", "No decision / status quo", "Budget frozen", "Champion left company",
                "Missing integration requirement", "Timing - revisit next fiscal year"]
LEAD_SOURCES = [("Web", 4), ("Partner Referral", 2), ("Phone Inquiry", 1), ("Other", 2), ("Purchased List", 1)]


def wchoice(rng, pairs):
    return rng.choices([p for p, _ in pairs], weights=[w for _, w in pairs])[0]


def add_months(d, n):
    y, m = divmod(d.month - 1 + n, 12)
    y += d.year
    m += 1
    days = [31, 29 if y % 4 == 0 and (y % 100 or y % 400 == 0) else 28, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31][m - 1]
    return dt.date(y, m, min(d.day, days))


def round_amt(x):
    return int(round(x / 500.0) * 500)


def build(seed, today):
    rng = random.Random(seed)
    window_start = add_months(today, -18)
    accounts, contacts, opps, renewals = [], [], [], []

    # ---------- Accounts ----------
    seg_list = []
    for seg, (share, *_rest) in SEGMENTS.items():
        seg_list += [seg] * round(50 * share)
    seg_list = (seg_list + ["SMB"] * 50)[:50]
    rng.shuffle(seg_list)
    used = set()
    for i, seg in enumerate(seg_list, 1):
        ind = rng.choices(list(INDUSTRY_W), weights=list(INDUSTRY_W.values()))[0]
        while True:
            name = f"{rng.choice(NAME_A)} {rng.choice(NAME_B[ind])}"
            if name not in used:
                used.add(name)
                break
        _, arr_rng, emp_rng, con_rng, opp_w = SEGMENTS[seg]
        city, state, country = rng.choice(GEOS)
        emp = rng.randint(*emp_rng)
        slug = name.lower().replace(" ", "")
        accounts.append({
            "Demo_External_Id__c": f"DEMO-ACC-{i:03d}", "Name": name, "Segment__c": seg, "Industry": ind,
            "NumberOfEmployees": emp, "AnnualRevenue": round_amt(emp * rng.uniform(120_000, 350_000)),
            "BillingCity": city, "BillingStateCode": state, "BillingCountryCode": country,
            "Website": f"https://www.{slug}.example.com", "Phone": f"+1-555-{rng.randint(100,999)}-{rng.randint(1000,9999)}",
            "Type": "Prospect", "Rating": rng.choice(["Hot", "Warm", "Cold"]), "Data_Source__c": TAG,
            "_slug": slug, "_arr": arr_rng, "_opp_w": opp_w, "_contacts": [],
        })

    # ---------- Contacts ----------
    for a in accounts:
        lo, hi = SEGMENTS[a["Segment__c"]][3]
        titles = rng.sample(TITLES, rng.randint(lo, hi))
        for j, (title, role) in enumerate(titles, 1):
            fn, ln = rng.choice(FIRST), rng.choice(LAST)
            cid = f"DEMO-CON-{a['Demo_External_Id__c'][-3:]}-{j}"
            contacts.append({
                "Demo_External_Id__c": cid, "Account.Demo_External_Id__c": a["Demo_External_Id__c"],
                "FirstName": fn, "LastName": ln, "Title": title,
                "Email": f"{fn.lower()}.{ln.lower()}@{a['_slug']}.example.com",
                "Phone": f"+1-555-{rng.randint(100,999)}-{rng.randint(1000,9999)}",
                "LeadSource": wchoice(rng, LEAD_SOURCES), "Data_Source__c": TAG, "_role": role,
            })
            a["_contacts"].append((cid, role))

    # ---------- New business + expansion opportunities (~120) ----------
    target = 120
    weights = [a["_opp_w"] for a in accounts]
    counts = [1] * len(accounts)  # every account gets at least one
    while sum(counts) < target:
        counts[rng.choices(range(len(accounts)), weights=weights)[0]] += 1

    n = 0
    span = (today - window_start).days
    for a, cnt in zip(accounts, counts):
        # spread created/close dates across window; later opps on an account are expansions
        starts = sorted(rng.randint(0, span + 150) for _ in range(cnt))
        is_customer = False
        for k, offset in enumerate(starts):
            n += 1
            close = window_start + dt.timedelta(days=offset)
            lo, hi = a["_arr"]
            expansion = is_customer and k > 0
            amt = round_amt(rng.uniform(lo, hi) * (rng.uniform(0.2, 0.5) if expansion else 1))
            product = rng.choice(PRODUCTS[1:4]) if expansion else rng.choice(["Platform", "Enterprise Bundle", "Platform"])
            if close < today - dt.timedelta(days=21):
                won = rng.random() < (0.62 if expansion else 0.5)
                stage = "Closed Won" if won else "Closed Lost"
            elif close < today:
                # recently passed close date: mostly closed, a few slipped open deals
                r = rng.random()
                stage = "Closed Won" if r < 0.4 else "Closed Lost" if r < 0.6 else wchoice(rng, OPEN_STAGES[4:])
                if stage not in ("Closed Won", "Closed Lost"):
                    close = today + dt.timedelta(days=rng.randint(7, 45))  # rep pushed the date
            else:
                # future: earlier stage the further out it is
                days_out = (close - today).days
                pool = OPEN_STAGES[3:] if days_out < 45 else OPEN_STAGES[1:6] if days_out < 100 else OPEN_STAGES[:4]
                stage = wchoice(rng, pool)
            won = stage == "Closed Won"
            o = {
                "Demo_External_Id__c": f"DEMO-OPP-{n:04d}", "Account.Demo_External_Id__c": a["Demo_External_Id__c"],
                "Name": f"{a['Name']} - {product}{' Expansion' if expansion else ''}",
                "Type": "Existing Customer - Upgrade" if expansion else "New Customer",
                "StageName": stage, "CloseDate": close.isoformat(), "Amount": amt,
                "LeadSource": "" if expansion else wchoice(rng, LEAD_SOURCES),
                "NextStep": "" if stage.startswith("Closed") else rng.choice(
                    ["Schedule technical deep-dive", "Send revised pricing", "Security review in progress",
                     "Exec sponsor meeting", "Legal redlines", "Build business case with champion",
                     "Discovery call booked"]),
                "Description": rng.choice(LOSS_REASONS) if stage == "Closed Lost" else
                f"Primary competitor: {rng.choice(COMPETITORS)}",
                "Contract_Start_Date__c": close.isoformat() if won else "",
                "Contract_End_Date__c": (add_months(close, 12) - dt.timedelta(days=1)).isoformat() if won else "",
                "Prior_ARR__c": "", "Renewal_Status__c": "",
                "Data_Source__c": TAG, "_seg": a["Segment__c"],
            }
            opps.append(o)
            if won:
                is_customer = True
                a["Type"] = "Customer - Direct"

    # ---------- Renewals for every closed-won deal ----------
    r = 0
    for o in opps:
        if o["StageName"] != "Closed Won":
            continue
        r += 1
        start = dt.date.fromisoformat(o["Contract_Start_Date__c"])
        due = add_months(start, 12)
        prior = o["Amount"]
        base = {
            "Demo_External_Id__c": f"DEMO-REN-{r:04d}",
            "Account.Demo_External_Id__c": o["Account.Demo_External_Id__c"],
            "Name": o["Name"].replace(" Expansion", "") + f" - Renewal FY{due.year % 100:02d}",
            "Type": "Renewal", "Prior_ARR__c": prior, "LeadSource": "",
            "Original_Opportunity__r.Demo_External_Id__c": o["Demo_External_Id__c"],
            "Contract_Start_Date__c": due.isoformat(),
            "Contract_End_Date__c": (add_months(due, 12) - dt.timedelta(days=1)).isoformat(),
            "Data_Source__c": TAG, "_seg": o["_seg"],
        }
        x = rng.random()
        if due <= today - dt.timedelta(days=10):
            # renewal already decided
            if x < 0.68:
                amt, stage, status = round_amt(prior * rng.uniform(1.0, 1.10)), "Closed Won", "Renewed"
                desc = "Renewed with standard uplift"
            elif x < 0.86:
                amt, stage, status = round_amt(prior * rng.uniform(0.55, 0.85)), "Closed Won", "Renewed - Downsell"
                desc = rng.choice(["Seat reduction after layoffs", "Dropped add-on module", "Consolidated business units"])
            else:
                amt, stage, status = 0, "Closed Lost", "Churned"
                prior_amt = prior
                desc = rng.choice(["Churned to competitor", "Low adoption - did not renew", "Acquired; parent standardized elsewhere"])
            close = due + dt.timedelta(days=rng.randint(-20, 15))
            if stage == "Closed Lost":
                amt = prior_amt  # keep lost ARR visible on the opp
            nxt = ""
        else:
            # open renewal: filled in by plant_risk() once each account has a scenario
            base.update({"_open": True, "_due": due, "_prior": prior})
            renewals.append(base)
            continue
        base.update({"StageName": stage, "CloseDate": close.isoformat(), "Amount": amt,
                     "Renewal_Status__c": status, "Description": desc, "NextStep": nxt})
        renewals.append(base)

    cases, tasks, answer_key = plant_risk(random.Random(seed + 1), today, accounts, contacts, renewals)
    return accounts, contacts, opps, renewals, cases, tasks, answer_key


# ---------------------------------------------------------------------------
# Risk scenarios (see docs/risk-rubric.md)
#
# Each account with an open renewal gets one scenario. The scenario decides
# which warning signs we plant in Salesforce and what the rep's Renewal_Status__c says:
#
#   healthy      rep: On Track            no warning signs
#   downsell     rep: At Risk - Downsell  DOWNSELL + LOW_USAGE (+ maybe ESCALATIONS)
#   delayed      rep: At Risk - Delayed   SLIPPING + CHAMPION_LEFT + GONE_QUIET
#   hidden       rep: On Track            2-3 account warning signs the rep isn't reflecting
#   watch        rep: On Track            one moderate warning sign (lands in the Watch band)
#   false_alarm  rep: At Risk - Delayed   no warning signs (flag not backed by evidence)
#
# The answer key records exactly which signals each open renewal carries, so the agent can be checked against it.
# ---------------------------------------------------------------------------
SIGNAL_POINTS = {"PAST_DUE": 30, "SLIPPING": 20, "DOWNSELL": 20, "LOW_USAGE": 20,
                 "CHAMPION_LEFT": 15, "ESCALATIONS": 15, "GONE_QUIET": 10, "STALLED": 10}
SCENARIO_W = [("healthy", 45), ("watch", 10), ("downsell", 12), ("delayed", 10), ("hidden", 13), ("false_alarm", 10)]
# Renewals due this quarter (or past due) cycle through every scenario so the demo always shows each case.
IN_SCOPE_CYCLE = ["delayed", "hidden", "healthy", "downsell", "false_alarm", "watch", "hidden", "healthy"]
REP_STATUS = {"healthy": "On Track", "hidden": "On Track", "watch": "On Track", "downsell": "At Risk - Downsell",
              "delayed": "At Risk - Delayed", "false_alarm": "At Risk - Delayed"}
ESCALATION_SUBJECTS = ["Sync failures blocking forecast roll-up", "SSO outage for sales team",
                       "Data export missing opportunities", "Dashboard performance degraded",
                       "Integration errors after CRM upgrade"]
ROUTINE_SUBJECTS = ["How to add a user", "Report filter question", "Request for training session",
                    "Change billing contact", "Feature request: custom fields"]
TASK_SUBJECTS = ["QBR", "Check-in call", "Sent product update", "Adoption review", "Exec sponsor sync",
                 "Renewal planning call", "Training session"]


def quarter_bounds(d):
    """Calendar quarter (the demo org's fiscal year starts in January)."""
    start = dt.date(d.year, 3 * ((d.month - 1) // 3) + 1, 1)
    return start, add_months(start, 3) - dt.timedelta(days=1)


def plant_risk(rng, today, accounts, contacts, renewals):
    q_start, q_end = quarter_bounds(today)
    acc_by_id = {a["Demo_External_Id__c"]: a for a in accounts}
    open_by_acc = {}
    for r in renewals:
        if r.get("_open"):
            open_by_acc.setdefault(r["Account.Demo_External_Id__c"], []).append(r)

    # 1. Pick a scenario per account (earliest open renewal decides whether it's "in scope" this quarter)
    def in_scope(r):
        return r["_due"] <= q_end
    accs = sorted(open_by_acc, key=lambda a: min(r["_due"] for r in open_by_acc[a]))
    i = 0
    for acc_id in accs:
        if any(in_scope(r) for r in open_by_acc[acc_id]):
            acc_by_id[acc_id]["_scenario"] = IN_SCOPE_CYCLE[i % len(IN_SCOPE_CYCLE)]
            i += 1
        else:
            acc_by_id[acc_id]["_scenario"] = wchoice(rng, SCENARIO_W)

    # 2. Decide the account-level signals for each scenario
    for a in accounts:
        sc = a.get("_scenario")
        sig = set()
        if sc == "downsell":
            sig = {"LOW_USAGE"} | ({"ESCALATIONS"} if rng.random() < 0.5 else set())
        elif sc == "delayed":
            sig = {"CHAMPION_LEFT", "GONE_QUIET"}
        elif sc == "hidden":
            sig = rng.choice([{"ESCALATIONS", "LOW_USAGE", "GONE_QUIET"}, {"CHAMPION_LEFT", "ESCALATIONS", "GONE_QUIET"}])
        elif sc == "watch":
            sig = rng.choice([{"LOW_USAGE"}, {"ESCALATIONS", "GONE_QUIET"}])
        a["_acc_signals"] = sig

    # 3. Fill in the open renewals (rep status, amount, close date, stage) per scenario
    for acc_id, rens in open_by_acc.items():
        sc = acc_by_id[acc_id]["_scenario"]
        for r in rens:
            due, prior = r["_due"], r["_prior"]
            days_out = (due - today).days
            if sc == "downsell":
                amt = round_amt(prior * rng.uniform(0.5, 0.8))
                desc = rng.choice(["Customer requested 30% seat reduction", "Budget cuts; asking for fewer seats",
                                   "Evaluating cheaper competitor for part of the footprint"])
                nxt = "Exec QBR to defend seat count"
            else:
                amt = round_amt(prior * rng.uniform(1.0, 1.08))
                desc = {"delayed": "Procurement slow; renewal paperwork not started",
                        "false_alarm": "Rep worried about timing; customer has not confirmed",
                        }.get(sc, "Standard renewal")
                nxt = "Secure short-term extension" if sc == "delayed" else (
                    "Send renewal quote" if days_out < 60 else "Renewal kickoff at 90 days")
            if sc == "delayed":
                close = due + dt.timedelta(days=rng.randint(30, 90))   # rep pushed it past the renewal date
            elif due >= today:
                close = due
            else:
                close = today + dt.timedelta(days=rng.randint(3, 10))  # past due: rep moved close date forward
            stage = ("Negotiation/Review" if days_out < 30 else "Proposal/Price Quote" if days_out < 90
                     else "Value Proposition" if days_out < 180 else "Qualification")
            if sc in ("downsell", "delayed") and stage == "Negotiation/Review":
                stage = "Perception Analysis"
            if sc == "hidden" and 0 <= days_out <= 60 and rng.random() < 0.5:
                stage = "Value Proposition"  # quietly stuck
            r.update({"StageName": stage, "CloseDate": close.isoformat(), "Amount": amt,
                      "Renewal_Status__c": REP_STATUS[sc], "Description": desc, "NextStep": nxt})

    # 4. Write account-level signals into Salesforce records
    cases, tasks = [], []
    con_by_acc = {}
    for c in contacts:
        con_by_acc.setdefault(c["Account.Demo_External_Id__c"], []).append(c)
    n_case = 0
    for a in accounts:
        acc_id, sig = a["Demo_External_Id__c"], a["_acc_signals"]
        is_customer = a["Type"] == "Customer - Direct"
        # License utilization (customers only)
        a["License_Utilization__c"] = (rng.randint(22, 44) if "LOW_USAGE" in sig
                                       else rng.randint(60, 96) if is_customer else "")
        # Champion: the contact whose title maps to the Champion role (else the first contact)
        cons = con_by_acc.get(acc_id, [])
        champ = next((c for c in cons if c["_role"] == "Champion"), cons[0] if cons else None)
        for c in cons:
            c["Champion__c"] = "true" if c is champ else "false"
            # noise: a non-champion occasionally leaves (must NOT trigger CHAMPION_LEFT)
            c["Left_Company__c"] = "true" if (c is champ and "CHAMPION_LEFT" in sig) or (
                c is not champ and rng.random() < 0.06) else "false"
        if not is_customer:
            continue
        # Cases: history for everyone (closed, any priority) + routine open low/medium noise
        for _ in range(rng.randint(1, 4)):
            n_case += 1
            cases.append(case_row(n_case, acc_id, rng.choice(ROUTINE_SUBJECTS + ESCALATION_SUBJECTS),
                                  rng.choice(["High", "Medium", "Low"]), "Closed"))
        if rng.random() < 0.4:
            n_case += 1
            cases.append(case_row(n_case, acc_id, rng.choice(ROUTINE_SUBJECTS), rng.choice(["Medium", "Low"]),
                                  rng.choice(["New", "Working"])))
        if "ESCALATIONS" in sig:
            for _ in range(rng.randint(1, 2)):
                n_case += 1
                cases.append(case_row(n_case, acc_id, rng.choice(ESCALATION_SUBJECTS), "High",
                                      rng.choice(["New", "Working", "Escalated"])))
        # Activity: completed touches; newest within 25 days, or 35-120 days ago if GONE_QUIET
        newest = rng.randint(35, 120) if "GONE_QUIET" in sig else rng.randint(2, 25)
        for k in range(rng.randint(3, 7)):
            ago = newest if k == 0 else rng.randint(newest + 3, 180)
            tasks.append({"_acc": acc_id, "Subject": rng.choice(TASK_SUBJECTS), "Status": "Completed",
                          "Priority": "Normal", "ActivityDate": (today - dt.timedelta(days=ago)).isoformat(),
                          "Data_Source__c": TAG})
        if rng.random() < 0.5:  # noise: a scheduled (not completed) future task doesn't count as activity
            tasks.append({"_acc": acc_id, "Subject": "Renewal planning call", "Status": "Not Started",
                          "Priority": "Normal", "ActivityDate": (today + dt.timedelta(days=rng.randint(3, 20))).isoformat(),
                          "Data_Source__c": TAG})

    # 5. Answer key: what each open renewal really looks like
    stage_rank = {s: i for i, s in enumerate(STAGE_ORDER)}
    key = []
    for acc_id, rens in open_by_acc.items():
        a = acc_by_id[acc_id]
        for r in rens:
            due, close = r["_due"], dt.date.fromisoformat(r["CloseDate"])
            sig = set(a["_acc_signals"])
            if due < today:
                sig.add("PAST_DUE")
            elif close > due:  # only counts before the renewal date; afterwards PAST_DUE covers it
                sig.add("SLIPPING")
            if r["Amount"] <= 0.9 * r["_prior"]:
                sig.add("DOWNSELL")
            if (due - today).days <= 60 and stage_rank[r["StageName"]] < stage_rank["Proposal/Price Quote"]:
                sig.add("STALLED")
            score = sum(SIGNAL_POINTS[s] for s in sig)
            key.append({
                "renewal_id": r["Demo_External_Id__c"], "account": a["Name"], "segment": a["Segment__c"],
                "renewal_date": due.isoformat(), "in_scope_this_quarter": "yes" if due <= q_end else "no",
                "scenario": a["_scenario"], "rep_status": r["Renewal_Status__c"],
                "prior_arr": r["_prior"], "amount": r["Amount"],
                "signals": ";".join(sorted(sig, key=lambda s: -SIGNAL_POINTS[s])), "score": score,
                "band": "At Risk" if score >= 40 else "Watch" if score >= 20 else "Healthy",
            })
    key.sort(key=lambda k: (k["renewal_date"], k["renewal_id"]))
    return cases, tasks, key


def case_row(n, acc_id, subject, priority, status):
    return {"Account.Demo_External_Id__c": acc_id, "Subject": subject, "Priority": priority, "Status": status,
            "Origin": "Email", "Data_Source__c": TAG}


def write_csv(path, rows):
    cols = [k for k in rows[0] if not k.startswith("_")]
    with open(path, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=cols, extrasaction="ignore", lineterminator="\n")
        w.writeheader()
        w.writerows(rows)
    return path


def sf(*args):
    print("+ sf " + " ".join(args), flush=True)
    res = subprocess.run(["sf", *args], text=True, capture_output=True)
    print(res.stdout[-2500:], res.stderr[-2500:], sep="\n", flush=True)
    if res.returncode != 0:
        sys.exit(f"sf command failed ({res.returncode})")


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--target-org", default="revops-demo")
    p.add_argument("--seed", type=int, default=42)
    p.add_argument("--as-of", default=dt.date.today().isoformat(), help="'today' for date math (YYYY-MM-DD)")
    p.add_argument("--no-load", action="store_true", help="write CSVs only")
    p.add_argument("--no-wipe", action="store_true", help="don't delete existing tagged records first")
    args = p.parse_args()

    today = dt.date.fromisoformat(args.as_of)
    accounts, contacts, opps, renewals, cases, tasks, answer_key = build(args.seed, today)
    os.makedirs(OUT, exist_ok=True)
    files = {
        "Account": write_csv(os.path.join(OUT, "accounts.csv"), accounts),
        "Contact": write_csv(os.path.join(OUT, "contacts.csv"), contacts),
        "Opportunity": write_csv(os.path.join(OUT, "opportunities.csv"), opps),
        "Renewal": write_csv(os.path.join(OUT, "renewals.csv"), renewals),
        "Case": write_csv(os.path.join(OUT, "cases.csv"), cases),
    }
    write_csv(os.path.join(OUT, "answer_key.csv"), answer_key)
    print(f"Generated {len(accounts)} accounts, {len(contacts)} contacts, {len(opps)} opportunities, "
          f"{len(renewals)} renewals, {len(cases)} cases, {len(tasks)} tasks -> {OUT}")
    print(f"Answer key: {OUT}/answer_key.csv (as of {today}, {len(answer_key)} open renewals)")
    if args.no_load:
        return

    if not args.no_wipe:
        subprocess.run([sys.executable, os.path.join(HERE, "wipe.py"), "--target-org", args.target_org, "--yes"], check=True)
    org = ["--target-org", args.target_org, "--wait", "15", "--external-id", "Demo_External_Id__c"]
    sf("data", "upsert", "bulk", "--sobject", "Account", "--file", files["Account"], *org)
    sf("data", "upsert", "bulk", "--sobject", "Contact", "--file", files["Contact"], *org)
    sf("data", "upsert", "bulk", "--sobject", "Opportunity", "--file", files["Opportunity"], *org)
    sf("data", "upsert", "bulk", "--sobject", "Opportunity", "--file", files["Renewal"], *org)
    ins = ["--target-org", args.target_org, "--wait", "15"]
    sf("data", "import", "bulk", "--sobject", "Case", "--file", files["Case"], *ins)
    # Tasks link to accounts by Salesforce Id (WhatId is polymorphic), so look the Ids up first
    acc_ids = account_id_map(args.target_org)
    for t in tasks:
        t["WhatId"] = acc_ids[t["_acc"]]
    sf("data", "import", "bulk", "--sobject", "Task", "--file", write_csv(os.path.join(OUT, "tasks.csv"), tasks), *ins)
    print("Load complete.")


def account_id_map(org):
    res = subprocess.run(["sf", "data", "query", "--query",
                          "SELECT Id, Demo_External_Id__c FROM Account WHERE Data_Source__c = 'Demo Data'",
                          "--target-org", org, "--json"], text=True, capture_output=True)
    return {r["Demo_External_Id__c"]: r["Id"] for r in json.loads(res.stdout)["result"]["records"]}


if __name__ == "__main__":
    main()
