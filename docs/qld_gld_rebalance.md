# QLD / GLD Monthly Rebalance — Operator Manual

> **Strategy:** 30% ProShares Ultra QQQ (QLD) / 70% SPDR Gold Shares (GLD), monthly rebalance.
> **Account baseline:** $25,000.
> **Backtest result (TRA-72):** 17.50% CAGR | Max drawdown -29.38% | Worst year -21.42% | SPY CAGR 12.58% over same period.

---

## Why This Should Work

Gold (GLD) acts as a macro hedge — it rises when real rates fall and when equity stress peaks,
the exact opposite of what a risk-on 2x equity fund does. Holding 70% GLD as ballast dampens
the extreme volatility of 30% QLD. The monthly rebalance systematically buys the laggard and
sells the leader, harvesting a rebalancing premium over the long run.

**Edge source:** Structural diversification between a 2x leveraged equity ETF and a commodity
inflation hedge, combined with a simple fixed-weight discipline that prevents drift into
concentrated risk.

**Acknowledged limitations:**
- QLD's 2x daily leverage causes volatility decay (beta-slippage) in choppy, non-trending markets.
- Worst annual loss of -21.42% must be accepted without panic-selling — see Risk section below.
- No discretionary override in code; do not infer one.

---

## Operating Rules

### 1. Monthly Rebalance Timing

- Rebalance on the **first trading day of each calendar month** (the first day the US equity market is open: NYSE/NASDAQ session).
- Acceptable window: **first three trading days** if the first day falls on a holiday or the order cannot be filled cleanly.
- Do not rebalance intra-month except when drift tolerance is breached (see below).

### 2. Drift Tolerance

A mid-month rebalance is triggered only when drift exceeds **±5 percentage points** from the target weight:

| Symbol | Target | Trigger band |
|--------|--------|-------------|
| QLD | 30% | < 25% or > 35% |
| GLD | 70% | < 65% or > 75% |

Use `python tools/qld_gld_rebalance.py` to check current drift. If `rebalance_needed` is `true`,
act on the next available trading day during market hours.

### 3. Position Sizing

All sizing is computed by the tool. Rules enforced:

- **QLD target notional:** 30% × account value → floor-divide by QLD price → integer shares.
- **GLD target notional:** 70% × account value → floor-divide by GLD price → integer shares.
- Cash residual (fractional-share remainder) stays idle in account — do not chase.
- Do not scale position size up; do not use margin.

### 4. Risk Points (Hard Stops)

| Symbol | Stop distance | Rationale |
|--------|--------------|-----------|
| QLD | -15% from entry price | 2x leveraged; gap-down risk is elevated |
| GLD | -8% from entry price | Commodity drawdowns can be sharp but recoverable |

Place stop-loss orders **immediately after each rebalance fill**. The tool outputs `risk_point`
for each leg in the order intent.

**Do not move stops to breakeven or trail intra-month.** Let the monthly rebalance cycle control exposure.

### 5. No Averaging Losers

This is a hard desk rule. If QLD or GLD is down from entry:

- Do **not** add to the losing leg between rebalances.
- The monthly rebalance may mathematically increase the losing leg if drift has moved it below target — this is allowed because it is the systematic rule, not a discretionary decision.
- Discretionary overrides are not permitted in code and not permitted at the desk.

### 6. Order Execution

Use MOO (Market-on-Open) or LOO (Limit-on-Open) orders at the monthly rebalance session:

```
Preferred:  MOO on first trading day of month
Fallback:   LOO within 2% of prior close
Emergency:  Market order before noon ET if MOO rejected
```

Execute QLD first (smaller notional, faster fill), then GLD.

### 7. Failure Handling

| Failure type | Action |
|---|---|
| Data feed down (cannot get current prices) | Hold existing positions; retry next business day |
| Order rejected / partial fill on QLD | Fill GLD anyway; resubmit QLD next trading day |
| Order rejected / partial fill on GLD | Fill QLD anyway; resubmit GLD next trading day |
| Both fills fail | Hold; alert desk owner; retry next trading day |
| TWS / broker outage > 3 days | Escalate to desk owner; do not panic-sell |

Do not close both legs simultaneously due to a short-term outage. The portfolio is designed to hold through monthly periods.

---

## Risk Warnings

### QLD — 2x Daily Leveraged ETF

> **QLD seeks to provide 2x the *daily* return of the Nasdaq-100 index, not 2x the long-term return.**

Key risks:
- **Volatility decay:** In choppy, sideways markets, compounding of daily resets erodes NAV even if the index is flat over the period.
- **Gap-down risk:** QLD can open 10–20% below prior close during overnight events (earnings, macro shocks). The -15% stop does not protect against opening below the stop.
- **Drawdown depth:** QLD lost >80% in 2000–2002 and >70% in 2008. The 30% allocation limits portfolio exposure, but the QLD leg can still become nearly worthless.
- **No averaging down:** Once the -15% stop fires, exit fully. Do not re-enter until next monthly rebalance decision.

### GLD — Gold Commodity ETF

- Less volatile than QLD, but can draw down 30–40% in sustained dollar-strength environments.
- Provides diversification benefit but is not "safe haven" in all macro regimes.

### Maximum Drawdown Acceptance

The backtest shows **-29.38% max drawdown** and a **-21.42% worst year**. To benefit from the 17.50% CAGR, the desk must hold through these drawdowns without deviating from the monthly rebalance rule.

---

## Tool Usage

```bash
# Check current state and generate rebalance orders for existing positions:
python tools/qld_gld_rebalance.py \
    --qld-price 85.50 --qld-shares 87 \
    --gld-price 195.20 --gld-shares 90 \
    --account 25000

# Compute initial buy orders (no existing positions):
python tools/qld_gld_rebalance.py \
    --qld-price 85.50 \
    --gld-price 195.20 \
    --initial-buy

# Dry run (uses placeholder prices — for testing/CI only):
python tools/qld_gld_rebalance.py --dry-run
```

### Example output (initial buy, QLD=$85.50, GLD=$195.20, account=$25k)

```json
{
  "strategy": "lbr_tactical_rotation_qld_gld",
  "account_value": 25000.0,
  "rebalance_needed": false,
  "drift_tolerance_pct": 5.0,
  "current_state": {
    "QLD": {"shares": 0, "price": 85.50, "market_value": 0.0, "weight": 0.0},
    "GLD": {"shares": 0, "price": 195.20, "market_value": 0.0, "weight": 0.0},
    "total_portfolio_value": 0.0
  },
  "order_intents": [
    {
      "symbol": "QLD",
      "side": "BUY",
      "quantity": 87,
      "price_ref": 85.50,
      "target_shares": 87,
      "estimated_notional": 7438.5,
      "risk_point": 72.67
    },
    {
      "symbol": "GLD",
      "side": "BUY",
      "quantity": 89,
      "price_ref": 195.20,
      "target_shares": 89,
      "estimated_notional": 17372.8,
      "risk_point": 179.58
    }
  ]
}
```

---

## Backtest Reference

Results from TRA-72 (relaxed mandate, static allocations):

| Metric | Value |
|--------|-------|
| CAGR | 17.50% |
| Ending equity ($25k start) | $133,572 |
| Max drawdown | -29.38% |
| Worst year | -21.42% |
| SPY CAGR (same period) | 12.58% |

Full backtest scaffold: `backtesting/lbr_tactical_rotation/`
Full results report: `backtesting/lbr_tactical_rotation/results/relaxed_mandate_static_allocations.md`
