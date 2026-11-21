// Page controller: loads the scan, renders tiles / tabs / table, opens the single-stock panel.

import {
  POLL_MS, TV_LOGIN_AVAILABLE, getScan, getScanStatus, getStock, getTickers, getTradingView, getZoneResults, signInTradingView,
  getHhHlResults, getHhHlStatus,
  signOutTradingView, startScan,
} from "./api.js";
import { renderDetail, renderDetailMessage } from "./detail.js";
import { renderCards } from "./cards.js";
import { MARKET_HOURS_TEXT, isMarketHours } from "./market.js";
import { DASH, el, formatAngle, formatBarTime, formatDate, formatDateTime, formatPercent, formatPrice, isNumber } from "./format.js";
import { renderTable, sortRows } from "./table.js";
import { renderWinRate } from "./winrate.js";
import { renderHhHl } from "./hhhl.js";
import { downloadCsv, exportDate, toCsv } from "./export.js";
import { initThemeToggle } from "./theme.js";

const RECENT_SESSIONS = 5;
const MRI_SESSIONS = 10;  // how far back a confirmed SPV / PV still counts as fresh
const MAX_POLL_FAILURES = 5;
const SYMBOL_LIKE = /^[A-Za-z0-9][A-Za-z0-9&.\-]*$/;

const TICKER = { key: "ticker", label: "Stock", type: "ticker" };
const SECTOR = { key: "sector", label: "Sector", type: "text" };
const CLOSE = { key: "close", label: "Close", type: "price" };
const CHANGE = { key: "pct_change", label: "Chg %", type: "pct" };
const CCI = { key: "cci", label: "CCI", type: "num2" };
const RSI = { key: "rsi", label: "RSI", type: "num2" };
const CAP = { key: "cap_category", label: "Cap", type: "text" };
const MARKET_CAP = { key: "market_cap_cr", label: "Mkt cap (Cr)", type: "crore" };
const DEBT = { key: "debt_to_equity", label: "Debt/Eq", type: "num2" };
const CAP_FILTERS = [
  { id: "all", label: "All caps" }, { id: "Large", label: "Large" },
  { id: "Mid", label: "Mid" }, { id: "Small", label: "Small" },
];
const WIDE_LAYOUT = "(min-width: 1100px)";  // list and detail side by side; below this the detail is a sheet
const TO_TOP_AFTER_PX = 600;
const LIVE_PRICE_WARNING = `NSE trades ${MARKET_HOURS_TEXT}. Today's candle is still forming, so the scan uses the live price at this moment as today's close, not the final closing price. Entry, Wait and Valid signals for today can change by 3:30 PM: run the scan again after the close for final results.`;
const LIVE_PRICE_NOTE = "Market open: today's prices are live, not the final close.";
const fixed = (value) => (isNumber(value) ? value.toFixed(2) : DASH);
const days = (count) => `${count} day${count === 1 ? "" : "s"}`;
const STAGE_TEXT = { fundamentals: "Loading market cap and debt", prices: "Scanning prices" };
const NO_FUNDAMENTALS = " Market cap and debt/equity are not loaded for this scan, so those filters were skipped: run the scan again.";
const NO_SWINGS = " 2-hour prices did not load from TradingView for this scan (check TV_USERNAME / TV_PASSWORD), so this list is empty: run the scan again.";
const NO_DAILY_SWINGS = " No stock had enough daily prices for swing labels in this scan, so this list is empty.";
const OLD_SCAN = " This scan was made before this screen was added: run the scan again.";

// Swing lists (TradingView.ipynb on 2-hour bars). A swing label is only known 5 bars after its pivot,
// so each list keeps records up to a fixed number of 2-hour bars old (NSE has about 4 per session).
const SWING_NOTE = " Labels are confirmed 5 bars (about 1 session) after the pivot, and an HH can still turn into LH if price later closes below the previous LL.";
/** True when a record `bars` 2-hour bars old is at most `maxBars` old. */
const withinBars = (bars, maxBars) => isNumber(bars) && bars <= maxBars;
const TIGHT_DAYS = 7;   // Tight Range: same numbers as stock_entry/tight_range.py
const TIGHT_PCT = 2;
const FALL_DAYS = 20;   // TBC Strict: the fall is looked for in this many trading days before the range
const FALL_PCT = 8;
const TIGHT_VOLUME_DAYS = 20;   // Entry: the breakout volume is compared with the average of this many days
const TIGHT_ENTRY_DAYS = 5;     // Entry: breakouts of the last this many trading days are listed
const WAIT_RSI_MAX = 45;   // Wait tabs: only stocks with RSI below this and CCI below WAIT_CCI_MAX
const WAIT_CCI_MAX = 0;
const waitMomentum = (row) => isNumber(row.rsi) && row.rsi < WAIT_RSI_MAX && isNumber(row.cci) && row.cci < WAIT_CCI_MAX;
const WAIT_RULE = ` Wait tab: only stocks with RSI below ${WAIT_RSI_MAX} and CCI below ${WAIT_CCI_MAX} on the latest day.`;
const TIGHT_TABS = "Wait";      // tab name of the lists of stocks still inside the range
// Entry tab of the two tight-range lists: a breakout up from the range, on volume.
const ENTRY_RULE = `Entry: the close broke more than ${TIGHT_PCT}% above the base close of a tight range (${TIGHT_DAYS} or more days), `
  + `and that day's volume was above the volume of every day of the range and above the average volume of the ${TIGHT_VOLUME_DAYS} days before it. `
  + `Breakouts of the last ${TIGHT_ENTRY_DAYS} trading days are listed, newest first.`;
// Parallel channel (daily bars): same numbers as stock_entry/channel.py
const CHANNEL_RULE = "Parallel channel on daily bars: two parallel lines through swing points (pivots with 5 bars on each side). "
  + "The base line joins the last two swing lows (rising or flat) or swing highs (falling or flat); the parallel line passes through the farthest swing point on the other side, "
  + "and a second swing point must touch it. Channel: 30 or more trading days long, 3% to 20% wide, no bar outside the lines while it formed. "
  + "Position: 0% = on the lower line, 100% = on the upper line. Breakout up / Breakdown = the first close outside the lines, listed for 5 trading days.";
const CHANNEL_COLUMNS = [TICKER,
  { key: "channel_zone", label: "Where", title: "Near lower line: position 25% or less. Near upper line: 75% or more. Or the break of the channel" },
  { key: "channel_position_pct", label: "Position", type: "pctplain", title: "Latest close inside the channel: 0% = lower line, 100% = upper line" },
  { key: "channel_lower", label: "Lower line", type: "price", title: "Value of the lower line on the latest day" },
  { key: "channel_upper", label: "Upper line", type: "price", title: "Value of the upper line on the latest day" },
  CLOSE,
  { key: "channel_width_pct", label: "Width", type: "pctplain", title: "Distance between the lines, in percent of the middle line" },
  { key: "channel_slope_pct", label: "Slope / day", type: "pct", title: "Rise or fall of the lines per trading day, in percent of the price" },
  { key: "channel_since", label: "Since", type: "date", title: "First anchor of the channel" },
  { key: "channel_days", label: "Days", type: "int", title: "Trading days from the first anchor to the latest day" },
  { key: "channel_break_date", label: "Break on", type: "date", title: "Day of the first close outside the lines" },
  RSI, CAP];
const channelView = (kind, first) => ({
  id: `channel${kind.toLowerCase()}`, label: `Parallel Channel (1D): ${kind}`, mark: null, needsKey: "channel",
  chip: "Parallel Channel (1D)", tabGroup: "channel", tabLabel: kind, ...(first ? {} : { tabOf: "channelascending" }),
  note: (row) => `${kind} channel since ${formatDate(row.channel_since)} · ${row.channel_zone} · ${formatPrice(row.channel_lower)} to ${formatPrice(row.channel_upper)}`,
  hint: `${CHANNEL_RULE} ${kind}: ${kind === "Horizontal" ? "the lines move less than 0.3% of the price per day" : `the lines ${kind === "Ascending" ? "rise" : "fall"} more than 0.3% of the price per day`}.`,
  filter: (row) => row.channel === true && row.channel_type === kind,
  columns: CHANNEL_COLUMNS,
  sort: { key: "channel_position_pct", dir: "asc" },
});
const entryColumns = (fall) => [TICKER,
  { key: "tight_entry_date", label: "Breakout", type: "date", title: "Day the close broke above the range" },
  { key: "tight_entry_days_ago", label: "Days ago", type: "int", title: "Trading days since the breakout (0 = the latest day)" },
  { key: "tight_entry_pct", label: "Breakout %", type: "pct", title: "Close of the breakout day against the base close of the range" },
  { key: "tight_entry_volume_x", label: "Volume ×", type: "num2", title: `Breakout day's volume as a multiple of the average of the ${TIGHT_VOLUME_DAYS} days before it` },
  { key: "tight_entry_range_days", label: "Range days", type: "int", title: "Length of the tight range before the breakout" },
  ...(fall ? [{ key: "tight_entry_fall_pct", label: "Fall", type: "pct", title: "Base close of the range against the highest close of the 20 trading days before it" }] : []),
  { key: "tight_entry_base_close", label: "Base close", type: "price" },
  CLOSE, { key: "tight_entry_now_pct", label: "From base", type: "pct", title: "Latest close against the base close" }, CAP];
