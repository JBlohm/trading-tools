# PTJ-Inspired Risk Overlay For All Strategies

> This is the desk survival layer. A strategy without a risk point is not a strategy; it is a story with leverage.

## One-Sentence Thesis

Protect capital first by sizing every idea around a known invalidation point, cutting losers quickly, and pressing only confirmed winners.

## Required Pre-Trade Checklist

Before any trade from the strategy library is live, write down:

1. Thesis in one sentence.
2. Instrument and why it is the cleanest liquid expression.
3. Entry trigger or confirmation evidence.
4. Invalidation level and maximum loss.
5. Reward-to-risk target; prefer asymmetric setups.
6. Position size and total portfolio heat.
7. Correlation and hidden-factor overlap.
8. Catalyst, time horizon, and exit/de-risk plan.
9. Immediate action if wrong.

## Desk Ticket Checklist

Every executable idea should fit on one order ticket:

- Direction and instrument.
- Trigger price or condition.
- Entry type: market, limit, stop, stop-limit, option structure, or staged probe.
- Initial stop and maximum slippage allowed.
- First target or partial-profit level.
- Add level and add size, if any.
- Kill switch: price, time, event, or liquidity condition that forces flat.
- Correlated open positions that share the same risk factor.

If any line is blank, the idea is not ready for live execution.

## Position Sizing Rule

- Size from stop distance, not conviction.
- Use smaller initial probes in uncertain regimes.
- Add only after confirmation and only if total risk remains inside budget.
- Reduce when volatility rises and stop distance widens.

## Order Entry Standards

- Use limit orders for planned entries around retests when liquidity allows.
- Use stop orders only for breakout participation where slippage has been modeled.
- Use stop-limit orders carefully; they control price but can leave the desk unfilled in a fast market.
- Do not send an add order unless the current stop, new blended stop, and total risk are already recalculated.
- For options, check spread width, implied-volatility percentile, open interest, and exercise/assignment risk before entry.

## Stop / Invalidation Rule

- Every trade needs a price or fact pattern that proves the trade wrong.
- If the stop is hit, exit. Re-entry requires a fresh setup, not ego.
- A macro thesis can stay intellectually valid while the trade is still wrong. Price and timing matter.

## Stop Management Rule

- Never widen the original stop after entry.
- Trail only after the market confirms with a new swing in the trade direction.
- Move to breakeven because the structure improved, not because the desk wants emotional comfort.
- If volatility expands so much that the correct stop is now unaffordable, reduce size instead of moving the stop farther away.

## Portfolio Heat Rule

Group positions by hidden factor:

- Risk-on / risk-off beta.
- Dollar liquidity.
- Real yields / duration.
- Inflation / commodity beta.
- Credit spread exposure.
- Regional political or policy exposure.

If three trades all lose money in the same scenario, treat them as one larger trade.

## Add-To-Winners Rule

Allowed:

- Price confirms the thesis.
- Stop can be trailed or risk remains defined.
- Cross-asset evidence improves.
- Portfolio heat remains acceptable.

Not allowed:

- Adding because the position is down and feels cheap.
- Adding to defend an opinion.
- Adding after liquidity has deteriorated.

## Exit Rule

Cut or reduce when any of these occur:

1. Invalidation level is hit.
2. Original catalyst passes and the expected move does not occur.
3. Correlation risk becomes larger than planned.
4. Liquidity deteriorates.
5. A better asymmetric use of capital appears.

## Intraday Monitoring

For live positions, monitor:

- Price versus trigger, VWAP, prior session high/low, and invalidation level.
- Liquidity: spread width, depth, volume pace, futures roll, borrow, and option market width.
- Event calendar: central-bank speakers, auctions, data releases, earnings for index-heavy names, and policy headlines.
- Factor overlap: dollar, duration, equity beta, credit, commodities, and volatility.
- Realized loss versus planned loss; if the live loss exceeds the ticketed loss, flatten first and review later.

## Example: Turning A View Into A Controlled Bet

Bad: "Inflation is sticky, so short bonds."

Better:

- Thesis: Sticky inflation and hawkish central-bank pricing push 10-year yields higher.
- Instrument: Treasury futures because they are liquid and direct.
- Entry: Break below futures support after CPI confirms.
- Stop: Close back above the failed-breakdown high.
- Size: Risk 0.50% of portfolio on the first unit.
- Add: Only after a lower high and a new low, with first-unit risk reduced.
- Exit: Reduce ahead of FOMC if pricing is already extreme or if employment data weakens.

## Sources / Further Reading

- Jack D. Schwager, *Market Wizards*, Paul Tudor Jones interview: risk control, cutting losses, and emotional discipline.
- Paul Tudor Jones public interviews discussing defense, capital preservation, and avoiding large losses.
- CFTC and NFA risk disclosures for leverage, futures, and options.
- Van K. Tharp, *Trade Your Way to Financial Freedom*, for position sizing and R-multiple framing.
