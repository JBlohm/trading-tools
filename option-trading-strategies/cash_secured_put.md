# Cash-Secured Put

## Overview

A cash-secured put involves selling (writing) an out-of-the-money (OTM) put option while holding enough cash in the account to purchase the underlying shares at the strike price if assigned. The strategy generates premium income from neutral-to-bullish underlyings and is effectively an obligation to buy the stock at the strike price.

**Outlook:** Neutral to moderately bullish  
**Risk profile:** Maximum loss is strike price minus premium collected (stock falls to zero). Maximum gain is the premium collected.  
**Account level required:** Cash or margin (cash-secured requires full collateral; naked put requires margin but same risk profile)

---

## Construction

| Leg | Action | Option type | Strike |
|-----|--------|-------------|--------|
| 1 | Sell to open | Put | OTM (below current stock price) |
| Cash reserve | Hold | Cash | Strike price × 100 × number of contracts |

- Selling 1 put contract on a $50 stock at the $50 strike requires $5,000 of reserved cash.
- In a margin account, the same trade as a **naked short put** requires less collateral but carries identical risk.

---

## Entry Rules

- **DTE:** 30–45 DTE to capture the steepest part of the theta decay curve.
- **Strike selection:** Sell a put with a **delta of 0.20–0.30** (20–30% probability of expiring ITM). Higher deltas collect more premium but increase assignment likelihood.
- **IV rank:** Enter when IVR ≥ 30%. High IV environments inflate option premiums, improving the risk/reward ratio.
- **Underlying selection:** Use liquid, large-cap stocks or ETFs you would genuinely want to own at the strike price. This matters because assignment can occur.
- **Premium target:** Aim to collect at least **1–2% of the strike price** per month.
- **Avoid earnings:** Do not enter a cash-secured put into an earnings event unless deliberately harvesting the elevated IV, understanding the binary risk involved.

---

## Position Management

- **50% profit target:** Buy back the short put when it has decayed to **50% of the original credit**. This is the tastytrade standard — it removes residual risk and frees capital faster than waiting for full decay.
- **21 DTE rule:** At 21 DTE, evaluate whether to close or roll. Inside 21 days, gamma risk (acceleration of delta changes) increases and can create outsized losses from a sudden move.
- **Rolling down and out:** If the underlying drops toward the strike, consider rolling the put **down in strike and out in time** to collect more premium while reducing assignment probability and extending the trade. Only roll for a net credit.
- **Rolling out only:** If the strike is still OTM but expiration is approaching, roll out in time (same strike, later expiration) to collect additional premium and avoid assignment.

---

## Exit / Closure

| Scenario | Action |
|----------|--------|
| Put expires worthless (OTM) | Keep full premium; sell new put for next cycle |
| Put reaches 50% profit | Buy back for half the credit; re-enter or wait |
| Stock drops near/through strike | Evaluate: roll down and out for credit, or accept assignment and transition to covered call (Wheel) |
| Assignment at expiration | Shares purchased at strike price; net cost = strike − premium collected. Transition to Wheel strategy by selling covered calls |
| Stop-loss trigger | Close the position if the loss reaches **2–3× the premium received** to prevent catastrophic loss |

---

## Key Metrics

| Metric | Formula |
|--------|---------|
| Max profit | Premium collected |
| Max loss | (Strike price − Premium) × 100 per contract |
| Breakeven | Strike price − Premium collected |
| Profit zone | Stock stays above strike at expiration |

---

## Transition to the Wheel

If assigned, you now own 100 shares at the strike price (net cost = strike − put premium). Immediately enter the **Covered Call** phase:
1. Sell an OTM call on the assigned shares.
2. Collect call premium to further reduce your cost basis.
3. Repeat until shares are called away or you exit voluntarily.

See [wheel_strategy.md](wheel_strategy.md) for the full cycle.

---

## Practical Notes (tastytrade / tastylive approach)

- Cash-secured puts are considered a premium-selling strategy — you are on the short-volatility side of the trade.
- The tastylive standard is to close at **50% of max profit** regardless of DTE, to maximise the win rate over time.
- Never sell puts on a stock you are **unwilling to own at the strike price**.
- Track your **cost basis** carefully: premium collected reduces your effective purchase price on assignment.
