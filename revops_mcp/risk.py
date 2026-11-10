"""Renewal risk scoring: the rules from docs/risk-rubric.md, in code.

Every renewal starts at 0. Each warning sign found in Salesforce adds points.
40+ = At Risk, 20-39 = Watch, under 20 = Healthy. The rep's Renewal_Status__c
is NOT used in the score. It is only compared against the score afterwards.
"""
import datetime as dt
from dataclasses import dataclass, asdict

from salesforce import Salesforce, soql_ids, soql_str

# ---- The rubric (keep in sync with docs/risk-rubric.md) ----
POINTS = {
    "PAST_DUE": 30,       # renewal date has passed, opp still open
    "SLIPPING": 20,       # renewal date still ahead, but close date is after it
    "DOWNSELL": 20,       # expected amount at least 10% below prior ARR
    "LOW_USAGE": 20,      # license utilization under 50%
    "CHAMPION_LEFT": 15,  # the account's champion has left the company
    "ESCALATIONS": 15,    # at least one open High-priority case
    "GONE_QUIET": 10,     # no completed activity in the last 30 days
    "STALLED": 10,        # due within 60 days and not yet at Proposal/Price Quote
}
AT_RISK, WATCH = 40, 20
DOWNSELL_THRESHOLD = 0.9
LOW_USAGE_PCT = 50
QUIET_DAYS = 30
STALLED_DAYS = 60
STALLED_BEFORE_STAGE = "Proposal/Price Quote"
DEMO_FILTER = "Data_Source__c = 'Demo Data'"


@dataclass
class Signal:
    code: str
    points: int
    detail: str


def band(score: int) -> str:
    return "At Risk" if score >= AT_RISK else "Watch" if score >= WATCH else "Healthy"


def money(x) -> str:
    return f"${x:,.0f}"