const BEFORE_ENTRY_MAX_BARS = 12;  // TBC Zone Entry (2h): only HHs at most this many 2-hour bars old
const BEFORE_ENTRY_MAX_SESSIONS = 15;   // TBC Zone Entry (1D): HHs at most this many daily bars (sessions) old
const LL_HH_MAX_BARS = 12;         // 3 LL -> HH and 4 LL -> HH lists: same idea, a wider window
const LH_BREAKOUT_MAX_BARS = 3;    // LH breakout list: only fresh breakouts
const ANG_LL_LL = { key: "ang_ll_ll", label: "LL→LL", type: "angle", title: "Angle of the latest LL low to the next LL low (% change vs bars)" };
const ANG_LL_HL = { key: "ang_ll_hl", label: "LL→HL", type: "angle", title: "Angle of the latest LL low to the HL low, when an HH followed" };
const PATTERN_FILTERS = [
  { id: "all", label: "All" }, { id: "3LL-HH", label: "3LL-HH" },
  { id: "4LL-HH", label: "4LL-HH" },
];
const LL_COUNTS = [3, 4];
// Swing fields of daily bars carry this prefix in a scan row (d1_before_entry_at, ...).
const DAILY = "d1_";
const DAILY_SWING_NOTE = " Labels are confirmed 5 sessions after the pivot, and an HH can still turn into LH if price later closes below the previous LL.";

// Each swing signal is one chip; a 2 hours / 1 day switch picks which bars it reads.
const TIMEFRAMES = [
  {
    id: "2h", label: "2 hours", short: "2h", prefix: "", timeType: "bartime", format: formatBarTime,
    barWord: "two-hour", candle: "2-hour candle", note: SWING_NOTE,
    barsTitle: "2-hour bars since the record (about 4 per session)",
  },
  {
    id: "1d", label: "1 day", short: "1D", prefix: DAILY, timeType: "date", format: formatDate,
    barWord: "daily", candle: "daily candle", note: DAILY_SWING_NOTE,
    barsTitle: "Sessions since the record",
  },
];
const DEFAULT_TIMEFRAME = "2h";

const patternText = (row, prefix = "") => row[`${prefix}before_entry_pattern`] || `${row[`${prefix}before_entry_ll`] ?? "?"} LL (other)`;

/** Fields every swing view of one timeframe shares: id, chip, key helper, the timeframe switch. */
const swingBase = (group, chip, tf) => ({
  id: tf.id === DEFAULT_TIMEFRAME ? group : `${group}1d`, group, tf: tf.id, chip, mark: null,
  needsKey: `${tf.prefix}swing_bars`, prefix: tf.prefix,
});
const barsColumn = (key, tf) => ({ key, label: "Bars ago", type: "int", title: tf.barsTitle });
const angleColumns = (tf, both = true) => [{ ...ANG_LL_LL, key: `${tf.prefix}ang_ll_ll` },
  ...(both ? [{ ...ANG_LL_HL, key: `${tf.prefix}ang_ll_hl` }] : [])];

/** TBC Zone Entry: a clean HH that completes a 3LL-HH or 4LL-HH pattern. */
const beforeEntryView = (tf) => {
  const key = (name) => `${tf.prefix}${name}`;
  const maxBars = tf.prefix === DAILY ? BEFORE_ENTRY_MAX_SESSIONS : BEFORE_ENTRY_MAX_BARS;
  return {
    ...swingBase("before", "TBC Zone Entry (3LL & 4LL)", tf), label: `TBC Zone Entry (3LL & 4LL) (${tf.short})`,
    patternMatch: (row, id) => id === "all" || row[key("before_entry_pattern")] === id,
    note: (row) => `TBC Zone Entry ${tf.format(row[key("before_entry_at")])} · ${patternText(row, tf.prefix)} · LL→HL ${formatAngle(row[key("ang_ll_hl")])}`,
    hint: `TBC Zone Entry (the notebook's Final = "Before Entry"): a higher high (HH) that survived the clean-up (${tf.barWord} bars). Only HHs that complete a 3LL-HH or 4LL-HH pattern (3 or 4 lower lows before it), from the last ${maxBars} ${tf.barWord} bars. Stocks at a new closing high (latest close ≥ every close of the last 500 daily candles) get no swing analysis and are not listed.${tf.note}`,
    filter: (row) => Boolean(row[key("before_entry_pattern")])   // 3LL-HH or 4LL-HH only
      && withinBars(row[key("before_entry_bars")], maxBars),
    columns: [TICKER, { key: key("before_entry_at"), label: "Zone entry", type: tf.timeType },
      barsColumn(key("before_entry_bars"), tf),
      { key: key("before_entry_pattern"), label: "Pattern", type: "pattern", llKey: key("before_entry_ll"), title: "Lower lows since the previous HH: 3LL-HH, 4LL-HH or other" },
      { key: key("before_entry_close"), label: "HH close", type: "price" }, CLOSE,
      // Change % of the Zone entry HH on this timeframe's swings (HH close vs the close of the last LH before it)
      { key: key("before_entry_change_pct"), label: "Change %", type: "pct",
        title: `Change % of the Zone entry HH (${tf.short} swings): (HH close - last LH close) / last LH close` },
      ...angleColumns(tf)],
    sort: { key: key("before_entry_change_pct"), dir: "desc" },   // biggest HH-over-LH move first
  };
};

/** 3 or 4 LL then HH in one list: the latest of the two (fields added by withLlHh). */
const llThenHhView = (tf) => {
  const key = (name) => `${tf.prefix}${name}`;
  const inWindow = (row, count) => withinBars(row[key(`ll${count}_hh_bars`)], LL_HH_MAX_BARS);
  return {
    ...swingBase("llhh", "LL → HH", tf), label: `3 / 4 LL then HH (${tf.short})`,
    patternMatch: (row, id) => id === "all" || LL_COUNTS.some((count) => id === `${count}LL-HH` && inWindow(row, count)),
    note: (row) => `HH ${tf.format(row[key("llhh_at")])} after ${row[key("llhh_count")]} lower lows · LL→LL ${formatAngle(row[key("ang_ll_ll")])}`,
    hint: `Exactly 3 or 4 lower lows (LL) since the previous higher high, then a new HH (${tf.barWord} bars), in the last ${LL_HH_MAX_BARS} ${tf.barWord} bars. Use 3LL-HH / 4LL-HH to see one count.${tf.note}`,
    filter: (row) => LL_COUNTS.some((count) => inWindow(row, count)),
    columns: [TICKER, { key: key("llhh_at"), label: "HH bar", type: tf.timeType }, barsColumn(key("llhh_bars"), tf),
      { key: key("llhh_pattern"), label: "Pattern", type: "pattern", title: "Lower lows since the previous HH" },
      CLOSE, CHANGE, ...angleColumns(tf)],
    sort: { key: key("llhh_at"), dir: "desc" },
  };
};

