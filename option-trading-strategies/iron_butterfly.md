# Iron Butterfly

## Overview

The iron butterfly is a defined-risk, four-leg options strategy that combines a short at-the-money (ATM) straddle with two long OTM options on the wings. It is a tighter, higher-premium version of an iron condor — the two short strikes converge to the same ATM price, creating a narrow profit zone but a larger credit.

**Outlook:** Strictly neutral — you profit from a very tight range and maximum time decay  
**Risk profile:** Defined — max loss is the width of one wing minus the net credit  
**Account level required:** Standard options approval (spreads)

---

## Construction

| Leg | Action | Option type | Strike | Role |
|-----|--------|-------------|--------|------|
| 1 | Buy to open | Put | Lower OTM | Long put wing |
| 2 | Sell to open | Put | ATM (same as call) | Short put body |
| 3 | Sell to open | Call | ATM (same as put) | Short call body |
| 4 | Buy to open | Call | Upper OTM | Long call wing |

All four legs share the **same expiration date**. Legs 2 and 3 are at the **same ATM strike**, forming a short straddle.

**Alternatively viewed as:** Short ATM straddle + long OTM strangle (the wings).

**Example:** Underlying at $100 → Buy 90 put / Sell 100 put / Sell 100 call / Buy 110 call → 10-point-wide wings, ATM short straddle at 100.

---

## Entry Rules

- **DTE:** 30–45 DTE — captures the peak theta decay curve.
- **Short strikes:** Both at-the-money (delta ~0.50 each for the short put and short call).
- **Wing strikes:** Equal distance OTM on each side (symmetric), typically 5–10 points from the ATM short strike.
- **Credit target:** Iron butterflies typically collect **40–50% of the wing width**. On a 10-point wing, target ≥ $4.00–5.00 credit. The high credit reflects the ATM nature of the short straddle.
- **IV rank:** Works best in **moderate-to-high IV environments (IVR ≥ 30–50%)** where the ATM options carry substantial premium.
- **Avoid earnings:** Do not hold through binary events; the single ATM body is highly exposed to large moves.

---

## Position Management

- **25% profit target:** Close the butterfly when it has decayed to **25–50% of the original credit**. Because the credit is large relative to risk, even capturing 25% of max profit is meaningful. Tastytrade often uses 25% for butterflies vs. 50% for condors.
- **Adjustment — convert to condor:** If the underlying moves off the ATM strike, the iron butterfly transforms into an asymmetric iron condor (one side ITM, one OTM). You can roll the threatened short strike further OTM to widen the body and reduce delta exposure, effectively converting to an iron condor.
- **Delta management:** Since both short strikes are ATM, the position delta starts near zero but moves quickly as the underlying shifts. Monitor delta closely.
- **Rolling:** Roll the entire structure out in time if the underlying stays near the strike but the trade needs more time. Only roll for a net credit.

---

## Exit / Closure

| Scenario | Action |
|----------|--------|
| Position decays to 25–50% of credit | Close all four legs; take profit |
| Underlying pins ATM at expiration | Maximum profit at expiration; close same day to avoid pin risk |
| Underlying moves significantly from ATM | One wing is threatened; convert to condor (roll body out) or close for a loss |
| Stop-loss: loss = 2× wing width | Close the entire position |
| Inside 21 DTE | Accelerated gamma; evaluate daily and close if strike is breached |

---

## Key Metrics

| Metric | Formula |
|--------|---------|
| Max profit | Net credit collected (underlying closes exactly at ATM strike at expiration) |
| Max loss | (Wing width − Net credit) × 100 per contract |
| Breakeven (downside) | ATM strike − Net credit |
| Breakeven (upside) | ATM strike + Net credit |
| Profit zone | Underlying stays within ± net credit of the ATM strike |

**Example (10-point wings, $4.50 credit):**
- Max profit: $450 (underlying closes at exactly $100)
- Max loss: $(10.00 − 4.50) × 100 = $550
- Downside breakeven: $100 − $4.50 = $95.50
- Upside breakeven: $100 + $4.50 = $104.50

---

## Iron Butterfly vs. Iron Condor

| Feature | Iron Butterfly | Iron Condor |
|---------|---------------|-------------|
| Short strikes | Both ATM (same strike) | Separated OTM |
| Credit collected | Higher (40–50% of width) | Lower (25–33% of width) |
| Profit zone | Narrow (±credit from ATM) | Wider (between short strikes) |
| Theta decay | Faster (ATM options decay fastest) | Slower |
| Gamma risk | Higher near expiration | Lower |
| Ideal IV | Moderate-to-high | Moderate |

---

## Practical Notes (tastytrade / tastylive approach)

- The iron butterfly is best used when you expect the underlying to **pin near the current price** with very little movement.
- Because of the high gamma exposure, daily monitoring inside 21 DTE is essential.
- **Pin risk at expiration:** If the underlying is exactly at the short strikes at expiration, you are short two ATM options with uncertain assignment. Always close the iron butterfly before expiration (same day at minimum).
- The large credit makes the iron butterfly a capital-efficient strategy when IV is elevated: you collect more premium relative to width than a condor.
