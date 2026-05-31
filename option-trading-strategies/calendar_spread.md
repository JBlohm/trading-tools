# Calendar Spread (Time Spread / Horizontal Spread)

## Overview

A calendar spread (also called a time spread or horizontal spread) involves selling a shorter-dated option and buying a longer-dated option at the **same strike price**. The goal is to collect the short option's premium while the long option retains more time value. The strategy profits from time decay differential and, ideally, rising implied volatility in the back month.

**Outlook:** Neutral (for ATM calendars) or mildly directional (for OTM calendars placed near an expected price target)  
**Risk profile:** Defined — max loss is the net debit paid (the difference in premium between the two options)  
**Account level required:** Standard options approval (spreads); note that margins differ by broker

---

## Construction

Two variants: **call calendar** and **put calendar**. Both work identically in mechanics.

### Call Calendar Spread

| Leg | Action | Option type | Strike | Expiration |
|-----|--------|-------------|--------|------------|
| 1 | Sell to open | Call | Same strike | Near-term (front month) |
| 2 | Buy to open | Call | Same strike | Longer-dated (back month) |

### Put Calendar Spread

| Leg | Action | Option type | Strike | Expiration |
|-----|--------|-------------|--------|------------|
| 1 | Sell to open | Put | Same strike | Near-term (front month) |
| 2 | Buy to open | Put | Same strike | Longer-dated (back month) |

**Both legs share the same strike; only the expirations differ.**

**Example (Call Calendar):** Underlying at $100 → Sell 100 call expiring in 30 days / Buy 100 call expiring in 60 days → Net debit = back-month premium − front-month premium.

---

## Entry Rules

- **Front month DTE:** 20–30 DTE for the short option — capturing the steepest part of the theta decay curve.
- **Back month DTE:** 45–90 DTE for the long option — enough time value to retain value even if the front month expires worthless.
- **Strike selection:**
  - **ATM calendar:** Strike at current price for maximum time-decay advantage.
  - **OTM calendar:** Strike placed at a price target you expect the underlying to reach by front-month expiration.
- **IV structure:** Calendars benefit from **contango** in the IV term structure (back month IV ≥ front month IV). If near-term IV is unusually elevated (e.g., earnings in the front month), the short option is richly priced — but earnings risk is then present in the front month.
- **IV rank:** A low-to-moderate IVR environment is often preferable; you want cheap back-month premium relative to the front-month credit.
- **Debit target:** The net debit should be no more than **25–33% of the back-month option's premium** — this ensures the front-month decay more than offsets the debit paid.

---

## How the Strategy Works

The short front-month option decays faster than the long back-month option (theta decay is not linear — it accelerates near expiration). If the underlying stays near the strike:
1. Front-month option decays toward zero (profitable short).
2. Back-month option retains most of its value.
3. The **difference** (spread value) widens — that is the profit.

Additionally, if implied volatility rises, the back-month option (which you are long) appreciates more than the short front month due to its longer vega exposure. Calendars have **positive vega** — they benefit from rising IV.

---

## Position Management

- **Profit target:** Close when the spread has gained **25–50% of the net debit paid**. Because the maximum profit window is narrow (underlying must be near the strike at front-month expiration), taking gains early is prudent.
- **Front-month expiration management:** At front-month expiration:
  - If the front-month option expires **OTM and worthless**: You now hold the back-month option outright (naked long). You can sell a new front month (rolling forward) or close the back-month option.
  - If the front-month option is **ITM at expiration**: Close before expiration to avoid assignment risk. The back-month option will have appreciated to offset some loss.
- **Rolling forward:** After the front month expires, sell the next available near-term option at the same (or adjusted) strike to create a new calendar. This extends the strategy.
- **Stop-loss:** Close if the net position loss reaches **50% of the debit paid**.

---

## Exit / Closure

| Scenario | Action |
|----------|--------|
| Spread gains 25–50% of debit | Close both legs; take profit |
| Front-month approaches expiration OTM | Let front expire or close; decide whether to keep back month |
| Front-month moves ITM before expiration | Close both legs to avoid assignment |
| Underlying moves far from strike | Both options lose time-value advantage; close at stop-loss |
| Volatility collapses (negative vega impact) | Back-month loses value; close to limit losses |
| Roll decision after front-month expiry | Sell new near-term option against back-month (new calendar) |

---

## Key Metrics

| Metric | Formula |
|--------|---------|
| Max loss | Net debit paid × 100 per contract |
| Max profit | Not precisely calculable at entry (depends on IV at front expiration); typically realised near the strike at front expiry |
| Breakeven | Approximately ± net debit from the strike at front-month expiration |
| Profit zone | Underlying near the strike at front-month expiration |
| Vega | Positive (benefits from rising IV in back month) |
| Theta | Initially positive net (front-month decay > back-month decay when near expiry) |

---

## Relationship to Other Strategies

- **Diagonal spread:** A calendar with **different strikes** — adds directional exposure to the time-decay benefit.
- **Poor Man's Covered Call (PMCC):** A diagonal spread using deep ITM long-dated options as a stock substitute.
- **Short calendar:** Reverse of the above — sell back month, buy front month. Profits from IV collapse; negative vega. Rarely used by retail traders.

---

## Practical Notes (tastytrade / tastylive approach)

- Calendar spreads are more sensitive to **IV changes** than most defined-risk strategies. A sudden IV collapse in the back month erodes the position quickly.
- The strategy is best deployed when you expect a **stable, range-bound underlying** for the front-month duration.
- **Earnings calendars** are a specialised tactic: place an ATM calendar just before earnings so the front month captures the high pre-earnings IV while the back month is cheaper. Close before the earnings date or let the IV crush work against the front-month option. This is an advanced use case.
- Avoid calendars on **illiquid underlyings** — bid-ask spreads on two different expirations can significantly erode theoretical edge.
- Always be aware of the **assignment risk** on the short front-month option if it moves ITM. Monitor daily inside 5 DTE.