/** LH breakout: after 3+ LLs or LHs, the first close above the last LH high. */
const lhBreakoutView = (tf) => {
  const key = (name) => `${tf.prefix}${name}`;
  return {
    ...swingBase("lhbreak", "LH breakout", tf), label: `LH breakout (${tf.short})`,
    note: (row) => `Closed above LH high ${formatPrice(row[key("lh_breakout_level")])} on ${tf.format(row[key("lh_breakout_at")])}`,
    hint: `3 or more LLs or LHs since the last HH, then a ${tf.candle} closed above the high of the last LH, in the last ${LH_BREAKOUT_MAX_BARS} ${tf.barWord} bars.${tf.note}`,
    filter: (row) => withinBars(row[key("lh_breakout_bars")], LH_BREAKOUT_MAX_BARS),
    columns: [TICKER, { key: key("lh_breakout_at"), label: "Breakout bar", type: tf.timeType },
      barsColumn(key("lh_breakout_bars"), tf),
      { key: key("lh_breakout_level"), label: "LH high", type: "price", title: "High of the last LH that the close broke" },
      CLOSE, CHANGE, ...angleColumns(tf, false)],
    sort: { key: key("lh_breakout_at"), dir: "desc" },
  };
};

// The "i" next to the Stage column of TBC Zone Entry Sequence: what each stage means.
const STAGE_INFO = {
  label: "What the stages mean",
  title: "Stage: how far the stock is in the sequence LH → HH → HL → HH",
  columns: ["Stage", "Latest Clean labels", "Meaning"],
  rows: [
    ["HH1", "LH, HH", "A higher high has formed right after a lower high. This is the start of the sequence; nothing to act on yet."],
    ["HL1", "LH, HH, HL", "A higher low followed. The stock is now waiting for an Entry 1 candle."],
    ["Entry 1 fired", "LH, HH, HL", "Same labels as HL1, and a 3-rule candle has already appeared since the HL was confirmed."],
    ["HH2", "LH, HH, HL, HH", "A second higher high followed. The stock is now waiting for an Entry 2 candle."],
    ["Entry 2 fired", "LH, HH, HL, HH", "Same labels as HH2, and the Entry 2 candle has already appeared."],
  ],
};

/** TBC Zone Entry Sequence: stocks in an LH → HH → HL → HH sequence right now, by stage (2-hour or daily bars). */
const zoneEntry2View = (tf) => {
  const key = (name) => `${tf.prefix}${name}`;
  return {
    ...swingBase("hhhl", "TBC Zone Entry Sequence", tf), label: `TBC Zone Entry Sequence (${tf.short})`,
    note: (row) => `Stage: ${row[key("hhhl_status")] || DASH}`,
    hint: `Stocks in an LH → HH → HL → HH sequence right now (Clean labels in a row, ${tf.barWord} bars), by stage: HH1 (LH, HH), HL1, HH2, or an entry already fired. `
      + "Entry 1 (assumed rule) = first 3-rule candle after HL1 is confirmed; Entry 2 = first 3-rule candle after HH2 is confirmed that closes below HH2's close. "
      + "The Results tab shows how these entries did in the past.",
    filter: (row) => Boolean(row[key("hhhl_status")]),
    columns: [TICKER, { key: key("hhhl_status"), label: "Stage", type: "text", info: STAGE_INFO },
      CAP, CLOSE, CHANGE, RSI],
    sort: { key: key("hhhl_rank"), dir: "desc" },
  };
};

const SWING_VIEWS = [beforeEntryView, llThenHhView, lhBreakoutView]
  .flatMap((make) => TIMEFRAMES.map((tf) => make(tf)));

/** The latest 3 LL or 4 LL then HH of one timeframe as llhh_* fields (null when there is none). */
function llHhFields(row, prefix) {
  const hits = LL_COUNTS
    .map((count) => ({ count, at: row[`${prefix}ll${count}_hh_at`], bars: row[`${prefix}ll${count}_hh_bars`] }))
    .filter((hit) => isNumber(hit.bars))
    .sort((a, b) => a.bars - b.bars);
  const latest = hits[0];
  return {
    [`${prefix}llhh_at`]: latest ? latest.at : null,
    [`${prefix}llhh_bars`]: latest ? latest.bars : null,
    [`${prefix}llhh_count`]: latest ? latest.count : null,
    [`${prefix}llhh_pattern`]: latest ? `${latest.count}LL-HH` : null,
  };
}

/** The scan with the combined LL → HH fields added to each row (a new object; the input is unchanged). */
function withLlHh(scan) {
  if (!scan) return scan;
  return { ...scan, rows: scan.rows.map((row) => ({ ...row, ...llHhFields(row, ""), ...llHhFields(row, DAILY) })) };
}

// Old bookmark ids, from when each timeframe and LL count had its own chip.
const LEGACY_LINKS = {
  before1d: { list: "before1d" }, lhbreak1d: { list: "lhbreak1d" },
  ll3: { list: "llhh", pattern: "3LL-HH" }, ll4: { list: "llhh", pattern: "4LL-HH" },
  ll31d: { list: "llhh1d", pattern: "3LL-HH" }, ll41d: { list: "llhh1d", pattern: "4LL-HH" },
};

/** Each list: which rows it shows (given the latest market date), its columns and default sort. */
// Lists kept in the code but not shown as chips (turn one back on by removing its id here).
const HIDDEN_VIEWS = new Set(["recent", "cci", "spv", "pv", "llhh", "llhh1d", "lhbreak", "lhbreak1d", "tight", "tightentry",
  "entry", "wait", "breakout", "screen"]);
