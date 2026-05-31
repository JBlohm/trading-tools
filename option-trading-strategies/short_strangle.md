# Short Strangle

## Overview

A short strangle involves selling an OTM call and an OTM put in the same expiration cycle, collecting a net credit. The trade profits from time decay and low volatility when the underlying stays between the two short strikes. Unlike an iron condor, a short strangle has **undefined risk** — there are no protective long options.

**Outlook:** Neutral (range-bound) — profits from time decay and volatility contraction  
**Risk profile:** Undefined — loss on the call side is theoretically unlimited; loss on the put side is substantial (underlying goes to zero). Requires margin.  
**Account level required:** Margin account with naked options approval (higher account level)

---

## Construction

| Leg | Action | Option type | Strike |
|-----|--------|-------------|--------|
| 1 | Sell to open | Put | OTM (below current price) |
| 2 | Sell to open | Call | OTM (above current price) |

Both in the **same expiration**. There are no long protective legs.

**Example:** Underlying at $100 → Sell 95 put / Sell 105 call → strangle width of 10 points.

---

## Entry Rules

- **DTE:** 30–45 DTE.
- **Strike selection:** Sell each leg at **1 standard deviation OTM** (delta ~0.16–0.20 for each). This gives approximately 68% probability the underlying stays within the strikes.
- **Premium target:** Collect a credit that represents a meaningful percentage of the underlying's value. As a rough guide, target **≥ 2–4% of the underlying price** for index products.
- **IV rank:** IVR ≥ 30–50%. High-IV environments are ideal for strangle selling — the premium compensates for the undefined risk.
- **Underlying selection:** Liquid, large-cap stocks or ETFs with known, well-behaved price histories. Avoid highly volatile or news-driven underlyings.
- **Avoid earnings:** The short strangle's undefined risk makes it particularly dangerous through binary events. Enter after earnings when IV is elevated but the event risk has passed.

---

## Position Management

- **50% profit target:** Close both legs when the total position has decayed to **50% of the original credit**.
- **21 DTE rule:** Evaluate at 21 DTE; if not at target, consider closing or rolling to avoid accelerated gamma exposure.
- **Untested-side management:** When one side (e.g., the call) moves toward the money, the opposite side (the put — "untested") has decayed. You can close the untested side for a small debit to **reduce net risk** while the threatened side is addressed separately.
- **Rolling the tested side:** Roll the threatened short option **further OTM and/or out in time** for a net credit, increasing the probability of finishing OTM.
- **Delta neutrality:** Monitor portfolio delta; if the position becomes significantly directional, sell a small amount of the opposing option to re-centre the tent.
- **Stop-loss:** Close the entire strangle if the loss reaches **2× the original credit** to prevent catastrophic loss.

---

## Exit / Closure

| Scenario | Action |
|----------|--------|
| Position decays to 50% of credit | Close both legs; close the trade |
| Both options expire OTM | Full credit kept |
| Underlying moves toward one short strike | Roll that leg further OTM/out; consider closing untested side |
| Stop-loss: loss = 2× original credit | Close entire strangle immediately |
| Inside 21 DTE, not at target | Evaluate: close or roll out in time |

---

## Key Metrics

| Metric | Formula |
|--------|---------|
| Max profit | Net credit collected |
| Max loss | Theoretically unlimited (call side); up to strike × 100 (put side) |
| Breakeven (downside) | Short put strike − Net credit |
| Breakeven (upside) | Short call strike + Net credit |
| Profit zone | Underlying stays between short put and short call strikes |

**Example ($0.90 credit on 95/105 strangle):**
- Max profit: $90 per contract
- Downside breakeven: $95 − $0.90 = $94.10
- Upside breakeven: $105 + $0.90 = $105.90
- Max loss: undefined (unlimited to the upside)

---

## Short Strangle vs. Iron Condor

| Feature | Short Strangle | Iron Condor |
|---------|---------------|-------------|
| Risk | Undefined (naked) | Defined (protected wings) |
| Credit collected | Higher | Lower |
| Margin required | Higher | Lower (spread-defined) |
| Suitable account | Margin + naked approval | Standard spreads |
| Flexibility | More (no wing to work around) | Less |

---

## Practical Notes (tastytrade / tastylive approach)

- Strangles are a core tastytrade premium-selling strategy for larger, margin-approved accounts.
- The **undefined risk** means position sizing is critical — never size strangles so that a 3σ move creates an account-threatening loss.
- Tastylive uses the **1 standard deviation (16-delta) rule** for each short leg as a starting point.
- **Implied volatility contraction** (IV crush after news or high-fear events) is the primary short-term profit driver; theta takes over the rest.
- Strangles on **index ETFs (SPY, QQQ)** are popular because indexes are less prone to gap moves than individual stocks.
- Always have a defined plan for when the trade moves against you before entering — know your rolling rules and stop-loss levels in advance.
