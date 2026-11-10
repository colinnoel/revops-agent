# Renewal Risk Rubric (v1)

**Plain-English summary:** each open renewal starts at 0 points. Each warning sign found in Salesforce adds points. **40 or more means At Risk.** 20–39 means Watch. Every point traces back to a specific record.

## Which renewals are in scope
Open renewal opportunities (`Type = 'Renewal'`) where either:
- the **renewal date** (`Contract_Start_Date__c`, the day the new term starts) falls in the current fiscal quarter, **or**
- the renewal date has already passed and the opp is still open (past-due renewals carry over).

## Warning signs

| Signal | Points | Fires when | Where it comes from |
|---|---|---|---|
| `PAST_DUE` | 30 | The renewal date has passed and the opp is still open | Opportunity |
| `SLIPPING` | 20 | The renewal date is still ahead, but the close date is after it, so the customer would lapse | Opportunity |
| `DOWNSELL` | 20 | The expected amount is at least 10% below Prior ARR | Opportunity |
| `LOW_USAGE` | 20 | The account uses less than 50% of its licenses | Account `License_Utilization__c` |
| `CHAMPION_LEFT` | 15 | The account's champion contact has left the company | Contact `Champion__c` + `Left_Company__c` |
| `ESCALATIONS` | 15 | The account has at least one open High-priority case | Case |
| `GONE_QUIET` | 10 | No completed activity on the account in the last 30 days | Task |
| `STALLED` | 10 | The renewal is due within 60 days but hasn't reached Proposal/Price Quote | Opportunity stage |

**Bands:** At Risk ≥ 40 · Watch 20–39 · Healthy < 20.

Two signals that each make sense on their own reach At Risk together. For example, low usage plus a downsell is 40, and slipping plus a departed champion plus going quiet is 45. A single signal on its own (except `PAST_DUE`) only reaches Watch.

## The rep's call vs. the evidence
`Renewal_Status__c` is the rep's own judgment. It is **not** used in the score. The agent compares the two and calls out disagreements:
- **Hidden risk:** the rep says On Track, but the score is At Risk.
- **Unsupported flag:** the rep says At Risk, but the evidence scores Healthy.

## How accuracy is checked
The demo-data generator **plants** specific signals in each renewal and writes an answer key to `scripts/demo-data/out/answer_key.csv`. Evals compare the agent's results with that key. They check which renewals were flagged, the reasons given, and the ARR at risk.
