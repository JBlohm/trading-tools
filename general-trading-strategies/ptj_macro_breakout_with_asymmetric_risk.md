# PTJ-Inspired Strategy: Macro Breakout With Asymmetric Risk

> Educational research note, not a mechanical trading system. Use only with current market data, liquidity checks, and desk risk limits.

## One-Sentence Thesis

When a major macro market breaks from a well-defined range in the direction of the dominant fundamental trend, take a liquid directional position with a nearby invalidation point and add only if price confirms.

## Cleanest Instruments

- Equity index futures / ETFs: S&P 500, Nasdaq, DAX, Euro Stoxx futures or liquid ETFs.
- Rates futures: Treasury, Bund, SOFR/Euribor expressions when the thesis is rates-led.
- FX futures / spot: major pairs only, where liquidity is deep.
- Commodity futures: only the front liquid contracts, with roll and storage seasonality checked.

Use the instrument that most directly expresses the macro pressure. Do not use options or leveraged products unless volatility, liquidity, and decay are explicitly part of the setup.

## Market Context

Look for a regime where several pieces line up:

1. A macro driver is visible: monetary tightening/easing, inflation shock, fiscal stress, dollar liquidity, recession risk, geopolitical supply shock, or credit stress.
2. Price has compressed into a range, wedge, or multi-week/month consolidation.
3. Trend filters agree or are turning: higher highs/lows for longs, lower highs/lows for shorts, moving-average slope, breadth/participation, or cross-asset confirmation.
4. The market is liquid enough to exit quickly if wrong.

## Setup

1. Define the range or pivot level before entry.
2. Identify the macro driver and the asset that should move first if the thesis is right.
3. Wait for a closing break, failed retest, or high-volume expansion through the level.
4. Confirm with at least one related market: credit spreads, rate differentials, dollar liquidity, curve structure, sector leadership, or breadth.

## Entry

- Probe: Enter 25-50% of intended risk on the breakout close or failed retest.
- Add: Add only after the market moves in favor and the stop can be trailed or risk is still inside budget.
- No averaging down: Do not add to a losing position just because the thesis sounds good. Price must confirm.

## Execution Trigger

The clean desk trigger is a two-step sequence:

1. Price closes outside the range or rejects the failed retest level during a liquid session.
2. The next session does not immediately close back inside the range and at least one confirmation market agrees.

For intraday execution, use a stop entry only when the breakout level, expected slippage, and first stop are already in the ticket. If the breakout occurs in the last minutes of a thin session, wait for the next liquid handoff unless the catalyst is time-sensitive.

## Stop Placement

- Initial stop: beyond the retest low for longs or retest high for shorts, with enough room for normal noise.
- Time stop: if the trade has not moved at least 0.75R in favor within the expected catalyst window, cut size or flatten.
- Event stop: reduce before data releases that can gap through the structure unless the position is already financed by open profit.

## Trade Management

- Move the first unit to breakeven only after the market prints a fresh continuation high/low, not immediately after entry.
- Take partial profit into a 2R-3R extension when the move becomes one-sided and liquidity thins.
- Trail the remainder behind the last higher low/lower high or a short moving average that fits the holding period.
- Do not add after the third clean directional day unless there is a fresh consolidation; late adds usually pay the worst price.

## Exit / De-Risk Plan

- Exit immediately if price closes back inside the range and the breakout fails.
- Reduce into vertical moves, especially ahead of central-bank decisions, CPI/payrolls, or major event risk.
- Trail risk under/over successive swing levels.
- If the macro thesis weakens but price has not yet broken, cut size first; do not wait for a full stop if the edge is gone.

## Risk Point

- Initial stop goes just beyond the opposite side of the breakout/retest structure, not at an arbitrary percent.
- Maximum loss per idea should be predefined before entry.
- Total portfolio heat must include correlated positions. A long Nasdaq breakout, short dollar, and long copper can all be the same liquidity bet.

## Example Template

- Context: Fed pause expectations rise, real yields stop rising, dollar softens, equities compress below resistance.
- Setup: S&P 500 futures close above a 6-week range high and hold the level on the next session's retest.
- Entry: Buy a half unit on the retest hold.
- Stop: Close back below the breakout level or below the retest low.
- Add: Add if breadth expands and price makes a higher high while stop can move to breakeven on the first unit.
- Exit: Take partial profits into a 2.5R-3R move or ahead of CPI/FOMC if the event can gap through the stop.

## Desk Example

- Pre-trade: ES has a 6-week range high at 5,300, breadth is improving, 10-year real yields stop rising, and USD weakens.
- Trigger: Buy 0.5 risk unit only if ES closes above 5,300 and the next liquid session holds 5,285-5,300 on a retest.
- Stop: Initial stop below 5,275, the retest low. If CPI is within 24 hours, halve size or wait.
- First management point: If ES trades to 5,365, stop moves to entry only after breadth remains positive into the close.
- Add: Add 0.25-0.5 unit only after a higher low above 5,300 and a new high, with total open risk still within the original risk budget.
- Exit: Sell one third around 2.5R, trail the balance below the latest daily higher low, and flatten if ES closes back below 5,300.

## Failure Modes

- False breakout during thin liquidity.
- Macro thesis is real but already priced.
- Position overlaps with other trades and creates hidden beta.
- Event gap makes stop loss larger than modeled.

## Sources / Further Reading

- Jack D. Schwager, *Market Wizards*, interview with Paul Tudor Jones: trend following, risk control, and cutting losers quickly.
- PBS / WNET, *Trader: The Documentary* (1987), Paul Tudor Jones trading around the 1987 crash period.
- CME Group education material on futures contract specifications, liquidity, and risk controls.
- CFTC futures risk disclosures: futures are leveraged and losses can exceed initial margin.
