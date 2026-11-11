# RevOps Agent

**An AI agent that finds revenue risk before anyone asks, and tells sales leaders what to do about it.**

It connects Claude to Salesforce (with Slack and Gmail next), reads the signals already sitting in the CRM, and surfaces the deals that need attention. Every finding comes with the dollar impact, the evidence, and a recommended next step.

---

## Why I'm building this

Most RevOps tooling is reactive. A leader has to know the right question, build the report, and read the dashboard, and by the time a renewal shows up as a problem, it's often already lost.

I want to flip that around:

- **Proactive, not prompted.** The agent watches the pipeline and raises risk on its own, instead of waiting for someone to ask.
- **Anticipates needs.** It knows what a sales leader cares about (ARR at risk, forecast integrity, pipeline health) and briefs them on it before the QBR, not after.
- **What reps say vs. what the data says.** Rep-entered fields like "On Track" are opinions. The agent compares them against real signals (usage, support escalations, champion changes, activity) and calls out where they disagree.

That last one is where a lot of the value lives: a renewal marked "On Track" with 41% license usage and no contact in two months is the one that churns quietly.

## The business goal

**Reduce churn and failed renewals by catching risk early enough to act on it.**

Renewals are the starting use case because the cost of a miss is concrete and the signals are measurable. If it works, the same approach extends to forecast integrity (slipped close dates, stalled late-stage deals) and pipeline health (coverage, concentration, hygiene).

I approach this with a product manager lens: sales leaders and reps are the customers, and the goal is to save them time, money, and headaches by handing them the answer, not the report.

---

## What it looks like

Asking *"Which renewals are at risk this quarter and why?"* against the demo org returns:

> ## $1.15M at risk this quarter (3 of 6 renewals)
>
> | | Account | ARR | Renews | Rep says |
> |---|---|---|---|---|
> | 🔴 | Orchard Systems | $589.5K | Nov 16 | On Track ⚠️ |
> | 🔴 | Summit Ash Outfitters | $440.5K | Dec 3 | At Risk |
> | 🔴 | Zephyr Retail Co | $116K | Nov 5 (past due) | At Risk |
> | 🟡 | Bramble Manufacturing | $31K | Dec 29 | On Track |
> | 🟢 | Moonridge Labs | $267.5K | Dec 22 | At Risk ⚠️ |
>
> ⚠️ = rep status and evidence disagree
>
> **🔴 Orchard Systems: $589.5K**
> **Why:** 41% license use · open High-priority case · no contact in 61 days
> **Do:** Book an exec call this week. Fix case #00001089 before signing.

The biggest risk of the quarter was marked On Track by the rep, and one "At Risk" flag had no evidence behind it at all. Both are exactly what a leader wants to know about.

---

## How it works

```
Claude ──▶ RevOps MCP server (read-only) ──▶ Salesforce
  │              │
  │              └─ applies the risk rubric in code: signals, points, $ at risk
  └─ ranks, explains, and recommends the next action
```

- **The math is code, not AI.** Scores, dates, and dollar figures come from fixed rules, so the same question always gets the same numbers.
- **Claude does the judgment.** It ranks what matters, writes the brief, and flags rep vs. evidence gaps.
- **Every claim traces to a record.** Each reason points to a real case, contact, activity, or field in Salesforce.

### The risk rubric

Each open renewal starts at 0. Each warning sign adds points: **40+ is At Risk, 20 to 39 is Watch.**

| Signal | Points | | Signal | Points |
|---|---|---|---|---|
| Past due, still open | 30 | | Champion left | 15 |
| Close date after renewal date | 20 | | Open High-priority case | 15 |
| Downsell of 10% or more | 20 | | No activity in 30 days | 10 |
| Under 50% license use | 20 | | Stalled in an early stage | 10 |

Full definitions: [docs/risk-rubric.md](docs/risk-rubric.md)

---

## The demo environment

This runs against a real Salesforce Developer org, seeded to look like a B2B SaaS business:

- **50 accounts** across SMB, Mid-Market, and Enterprise, with **179 contacts**
- **120 opportunities** across every stage, with close dates over the last 18 months
- **49 renewals** for every closed-won deal, plus **97 support cases** and **193 activities**

The data isn't just random. Each account is given a scenario (healthy, watch, downsell, delayed, hidden risk, or false alarm), and the matching evidence is planted in Salesforce. That includes decoys the agent should ignore, like closed escalations and non-champions who left.

The generator also writes an **answer key**, so accuracy is tested, not assumed. **13 automated tests** check every rule at its edges and confirm that the live org matches the answer key exactly.

Every demo record is tagged `Data_Source__c = 'Demo Data'`, so the whole dataset can be wiped and reloaded with one command.

---

## Status and roadmap

| | |
|---|---|
| ✅ **v1** | Renewal risk from Salesforce, on demand in Claude Code |
| 🔜 **Next** | Runs on a schedule and delivers the brief without being asked |
| 🔜 **v2** | Slack digest for sales leaders, plus Gmail drafts for follow-ups |
| 🔜 **v3** | Agentforce view inside Salesforce, reusing the same rules |
| 💡 **Later** | Forecast integrity and pipeline health using the same approach |

**Guardrails:** the agent is read-only today. When it starts taking action, it drafts first (Gmail drafts, not sends) and asks before posting to channels or changing real records.

---

## Running it yourself

**You'll need:** the [Salesforce CLI](https://developer.salesforce.com/tools/salesforcecli), [uv](https://docs.astral.sh/uv/), [Claude Code](https://claude.com/claude-code), and a Salesforce org authorized with the CLI.

```bash
# 1. Deploy the custom fields and give yourself access
sf project deploy start --source-dir force-app --target-org <your-org>
sf org assign permset --name RevOps_Demo_Data --target-org <your-org>

# 2. Seed the demo data (re-runnable; wipes and reloads tagged records)
python3 scripts/demo-data/generate.py --target-org <your-org> --as-of 2026-11-10

# 3. Run the tests
uv run --project revops_mcp pytest revops_mcp/tests

# 4. Open Claude Code here, approve the "revops" server, and ask away
claude
```

The defaults point to an org with the alias `revops-demo`. Set `REVOPS_ORG` in `.mcp.json` to use a different one. No credentials live in this repo, because the server borrows the Salesforce CLI's existing login.

### What's where

| Path | What it is |
|---|---|
| `revops_mcp/` | The MCP server: Salesforce access, risk scoring, tests |
| `scripts/demo-data/` | Demo data generator, answer key, and wipe script |
| `force-app/` | Salesforce custom fields and permission set |
| `docs/` | Risk rubric and v1 plan |
| `CLAUDE.md` | How the agent should think, write, and behave |

---

Built by Colin Noel with Claude Code. Questions or ideas? Open an issue, I'd love to hear them!
