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
                "LeadSource": wchoice(rng, LEAD_SOURCES), "Data_Source__c": TAG,
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
            # open renewal
            days_out = (due - today).days
            if x < 0.17:
                status = "At Risk - Downsell"
                amt = round_amt(prior * rng.uniform(0.5, 0.8))
                desc = rng.choice(["Customer requested 30% seat reduction", "Usage down 40% QoQ; budget cuts",
                                   "Evaluating cheaper competitor for part of the footprint"])
                nxt = "Exec QBR to defend seat count"
            elif x < 0.32:
                status = "At Risk - Delayed"
                amt = round_amt(prior * rng.uniform(0.95, 1.05))
                desc = rng.choice(["Procurement freeze until next quarter", "New CFO re-reviewing all vendors",
                                   "Champion departed; re-establishing sponsor", "Legal stuck on DPA terms"])
                nxt = "Secure short-term extension"
            else:
                status = "On Track"
                amt = round_amt(prior * rng.uniform(1.0, 1.08))
                desc = "Healthy usage; standard renewal"
                nxt = "Send renewal quote" if days_out < 60 else "Renewal kickoff at 90 days"
            if status == "At Risk - Delayed":
                close = due + dt.timedelta(days=rng.randint(30, 90))
            else:
                close = max(due, today + dt.timedelta(days=5))
            if days_out < 30:
                stage = "Negotiation/Review"
            elif days_out < 90:
                stage = "Proposal/Price Quote"
            elif days_out < 180:
                stage = "Value Proposition"
            else:
                stage = "Qualification"
            if status != "On Track" and stage in ("Negotiation/Review",):
                stage = "Perception Analysis"
        base.update({"StageName": stage, "CloseDate": close.isoformat(), "Amount": amt,
                     "Renewal_Status__c": status, "Description": desc, "NextStep": nxt})
        renewals.append(base)

    return accounts, contacts, opps, renewals


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
    accounts, contacts, opps, renewals = build(args.seed, today)
    os.makedirs(OUT, exist_ok=True)
    files = {
        "Account": write_csv(os.path.join(OUT, "accounts.csv"), accounts),
        "Contact": write_csv(os.path.join(OUT, "contacts.csv"), contacts),
        "Opportunity": write_csv(os.path.join(OUT, "opportunities.csv"), opps),
        "Renewal": write_csv(os.path.join(OUT, "renewals.csv"), renewals),
    }
    print(f"Generated {len(accounts)} accounts, {len(contacts)} contacts, "
          f"{len(opps)} opportunities, {len(renewals)} renewals -> {OUT}")
    if args.no_load:
        return

    if not args.no_wipe:
        subprocess.run([sys.executable, os.path.join(HERE, "wipe.py"), "--target-org", args.target_org, "--yes"], check=True)
    org = ["--target-org", args.target_org, "--wait", "15", "--external-id", "Demo_External_Id__c"]
    sf("data", "upsert", "bulk", "--sobject", "Account", "--file", files["Account"], *org)
    sf("data", "upsert", "bulk", "--sobject", "Contact", "--file", files["Contact"], *org)
    sf("data", "upsert", "bulk", "--sobject", "Opportunity", "--file", files["Opportunity"], *org)
    sf("data", "upsert", "bulk", "--sobject", "Opportunity", "--file", files["Renewal"], *org)
    print("Load complete.")


if __name__ == "__main__":
    main()
