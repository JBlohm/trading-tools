# Short Call Vertical Spread (Bear Call Spread)

## Overview

A short call vertical spread (also called a bear call spread or credit call spread) is a defined-risk, bearish options strategy. You sell an OTM call and buy a further OTM call at a higher strike in the same expiration, collecting a net credit. The long call limits maximum loss.

**Outlook:** Neutral to bearish (you profit when the underlying stays below the short call strike)  
**Risk profile:** Defined — max loss is the spread width minus the net credit  
**Account level required:** Standard options approval (spreads)

---

## Construction

| Leg | Action | Option type | Strike |
|-----|--------|-------------|--------|
| 1 | Sell to open | Call | Lower (closer to ATM) |
| 2 | Buy to open | Call | Higher (further OTM) |

Both options share the **same expiration date**. The difference between strikes is the **spread width**.

**Example:** Underlying at $100 → Sell the 105 call, Buy the 110 call → 5-point-wide spread.

---

## Entry Rules

- **DTE:** 30–45 DTE.
- **Strike — short call (leg 1):** Delta ~0.20–0.30 (OTM, ~20–30% probability of expiring ITM).
- **Strike — long call (leg 2):** 5–10 points above the short call.
- **Credit target:** Collect **at least 1/3 of the spread width**. On a 5-point spread, target ≥ $1.65 credit.
- **IV rank:** Enter when IVR ≥ 30%.
- **Avoid earnings:** Avoid carrying the position through earnings announcements.

---

## Position Management

- **50% profit target:** Buy back the spread when it has decayed to 50% of the original credit.
- **21 DTE rule:** At 21 DTE, evaluate. If not at profit target, consider closing or rolling out in time.
- **Defending the trade:** If the underlying rises toward the short call strike, roll the **entire spread** up and out in time for a net credit.
- **Untested-side management:** As part of an iron condor, if the put side is threatened, the call spread (untested side) can be closed cheaply to reduce overall risk.

---

## Exit / Closure

| Scenario | Action |
|----------|--------|
| Spread decays to 50% of credit | Buy back the full spread; close the trade |
| Both options expire OTM | Full credit is kept |
| Underlying rises toward short call | Roll the spread up and out for a net credit |
| Stop-loss: loss = 2× credit | Buy back the spread |
| Inside 21 DTE, not at 50% | Evaluate: close or roll out in time |

---

## Key Metrics

| Metric | Formula |
|--------|---------|
| Max profit | Net credit collected |
| Max loss | (Spread width − Net credit) × 100 per contract |
| Breakeven at expiration | Short call strike + Net credit |
| Profit zone | Underlying closes below the short call strike at expiration |
| Capital at risk (margin) | (Spread width − Net credit) × 100 per contract |

**Example (5-point spread, $1.80 credit):**
- Max profit: $180
- Max loss: $(5.00 − 1.80) × 100 = $320
- Breakeven: 105 + 1.80 = $106.80

---

## Relationship to Other Strategies

- **Iron condor:** Add a short put vertical spread below the market to convert this into an iron condor.
- **Naked short call:** The short leg alone carries unlimited theoretical risk; the long call converts it to a defined-risk spread.
- **Bear put spread:** Similar bearish defined-risk structure, but uses puts and costs a debit.

---

## Practical Notes (tastytrade / tastylive approach)

- The short call vertical is the **upper wing** of an iron condor.
- Because call premium can expand sharply in a short squeeze or gap-up event, managing at 50% profit is particularly important to avoid giving back gains.
- Keep spread widths consistent with the underlying's price range and your account size.
- Sell call spreads in high-IV environments to capture elevated premium; in low-IV environments, the credit may not justify the risk.
