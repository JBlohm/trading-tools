# Short Straddle

## Overview

A short straddle involves selling an at-the-money (ATM) call and an ATM put in the same expiration cycle, both at the same strike price. It collects the highest possible premium but has the tightest profit zone — the underlying must stay very close to the strike. It is an undefined-risk strategy.

**Outlook:** Strictly neutral — profits from the underlying pinning near the strike, maximum time decay, and volatility contraction  
**Risk profile:** Undefined — call side theoretically unlimited upside risk; put side risk up to the strike price  
**Account level required:** Margin account with naked options approval

---

## Construction

| Leg | Action | Option type | Strike |
|-----|--------|-------------|--------|
| 1 | Sell to open | Put | ATM |
| 2 | Sell to open | Call | ATM (same strike) |

Both in the **same expiration** at the **same strike price** (typically the closest strike to the current underlying price).

**Example:** Underlying at $100 → Sell 100 put / Sell 100 call.

---

## Entry Rules

- **DTE:** 30–45 DTE.
- **Strike selection:** ATM — the strike price closest to the current underlying price. Both the put and call carry delta ~0.50, making the initial position delta-neutral.
- **IV rank:** IVR ≥ 30–50%. Because you are selling ATM options (which contain maximum extrinsic value), high IV is critical for collecting premium that compensates for the narrow profit zone.
- **Premium target:** Because ATM options have the highest extrinsic value, straddle credits are significantly larger than strangle credits. This premium is the buffer against adverse moves.
- **Underlying selection:** Use liquid, large-cap names with regular, gradual price action. Avoid earnings events and event-driven stocks.

---

## Position Management

- **25% profit target:** Close when the straddle decays to **25% of the original credit** (i.e., when the straddle can be bought back for 75% of what you received). Tastytrade uses 25% for straddles vs. 50% for strangles/condors because the ATM position accumulates credit faster relative to risk.
- **Delta management:** The straddle becomes directional quickly when the underlying moves. Monitor net delta daily. If the position becomes significantly directional (e.g., delta > 30 in either direction), the tastylive approach is to **roll the untested (winning) side closer to ATM** — this collects additional credit and re-centres the position's delta. Do NOT sell additional options on the tested (losing) side, as that increases exposure to the side already under pressure.
- **Rolling the tested leg:** If the underlying moves meaningfully in one direction, roll the **losing short option** (the one moving ITM) further OTM in the same expiration or out in time, collecting additional credit. The winning side decays and may be closed for a small debit.
- **Inside 21 DTE:** Gamma accelerates sharply. Monitor daily. Close the position by 21 DTE unless the profit target has been met.
- **Stop-loss:** Close the entire straddle if the loss reaches **2× the original credit**.

---

## Exit / Closure

| Scenario | Action |
|----------|--------|
| Straddle decays to 25% of original credit | Close both legs; take profit |
| Underlying pins at strike at expiration | Maximum profit; close same day (pin risk) |
| Underlying moves significantly from strike | Roll the tested leg further OTM/out in time for a credit |
| Stop-loss: loss = 2× original credit | Close the entire position |
| Inside 21 DTE | Evaluate daily; close or roll to avoid gamma spike |

---

## Key Metrics

| Metric | Formula |
|--------|---------|
| Max profit | Net credit collected (underlying closes exactly at strike) |
| Max loss | Unlimited (call side); strike × 100 minus credit (put side) |
| Breakeven (downside) | Strike − Net credit |
| Breakeven (upside) | Strike + Net credit |
| Profit zone | Underlying stays within ± net credit of the short strike |

**Example (100 strike straddle, $5.00 credit):**
- Max profit: $500 per contract (underlying closes at $100)
- Downside breakeven: $100 − $5.00 = $95
- Upside breakeven: $100 + $5.00 = $105
- Max loss: unlimited (upside); $9,500 (downside, if stock goes to zero)

---

## Short Straddle vs. Short Strangle

| Feature | Short Straddle | Short Strangle |
|---------|---------------|---------------|
| Short strikes | Both ATM (same strike) | Separated OTM |
| Credit collected | Highest (ATM premium) | Moderate (OTM premium) |
| Profit zone | Narrowest (±credit from ATM) | Wider (between OTM strikes) |
| Theta decay | Fastest (ATM decays most) | Slower |
| Gamma risk | Highest | High |
| Adjustment flexibility | More complex | Easier |

---

## Relationship to Iron Butterfly

An iron butterfly is simply a short straddle with **long OTM wings added** to define the risk. If you add protective long calls and puts to a short straddle, you have an iron butterfly. The wings reduce both the credit collected and the maximum risk.

---

## Practical Notes (tastytrade / tastylive approach)

- The short straddle is the highest-premium, highest-risk version of the neutral premium-selling strategies.
- It requires a larger account with naked options approval and strict position sizing.
- **Never size a short straddle so large that a 2σ move creates an unacceptable account loss.** A 2σ move corresponds roughly to the distance of the straddle breakevens.
- Tastylive uses the short straddle selectively, often after high-IV events (e.g., just after an earnings release when IV is still elevated but the event risk has passed).
- **Pin risk at expiration** is a real concern: if the underlying is near the strike at expiration, one short option may be exercised while the other expires worthless — close the straddle before expiration to eliminate this ambiguity.