const ALL_VIEWS = [
  {
    id: "entry", label: "Entry today", mark: "entry",
    chip: "Entry today",
    note: (row) => (row.screens ? `Entry on the latest bar · also passes ${row.screens}` : "Entry on the latest bar: first up day after the pullback"),
    hint: "Reversal on the latest bar: first up day after a breakout and pullback.",
    filter: (row, dataDate) => row.status === "entry" && row.date === dataDate,
    columns: [TICKER, { key: "close", label: "Entry price", type: "price" }, CHANGE,
      { key: "screen_count", label: "Also passes", type: "screens" }, RSI, CCI],
    sort: { key: "screen_count", dir: "desc" },
  },
  {
    id: "recent", label: `Entries, last ${RECENT_SESSIONS} sessions`, mark: "entry",
    chip: `Entries (${RECENT_SESSIONS} sess)`,
    note: (row) => `Entry ${formatDate(row.last_entry_date)} at ${formatPrice(row.last_entry_price)}, ${formatPercent(row.since_entry_pct)} since`,
    hint: "Entry signals from the last few sessions and how price has moved since.",
    filter: (row) => row.last_entry_date !== null && row.sessions_since_entry < RECENT_SESSIONS,
    columns: [TICKER, { key: "last_entry_date", label: "Entry date", type: "date" },
      { key: "last_entry_price", label: "Entry price", type: "price" }, CLOSE,
      { key: "since_entry_pct", label: "Since entry", type: "pct" }],
    sort: { key: "last_entry_date", dir: "desc" },
  },
  {
    id: "wait", label: "Waiting", mark: "wait",
    chip: "Waiting",
    note: (row) => `Waiting ${days(row.wait_days)}, ${formatPercent(row.pullback_pct)} from breakout`,
    hint: "Breakout done, pullback running. The first up day becomes the entry: watch these.",
    filter: (row) => row.status === "wait",
    columns: [TICKER, SECTOR, CLOSE, CHANGE, { key: "wait_days", label: "Days waiting", type: "int" },
      { key: "pullback_pct", label: "From breakout", type: "pct" }],
    sort: { key: "wait_days", dir: "asc" },
  },
  {
    id: "breakout", label: "TBC Trigger Day", mark: "breakout",
    chip: "TBC Trigger Day",
    note: (row) => `Closed above the 4-day high of ${formatPrice(row.prev4_max_close)}`,
    hint: "Valid = True on the latest bar: up day closing above the previous 4-day high.",
    filter: (row, dataDate) => row.valid === true && row.date === dataDate,
    columns: [TICKER, SECTOR, CLOSE, CHANGE, { key: "prev4_max_close", label: "Prev 4-day high", type: "price" }],
    sort: { key: "pct_change", dir: "desc" },
  },
  {
    id: "cci", label: "CCI up-cross", mark: null, usesFundamentals: true,
    chip: "CCI up-cross",
    note: (row) => `CCI ${fixed(row.cci)} · RSI ${fixed(row.rsi)} · ATR ${fixed(row.atr_pct)}% (${row.volatility || DASH})`,
    hint: "CCI crossed above its EMA 9 today while CCI ≤ 0, close above EMA 7, gap ≥ 4, debt/equity ≤ 0.4.",
    filter: (row, dataDate) => row.cci_setup === true && row.date === dataDate,
    columns: [TICKER, CLOSE, CHANGE, CCI, { key: "cci_gap", label: "Above EMA", type: "num2" }, RSI,
      { key: "atr_pct", label: "ATR %", type: "num2" }, { key: "volatility", label: "Volatility", type: "text" }],
    sort: { key: "cci", dir: "asc" },
  },
  {
    id: "screen", label: "TBC 5-Day Breakout", mark: null, usesFundamentals: true,
    chip: "TBC 5-Day Breakout",
    note: (row) => `${row.cap_category || DASH} cap · debt/equity ${fixed(row.debt_to_equity)}${row.top_pick ? " · Top pick" : ""}`,
    hint: "Close above the previous 5 closes, CCI above its EMA (gap ≥ 2, CCI ≤ 100), RSI ≥ 40, 3-rule candle, close above EMA 7, debt/equity ≤ 0.3. Top pick: small cap, RSI ≥ 45, CCI ≤ 0, debt/equity ≤ 0.2.",
    filter: (row, dataDate) => row.breakout_setup === true && row.date === dataDate,
    columns: [TICKER, CLOSE, CCI, RSI, CAP, MARKET_CAP, DEBT,
      { key: "top_pick", label: "Top pick", type: "flag" }, { key: "status", label: "Entry status", type: "status" }],
    sort: { key: "market_cap_cr", dir: "desc" },
  },
  {
    id: "spv", label: "MRI SPV", mark: null, needsKey: "spv_position",
    chip: "MRI SPV",
    note: (row) => `SPV ${formatDate(row.spv_date)} · LTP ${(row.spv_position || DASH).toLowerCase()} (${fixed(row.spv_distance_pct)}%)`,
    hint: `Bullish SPV from the MRI Scan indicator, confirmed in the last ${MRI_SESSIONS} sessions and still active: an order block candle that starts a fair value gap after an internal structure break up. Active = price has not traded below the SPV low.`,
    filter: (row) => isNumber(row.spv_sessions) && row.spv_sessions < MRI_SESSIONS,
    columns: [TICKER, { key: "spv_date", label: "SPV candle", type: "date" }, CLOSE,
      { key: "spv_position", label: "LTP is", type: "text", title: "Last price against the SPV candle's range" },
      { key: "spv_distance_pct", label: "By %", type: "num2", title: "Distance of the last price from the SPV range, as % of price" },
      { key: "spv_low", label: "SPV low", type: "price", title: "SPV stays active while price does not trade below this" }],
    sort: { key: "spv_distance_pct", dir: "asc" },
  },
  {
    id: "pv", label: "MRI PV", mark: null, needsKey: "spv_position",
    chip: "MRI PV",
    note: (row) => `PV ${formatDate(row.pv_date)} · trend ${row.smc_trend || DASH}`,
    hint: `Bullish PV from the MRI Scan indicator, confirmed in the last ${MRI_SESSIONS} sessions: the first fair value gap candle after the order block of an internal structure break up.`,
    filter: (row) => row.pv_kind === "PV bull" && isNumber(row.pv_sessions) && row.pv_sessions < MRI_SESSIONS,
    columns: [TICKER, { key: "pv_date", label: "PV candle", type: "date" }, CLOSE, CHANGE,
      { key: "smc_trend", label: "Trend", type: "text", title: "Internal structure: direction of the last BOS / CHoCH" },
      { key: "pv_high", label: "PV high", type: "price" }, { key: "pv_low", label: "PV low", type: "price" }],
    sort: { key: "pv_date", dir: "desc" },
  },
  ...SWING_VIEWS,
  ...TIMEFRAMES.map((tf) => zoneEntry2View(tf)),
  {
    id: "tight", label: `Tight Range (${TIGHT_DAYS} days, ±${TIGHT_PCT}%)`, mark: null, needsKey: "tight_range",
    chip: "Tight Range", tabGroup: "tight", tabLabel: TIGHT_TABS,
    note: (row) => `${row.tight_days} days within ±${TIGHT_PCT}% of ${formatPrice(row.tight_base_close)} (${formatDate(row.tight_base_date)})`,
    hint: `The close has stayed within ±${TIGHT_PCT}% of a base close for ${TIGHT_DAYS} or more trading days in a row, up to the latest day (daily bars). `
      + "The base is the close of the first day of the run. Days = length of the run, base day included. "
      + "Lowest / Highest = the lowest and highest close of the run against the base; From base = the latest close against it."
      + WAIT_RULE,
    filter: (row) => row.tight_range === true && waitMomentum(row),
    columns: [TICKER, { key: "tight_days", label: "Days", type: "int", title: "Trading days in a row inside the range, the base day included" },
      { key: "tight_base_date", label: "Since", type: "date", title: "First day of the run: its close is the base" },
      { key: "tight_base_close", label: "Base close", type: "price" },
      { key: "tight_low_pct", label: "Lowest", type: "pct", title: "Lowest close of the run against the base close" },
      { key: "tight_high_pct", label: "Highest", type: "pct", title: "Highest close of the run against the base close" },
      CLOSE, { key: "tight_change_pct", label: "From base", type: "pct", title: "Latest close against the base close" }, RSI, CCI, CAP],
    sort: { key: "tight_days", dir: "desc" },
  },
  {
    id: "tightentry", label: "Tight Range: Entry", mark: null, needsKey: "tight_entry",
    chip: "Tight Range", tabGroup: "tight", tabLabel: "Entry", tabOf: "tight",
    note: (row) => `Broke out ${formatPercent(row.tight_entry_pct)} on ${formatDate(row.tight_entry_date)} · volume ${fixed(row.tight_entry_volume_x)}× average`,
    hint: ENTRY_RULE,
    filter: (row) => row.tight_entry === true,
    columns: entryColumns(false),
    sort: { key: "tight_entry_date", dir: "desc" },
  },
  {
    id: "falltight", label: "TBC Strict (1D)", mark: null, needsKey: "tight_after_fall",
    chip: "TBC Strict (1D)", tabGroup: "falltight", tabLabel: TIGHT_TABS,
    note: (row) => `Fell ${formatPercent(row.tight_fall_pct)} from ${formatPrice(row.tight_peak_close)} (${formatDate(row.tight_peak_date)}), then ${row.tight_days} days within ±${TIGHT_PCT}%`,
    hint: `A downtrend that has gone flat (daily closes). First the close fell ${FALL_PCT}% or more: from the highest close of the ${FALL_DAYS} trading days before the range to the base close. `
      + `Then the close stayed within ±${TIGHT_PCT}% of that base close for ${TIGHT_DAYS} or more trading days in a row, up to the latest day. `
      + "Fall = base close against that highest close; Days = length of the tight range."
      + WAIT_RULE,
    filter: (row) => row.tight_after_fall === true && waitMomentum(row),
    columns: [TICKER, { key: "tight_fall_pct", label: "Fall", type: "pct", title: "Base close of the range against the highest close of the 20 trading days before it" },
      { key: "tight_peak_date", label: "High on", type: "date", title: "Day of that highest close" },
      { key: "tight_peak_close", label: "High close", type: "price" },
      { key: "tight_base_date", label: "Range since", type: "date", title: "First day of the tight range: its close is the base" },
      { key: "tight_base_close", label: "Base close", type: "price" },
      { key: "tight_days", label: "Days", type: "int", title: "Trading days in a row inside the range, the base day included" },
      CLOSE, { key: "tight_change_pct", label: "From base", type: "pct", title: "Latest close against the base close" }, RSI, CCI, CAP],
    sort: { key: "tight_fall_pct", dir: "asc" },
  },
  {
    id: "falltightentry", label: "TBC Strict (1D): Entry", mark: null, needsKey: "tight_entry",
    chip: "TBC Strict (1D)", tabGroup: "falltight", tabLabel: "Entry", tabOf: "falltight",
    note: (row) => `Fell ${formatPercent(row.tight_entry_fall_pct)}, ranged ${row.tight_entry_range_days} days, broke out ${formatPercent(row.tight_entry_pct)} on ${formatDate(row.tight_entry_date)}`,
    hint: `${ENTRY_RULE} Only ranges that came after a fall of ${FALL_PCT}% or more (from the highest close of the ${FALL_DAYS} trading days before the range).`,
    filter: (row) => row.tight_entry === true && row.tight_entry_after_fall === true,
    columns: entryColumns(true),
    sort: { key: "tight_entry_date", dir: "desc" },
  },
  channelView("Ascending", true), channelView("Descending", false), channelView("Horizontal", false),
  {
    id: "all", label: "All stocks", mark: null,
    chip: "All",
    note: (row) => [row.sector, row.cap_category && `${row.cap_category} cap`].filter(Boolean).join(" · ") || "Scanned",
    hint: "Every stock from the last scan.",
    filter: () => true,
    columns: [TICKER, SECTOR, CAP, MARKET_CAP, DEBT, CLOSE, CHANGE,
      { key: "status", label: "Status", type: "status" }, { key: "last_entry_date", label: "Last entry", type: "date" }],
    sort: { key: "ticker", dir: "asc" },
  },
];
const VIEWS = ALL_VIEWS.filter((view) => !HIDDEN_VIEWS.has(view.id));

