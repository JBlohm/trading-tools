# Wheel Strategy

## Overview

The Wheel Strategy is a systematic, income-generating approach that chains cash-secured puts and covered calls into a repeating cycle. The goal is to collect option premium continuously from a single underlying, with the stock itself acting as the "vehicle" that moves between phases.

**Outlook:** Neutral to moderately bullish (requires comfort owning the underlying)  
**Risk profile:** Downside risk of long stock position, partially offset by accumulated premium. Not suitable for stocks expected to trend sharply lower.  
**Account level required:** Cash or margin (cash-secured requires full collateral)

---

## The Three Phases

### Phase 1 — Sell the Cash-Secured Put

Enter a short put on a stock you are willing to own.

| Leg | Action | Option type | Strike |
|-----|--------|-------------|--------|
| 1 | Sell to open | Put | OTM (delta ~0.20–0.30) |

- Hold cash equal to strike × 100 per contract as collateral.
- **Objective:** Collect premium; let the put expire worthless and repeat Phase 1.

**Exit if profitable:** Buy back the put at 50% of the original credit and sell a new put for the next cycle.

**Proceed to Phase 2 if assigned.**

---

### Phase 2 — Assignment / Stock Acquisition

The stock falls to or below the strike price; 100 shares are purchased per contract at the strike price.

- Net cost basis = Strike price − All put premium collected so far.
- Record the total premium collected as it reduces the effective cost.

---

### Phase 3 — Sell the Covered Call

Now owning shares, sell an OTM call to collect additional premium.

| Leg | Action | Option type | Strike |
|-----|--------|-------------|--------|
| 1 | Long (assigned shares) | Stock | — |
| 2 | Sell to open | Call | OTM at or above your cost basis |

- **Strike selection:** Sell a call at or above your net cost basis so that if assigned you achieve a profit on the shares.
- **DTE:** 30–45 DTE.
- **Delta:** ~0.20–0.35.

**Exit if profitable:** Buy back the call at 50% of original credit and sell a new call.

**Proceed back to Phase 1 if shares called away.**

---

## Full Cycle

```
[Start] → Sell Cash-Secured Put (Phase 1)
              |
    Put expires OTM → collect premium → Sell new Put (repeat Phase 1)
              |
    Put assigned → acquire shares at strike (Phase 2)
              |
         Sell Covered Call (Phase 3)
              |
    Call expires OTM → collect premium → Sell new Call (repeat Phase 3)
              |
    Call assigned → shares sold at strike → collect premium → [Start]
```

---

## Entry Rules

- **Underlying selection:** Choose liquid, large-cap stocks or ETFs that you are genuinely comfortable owning long-term. The Wheel works best on fundamentally sound names, not speculative or volatile stocks.
- **IV rank:** Enter Phase 1 when IVR ≥ 30% for enhanced premium collection.
- **Strike — put:** OTM put with delta ~0.20–0.30; this provides a ~70–80% probability that the put expires worthless.
- **Strike — call:** Sell the call at or above your net cost basis (strike price of put minus all premium collected). This ensures that share assignment in Phase 3 results in a profit.
- **DTE:** 30–45 DTE for both phases.

---

## Position Management

| Phase | Action when profitable | Action when challenged |
|-------|----------------------|----------------------|
| Phase 1 (short put) | Close at 50% profit; re-enter | Roll down-and-out for a net credit; accept assignment if roll unfeasible |
| Phase 3 (covered call) | Close at 50% profit; re-enter | Roll up-and-out; accept assignment at strike |

- **Rolling puts:** Only roll for a **net credit**. Never roll for a debit — it increases your cost basis without guaranteed compensation.
- **Rolling calls:** Roll up-and-out if the stock surges past your short call and you want to avoid assignment. Ensure the new strike is still above your net cost basis.
- **Stop-loss on shares:** If the stock drops well below your cost basis and the thesis is broken, exit the position rather than continuing to sell covered calls into a declining stock (this converts the Wheel into a losing "catching a falling knife" scenario).

---

## Exit / Closure

| Scenario | Action |
|----------|--------|
| Put expires worthless | Sell new put (Phase 1 repeat) |
| Put profits at 50% | Buy back; re-enter Phase 1 |
| Shares assigned | Move to Phase 3 (sell covered call) |
| Call expires worthless | Sell new call (Phase 3 repeat) |
| Call profits at 50% | Buy back; re-enter Phase 3 |
| Shares called away | Return to Phase 1 with refreshed cash |
| Fundamental breakdown | Exit shares at market; end the cycle on this underlying |

---

## Key Metrics

| Metric | Formula |
|--------|---------|
| Net cost basis | Put strike − Σ all premium collected (puts + calls) |
| Breakeven | Net cost basis at any point in the cycle |
| Annualised yield | (Total premium collected ÷ Capital deployed) × (365 ÷ Days in cycle) |
| Max loss | Net cost basis × 100 per contract (stock goes to zero) |

---

## Practical Notes (tastytrade / tastylive approach)

- The Wheel is a **defined-risk income strategy** over time, but the risk of the underlying stock still exists in Phase 2 and 3.
- Track every premium collected to know your true cost basis at all times.
- The strategy works best in **range-bound or slowly rising markets**. A strongly trending-down stock will erode gains faster than premium income can compensate.
- Do not Wheel volatile, leveraged ETFs or highly speculative stocks — assignment in these underlyings can result in rapid capital destruction.
- Tastylive recommends using liquid underlyings with **tight bid-ask spreads** to minimise slippage on entries and exits.
