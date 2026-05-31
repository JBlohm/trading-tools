# Iron Condor

## Overview

The iron condor is a defined-risk, four-leg options strategy that combines a short call spread (above the market) with a short put spread (below the market) in the same expiration cycle, collecting a net credit. It profits when the underlying stays within a price range through expiration.

**Outlook:** Neutral (range-bound) — you profit from low volatility and time decay  
**Risk profile:** Fully defined — max loss is the width of the wider spread minus the net credit  
**Account level required:** Standard options approval (spreads)

---

## Construction

| Leg | Action | Option type | Strike | Role |
|-----|--------|-------------|--------|------|
| 1 | Sell to open | Put | Lower OTM | Short put (lower body) |
| 2 | Buy to open | Put | Further lower OTM | Long put (lower wing) |
| 3 | Sell to open | Call | Upper OTM | Short call (upper body) |
| 4 | Buy to open | Call | Further upper OTM | Long call (upper wing) |

All four legs share the **same expiration date**.

**Example:** Underlying at $100 → Sell 95 put / Buy 90 put / Sell 105 call / Buy 110 call → 5-point-wide spreads each side.

The iron condor = short put vertical (legs 1–2) + short call vertical (legs 3–4).

---

## Entry Rules

- **DTE:** 30–45 DTE — the peak of the theta decay curve.
- **Short strikes:** Place short put and short call each at approximately **1 standard deviation OTM** (delta ~0.16–0.20 for each short leg). This gives ~68% statistical probability the underlying remains between strikes.
- **Spread width:** 5–10 points per wing for index products; proportional for lower-priced stocks.
- **Credit target:** Aim to collect **at least 1/3 of the width of one spread (one wing)**. For a 5-point wing, target ≥ $1.67 credit. This aligns with the max-loss basis: max loss is the wider single spread width minus the total credit, so the credit target is correctly framed against one wing, not the sum of both wings (only one side can be fully ITM at expiration).
- **IV rank:** IVR ≥ 30% for meaningful premium. In very high IV environments (IVR > 50%), the condor may command more than 1/3 width and offer excellent risk/reward.
- **Avoid earnings:** Do not hold an iron condor through an earnings report.

---

## Position Management

- **50% profit target:** Close the entire condor when it has decayed to **50% of the original credit**. Studies by tastylive show this materially improves the win rate while still capturing most of the premium.
- **21 DTE rule:** At 21 DTE, evaluate. If not at profit target, consider closing to avoid accelerated gamma risk.
- **Untested-side management:** When one side of the condor is challenged, the **opposite side** (untested/winning side) has often decayed significantly. You can close the profitable side for a small debit and convert the condor into a single-leg credit spread, reducing overall risk and required margin.
- **Rolling the threatened side:** If the underlying approaches a short strike, **roll that spread** (both legs) further OTM in the same expiration or to a later expiration for a net credit. Rolling extends duration and moves the strike further away from the price.
- **Never roll for a debit** — if you cannot roll for a credit, it is better to take the loss and close.

---

## Exit / Closure

| Scenario | Action |
|----------|--------|
| Condor decays to 50% of credit | Buy back all four legs; close the trade |
| Both spreads expire OTM | Full credit kept; no further action |
| One side threatened (price near short strike) | Roll threatened side OTM/out-in-time; optionally close untested side |
| Stop-loss: loss = 2× credit collected | Close the entire condor |
| Inside 21 DTE, not at target | Evaluate: close or roll out |
| Expiration between short strikes | Max profit zone; let expire or close day-of |

---

## Key Metrics

| Metric | Formula |
|--------|---------|
| Max profit | Net credit collected |
| Max loss | (Wider spread width − Net credit) × 100 per contract |
| Breakeven (downside) | Short put strike − Net credit |
| Breakeven (upside) | Short call strike + Net credit |
| Profit zone | Underlying stays between the two short strikes at expiration |
| Capital at risk (margin) | (Wider spread width − Net credit) × 100 |

**Example (two 5-point wings, $3.00 credit):**
- Max profit: $300
- Max loss: $(5.00 − 3.00) × 100 = $200
- Downside breakeven: 95 − 3.00 = $92
- Upside breakeven: 105 + 3.00 = $108

---

## Relationship to Other Strategies

- **Iron butterfly:** An iron condor with the two short strikes moved to the same ATM strike — tighter body, higher credit, narrower profit zone.
- **Short strangle:** An iron condor without the protective long wings (undefined risk).
- **Short put vertical:** The lower half of the condor.
- **Short call vertical:** The upper half of the condor.

---

## Practical Notes (tastytrade / tastylive approach)

- The iron condor is one of the most-used tastytrade strategies for neutral markets.
- **Symmetric vs. asymmetric condors:** You can place strikes equidistant from the current price (symmetric) or skew one side further OTM based on a mild directional view (asymmetric).
- Use **index ETFs (SPY, QQQ, IWM)** for liquid, well-behaved underlyings with tight bid-ask spreads.
- Tastylive's research shows that **closing at 50% of max profit** increases the trade win rate from ~50% to >60% compared with holding to expiration.
- **Correlation risk:** Multiple iron condors on highly correlated assets can behave like a single large position in a market move — diversify across uncorrelated underlyings.
