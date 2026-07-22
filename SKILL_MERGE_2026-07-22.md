MERGE TEXT FOR dtao-trading-system SKILL.md — apply manually (skill is manually authored, agent can't patch it)

=== 1. ADD to "CRITICAL BACKTEST FINDING" section (or new section after it) ===

### CHAIN BUY BACKTEST (July 22, 2026) — chain buys are NOT a convergence force

Verified from subtensor source (`run_coinbase.rs::inject_and_maybe_swap`): excess TAO is swapped for alpha EVERY block with NO equilibrium gate. The chain buys at any price — above equilibrium it literally buys the top. The bought alpha goes to `SubnetProtocolAlpha` (this is where proto holdings come from).

Backtest (`backtest_chain_buys.py`, 7/10/14/21/30d windows, Pearson r of cb_vs_pool vs forward return):
- ALL subnets: -0.83 (7d), -0.72 (10d), -0.46 (21d), -0.35 (14d), -0.17 (30d) — consistently NEGATIVE
- ABOVE equilibrium: -0.92, -0.68, -0.63, -0.48, -0.30 — strongly bearish in every window. High-CB half of above-eq subnets returned -24% to -31% vs -5% to +12% for low-CB half.
- BELOW equilibrium: -0.04, -0.21, -0.23, +0.06, +0.03 — scattered around zero. NO predictive power.

Interpretation: excess TAO grows mechanically as price outruns the alpha injection cap, so high CB is a SYMPTOM of overextension, not a cause of upside. Below eq it's noise — protocol buys do NOT close the valuation gap (contradicts the original convergence thesis).

Actions taken:
- Dashboard modal no longer claims "No chain buys" above eq or "only active below equilibrium"
- Bubble category shows a warning when prot_vel > 0.3%/day: "heavy chain-buy pressure above equilibrium precedes drawdowns (7d r = -0.92). The chain is buying the top."
- "Days to Equilibrium" relabeled "Theoretical Convergence Time" with explicit "Not a forecast — r ≈ 0 below equilibrium" caveat. Day counts removed from EqVel labels in table and modal.
- Composite does NOT use CB as a signal (its correlation is entirely via distance — see weight review below).

### WEIGHT REVIEW BACKTEST (July 22, 2026, `backtest_weights.py`)

Compared candidate scores using fully historical chain data (no current-snapshot leakage):
- A: raw distance_pct (inverted)
- B: current piecewise val_score (0-35)
- C: B + CB-above-eq penalty
- E: inverse CB standalone

Results (Pearson r vs fwd return, 7d/14d/30d):
- A_dist: +0.746 / +0.191 / +0.139 — BEATS the piecewise scoring at every window
- B_val:  +0.655 / +0.122 / +0.136
- C:      +0.652 / +0.122 / +0.142 — CB penalty adds NOTHING (CB correlates only via distance; it's distance wearing a costume)
- E_invcb: +0.827 / +0.344 / +0.174 — strong alone but no incremental value over distance

Lessons:
1. The piecewise val_score transform DESTROYS ~0.09 r at 7d vs raw distance. Scoring curves should be checked against the raw metric — transformations can cost signal.
2. Don't add CB to the composite in any form — no independent signal. Keep it as display/warning only.
3. Distance/valuation is horizon-dependent: monster at 7d (r≈0.75), weak at 14-30d. It's a swing-trade signal, not a position-trade signal.

=== 2. UPDATE "Trading interpretation" under Chain Buys ===

Replace:
- "High CB vs Pool % = chain is buying, but this is NOT a buy signal (see Backtest below)"
With:
- "High CB vs Pool % = chain is buying, but this is actively BEARISH above equilibrium (r = -0.92 7d). The chain buys every block at ANY price (no equilibrium gate in run_coinbase.rs) — high CB means price has outrun the injection cap, i.e., the subnet is overextended. Below equilibrium, CB is noise (r ≈ 0), NOT convergence support."

Delete/replace: "Chain buys are ongoing protocol buy PRESSURE" framing — mechanically true but predictively useless-to-inverse.

=== 3. UPDATE EqVel section ===

- Remove the claim that protocol velocity drives convergence. Replace "Days to equilibrium (no flow)" subsection with: eq_days is THEORETICAL arithmetic (gap ÷ velocity), NOT a forecast — backtest showed protocol pressure has zero correlation with actual convergence below equilibrium.
- Category table: "↑Fast / ↑Slow" descriptions should drop "converging" language → "buy pressure active".
- Add: "Bubble + prot_vel > 0.3%/day = chain buying the top, historically precedes drawdowns (7d r = -0.92)."

=== 4. UPDATE Cron Jobs Summary table ===

| Job | Schedule | Type | Deliver | Purpose |
|---|---|---|---|---|
| Lite signals | */2 * * * * | no_agent script | @dogequant | Emission toggles + price moves |
| Full signals | */15 * * * * | no_agent script | @dogequant | All signals |
| Agent analysis | 0 12-23 * * * | LLM agent (pinned z-ai/glm-5.2, provider nous) | @dogequant | Analysis of recent signals (waking hours only) |
| Holder scan | 0 * * * * | no_agent script (dtao_holder_scan.sh) | local | Hourly neuron scan |
| Ranking update | 30 * * * * | no_agent script (dtao_ranking_update.sh) | local | health_scanner → ranking → push |
| Flow scanner | 0 5 * * * | no_agent script (dtao_flow_scan.sh) | local | Daily 7d net stake flow |
| Backtest | 0 6 * * 1 | agent | local | Weekly ranking vs price test |

=== 5. ADD pitfall ===

### health_scanner must run before EVERY ranking — miner burn goes stale
`data/subnet_health.json` is the only source of miner_burn_pct for ranking + dashboard. If health_scanner isn't in the ranking cron, burn data silently goes stale (SN7 showed 80% burn for 2 days after going to 0). `dtao_ranking_update.sh` runs health_scanner.py (~35s) before ranking.py every hour. Never run ranking.py alone in automation.

### Model drift guard breaks unpinned agent crons
Hermes refuses to run unpinned agent cron jobs after the global model changes ("prevent unintended spend"). Symptom: cron reports error, output file shows RuntimeError. Fix: either pin the job (cronjob update with model+provider) or convert to no_agent script. All dTAO shell-command crons are now no_agent; the agent analysis job is pinned to z-ai/glm-5.2/nous.

=== 6. ADD to Backtesting section ===

### Chain buy backtest (`backtest_chain_buys.py`)
Tests whether protocol chain-buy pressure (cb_vs_pool, burn-adjusted prot_vel) predicts forward returns, split by above/below equilibrium. Found CB is bearish above eq (r to -0.92), noise below. Run after any EqVel logic change.

### Weight review backtest (`backtest_weights.py`)
Compares composite-score candidates on fully historical chain data (no current-snapshot leakage). Use this before changing score_valuation or adding any new composite component. Key design: only chain-state data is historically reconstructible — conviction/concept/activity are current-snapshot-only, so they can't be fairly backtested this way.