const $ = (id) => document.getElementById(id);
const dom = {
  meta: $("scan-meta"), scanButton: $("scan-button"), progress: $("progress"), progressFill: $("progress-fill"),
  progressText: $("progress-text"), notice: $("notice"), tiles: $("tiles"), cards: $("stock-cards"),
  sortSelect: $("sort-select"), detailPane: $("detail-pane"), detailClose: $("detail-close"), scrim: $("scrim"),
  toTop: $("to-top"), confirm: $("scan-confirm"), confirmText: $("confirm-text"), confirmRun: $("confirm-run"),
  confirmCancel: $("confirm-cancel"), marketWarning: $("market-warning"), marketWarningText: $("market-warning-text"),
  searchForm: $("search-form"), searchInput: $("search-input"), tickerList: $("ticker-list"),
  listTitle: $("list-title"), listCount: $("list-count"), listHint: $("list-hint"), winRate: $("win-rate"),
  listTabs: $("list-tabs"), listPane: document.querySelector(".list-pane"),
  pickBar: $("pick-bar"),
  table: $("stock-table"), listEmpty: $("list-empty"), detail: $("detail"), capFilter: $("cap-filter"),
  patternFilter: $("pattern-filter"), timeframeFilter: $("timeframe-filter"), topbar: document.querySelector(".topbar"),
  tvButton: $("tv-button"), tvDialog: $("tv-dialog"), tvForm: $("tv-form"), tvStatus: $("tv-status"),
  tvUsername: $("tv-username"), tvPassword: $("tv-password"), tvError: $("tv-error"), tvCancel: $("tv-cancel"),
  tvSubmit: $("tv-submit"), tvSignout: $("tv-signout"), tvCheck: $("tv-check"),
};

let state = {
  scan: undefined,  // undefined until the first load answers; null when there is no scan yet
  progress: { running: false }, tickers: [], viewId: VIEWS[0].id,
  sort: VIEWS[0].sort, query: "", selected: null, capFilter: "all", patternFilter: "all", zoneTab: "stocks",
  timeframe: DEFAULT_TIMEFRAME,
};
let pollTimer = null;
let detailCleanup = null;
let detailRequest = 0;

const setState = (changes) => {
  state = { ...state, ...changes };
  render();
};
const currentView = () => VIEWS.find((view) => view.id === state.viewId);
/** Chips: every list, but swing lists only for the selected timeframe (one chip per signal). */
const chipViews = () => VIEWS.filter((view) => !view.tabOf && (!view.tf || view.tf === state.timeframe));
/** The lists of one section that are shown as tabs (e.g. Wait / Entry), or null when the list has none. */
const tabViews = (view) => (view.tabGroup ? VIEWS.filter((other) => other.tabGroup === view.tabGroup) : null);
/** The same signal on another timeframe, e.g. TBC Zone Entry 2h -> TBC Zone Entry 1D. */
const sibling = (view, tf) => VIEWS.find((other) => other.group === view.group && other.tf === tf) || view;
/** Current stage per ticker, { daily: {...}, twoHour: {...} } (data/hh_hl_entries.json); loaded once with the page. */
let hhhlStatus = null;
const HHHL_RANK = { "Entry 2 fired": 6, HH2: 5, "Entry 1 fired": 4, HL1: 3, HH1: 2 };
let staged = { scan: null, status: null, rows: [] };
/** Scan rows, each with its HH/HL stage (hhhl_status, hhhl_rank) once the stages are loaded. */
const rows = () => {
  if (!state.scan) return [];
  if (!hhhlStatus) return state.scan.rows;
  if (staged.scan !== state.scan || staged.status !== hhhlStatus) {
    staged = { scan: state.scan, status: hhhlStatus, rows: state.scan.rows.map((row) => {
      const stage = hhhlStatus.twoHour[row.ticker] || null;      // 2-hour fields have no prefix, daily ones "d1_"
      const daily = hhhlStatus.daily[row.ticker] || null;
      return { ...row, hhhl_status: stage, hhhl_rank: HHHL_RANK[stage] || 0,
        [`${DAILY}hhhl_status`]: daily, [`${DAILY}hhhl_rank`]: HHHL_RANK[daily] || 0 };
    }) };
  }
  return staged.rows;
};
/** Rows of a list before the market-cap filter. */
const viewRows = (view) => rows().filter((row) => view.filter(row, state.scan.data_date));
const capMatches = (row, capId) => capId === "all" || row.cap_category === capId;
/** Rows of a list for the selected market-cap group (chip counts ignore the pattern filter). */
const capRows = (view) => viewRows(view).filter((row) => capMatches(row, state.capFilter));
/** Rows of a list for the selected market-cap group and, on Before Entry, the selected pattern. */
const rowsFor = (view) => viewRows(view).filter((row) => capMatches(row, state.capFilter)
  && (!view.patternMatch || view.patternMatch(row, state.patternFilter)));
const errorText = (error) => (error instanceof Error ? error.message : "Something went wrong.");

function showNotice(text) {
  dom.notice.textContent = text || "";
  dom.notice.hidden = !text;
}

function matchesQuery(row, query) {
  const needle = query.trim().toLowerCase();
  return !needle || row.ticker.toLowerCase().includes(needle) || (row.name || "").toLowerCase().includes(needle);
}

function renderMeta() {
  const { scan, progress } = state;
  dom.scanButton.disabled = progress.running;
  dom.scanButton.textContent = progress.running ? "Scanning…" : scan ? "Run scan again" : "Run scan";
  dom.progress.hidden = !progress.running;
  if (progress.running) {
    const share = progress.total ? Math.round((progress.done / progress.total) * 100) : 0;
    dom.progressFill.style.width = `${share}%`;
    const stage = STAGE_TEXT[progress.stage] || "Scanning";
    const status = progress.total ? `${stage}: ${progress.done} of ${progress.total} stocks` : "Starting…";
    dom.progressText.textContent = isMarketHours() ? `${status} · ${LIVE_PRICE_NOTE}` : status;
  }
  dom.meta.textContent = scan === undefined ? "Loading the latest scan…" : scan
    ? `Prices up to ${formatDate(scan.data_date, true)} · scanned ${formatDateTime(scan.scanned_at)}`
    : "No scan yet. Press Run scan to check every stock in tickers.csv.";
}

/** One chip per list with its stock count; the row scrolls sideways on small screens. */
function renderTiles() {
  const { scan } = state;
  const failed = scan ? scan.failed.length : 0;
  dom.tiles.replaceChildren(...chipViews().map((view) => {
    const selected = state.viewId === view.id || currentView().tabOf === view.id;   // a tab of this section is open
    const title = view.id === "all" && failed ? `${view.hint} ${failed} returned no data.` : view.hint;
    return el("button", {
      type: "button", class: "chip", "aria-pressed": String(selected), title,
      onclick: () => selectView(view.id),
    }, [
      view.mark ? el("span", { class: `mark ${view.mark}`, "aria-hidden": "true" }) : null,
      el("span", { text: view.chip }),
      el("span", { class: "chip-count", text: `(${scan ? capRows(view).length : DASH})` }),
    ]);
  }));
  // Keep the selected chip visible by scrolling the chip row only (scrollIntoView would also move the page).
  const active = dom.tiles.querySelector('[aria-pressed="true"]');
  if (!active) return;
  const row = dom.tiles;
  const left = active.offsetLeft - row.offsetLeft;
  if (left < row.scrollLeft || left + active.offsetWidth > row.scrollLeft + row.clientWidth) {
    row.scrollLeft = Math.max(0, left - 16);
  }
}

