# Long Put Vertical Spread (Bear Put Spread)

## Overview

A long put vertical spread (also called a bear put spread or debit put spread) is a defined-risk, bearish options strategy. You buy a put closer to the money and sell a further OTM put at a lower strike in the same expiration, paying a net debit. The short put reduces cost but caps the downside profit.

**Outlook:** Bearish (you profit when the underlying falls below the long put strike by expiration)  
**Risk profile:** Defined — max loss is the net debit paid; max gain is capped at the spread width minus the debit  
**Account level required:** Standard options approval (spreads)

---

## Construction

| Leg | Action | Option type | Strike |
|-----|--------|-------------|--------|
| 1 | Buy to open | Put | Higher (ATM or slightly OTM) |
| 2 | Sell to open | Put | Lower (further OTM) |

Both options share the **same expiration date**.

**Example:** Underlying at $100 → Buy the 100 put, Sell the 95 put → 5-point-wide spread, net debit of $2.00.

---

## Entry Rules

- **DTE:** 30–60 DTE gives the trade time to develop.
- **Strike — long put (leg 1):** ATM or slightly OTM (delta ~−0.40 to −0.55).
- **Strike — short put (leg 2):** At or near the expected downside target (delta ~−0.20 to −0.30); typically 5–10 points below leg 1.
- **Cost target:** Net debit should be **no more than 50% of the spread width**.
- **IV environment:** Prefer low-to-moderate IV environments. High IV inflates debit cost and makes the breakeven harder to reach.
- **Directional catalyst:** Use when you have a specific bearish thesis (technical breakdown, deteriorating fundamentals, negative catalyst).

---

## Position Management

- **Profit target:** Close when spread reaches **50–75% of max profit**.
- **Early exit:** Take profits on a strong downward move rather than waiting for expiration.
- **Stop-loss:** Close the position if the debit loses **50% of its value** to cap losses.
- **Rolling:** If the underlying hasn't moved by mid-life, evaluate whether to close and re-enter or exit; rolling costs additional debit.

---

## Exit / Closure

| Scenario | Action |
|----------|--------|
| Spread reaches 50–75% of max profit | Close the full spread; take gains |
| Underlying plunges well below short strike | Spread approaches max value; close to lock in gains |
| Underlying stays flat or rises | Close at stop-loss (50% of debit paid) |
| Expiration with underlying between strikes | Partial profit; close before expiration |
| Expiration with underlying below short strike | Max profit; close day-of or let expire |

---

## Key Metrics

| Metric | Formula |
|--------|---------|
| Max profit | (Spread width − Net debit) × 100 per contract |
| Max loss | Net debit paid × 100 per contract |
| Breakeven at expiration | Long put strike − Net debit |
| Profit zone | Underlying closes below the long put strike − debit by expiration |

**Example (5-point spread, $2.00 debit):**
- Max profit: $(5.00 − 2.00) × 100 = $300
- Max loss: $2.00 × 100 = $200
- Breakeven: $100 − $2.00 = $98

---

## Relationship to Other Strategies

- **Short call vertical:** Achieves a similar bearish, defined-risk profile but via a credit.
- **Long put (naked):** No short put cap — more directional leverage but higher cost.
- **Iron condor / Condor:** The long put spread can serve as the put wing of a condor structure.

---

## Practical Notes (tastytrade / tastylive approach)

- Bear put spreads are appropriate for **specific bearish directional views** with defined risk.
- Tastytrade uses these primarily in low-IV environments when buying premium is cost-effective.
- Avoid holding through binary events (earnings) unless the strategy is built around the event.
- Time decay (negative theta) works against you as the holder of a debit spread — you need a move, not patience.
- Size positions so max loss is **no more than 2–5% of account value**.
