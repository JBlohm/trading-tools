# Long Call Vertical Spread (Bull Call Spread)

## Overview

A long call vertical spread (also called a bull call spread or debit call spread) is a defined-risk, bullish options strategy. You buy a call closer to the money and sell a further OTM call at a higher strike in the same expiration, paying a net debit. The short call reduces the cost of the long call but caps the upside.

**Outlook:** Bullish (you profit when the underlying rises above the long call strike by expiration)  
**Risk profile:** Defined — max loss is the net debit paid; max gain is capped at the spread width minus the debit  
**Account level required:** Standard options approval (spreads)

---

## Construction

| Leg | Action | Option type | Strike |
|-----|--------|-------------|--------|
| 1 | Buy to open | Call | Lower (ATM or slightly OTM) |
| 2 | Sell to open | Call | Higher (further OTM) |

Both options share the **same expiration date**.

**Example:** Underlying at $100 → Buy the 100 call, Sell the 105 call → 5-point-wide spread, net debit of $2.00.

---

## Entry Rules

- **DTE:** 30–60 DTE gives the trade time to develop; shorter DTE increases the need for a quick move.
- **Strike — long call (leg 1):** At-the-money (ATM) or slightly OTM (delta ~0.40–0.55). This is the primary directional driver.
- **Strike — short call (leg 2):** At or near the expected target price for the underlying (delta ~0.20–0.30); typically 5–10 points above leg 1.
- **Cost target:** The debit should be **no more than 50% of the spread width**. Paying more than half the width means the breakeven is too far from the current price.
- **IV environment:** Prefer entering in **low-to-moderate IV** environments. High IV inflates the debit cost and the breakeven is harder to reach.
- **Directional catalyst:** Use when you have a specific bullish thesis (technical breakout, fundamental catalyst, expected trend continuation).

---

## Position Management

- **Profit target:** Close the spread when it reaches **50–75% of max profit** (when the spread is worth 2.50–3.75 on a 5-point, $2.00-debit spread).
- **Early exit:** If the trade moves strongly in your favour quickly, take profits early rather than waiting for expiration; time decay works against long premium positions.
- **Stop-loss:** Close the position if the debit loses **50% of its value** (the $2.00 debit falls to $1.00). This preserves capital.
- **Rolling:** If the trade is near expiration but not yet profitable, rolling out in time by closing and re-entering at the same or adjusted strikes costs additional debit; evaluate carefully whether the thesis still holds.

---

## Exit / Closure

| Scenario | Action |
|----------|--------|
| Spread reaches 50–75% of max profit | Close the full spread; take gains |
| Underlying surges well above short strike | Spread approaches max value (spread width); close to lock in gains |
| Underlying stays flat near entry | Time decay hurts; close at stop-loss level |
| Underlying drops | Close at stop-loss (50% of debit paid) to limit loss |
| Expiration with underlying between strikes | Partial profit; close before expiration to avoid assignment risk |
| Expiration with underlying above short strike | Max profit achieved; let expire or close day-of |

---

## Key Metrics

| Metric | Formula |
|--------|---------|
| Max profit | (Spread width − Net debit) × 100 per contract |
| Max loss | Net debit paid × 100 per contract |
| Breakeven at expiration | Long call strike + Net debit |
| Profit zone | Underlying closes above the long call strike + debit by expiration |

**Example (5-point spread, $2.00 debit):**
- Max profit: $(5.00 − 2.00) × 100 = $300
- Max loss: $2.00 × 100 = $200
- Breakeven: $100 + $2.00 = $102

---

## Relationship to Other Strategies

- **Short put vertical:** Achieves a similar bullish, defined-risk profile but via a credit (premium selling) rather than a debit.
- **Long call (naked):** Removes the short call cap and debit reduction — more directional leverage but higher cost.
- **Iron condor:** The short call vertical component of a long call spread can be combined with puts to build a condor.

---

## Practical Notes (tastytrade / tastylive approach)

- Debit spreads are appropriate when you have a **specific directional view** and want defined risk.
- Tastytrade leans toward credit strategies, but long call verticals are useful when IV is **low** (options are cheap) and a directional move is expected.
- Size positions so max loss is **no more than 2–5% of account value**.
- The long leg of a vertical spread has positive theta cost (time decay hurts you), so this strategy benefits from fast moves rather than patient, slow grinds.