/** Sort picker for the card layout, where there are no column headers to click. */
function renderSortSelect(view) {
  const options = view.columns.flatMap((column) => [
    el("option", { value: `${column.key}:desc`, text: `${column.label} ↓` }),
    el("option", { value: `${column.key}:asc`, text: `${column.label} ↑` }),
  ]);
  dom.sortSelect.replaceChildren(...options);
  dom.sortSelect.value = `${state.sort.key}:${state.sort.dir}`;
}

/** Large / Mid / Small buttons with the number of stocks of the current list in each group. */
function renderCapFilter(view) {
  const all = state.scan ? viewRows(view) : [];
  dom.capFilter.replaceChildren(...CAP_FILTERS.map((cap) => el("button", {
    type: "button", "aria-pressed": String(state.capFilter === cap.id),
    text: `${cap.label} (${all.filter((row) => capMatches(row, cap.id)).length})`,
    onclick: () => setState({ capFilter: cap.id }),
  })));
  dom.capFilter.hidden = !state.scan;
}

/** 2 hours / 1 day switch on the swing lists, with the stock count of the same signal on each. */
function renderTimeframeFilter(view) {
  dom.timeframeFilter.hidden = !(view.tf && state.scan);
  if (dom.timeframeFilter.hidden) return;
  dom.timeframeFilter.replaceChildren(...TIMEFRAMES.map((tf) => el("button", {
    type: "button", "aria-pressed": String(view.tf === tf.id),
    text: `${tf.label} (${capRows(sibling(view, tf.id)).length})`,
    onclick: () => selectTimeframe(tf.id),
  })));
}

/** All / 3LL-HH / 4LL-HH buttons, on TBC Zone Entry and LL → HH. */
function renderPatternFilter(view) {
  dom.patternFilter.hidden = !(view.patternMatch && state.scan);
  if (dom.patternFilter.hidden) return;
  const inList = viewRows(view).filter((row) => capMatches(row, state.capFilter));
  dom.patternFilter.replaceChildren(...PATTERN_FILTERS.map((option) => el("button", {
    type: "button", "aria-pressed": String(state.patternFilter === option.id),
    text: `${option.label} (${inList.filter((row) => view.patternMatch(row, option.id)).length})`,
    onclick: () => setState({ patternFilter: option.id }),
  })));
}

const SOURCE_NAMES = {
  tradingview: "TradingView", "tradingview-nologin": "TradingView (no login)", "tbc-csv": "TBC_all_stocks_bars.csv",
};

/** " 2-hour prices: TradingView 826." for the scan rows that carry a source. */
function sourceNote(scanRows) {
  const counts = scanRows.reduce((acc, row) => (row.swing_source
    ? { ...acc, [row.swing_source]: (acc[row.swing_source] || 0) + 1 } : acc), {});
  const parts = Object.entries(counts).map(([source, count]) => `${SOURCE_NAMES[source] || source} ${count}`);
  return parts.length ? ` 2-hour prices: ${parts.join(", ")}.` : "";
}

/** Win rate of the replayed Zone Entry trades above the TBC Zone Entry lists; loaded once, on first view. */
let zoneResults = null;
let zoneResultsAsked = false;
let zoneResultsError = null;
const ZONE_TABS = [{ id: "stocks", label: "Stocks" }, { id: "winrate", label: "Win rate" }];
const HHHL_TABS = [{ id: "stocks", label: "Stocks" }, { id: "winrate", label: "Results" }];
/** Lists with a second tab beside the stocks: TBC Zone Entry (win rate) and TBC Zone Entry Sequence (results). */
const tabsOf = (view) => (view.group === "before" ? ZONE_TABS : view.group === "hhhl" ? HHHL_TABS : null);
let hhhlResults = null;
let hhhlResultsAsked = false;
let hhhlResultsError = null;

/** Stocks / Win rate tabs of the TBC Zone Entry list: the win rate gets its own tab so the list keeps its height. */
function renderListTabs(view) {
  const lists = tabViews(view);
  if (lists) {   // tabs that are lists of their own, e.g. Wait / Entry
    dom.listTabs.hidden = false;
    dom.listPane.classList.add("zone-list");
    dom.listPane.classList.remove("show-win-rate");
    dom.listTabs.replaceChildren(...lists.map((list) => el("button", {
      type: "button", "aria-pressed": String(list.id === view.id),
      text: `${list.tabLabel} (${state.scan ? capRows(list).length : DASH})`,
      onclick: () => selectView(list.id),
    })));
    return;
  }
  const tabs = tabsOf(view);
  const zone = Boolean(tabs);
  dom.listTabs.hidden = !zone;
  dom.listPane.classList.toggle("zone-list", zone);
  dom.listPane.classList.toggle("show-win-rate", zone && state.zoneTab === "winrate");
  if (!zone) return;
  dom.listTabs.replaceChildren(...tabs.map((tab) => el("button", {
    type: "button", "aria-pressed": String(state.zoneTab === tab.id), text: tab.label,
    onclick: () => setState({ zoneTab: tab.id }),
  })));
}

/** Results tab of the TBC Zone Entry Sequence list; the study is loaded once, on first view. */
function renderHhHlResults() {
  if (!hhhlResultsAsked) {
    hhhlResultsAsked = true;
    getHhHlResults()
      .then((data) => { hhhlResults = data; })
      .catch((error) => { hhhlResultsError = error.message; })
      .finally(() => renderZoneWinRate(currentView()));
  }
  if (hhhlResultsError) {
    dom.winRate.textContent = `TBC Zone Entry Sequence results not available: ${hhhlResultsError}`;
    return;
  }
  renderHhHl(dom.winRate, hhhlResults, (ticker) => openStock(ticker), currentView().tf === "2h" ? "2h" : "Daily");
}

function renderZoneWinRate(view) {
  const show = Boolean(tabsOf(view)) && state.zoneTab === "winrate";
  dom.winRate.hidden = !show;
  if (!show) return;
  if (view.group === "hhhl") {
    renderHhHlResults();
    return;
  }
  if (!zoneResultsAsked) {
    zoneResultsAsked = true;
    getZoneResults()
      .then((data) => { zoneResults = data; })
      .catch((error) => { zoneResultsError = error.message; })
      .finally(() => renderZoneWinRate(currentView()));
  }
  if (zoneResultsError) {
    dom.winRate.textContent = `Win rate not available: ${zoneResultsError}`;
    return;
  }
  const caps = new Map(rows().map((row) => [row.ticker, row.cap_category || ""]));
  renderWinRate(dom.winRate, zoneResults, view.tf, (ticker) => openStock(ticker), (ticker) => caps.get(ticker) || "");
}

/** Stocks ticked in each TBC Zone Entry list (view id -> tickers); kept while the page is open. */
const picked = new Map();
const pickedIn = (view) => picked.get(view.id) || new Set();
const canPick = (view) => view.group === "before";

function setPicked(view, tickers) {
  picked.set(view.id, new Set(tickers));
  renderList();
}

function pickColumn(view) {
  return {
    key: "_pick", label: "✓", type: "pick", title: "Tick stocks to export them to CSV",
    isPicked: (row) => pickedIn(view).has(row.ticker),
    onToggle: (row, on) => {
      const next = new Set(pickedIn(view));
      if (on) next.add(row.ticker); else next.delete(row.ticker);
      setPicked(view, next);
    },
  };
}

/** CSV fields of a list: its columns as shown (stock split into ticker and name), plus cap and timeframe. */
function exportFields(view) {
  const plain = (column) => (row) => {
    const value = row[column.key];
    if (column.type === "pattern") return value || `${row[column.llKey || "before_entry_ll"] ?? "?"} LL (other)`;
    return value;
  };
  return [
    { label: "Ticker", value: (row) => row.ticker },
    { label: "Name", value: (row) => row.name || "" },
    { label: "Cap", value: (row) => row.cap_category || "" },
    { label: "Timeframe", value: () => (view.tf === "1d" ? "1D" : "2h") },
    ...view.columns.filter((column) => column.type !== "ticker").map((column) => ({ label: column.label, value: plain(column) })),
  ];
}

