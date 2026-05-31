# Short Put Vertical Spread (Bull Put Spread)

## Overview

A short put vertical spread (also called a bull put spread or credit put spread) is a defined-risk, bullish options strategy. You sell an OTM put and buy a further OTM put at a lower strike in the same expiration, collecting a net credit. The long put limits maximum loss, making the trade fully defined.

**Outlook:** Neutral to bullish (you profit when the underlying stays above the short put strike)  
**Risk profile:** Defined — max loss is the spread width minus the net credit  
**Account level required:** Standard options approval (spreads)

---

## Construction

| Leg | Action | Option type | Strike |
|-----|--------|-------------|--------|
| 1 | Sell to open | Put | Higher (closer to ATM) |
| 2 | Buy to open | Put | Lower (further OTM) |

Both options share the **same expiration date**. The difference between strikes is the **spread width**.

**Example:** Underlying at $100 → Sell the 95 put, Buy the 90 put → 5-point-wide spread.

---

## Entry Rules

- **DTE:** 30–45 DTE.
- **Strike — short put (leg 1):** Delta ~0.20–0.30 (OTM, ~20–30% probability of expiring ITM).
- **Strike — long put (leg 2):** 5–10 points below the short put; wide enough to keep margin low but narrow enough to still collect a meaningful credit.
- **Credit target:** Aim to collect **at least 1/3 of the spread width**. On a 5-point spread, target ≥ $1.65 credit. A credit below this threshold often signals poor risk/reward.
- **IV rank:** Enter when IVR ≥ 30% for maximum premium collection.
- **Avoid earnings:** Do not enter through an upcoming earnings event unless you specifically want the IV crush.

---

## Position Management

- **50% profit target:** Close the spread when it has decayed to 50% of the original credit (buy the spread back for half the credit received). This is the tastytrade standard.
- **21 DTE rule:** At 21 DTE, evaluate the position. If not yet at 50% profit, consider closing to avoid accelerated gamma risk in the final weeks. If still comfortable with the trade, keep it but monitor daily.
- **Defending the trade — rolling:** If the underlying drops toward the short put strike, roll the **entire spread** down and out in time for a net credit. Never roll for a debit.
- **Untested-side logic:** Short put verticals are often the put-side leg of an iron condor. If used as a stand-alone, treat any threat to the short strike as a signal to manage.

---

## Exit / Closure

| Scenario | Action |
|----------|--------|
| Spread decays to 50% of credit | Buy back the full spread; close the trade |
| Both options expire OTM | Full credit is kept; no further action needed |
| Underlying approaches short put strike | Roll the spread down and out for a net credit |
| Stop-loss: loss = 2× credit collected | Buy back the spread to limit further damage |
| Inside 21 DTE, not at 50% | Evaluate: close or roll out in time |

---

## Key Metrics

| Metric | Formula |
|--------|---------|
| Max profit | Net credit collected |
| Max loss | (Spread width − Net credit) × 100 per contract |
| Breakeven at expiration | Short put strike − Net credit |
| Profit zone | Underlying closes above the short put strike at expiration |
| Capital at risk (margin) | (Spread width − Net credit) × 100 per contract |

**Example (5-point spread, $1.80 credit):**
- Max profit: $180
- Max loss: $(5.00 − 1.80) × 100 = $320
- Breakeven: 95 − 1.80 = $93.20

---

## Relationship to Other Strategies

- **Iron condor:** Add a short call vertical spread above the market to convert this into an iron condor.
- **Cash-secured put:** The short leg alone (without the long put) is a cash-secured/naked put with undefined risk.
- **Bull call spread:** Similar bullish defined-risk structure, but uses calls and costs a debit rather than a credit.

---

## Practical Notes (tastytrade / tastylive approach)

- Collect **≥ 1/3 of the spread width** as a minimum premium threshold; lower premiums do not compensate for the risk.
- Use spread widths of **5–10 points** on equity indexes; narrower spreads are acceptable on lower-priced stocks.
- Keep position sizing consistent: risking no more than **2–5% of account value** per spread trade.
- The defined-risk nature makes this strategy suitable for smaller accounts or for deploying in high-IV environments where naked puts would tie up too much capital.
