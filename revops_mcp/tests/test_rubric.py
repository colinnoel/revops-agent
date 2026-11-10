"""Rule-by-rule checks of the rubric, including edge cases. No Salesforce needed."""
import datetime as dt

from risk import band, fiscal_quarter, score_renewal

AS_OF = dt.date(2026, 11, 10)
RANK = {"Prospecting": 1, "Qualification": 2, "Needs Analysis": 3, "Value Proposition": 4, "Id. Decision Makers": 5,
        "Perception Analysis": 6, "Proposal/Price Quote": 7, "Negotiation/Review": 8}
HEALTHY_CTX = {"utilization": 80, "champions_left": [], "escalations": [], "last_activity": AS_OF - dt.timedelta(days=5)}


def opp(**kw):
    base = {"Contract_Start_Date__c": "2026-12-20", "CloseDate": "2026-12-20", "Amount": 100_000,
            "Prior_ARR__c": 100_000, "StageName": "Proposal/Price Quote"}
    base.update(kw)
    return base


def codes(o, ctx=None):
    return {s.code for s in score_renewal(o, {**HEALTHY_CTX, **(ctx or {})}, AS_OF, RANK)}


def test_healthy_renewal_has_no_signals():
    assert codes(opp()) == set()


def test_past_due_vs_slipping():
    assert codes(opp(Contract_Start_Date__c="2026-11-09", CloseDate="2026-11-20")) == {"PAST_DUE"}
    assert codes(opp(CloseDate="2026-12-21")) == {"SLIPPING"}
    assert codes(opp(Contract_Start_Date__c="2026-11-10", CloseDate="2026-11-10")) == set()  # due today: not past due


def test_downsell_threshold_is_10_percent():
    assert codes(opp(Amount=90_000)) == {"DOWNSELL"}
    assert codes(opp(Amount=90_500)) == set()
    assert codes(opp(Prior_ARR__c=None)) == set()


def test_low_usage_below_50():
    assert codes(opp(), {"utilization": 49}) == {"LOW_USAGE"}
    assert codes(opp(), {"utilization": 50}) == set()
    assert codes(opp(), {"utilization": None}) == set()


def test_gone_quiet_after_30_days():
    assert codes(opp(), {"last_activity": AS_OF - dt.timedelta(days=30)}) == set()
    assert codes(opp(), {"last_activity": AS_OF - dt.timedelta(days=31)}) == {"GONE_QUIET"}
    assert codes(opp(), {"last_activity": None}) == {"GONE_QUIET"}


def test_stalled_within_60_days_before_proposal():
    assert codes(opp(StageName="Value Proposition")) == {"STALLED"}            # 40 days out
    assert codes(opp(StageName="Value Proposition", Contract_Start_Date__c="2027-01-10",
                     CloseDate="2027-01-10")) == set()                          # 61 days out


def test_account_signals_and_bands():
    sig = score_renewal(opp(), {**HEALTHY_CTX, "champions_left": [{"Name": "A", "Title": "VP"}],
                                "escalations": [{"CaseNumber": "1", "Subject": "x", "Status": "New"}]}, AS_OF, RANK)
    assert {s.code for s in sig} == {"CHAMPION_LEFT", "ESCALATIONS"}
    assert band(sum(s.points for s in sig)) == "Watch"  # 30
    assert band(40) == "At Risk" and band(39) == "Watch" and band(19) == "Healthy"


def test_fiscal_quarters():
    assert fiscal_quarter(AS_OF, 1) == (dt.date(2026, 10, 1), dt.date(2026, 12, 31))
    assert fiscal_quarter(AS_OF, 1, offset=1) == (dt.date(2027, 1, 1), dt.date(2027, 3, 31))
    assert fiscal_quarter(AS_OF, 2) == (dt.date(2026, 11, 1), dt.date(2027, 1, 31))   # FY starts Feb
    assert fiscal_quarter(dt.date(2026, 1, 15), 2) == (dt.date(2025, 11, 1), dt.date(2026, 1, 31))