function renderPickBar(view, visible) {
  dom.pickBar.hidden = !canPick(view);
  if (!canPick(view)) return;
  const inList = new Set(visible.map((row) => row.ticker));
  const chosen = visible.filter((row) => pickedIn(view).has(row.ticker));
  const exportRows = () => {
    const text = toCsv(chosen, exportFields(view));
    downloadCsv(`${exportDate()}.csv`, text);
  };
  dom.pickBar.replaceChildren(
    el("button", { type: "button", text: `Select all (${visible.length})`, disabled: !visible.length,
      onclick: () => setPicked(view, [...pickedIn(view), ...inList]) }),
    el("button", { type: "button", text: "Clear", disabled: !chosen.length,
      onclick: () => setPicked(view, [...pickedIn(view)].filter((ticker) => !inList.has(ticker))) }),
    el("button", { type: "button", class: "primary", text: `Export CSV (${chosen.length})`, disabled: !chosen.length,
      title: `Save the ticked stocks as ${exportDate()}.csv`, onclick: exportRows }),
    el("span", { class: "pick-count dim", text: chosen.length ? `${chosen.length} selected` : "Tick stocks to export them" }),
  );
}

function renderList() {
  const view = currentView();
  renderCapFilter(view);
  renderTimeframeFilter(view);
  renderPatternFilter(view);
  const inView = state.scan ? rowsFor(view) : [];
  const visible = sortRows(inView.filter((row) => matchesQuery(row, state.query)), state.sort);
  dom.listTitle.textContent = view.label;
  let hint = view.hint;
  const needsKey = view.needsKey || "screen_count";
  if (state.scan && rows().length && rows()[0][needsKey] === undefined) hint += OLD_SCAN;
  else if (view.usesFundamentals && state.scan && !rows().some((row) => row.fundamentals_known)) hint += NO_FUNDAMENTALS;
  else if (needsKey.endsWith("swing_bars") && rows().length && !rows().some((row) => row[needsKey] > 0)) {
    hint += view.prefix === DAILY ? NO_DAILY_SWINGS : NO_SWINGS;
  }
  if (view.tf === "2h" && state.scan) hint += sourceNote(rows());
  dom.listHint.textContent = hint;
  renderListTabs(view);
  renderZoneWinRate(view);
  const shown = state.query.trim() ? `${visible.length} of ${inView.length}` : String(inView.length);
  dom.listCount.textContent = state.scan ? `· ${shown} stock${inView.length === 1 ? "" : "s"}` : "";
  renderSortSelect(view);
  const pick = canPick(view) ? pickColumn(view) : null;
  renderPickBar(view, visible);
  renderCards(dom.cards, { rows: visible, selected: state.selected, note: view.note, onSelect: (row) => openStock(row.ticker), pick });
  renderTable(dom.table, {
    rows: visible, columns: pick ? [pick, ...view.columns] : view.columns, sort: state.sort, selected: state.selected,
    onSort: (key) => setState({ sort: { key, dir: state.sort.key === key && state.sort.dir === "desc" ? "asc" : "desc" } }),
    onSelect: (row) => openStock(row.ticker),
  });
  let empty = "";
  if (!state.scan) empty = "Nothing to show yet. Run a scan first, or search for a single stock above.";
  else if (!inView.length) {
    const group = state.capFilter === "all" ? "" : ` (${state.capFilter} cap)`;
    empty = `No stocks in "${view.label}"${group} for ${formatDate(state.scan.data_date, true)}.`;
  }
  else if (!visible.length) empty = `No match for "${state.query.trim()}" in this list. Press Enter to open it directly.`;
  dom.listEmpty.textContent = empty;
  dom.listEmpty.hidden = !empty;
  dom.table.hidden = Boolean(empty);
  dom.cards.hidden = Boolean(empty);
}

function render() {
  renderMeta();
  renderTiles();
  renderList();
}

/** On phones and tablets the stock detail slides up as a sheet over the list. */
function setSheetOpen(open) {
  const isSheet = !window.matchMedia(WIDE_LAYOUT).matches;
  document.body.classList.toggle("sheet-open", open && isSheet);
  dom.scrim.hidden = !(open && isSheet);
  if (open && isSheet) dom.detailPane.scrollTop = 0;
}

/** Wide screens: the list uses the full width until a stock is opened, then splits with the detail. */
function setDetailShown(shown) {
  document.body.classList.toggle("has-detail", shown);
}

function closeSheet() {
  setSheetOpen(false);
  setDetailShown(false);  // in every layout, so widening the window later does not bring back a stale detail
  if (window.matchMedia(WIDE_LAYOUT).matches) {
    detailRequest += 1;  // a detail still loading must not reopen the pane
    if (detailCleanup) detailCleanup();
    detailCleanup = null;
    history.replaceState(null, "", location.pathname + location.search);
    setState({ selected: null });
  }
  const card = dom.cards.querySelector('[aria-pressed="true"]');
  if (card) card.focus({ preventScroll: true });
}

function watchScroll() {
  const update = () => { dom.toTop.hidden = window.scrollY < TO_TOP_AFTER_PX; };
  window.addEventListener("scroll", update, { passive: true });
  dom.toTop.addEventListener("click", () => window.scrollTo({ top: 0, behavior: "smooth" }));
  update();
}

function selectView(viewId) {
  setState({ viewId, sort: VIEWS.find((view) => view.id === viewId).sort });
}

/** Switch swing lists between 2-hour and daily bars; stays on the same signal. */
function selectTimeframe(timeframe) {
  const view = currentView();
  const next = view.tf ? sibling(view, timeframe) : view;
  setState({ timeframe, viewId: next.id, sort: next.id === view.id ? state.sort : next.sort });
}

async function openStock(symbol) {
  const request = ++detailRequest;
  if (detailCleanup) detailCleanup();
  detailCleanup = null;
  setState({ selected: symbol });
  renderDetailMessage(dom.detail, `Loading ${symbol}…`);
  setDetailShown(true);
  setSheetOpen(true);
  try {
    const detail = await getStock(symbol);
    if (request !== detailRequest) return;  // a newer click won
    detailCleanup = renderDetail(dom.detail, detail);
    setState({ selected: detail.summary.ticker });
    history.replaceState(null, "", `#${encodeURIComponent(detail.summary.ticker)}`);  // bookmarkable
  } catch (error) {
    if (request === detailRequest) renderDetailMessage(dom.detail, errorText(error));
  }
}

/** Resolve typed text to one symbol: exact symbol, else the only name match, else the text itself. */
function resolveQuery(text) {
  const query = text.trim();
  const upper = query.toUpperCase();
  const known = [...state.tickers, ...rows()];
  const exact = known.find((item) => item.ticker === upper || item.ticker.split(".")[0] === upper);
  if (exact) return exact.ticker;
  const matches = [...new Set(known.filter((item) => matchesQuery(item, query)).map((item) => item.ticker))];
  if (matches.length === 1) return matches[0];
  if (SYMBOL_LIKE.test(query)) return upper;
  return null;
}

function onSearchSubmit(event) {
  event.preventDefault();
  const text = dom.searchInput.value;
  if (!text.trim()) return;
  const symbol = resolveQuery(text);
  if (symbol) {
    showNotice("");
    openStock(symbol);
  } else {
    showNotice(`"${text.trim()}" matches several stocks or is not a symbol. Pick one from the list or type its symbol, e.g. TCS.`);
  }
}

async function refreshScan() {
  const data = await getScan();
  setState({ scan: withLlHh(data.scan), progress: data.progress });
  return data.progress;
}

function stopPolling() {
  clearInterval(pollTimer);
  pollTimer = null;
}

function startPolling() {
  if (pollTimer) return;
  let failures = 0;
  pollTimer = setInterval(async () => {
    try {
      const { progress } = await getScanStatus();
      failures = 0;
      if (progress.running) {
        setState({ progress });
        return;
      }
      stopPolling();
      await refreshScan();
      showNotice(progress.error ? `Scan failed: ${progress.error}` : "");
    } catch (error) {
      failures += 1;
      if (failures < MAX_POLL_FAILURES) return;  // a single dropped request must not end the progress view
      stopPolling();
      setState({ progress: { running: false } });
      showNotice(errorText(error));
    }
  }, POLL_MS);
}

/** Ask before scanning; during NSE hours also warn that today's close is a live price. */
function openScanConfirm() {
  const total = state.tickers.length || rows().length;
  dom.confirmText.textContent = `This downloads fresh daily prices for ${total ? `all ${total} stocks` : "every stock"} from Yahoo Finance and takes a few minutes. Everyone using the app will see the new results.`;
  const live = isMarketHours();
  dom.marketWarning.hidden = !live;
  dom.marketWarningText.textContent = live ? LIVE_PRICE_WARNING : "";
  dom.confirmRun.textContent = live ? "Scan with live prices" : "Run scan";
  dom.confirm.hidden = false;
  document.body.classList.add("modal-open");
  dom.confirmCancel.focus();
}

