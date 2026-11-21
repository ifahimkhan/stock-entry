# HH / HL uptrend entries: Entry 1 vs Entry 2

Run: `.venv\Scripts\python.exe analysis\hh_hl_entries.py` (about 2 minutes, needs `openpyxl`).

Output in `analysis/output/`: `signals.csv`, `hh_hl_entries.xlsx` (Summary, Comparison, Weekly trend split,
By stock, Current status, Signals, Parameters, Notes) and `run_log.txt` (the printed report).

Past results do not predict future performance.

## Assumptions

Every parameter is in the `CONFIG` block at the top of the script.

**Data**
- Input is `data/daily_bars.csv` (Ticker, Date, Open, High, Low, Close, Volume; about 500 daily bars per stock).
  It has no swing, 3-rule-candle or entry columns, so these are computed with the app's code
  (`stock_entry/swings.py`), as agreed.
- Each stock is handled separately. Only stocks in today's list are included (stocks dropped earlier are missing).

**Swings and stages**
- Swings are the app's labels (pivot 5 bars left, 5 right) after the app's clean-up rules.
- A swing is "confirmed" 5 bars after its pivot. Every signal uses the confirmation bar, never the swing bar.
- No lookahead: each bar is replayed with only the bars up to its close. The clean-up can relabel an old
  swing later; signals are kept as they were seen at the time.
- The sequence starts with an LH, as in the Swings 1D **Final2** column. Stage, from the latest confirmed
  clean labels in a row: `LH, HH` = HH1; `LH, HH, HL` = HL1; `LH, HH, HL, HH` = HH2.
- Any other latest label (LH, LL) means no stage. Long only; downtrends are ignored.

**Entries**
- 3-rule candle (app's rule): high > previous high, low > previous low, close > previous high.
- **Entry 1 is an assumed rule, not confirmed by the user:** the first 3-rule candle while the stage is HL1
  (HL1 confirmed, HH2 not yet confirmed). One per HH1-HL1 pair.
- Entry 2: the first 3-rule candle while the stage is HH2 that also closes below HH2's close (a 3-rule
  candle closing at or above it is skipped). One per HH1-HL1-HH2.
- Entry 2 (a): an Entry 1 fired earlier for the same HH1-HL1. Entry 2 (b): every Entry 2.
- The signal is known at the close of its candle; the entry price is the next candle's open.

**Outcomes (no exits)**
- Forward return at N bars: from the entry open to the close of the N-th bar held (entry bar = bar 1).
  N = 5, 10, 20 daily; 2, 4, 8 weekly; 1, 2, 3 monthly.
- MFE / MAE: highest high / lowest low within those N bars, vs the entry price.
- Cost: 0.2% round trip, subtracted from the return. Win = return after cost > 0.
- Signals without N bars of forward data are skipped for that horizon and counted (`skipped_no_data`).
- Signals overlap (a window can contain the next signal); the count is printed.

**Timeframes**
- 2h: the app's 2-hour bars from `TBC_all_stocks_bars.csv` (about 500 per stock, 4 per session, roughly six
  months). Horizons 4, 8 and 20 bars (1, 2 and 5 sessions). A swing is confirmed 5 two-hour bars after its pivot.
- Weekly bars are weeks ending Friday, monthly bars are calendar months (open = first, high = max, low = min,
  close = last). A period that is still running is dropped. The same swing code runs on the resampled bars.
- Monthly has about 24 bars per stock: too few for swings. Treat it as not usable.
- Weekly trend for a daily signal: taken from the last finished weekly bar on or before the signal day.
  "Up" = the last two confirmed weekly labels are both HH or HL; "unknown" = fewer than two weekly labels yet.

**Statistics**
- Groups with fewer than 30 measured signals are flagged as unreliable.
- Entry 1 vs Entry 2: two-proportion z-test on win rates, Welch z-test on average returns, 5% level.
  Signals overlap and stocks move together, so these p-values are optimistic.

## In the web app

The script also writes `data/hh_hl_entries.json`, which the app's **TBC Zone Entry Sequence** list reads:
the Stocks tab lists stocks by their current stage, the Results tab shows the tables above and every signal.
`--app-only` rewrites only that file (no report, CSV or workbook); the Daily scan workflow runs it that way.