# ---------------------------------------------------------------------------
# Periods
# ---------------------------------------------------------------------------
def fiscal_quarter(day: dt.date, fy_start_month: int, offset: int = 0) -> tuple[dt.date, dt.date]:
    """Start/end of the fiscal quarter containing `day` (offset=1 gives the next quarter).
    Uses the org's standard fiscal year start month (custom fiscal years aren't supported)."""
    def first_of_month(d: dt.date, shift: int) -> dt.date:
        i = d.year * 12 + d.month - 1 + shift
        return dt.date(i // 12, i % 12 + 1, 1)

    months_into_quarter = ((day.month - fy_start_month) % 12) % 3
    start = first_of_month(day, -months_into_quarter + 3 * offset)
    end = first_of_month(start, 3) - dt.timedelta(days=1)
    return start, end


# ---------------------------------------------------------------------------
# Scoring one renewal (pure function: no Salesforce calls, easy to test)
# ---------------------------------------------------------------------------
def score_renewal(opp: dict, ctx: dict, as_of: dt.date, stage_rank: dict) -> list[Signal]:
    """opp: Opportunity record. ctx: account context (utilization, champions_left, escalations, last_activity)."""
    sig = []
    renewal_date = dt.date.fromisoformat(opp["Contract_Start_Date__c"])
    close = dt.date.fromisoformat(opp["CloseDate"])
    days_to = (renewal_date - as_of).days

    if renewal_date < as_of:
        sig.append(Signal("PAST_DUE", POINTS["PAST_DUE"],
                          f"Renewal date {renewal_date} passed {-days_to} days ago and the renewal is still open"))
    elif close > renewal_date:
        sig.append(Signal("SLIPPING", POINTS["SLIPPING"],
                          f"Close date {close} is {(close - renewal_date).days} days after the renewal date {renewal_date}; "
                          "the customer would lapse"))

    prior, amount = opp.get("Prior_ARR__c"), opp.get("Amount")
    if prior and amount is not None and amount <= DOWNSELL_THRESHOLD * prior:
        sig.append(Signal("DOWNSELL", POINTS["DOWNSELL"],
                          f"Expected {money(amount)} vs prior ARR {money(prior)} ({(amount - prior) / prior:+.0%})"))

    util = ctx.get("utilization")
    if util is not None and util < LOW_USAGE_PCT:
        sig.append(Signal("LOW_USAGE", POINTS["LOW_USAGE"], f"Only {util:.0f}% of licenses in use"))

    if ctx.get("champions_left"):
        who = "; ".join(f"{c['Name']} ({c.get('Title') or 'no title'})" for c in ctx["champions_left"])
        sig.append(Signal("CHAMPION_LEFT", POINTS["CHAMPION_LEFT"], f"Champion left the company: {who}"))

    if ctx.get("escalations"):
        cases = ctx["escalations"]
        names = "; ".join(f"#{c['CaseNumber']} {c['Subject']} ({c['Status']})" for c in cases)
        sig.append(Signal("ESCALATIONS", POINTS["ESCALATIONS"], f"{len(cases)} open High-priority case(s): {names}"))

    last = ctx.get("last_activity")
    if last is None:
        sig.append(Signal("GONE_QUIET", POINTS["GONE_QUIET"], "No completed activity in the last year"))
    elif (as_of - last).days > QUIET_DAYS:
        sig.append(Signal("GONE_QUIET", POINTS["GONE_QUIET"],
                          f"Last completed activity {last} ({(as_of - last).days} days ago)"))

    stage = opp["StageName"]
    if days_to <= STALLED_DAYS and stage_rank.get(stage, 0) < stage_rank[STALLED_BEFORE_STAGE]:
        when = f"{days_to} days to renewal" if days_to >= 0 else f"{-days_to} days past renewal"
        sig.append(Signal("STALLED", POINTS["STALLED"], f"Still in '{stage}' with {when}"))

    return sorted(sig, key=lambda s: -s.points)


# ---------------------------------------------------------------------------
# Loading from Salesforce
# ---------------------------------------------------------------------------
RENEWAL_FIELDS = ("Id, Name, Demo_External_Id__c, AccountId, Account.Name, Account.Segment__c, "
                  "Account.License_Utilization__c, Owner.Name, StageName, CloseDate, Amount, Prior_ARR__c, "
                  "Contract_Start_Date__c, Renewal_Status__c, NextStep, Description, Original_Opportunity__r.Name")


def _stage_rank(sf: Salesforce) -> dict:
    rows = sf.query("SELECT MasterLabel, SortOrder FROM OpportunityStage WHERE IsActive = true")
    return {r["MasterLabel"]: r["SortOrder"] for r in rows}


def _account_context(sf: Salesforce, opps: list[dict], as_of: dt.date) -> dict:
    acc_ids = sorted({o["AccountId"] for o in opps})
    ctx = {a: {"champions_left": [], "escalations": [], "last_activity": None} for a in acc_ids}
    for o in opps:
        ctx[o["AccountId"]]["utilization"] = o["Account"]["License_Utilization__c"]
    if not acc_ids:
        return ctx
    for c in sf.query(f"SELECT AccountId, Name, Title FROM Contact WHERE Champion__c = true "
                      f"AND Left_Company__c = true AND AccountId IN {soql_ids(acc_ids)}"):
        ctx[c["AccountId"]]["champions_left"].append(c)
    for c in sf.query(f"SELECT AccountId, CaseNumber, Subject, Status FROM Case WHERE IsClosed = false "
                      f"AND Priority = 'High' AND AccountId IN {soql_ids(acc_ids)} ORDER BY CreatedDate"):
        ctx[c["AccountId"]]["escalations"].append(c)
    # Newest completed task per account (Task.ActivityDate can't be MAX()-ed in SOQL, so sort and take the first)
    since = as_of - dt.timedelta(days=365)
    for t in sf.query(f"SELECT AccountId, ActivityDate FROM Task WHERE Status = 'Completed' "
                      f"AND ActivityDate >= {since.isoformat()} AND ActivityDate <= {as_of.isoformat()} "
                      f"AND AccountId IN {soql_ids(acc_ids)} ORDER BY ActivityDate DESC"):
        if ctx[t["AccountId"]]["last_activity"] is None:
            ctx[t["AccountId"]]["last_activity"] = dt.date.fromisoformat(t["ActivityDate"])
    return ctx


def _disagreement(rep_status: str, b: str):
    rep_at_risk = (rep_status or "").startswith("At Risk")
    if not rep_at_risk and b == "At Risk":
        return "hidden_risk: rep says On Track, evidence says At Risk"
    if rep_at_risk and b == "Healthy":
        return "unsupported_flag: rep says At Risk, no evidence found"
    return None


def _row(sf, o, signals):
    score = sum(s.points for s in signals)
    b = band(score)
    return {
        "renewal": o["Name"], "account": o["Account"]["Name"], "segment": o["Account"]["Segment__c"],
        "owner": o["Owner"]["Name"], "renewal_date": o["Contract_Start_Date__c"], "close_date": o["CloseDate"],
        "stage": o["StageName"], "prior_arr": o["Prior_ARR__c"], "expected_amount": o["Amount"],
        "rep_status": o["Renewal_Status__c"], "score": score, "band": b,
        "signals": [asdict(s) for s in signals], "rep_vs_evidence": _disagreement(o["Renewal_Status__c"], b),
        "salesforce_id": o["Id"], "demo_id": o.get("Demo_External_Id__c"), "url": sf.record_url(o["Id"]),
    }


def assess_renewals(sf: Salesforce, period: str = "this_quarter", as_of: dt.date | None = None,
                    segment: str | None = None, demo_only: bool = True) -> dict:
    """Score open renewals in a period. Periods:
    this_quarter = renewal date on/before the end of the current fiscal quarter (includes past due),
    next_quarter = renewal date within the next fiscal quarter, all_open = every open renewal."""
    as_of = as_of or dt.date.today()
    fy_start = sf.query("SELECT FiscalYearStartMonth FROM Organization")[0]["FiscalYearStartMonth"]
    where = ["Type = 'Renewal'", "IsClosed = false"]
    if demo_only:
        where.append(DEMO_FILTER)
    if segment:
        where.append(f"Account.Segment__c = {soql_str(segment)}")
    if period == "this_quarter":
        start, end = fiscal_quarter(as_of, fy_start)
        where.append(f"Contract_Start_Date__c <= {end.isoformat()}")
        label = f"{start} to {end} (plus any past-due renewals)"
    elif period == "next_quarter":
        start, end = fiscal_quarter(as_of, fy_start, offset=1)
        where.append(f"Contract_Start_Date__c >= {start.isoformat()} AND Contract_Start_Date__c <= {end.isoformat()}")
        label = f"{start} to {end}"
    elif period == "all_open":
        label = "all open renewals"
    else:
        raise ValueError("period must be this_quarter, next_quarter or all_open")

    opps = sf.query(f"SELECT {RENEWAL_FIELDS} FROM Opportunity WHERE {' AND '.join(where)} "
                    "ORDER BY Contract_Start_Date__c")
    ctx = _account_context(sf, opps, as_of)
    ranks = _stage_rank(sf)
    rows = [_row(sf, o, score_renewal(o, ctx[o["AccountId"]], as_of, ranks)) for o in opps]
    rows.sort(key=lambda r: (-r["score"], -(r["prior_arr"] or 0)))

    def total(b):
        sel = [r for r in rows if r["band"] == b]
        return {"count": len(sel), "prior_arr": sum(r["prior_arr"] or 0 for r in sel)}
    return {
        "as_of": as_of.isoformat(), "period": label, "scope": "demo data only" if demo_only else "all records",
        "summary": {"renewals": len(rows), "at_risk": total("At Risk"), "watch": total("Watch"),
                    "healthy": total("Healthy"),
                    "rep_vs_evidence_disagreements": sum(1 for r in rows if r["rep_vs_evidence"])},
        "rubric": "docs/risk-rubric.md: At Risk >= 40 points, Watch 20-39, Healthy < 20",
        "renewals": rows,
    }


def renewal_detail(sf: Salesforce, ref: str, as_of: dt.date | None = None, demo_only: bool = True) -> dict:
    """Score + supporting evidence for one renewal. `ref` = Salesforce Id, demo id, or part of the name/account."""
    as_of = as_of or dt.date.today()
    where = ["Type = 'Renewal'"] + ([DEMO_FILTER] if demo_only else [])
    ref = ref.strip()
    if ref.startswith("006") and len(ref) in (15, 18):  # a Salesforce Opportunity Id
        match = f"Id = {soql_str(ref)}"
    else:
        like = soql_str(f"%{ref}%")
        match = f"(Demo_External_Id__c = {soql_str(ref)} OR Name LIKE {like} OR Account.Name LIKE {like})"
    opps = sf.query(f"SELECT {RENEWAL_FIELDS}, IsClosed FROM Opportunity WHERE {' AND '.join(where)} AND {match} "
                    "ORDER BY IsClosed, Contract_Start_Date__c")
    open_opps = [o for o in opps if not o["IsClosed"]]
    if not opps:
        return {"error": f"No renewal matched '{ref}'"}
    if not open_opps:
        return {"error": f"'{ref}' matched only closed renewals; scoring applies to open renewals",
                "closed_matches": [{"renewal": o["Name"], "stage": o["StageName"], "status": o["Renewal_Status__c"]}
                                   for o in opps]}
    o = open_opps[0]  # soonest renewal date; others are listed so nothing is silently hidden
    others = [{"renewal": x["Name"], "renewal_date": x["Contract_Start_Date__c"], "salesforce_id": x["Id"]}
              for x in open_opps[1:]]
    acc = o["AccountId"]
    ctx = _account_context(sf, [o], as_of)[acc]
    result = _row(sf, o, score_renewal(o, ctx, as_of, _stage_rank(sf)))
    result["as_of"] = as_of.isoformat()
    if others:
        result["other_open_renewals_matching"] = others
    result["rep_notes"] = {"description": o.get("Description"), "next_step": o.get("NextStep")}
    result["original_deal"] = (o.get("Original_Opportunity__r") or {}).get("Name")
    result["evidence"] = {
        "open_cases": sf.query(f"SELECT CaseNumber, Subject, Priority, Status, CreatedDate FROM Case "
                               f"WHERE AccountId = {soql_str(acc)} AND IsClosed = false ORDER BY Priority, CreatedDate"),
        "recent_activity": sf.query(f"SELECT Subject, Status, ActivityDate FROM Task WHERE AccountId = {soql_str(acc)} "
                                    f"AND ActivityDate <= {as_of.isoformat()} ORDER BY ActivityDate DESC LIMIT 5"),
        "contacts": sf.query(f"SELECT Name, Title, Champion__c, Left_Company__c FROM Contact "
                             f"WHERE AccountId = {soql_str(acc)} ORDER BY Champion__c DESC, Name"),
        "license_utilization_pct": o["Account"]["License_Utilization__c"],
    }
    for recs in result["evidence"].values():
        if isinstance(recs, list):
            for r in recs:
                r.pop("attributes", None)
    return result