function closeScanConfirm() {
  dom.confirm.hidden = true;
  document.body.classList.remove("modal-open");
  dom.scanButton.focus();
}

async function onScanClick() {
  closeScanConfirm();
  showNotice("");
  try {
    const { progress } = await startScan();
    setState({ progress });
    startPolling();
  } catch (error) {
    showNotice(errorText(error));
    await resumeIfScanning();
  }
}

/** If the server is already scanning (e.g. "already running" reply), show its progress again. */
async function resumeIfScanning() {
  try {
    const { progress } = await getScanStatus();
    if (!progress.running) return;
    setState({ progress });
    startPolling();
  } catch (error) {
    // Server unreachable: the notice from the failed click already says so.
  }
}

/* ---------- TradingView login (local app only) ---------- */
function tvStatusText(status) {
  if (!status.configured) return "Not signed in: 2-hour lists stay empty until you sign in and run a scan.";
  const who = status.username ? ` as ${status.username}` : "";
  const from = status.origin === "env" ? " (from TV_USERNAME / TV_PASSWORD)" : "";
  if (status.signed_in === false) return `TradingView did not accept the login${who}${from}. Sign in again.`;
  if (status.signed_in === null) return `Login set${who}${from}; it is checked on the next scan.`;
  return `Signed in${who}${from}.`;
}

function renderTvButton(status) {
  dom.tvCheck.hidden = !(status.configured && status.signed_in !== false);
  dom.tvButton.title = tvStatusText(status);
}

function showTvError(text) {
  dom.tvError.textContent = text || "";
  dom.tvError.hidden = !text;
}

async function openTvDialog() {
  showTvError("");
  dom.tvPassword.value = "";
  dom.tvDialog.hidden = false;
  document.body.classList.add("modal-open");
  dom.tvStatus.textContent = "Checking…";
  try {
    const status = await getTradingView();
    dom.tvStatus.textContent = tvStatusText(status);
    dom.tvSignout.hidden = !status.configured;
    if (status.username && !dom.tvUsername.value) dom.tvUsername.value = status.username;
  } catch (error) {
    dom.tvStatus.textContent = errorText(error);
  }
  (dom.tvUsername.value ? dom.tvPassword : dom.tvUsername).focus();
}

function closeTvDialog() {
  dom.tvPassword.value = "";  // never keep the password in the page longer than the request
  dom.tvDialog.hidden = true;
  document.body.classList.remove("modal-open");
  dom.tvButton.focus();
}

async function onTvSubmit(event) {
  event.preventDefault();
  const username = dom.tvUsername.value.trim();
  const password = dom.tvPassword.value;
  if (!username || !password) {
    showTvError("Type your TradingView username and password.");
    return;
  }
  showTvError("");
  dom.tvSubmit.disabled = true;
  dom.tvSubmit.textContent = "Signing in…";
  try {
    const status = await signInTradingView(username, password);
    renderTvButton(status);
    closeTvDialog();
    showNotice(`${tvStatusText(status)} Run a new scan to load 2-hour data from TradingView.`);
  } catch (error) {
    showTvError(errorText(error));
  } finally {
    dom.tvPassword.value = "";
    dom.tvSubmit.disabled = false;
    dom.tvSubmit.textContent = "Sign in";
  }
}

async function onTvSignOut() {
  try {
    const status = await signOutTradingView();
    renderTvButton(status);
    closeTvDialog();
    showNotice("Signed out of TradingView. New scans will have no 2-hour data until you sign in again.");
  } catch (error) {
    showTvError(errorText(error));
  }
}

async function setUpTradingView() {
  if (!TV_LOGIN_AVAILABLE) return;  // the live site takes its login from the Streamlit secrets
  dom.tvButton.hidden = false;
  dom.tvButton.addEventListener("click", openTvDialog);
  dom.tvForm.addEventListener("submit", onTvSubmit);
  dom.tvCancel.addEventListener("click", closeTvDialog);
  dom.tvSignout.addEventListener("click", onTvSignOut);
  dom.tvDialog.addEventListener("click", (event) => { if (event.target === dom.tvDialog) closeTvDialog(); });
  try {
    renderTvButton(await getTradingView());
  } catch (error) {
    // The button still opens the dialog, which shows the error.
  }
}

/** The sticky header's height, so the detail pane and table sit right under it. */
function trackHeaderHeight() {
  const update = () => document.documentElement.style.setProperty("--header-h", `${dom.topbar.offsetHeight}px`);
  if ("ResizeObserver" in window) new ResizeObserver(update).observe(dom.topbar);
  update();
}

async function loadTickers() {
  try {
    const tickers = await getTickers();
    state = { ...state, tickers };
    dom.tickerList.replaceChildren(...tickers.map((item) => el("option", { value: item.ticker, label: item.name })));
  } catch (error) {
    showNotice(errorText(error));  // search by exact symbol still works without the list
  }
}

async function init() {
  dom.scanButton.addEventListener("click", openScanConfirm);
  dom.confirmRun.addEventListener("click", onScanClick);
  dom.confirmCancel.addEventListener("click", closeScanConfirm);
  dom.confirm.addEventListener("click", (event) => { if (event.target === dom.confirm) closeScanConfirm(); });
  dom.searchForm.addEventListener("submit", onSearchSubmit);
  dom.searchInput.addEventListener("input", () => setState({ query: dom.searchInput.value }));
  dom.sortSelect.addEventListener("change", () => {
    const [key, dir] = dom.sortSelect.value.split(":");
    setState({ sort: { key, dir } });
  });
  dom.detailClose.addEventListener("click", closeSheet);
  dom.scrim.addEventListener("click", closeSheet);
  document.addEventListener("keydown", (event) => {
    if (event.key !== "Escape") return;
    if (!dom.tvDialog.hidden) closeTvDialog();
    else if (!dom.confirm.hidden) closeScanConfirm();
    else if (document.body.classList.contains("sheet-open") || document.body.classList.contains("has-detail")) closeSheet();
  });
  window.matchMedia(WIDE_LAYOUT).addEventListener("change", (event) => {
    if (event.matches) setSheetOpen(false);  // rotated / resized to the side-by-side layout
  });
  watchScroll();
  trackHeaderHeight();
  dom.listHint.addEventListener("click", () => dom.listHint.classList.toggle("expanded"));
  setUpTradingView();
  // Bookmarkable lists, e.g. /?list=screen&cap=Large opens TBC 5-Day Breakout for large caps.
  const params = new URLSearchParams(location.search);
  const legacy = LEGACY_LINKS[params.get("list")] || {};
  const listId = legacy.list || params.get("list");
  const linkedView = VIEWS.find((view) => view.id === listId);
  const linkedCap = CAP_FILTERS.find((cap) => cap.id === params.get("cap"));
  const linkedPattern = PATTERN_FILTERS.find((option) => option.id === (legacy.pattern || params.get("pattern")));
  if (linkedView) state = { ...state, viewId: linkedView.id, sort: linkedView.sort, timeframe: linkedView.tf || state.timeframe };
  if (linkedPattern) state = { ...state, patternFilter: linkedPattern.id };
  if (linkedCap) state = { ...state, capFilter: linkedCap.id };
  render();
  // HH/HL stages for the "TBC Zone Entry Sequence" list; the page works without them (the list is then empty)
  getHhHlStatus().then((data) => { hhhlStatus = { daily: data.status || {}, twoHour: data.status_2h || {} }; render(); })
    .catch(() => { hhhlStatus = null; });
  try {
    const progress = await refreshScan();
    if (progress.running) startPolling();
  } catch (error) {
    state = { ...state, scan: null };  // stop showing "Loading…" on later renders
    dom.meta.textContent = "Could not load the scan.";
    showNotice(errorText(error));
  }
  await loadTickers();
  let linked = "";
  try {
    linked = decodeURIComponent(location.hash.slice(1));
  } catch (error) {
    linked = "";  // malformed #fragment: just open the page without a stock
  }
  if (SYMBOL_LIKE.test(linked)) openStock(linked.toUpperCase());  // page opened from a bookmark like /#TCS.NS
}

initThemeToggle(document.getElementById("theme-button"));
init();
