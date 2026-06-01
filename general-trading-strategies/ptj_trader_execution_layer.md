# PTJ-Inspired Trader Execution Layer

> Practical desk layer for turning the PTJ macro/risk notes into executable trades. It is not a signal service and does not replace current market checks, compliance rules, or portfolio limits.

## Purpose

The macro note explains why a trade may exist. The execution layer defines when the desk is allowed to act, where the trade is wrong, how orders are staged, and how the position is managed after entry.

## Universal Trigger Standard

A trade is actionable only when all four conditions are true:

1. Thesis: the macro or liquidity driver is current, not stale commentary.
2. Price: the instrument breaks, retests, or rejects a clearly marked level.
3. Confirmation: a related market agrees, such as breadth, rates, FX, credit, volatility, or sector leadership.
4. Risk: the invalidation level is close enough that the trade can be sized inside desk limits.

If price confirms but the macro driver does not, treat it as a technical trade with smaller size. If macro confirms but price does not, keep it on watch.

## Order Staging

- Probe: 25-50% of intended risk when the trigger first fires and liquidity is acceptable.
- Confirmed add: 25-50% only after price moves in favor, a new swing confirms the thesis, and the original unit's risk can be reduced.
- No rescue add: never add to a losing position to improve average price.
- No late add: avoid adding after an extended one-way move unless the market builds a new base or lower-high/lower-low structure.

## Stop Framework

Use the tightest valid stop that still respects the market structure:

- Structure stop: beyond the failed-retest high/low, breakout shelf, or prior swing.
- Time stop: flatten or reduce when the expected catalyst window passes without follow-through.
- Event stop: reduce ahead of binary catalysts if gap risk can exceed planned loss.
- Thesis stop: exit when the macro fact pattern changes even if price has not touched the stop.

The desk should not widen stops. If volatility expands, cut size.

## Trade Management Rhythm

1. At entry: record entry, stop, target, catalyst, size, and correlated exposures.
2. At 1R in favor: check whether the thesis is improving; do not mechanically move the stop unless structure supports it.
3. At 2R-3R in favor: take partial profits or tighten the stop if the move is extended.
4. After an add: recalculate blended risk immediately.
5. Before events: decide whether to hold, hedge, reduce, or flatten before the event, not during the event.

## Desk Examples

### Macro Breakout

- Watch: ES compresses below a six-week high while dollar and real yields weaken.
- Trigger: ES closes above the range and holds the retest during the next liquid session.
- Ticket: Buy 0.5 risk unit, stop below the retest low, add only after a higher low and new high.
- Management: take one third at 2.5R, trail the balance below higher lows, flatten on a close back inside the range.

### Liquidity Breakdown

- Watch: SPX loses its 200-day area, breadth deteriorates, credit spreads widen, and volatility term structure inverts.
- Trigger: failed bounce below broken support, not the first panic print.
- Ticket: short 0.33-0.5 risk unit or use a defined-risk put spread if gap risk is too high.
- Management: cover into forced selling, reduce ahead of policy risk, flatten if support is reclaimed with improving breadth.

### Policy Divergence

- Watch: U.S. data is firm, Europe weakens, and front-end rate spreads widen.
- Trigger: EUR/USD breaks and fails to reclaim the range floor while spreads keep confirming.
- Ticket: short 0.5 risk unit, stop above the failed-retest high or on a material spread reversal.
- Management: add only on new lows confirmed by spreads, take partials before crowded policy events, flatten if central-bank pricing converges.

## Daily Position Review

For each open trade, answer:

- Is the original thesis still current?
- Is price confirming or rejecting the trade?
- Has correlation risk changed because of other desk positions?
- Has liquidity improved or deteriorated?
- Is the next action already defined if the market gaps against us?

If the answer to the last question is no, reduce size before the close.
