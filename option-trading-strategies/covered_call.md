# Covered Call

## Overview

A covered call combines 100 long shares of stock (or ETF) with one short (sold) out-of-the-money (OTM) call option per 100 shares. The strategy generates premium income and provides a small downside buffer, but caps the upside above the short call's strike price.

**Outlook:** Neutral to moderately bullish  
**Risk profile:** Defined downside (long stock can go to zero, offset by collected premium); capped upside at short strike  
**Account level required:** Covered (standard margin/cash account)

---

## Construction

| Leg | Action | Option type | Strike |
|-----|--------|-------------|--------|
| 1 | Long (already held) | Stock / ETF | — |
| 2 | Sell to open | Call | OTM (typically 1–2 strikes above current price) |

- One short call covers exactly 100 shares.
- Both legs must be in the **same underlying**.
- The call is sold in the **same account** that holds the shares.

---

## Entry Rules

- **DTE (days to expiration):** 30–45 DTE is the sweet spot for premium collection; theta decay accelerates in the final 30 days.
- **Strike selection:** Sell a call with a **delta of 0.20–0.35** (roughly 20–35% probability of expiring ITM). A lower delta = less premium but more room to run; a higher delta = more premium but higher assignment risk.
- **Implied volatility (IV):** Enter when IV rank (IVR) is **≥ 30%** to collect meaningful premium relative to the underlying's historical volatility.
- **Premium target:** Collect at least **1–2% of the stock's price** per month to make the trade worthwhile.
- **Avoid before earnings:** Do not carry a short call through an earnings announcement unless the goal is specifically to harvest elevated IV.

---

## Position Management

- **Profit target — take gains early:** Close or roll the short call when it has decayed to **50% of the original credit** (buy it back for half of what you collected). This frees capital and eliminates residual risk for relatively little money left on the table.
- **Rolling:** If the short call approaches the money or the original expiration is near but the underlying has not moved much, **roll out in time** (buy back the near-term call and sell a new call 30–45 DTE out) to collect additional premium and extend the trade. Rolling up in strike simultaneously locks in more profit but reduces future premium.
- **Dividend risk:** If the short call is ITM before an ex-dividend date, early assignment is likely. Consider closing the call before the ex-date or choosing an OTM strike that is unlikely to be exercised.
- **Earnings management:** Close the covered call (buy it back) before earnings if you want to retain full upside participation during the event; otherwise the collected premium is the maximum call-side profit.

---

## Exit / Closure

| Scenario | Action |
|----------|--------|
| Short call expires worthless (OTM) | Sell a new call for the next cycle (roll) or let shares sit naked |
| Short call reaches 50% profit | Buy back the call; re-enter immediately or wait for a better setup |
| Short call goes deep ITM (stock surges) | Accept assignment (shares called away at strike) and collect proceeds; or roll up and out to delay assignment |
| Stock drops sharply | The short call decays in your favour; buy it back cheaply and decide whether to hold shares or exit |
| Assignment at expiration | Shares are called away at the strike price; net result = strike price + original premium collected |

---

## Key Metrics

| Metric | Formula |
|--------|---------|
| Max profit | (Strike − Entry price of stock) + Premium collected |
| Max loss | Entry price of stock − Premium collected (stock goes to zero) |
| Breakeven | Entry price of stock − Premium collected |
| Profit zone | Stock price stays below the short call strike at expiration |

---

## Practical Notes (tastytrade / tastylive approach)

- Covered calls are best deployed on stocks you **want to own long-term** and are comfortable holding through pullbacks.
- Use the **16-delta** strike as a starting point; it approximates a ~1 standard-deviation OTM move.
- Avoid covered calls on highly illiquid stocks (wide bid-ask spreads erode the premium edge).
- For tax-aware accounts: selling calls less than 30 DTE on positions held < 1 year may affect long-term capital-gains treatment of the stock (qualified covered call rules vary by jurisdiction — consult a tax adviser).
