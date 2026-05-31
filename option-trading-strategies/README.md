# Option Trading Strategies

This folder contains detailed reference files for option strategies, based on the tastytrade/tastylive "Mike and His Whiteboard" educational series. Each file covers the full lifecycle of a strategy: construction, entry rules, position management, and exit/closure.

## Strategy Index

### Premium-Selling Strategies (Neutral / Income)

| File | Strategy | Risk | Outlook |
|------|----------|------|---------|
| [covered_call.md](covered_call.md) | Covered Call | Defined (stock downside) | Neutral to bullish |
| [cash_secured_put.md](cash_secured_put.md) | Cash-Secured Put | Defined (assignment) | Neutral to bullish |
| [wheel_strategy.md](wheel_strategy.md) | Wheel Strategy | Defined (stock downside) | Neutral to bullish |
| [short_strangle.md](short_strangle.md) | Short Strangle | Undefined | Neutral |
| [short_straddle.md](short_straddle.md) | Short Straddle | Undefined | Strictly neutral |

### Defined-Risk Credit Spreads

| File | Strategy | Risk | Outlook |
|------|----------|------|---------|
| [short_put_vertical_spread.md](short_put_vertical_spread.md) | Short Put Vertical (Bull Put Spread) | Defined | Neutral to bullish |
| [short_call_vertical_spread.md](short_call_vertical_spread.md) | Short Call Vertical (Bear Call Spread) | Defined | Neutral to bearish |
| [iron_condor.md](iron_condor.md) | Iron Condor | Defined | Neutral |
| [iron_butterfly.md](iron_butterfly.md) | Iron Butterfly | Defined | Strictly neutral |

### Defined-Risk Debit Spreads (Directional)

| File | Strategy | Risk | Outlook |
|------|----------|------|---------|
| [long_call_vertical_spread.md](long_call_vertical_spread.md) | Long Call Vertical (Bull Call Spread) | Defined | Bullish |
| [long_put_vertical_spread.md](long_put_vertical_spread.md) | Long Put Vertical (Bear Put Spread) | Defined | Bearish |

### Time / Volatility Strategies

| File | Strategy | Risk | Outlook |
|------|----------|------|---------|
| [calendar_spread.md](calendar_spread.md) | Calendar Spread (Time Spread) | Defined (debit) | Neutral / IV expansion |
| [diagonal_spread_pmcc.md](diagonal_spread_pmcc.md) | Diagonal Spread / Poor Man's Covered Call | Defined (debit) | Neutral to bullish |

---

## Core Principles (tastylive / Mike and His Whiteboard)

1. **Sell premium in high-IV environments** — enter when IV rank (IVR) ≥ 30%.
2. **Manage winners at 50% of max profit** — don't wait for expiration; take gains early.
3. **Use the 21 DTE rule** — evaluate or close positions inside 21 days to expiration to avoid gamma risk.
4. **Target 30–45 DTE** for new entries — optimal theta decay curve.
5. **Define your risk** — prefer defined-risk spreads in smaller accounts; undefined risk (strangles/straddles) requires margin and careful position sizing.
6. **Position sizing** — risk no more than 2–5% of account value per trade.
7. **Roll for credit only (short-premium default)** — for short-premium strategies (strangles, straddles, iron condors, verticals), never roll for a net debit; if a roll isn't available for a credit, take the loss. For debit strategies (debit spreads, calendars, PMCC/diagonals), closing and reopening or accepting a small debit on a roll may be valid if the thesis and risk still justify the position.
8. **1 standard deviation (16-delta) rule** — place short strikes approximately 1σ OTM for a ~68% probability of success.

---

## Order-Entry and Monitoring Checklist

Use this checklist at every trade entry and throughout the position lifecycle.

### At Entry
- [ ] **Multi-leg limit order** — always enter spreads, condors, and calendars as a single multi-leg order; never leg into a spread manually.
- [ ] **Work near mid-price** — place the limit at the mid-point of the bid/ask; adjust in small increments if not filled within 30–60 seconds.
- [ ] **Confirm buying power / max loss** — verify the trade's margin requirement and maximum defined loss before sending the order.
- [ ] **Record trade details** — log the following at entry: underlying, strategy, strikes/expirations, credit or debit, IVR at entry, DTE at entry, profit target ($ or %), and stop/loss rule.

### During the Trade
- [ ] **21-DTE alert** — set a calendar reminder or platform alert for 21 DTE on every short-premium position; evaluate whether to close, roll, or hold through expiration.
- [ ] **Profit-target alert** — set a GTC closing order or platform alert at the profit target (commonly 50% of credit for short premium, 25–50% of debit for debit strategies).
- [ ] **Assign monitoring responsibility** — confirm who is watching each open position for breach of stops or rolling triggers (especially for undefined-risk trades).

### At Adjustment / Roll
- [ ] Confirm the roll meets the credit or debit rule for the strategy type (see Core Principle 7 above).
- [ ] Re-record revised thesis, new strikes/expirations, and updated max loss after the adjustment.

---

## Source

Strategies are based on the [tastytrade "Mike and His Whiteboard" series](https://tastytrade.com/shows/mike-and-his-whiteboard/) and tastylive educational content.
