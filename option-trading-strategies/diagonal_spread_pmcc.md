# Diagonal Spread / Poor Man's Covered Call (PMCC)

## Overview

A diagonal spread combines a **calendar spread** (different expirations) with a **vertical spread** (different strikes). The most common application is the **Poor Man's Covered Call (PMCC)**: a deep ITM long-dated call (acting as a stock substitute) combined with a short OTM near-term call. It mimics a covered call but at a fraction of the capital cost.

**Outlook:** Neutral to moderately bullish  
**Risk profile:** Defined — max loss is the net debit paid (no stock ownership risk)  
**Account level required:** Standard options approval (spreads); some brokers treat as naked due to different expirations

---

## Variants

### Call Diagonal (PMCC)

| Leg | Action | Option type | Strike | Expiration |
|-----|--------|-------------|--------|------------|
| 1 | Buy to open | Call | Deep ITM (delta ~0.70–0.90) | Long-dated (6–12+ months out, or LEAPS) |
| 2 | Sell to open | Call | OTM (delta ~0.20–0.30) | Near-term (30–45 DTE) |

**Example:** Underlying at $100 → Buy 80 call expiring in 12 months (delta ~0.80) / Sell 105 call expiring in 30 days (delta ~0.25).

### Put Diagonal (Bearish)

| Leg | Action | Option type | Strike | Expiration |
|-----|--------|-------------|--------|------------|
| 1 | Buy to open | Put | Deep ITM (delta ~−0.70 to −0.90) | Long-dated |
| 2 | Sell to open | Put | OTM (delta ~−0.20 to −0.30) | Near-term |

---

## Why "Poor Man's" Covered Call?

A standard covered call requires purchasing 100 shares (e.g., $10,000 for a $100 stock) plus selling a call. The PMCC replaces the shares with a deep ITM LEAPS call that has a delta near 1.0 — it moves nearly like the stock but costs a fraction. The strategy generates ongoing premium income by repeatedly selling near-term OTM calls against the LEAPS.

---

## Entry Rules

- **Long leg (LEAPS / back month):**
  - Buy a call with **delta ≥ 0.70–0.80** (deep ITM).
  - Expiration: At least **6–12 months out** (LEAPS, 1–2 years preferred). The more time, the better — you need time value to justify the back-month cost.
  - Choose a strike roughly **20–30% below the current stock price** for adequate delta.

- **Short leg (front month):**
  - Sell an OTM call with **delta ~0.20–0.30** (same as a standard covered call).
  - Expiration: **30–45 DTE**.
  - Strike must be **higher than or equal to the long leg's strike** to avoid creating a vertical debit spread (a requirement for the PMCC to work correctly).

- **Debit target / setup quality check (tastylive):** Two tests must both pass:
  1. **Credit covers extrinsic value:** The near-term short call credit collected should be **≥ the extrinsic value (time value) of the long LEAPS call**. This ensures the short leg is paying for the time value you are long, making the position self-funding from the outset.
  2. **75% of strike width rule:** The net debit paid should be **no more than 75% of the spread width** (distance between the two strikes). If debit > 75% of width, the trade is too expensive relative to its upside — pass on the setup.

- **Break-even check:** Confirm that repeatedly selling front-month calls at the target credit can eventually recover the full debit paid — model out the expected number of monthly cycles.

---

## Position Management

### Managing the Short Front-Month Call

- **Profit target:** Close the short call at **50% of the credit collected** (same as a standard covered call management rule).
- **At 21 DTE:** Roll the short call out to the next 30–45 DTE cycle. Sell the same delta (~0.20–0.30) call for the new expiration.
- **If the front month call moves ITM (stock rallies):**
  - Roll the short call **up and out** — buy back the current short, sell a new short at a higher strike in the next expiration for a net credit or small debit.
  - Do NOT let the short call expire ITM without addressing it — the long LEAPS will cover assignment but the mechanics are complex.

### Managing the Long LEAPS Call

- **Refresh LEAPS annually:** When the LEAPS has ~6 months of time remaining, consider closing and reopening at a further-dated expiration to maintain the long time-value cushion.
- **Delta drift:** If the underlying moves sharply, the LEAPS delta will change. If the underlying falls significantly, the LEAPS may lose intrinsic value — consider whether to hold or exit the entire position.
- **Roll out in time:** As the LEAPS approaches nearer expiration, roll it forward (sell the old, buy a new longer-dated call at a similar or lower strike).

---

## Exit / Closure

| Scenario | Action |
|----------|--------|
| Front-month short reaches 50% profit | Buy back; sell new call for next cycle |
| Stock rises above short strike | Roll short call up and out; keep LEAPS |
| Full PMCC has recovered the initial debit | Consider exiting or continuing for pure profit |
| Stock drops sharply | LEAPS declines; evaluate stop-loss on entire position |
| LEAPS has < 6 months remaining | Roll LEAPS forward to a longer expiration |
| Exit entirely | Close both legs simultaneously |

---

## Key Metrics

| Metric | Formula |
|--------|---------|
| Net debit (initial cost) | LEAPS premium − Short call premium collected |
| Max loss | Net debit paid (if LEAPS expires worthless and all short calls collected go to zero) |
| Profit potential | Ongoing short-call premium collected minus the cost of the LEAPS |
| Breakeven | (Net debit − Total short-call premium collected) must reach zero |
| Capital efficiency | ~3–5× more capital-efficient than a traditional covered call on same underlying |

---

## Critical Rule

The short front-month call's strike must **always be higher than or equal to the LEAPS long call's strike**. If the short call's strike is *below* the LEAPS strike, you have created a net-debit vertical spread with capped upside — the PMCC structure is broken.

---

## Relationship to Other Strategies

- **Covered call:** PMCC is a capital-efficient substitute — replaces 100 shares with a deep ITM LEAPS.
- **Calendar spread:** PMCC uses different strikes; a calendar uses the same strike.
- **Bull call vertical:** If you close a PMCC at any point, the residual position (long LEAPS vs. short call) is effectively a long call diagonal/vertical.

---

## Practical Notes (tastytrade / tastylive approach)

- The PMCC is ideal for traders who want **covered call income on high-priced stocks** (e.g., AMZN, GOOGL) without committing the full capital for 100 shares.
- The deep ITM LEAPS must be on a **liquid underlying** with a tight LEAPS bid-ask spread to avoid high slippage costs.
- The strategy works best on **slowly rising or range-bound stocks** — similar to a covered call, aggressive upside moves may cap gains.
- Track the **cost basis** of the LEAPS meticulously. Once cumulative short-call credits exceed the original LEAPS debit, you are "playing with house money."
