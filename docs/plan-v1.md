# v1 Plan: "Which renewals are at risk this quarter, and why?"

**Scope:** Salesforce only, using the demo data. No Slack or Gmail yet.

## Architecture: Claude + a small custom MCP server
```
Claude (Claude Code) ──MCP──▶ revops_mcp server (Python, read-only) ──sf CLI──▶ Salesforce (revops-demo)
                                   └─ scores renewals using docs/risk-rubric.md
```
- **The server computes the facts.** It decides which renewals are in scope, which warning signs fire, and the points and ARR. This is plain code, so it's testable and every answer can be traced to records.
- **Claude does the judgment and writing.** It ranks the risks, explains them in plain English, compares the evidence with the rep's own status, and recommends one action per deal.
- **Why not Agentforce yet?** The end goal works across Salesforce, Slack and Gmail and runs on a schedule, which suits an external agent. Agentforce comes in v3 as an in-Salesforce view that reuses the same scoring rules.

## Steps
1. ✅ **Rubric:** 8 warning signs with points, and At Risk at 40 points or more. See [risk-rubric.md](risk-rubric.md).
2. ✅ **Demo data with planted signals:** license utilization, champion departures, cases, activity, and the rep's status kept separate from the evidence. The generator writes an answer key, and an independent check confirmed all 36 open renewals in the org match it.
3. ✅ **MCP server:** in `revops_mcp/`, with two read-only tools. `get_renewal_risk(period)` returns scored renewals with reasons, and `get_renewal_detail(renewal)` returns the evidence for one renewal. It reuses the `sf` CLI login, so no secrets go in the repo. 13 tests pass: 8 rule edge cases plus 5 checks of the live org against the answer key.
4. **Answer format:** total ARR at risk, then each deal with its reason and next action, plus where the rep's status and the evidence disagree.
5. **Evals:** compare the agent's results with the answer key. Check which renewals were flagged, the reasons, and the ARR, plus a check of the written answer.
6. **Run it** from Claude Code, then on a schedule (which leads into v2).

**Later:** v2 adds a Slack digest and Gmail drafts. v3 adds an Agentforce view in Salesforce.
