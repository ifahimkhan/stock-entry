// Single-stock panel: headline, plain-language status, chart, and the bars as a table.

import { infoButton } from "./infotip.js";
import { TILE_INFO } from "./overviewinfo.js";
import { PLOT_MARGIN, renderChart, SHOW_CYCLE_MARKS } from "./chart.js";
import { attachZoom, fitWindow, sliceWindow, zoomWindow, ZOOM_STEP } from "./chartzoom.js";
import { DASH, el, formatAngle, formatBarTime, formatDate, formatPercent, formatPrice, isNumber, signClass } from "./format.js";
import { renderTable } from "./table.js";

const HISTORY_ROWS = 40;
const RANGES = [{ label: "1M", bars: 22 }, { label: "3M", bars: 66 }, { label: "6M", bars: null }];
const DEFAULT_RANGE = "3M";
// Chart of the 2-hour bars (a stock opened from the Ascending list): 4 bars a session
const TWO_HOUR_RANGES = [{ label: "1W", bars: 20 }, { label: "2W", bars: 40 }, { label: "All", bars: null }];
const TWO_HOUR_DEFAULT_RANGE = "All";
const ENTRY_SHORT = { "Entry 1": "E1", "Entry 2": "E2" };   // P-Final of a bar, as written on the chart
const MODES = [{ label: "Candles", mode: "candles" }, { label: "Line", mode: "line" }];
const DEFAULT_MODE = "candles";
const FULL_VIEW_MIN_HEIGHT = 320;   // px: the chart is never shorter than outside the full view
const FULL_VIEW_SPARE = 62;         // px kept for the card padding and the zoom hint under the chart
const HISTORY_COLUMNS = [
  { key: "Date", label: "Date", type: "date" },
  { key: "Open", label: "Open", type: "price" },
  { key: "High", label: "High", type: "price" },
  { key: "Low", label: "Low", type: "price" },
  { key: "Close", label: "Close", type: "price" },
  { key: "pct_change", label: "Chg %", type: "pct" },
  { key: "prev4_max_close", label: "4D high", type: "price", title: "Highest close of the previous 4 days" },
  { key: "valid", label: "Valid", type: "valid" },
  { key: "status", label: "Status", type: "status" },
  { key: "entry_price", label: "Entry", type: "price", title: "Entry price (close of the entry day)" },
];

const plural = (count, word) => `${count} ${word}${count === 1 ? "" : "s"}`;

/** Legend items for the chart type actually drawn ("candles" or "line"). */
/** History whose bars from the channel's first anchor on carry the two line values (straight lines per bar). */
function withChannel(history, summary) {
  if (summary.channel !== true || !isNumber(summary.channel_start_lower) || !isNumber(summary.channel_lower)) return history;
  const since = String(summary.channel_since).slice(0, 10);   // a 2-hour channel starts on a bar: its day
  const first = history.findIndex((bar) => String(bar.Date).slice(0, 10) >= since);
  const last = history.length - 1;
  if (first < 0 || first >= last) return history;
  const width = summary.channel_upper - summary.channel_lower;
  const step = (summary.channel_lower - summary.channel_start_lower) / (last - first);
  return history.map((bar, index) => {
    if (index < first) return bar;
    const lower = summary.channel_start_lower + step * (index - first);
    return { ...bar, channel_lower: lower, channel_upper: lower + width };
  });
}

/** History whose days carry the cleaned daily swing label (HH / HL / LH / LL) of the Swings 1D tab, for the chart. */
function withSwingLabels(history, dailyBars) {
  const labels = new Map((dailyBars || []).filter((bar) => bar.label).map((bar) => [String(bar.time).slice(0, 10), bar.label]));
  if (!labels.size) return history;
  return history.map((bar) => {
    const label = labels.get(String(bar.Date).slice(0, 10));
    return label ? { ...bar, swing_label: label } : bar;
  });
}

/** The 2-hour bars of the Swings 2h table as chart bars, with the rising 2-hour channel's lines on the bars
 *  it covers (exact per bar: the lower line rises evenly from its first anchor to the latest bar). */
function twoHourHistory(bars, summary) {
  const last = bars.length - 1;
  const span = summary.ch2_bars - 1;                      // bars from the first anchor to the latest bar
  const step = span > 0 ? (summary.ch2_lower - summary.ch2_start_lower) / span : 0;
  const width = summary.ch2_upper - summary.ch2_lower;
  return bars.map((bar, index) => {
    const lower = summary.ch2_lower - step * (last - index);
    const previous = index > 0 ? bars[index - 1].close : null;
    return {
      Date: bar.time, Open: bar.open, High: bar.high, Low: bar.low, Close: bar.close, Volume: bar.volume,
      pct_change: isNumber(previous) && previous !== 0 ? ((bar.close - previous) / previous) * 100 : null,
      swing_label: bar.label || null, entry_mark: ENTRY_SHORT[bar.p_final] || null, entry_text: bar.p_final || null,
      ...(last - index <= span ? { channel_lower: lower, channel_upper: lower + width } : {}),
    };
  });
}

const hasDailyChannel = (summary) => summary.channel === true && isNumber(summary.channel_start_lower);
const hasTwoHourChannel = (summary) => summary.ch2_channel === true && isNumber(summary.ch2_start_lower);
/** The channel the chart draws, as the fields of a daily channel plus its legend text, or null.
 *  `preferTwoHour` (the stock was opened from the Ascending list): the rising 2-hour channel comes first;
 *  it is also drawn when the stock has no daily channel. On the daily chart its lines run from the day of
 *  its first anchor to the latest day. */
function chartChannel(summary, preferTwoHour) {
  const twoHour = hasTwoHourChannel(summary) && (preferTwoHour || !hasDailyChannel(summary));
  if (twoHour) {
    return { channel: true, channel_since: summary.ch2_since, channel_start_lower: summary.ch2_start_lower,
      channel_lower: summary.ch2_lower, channel_upper: summary.ch2_upper, legend: "Ascending channel (2h): lower, middle, upper line" };
  }
  return hasDailyChannel(summary) ? { ...summary, legend: `${summary.channel_type} channel: lower, middle, upper line` } : null;
}

function legendItems(drawn, channel, swings, twoHour = false) {
  const item = (mark, text) => el("span", { class: "badge" }, [mark, el("span", { text })]);
  const unit = twoHour ? "2h bar" : "day";
  const price = drawn === "candles"
    ? [item(el("span", { class: "candle up" }), `Up ${unit}`), item(el("span", { class: "candle down" }), `Down ${unit}`)]
    : [item(el("span", { class: "line" }), "Close")];
  return [
    ...price,
    ...(SHOW_CYCLE_MARKS ? [item(el("span", { class: "mark breakout" }), "Breakout (valid)"),
      item(el("span", { class: "mark wait" }), "Wait"), item(el("span", { class: "mark entry" }), "Entry")] : []),
    ...(drawn === "candles" && !twoHour ? [item(el("span", { class: "tag", text: "SPV" }), "MRI SPV / PV candle")] : []),
    ...(swings ? [item(el("span", { class: "tag", text: "HH" }), `HH / HL / LH / LL swing label (${twoHour ? "2h" : "1D"}, Clean)`)] : []),
    ...(twoHour ? [item(el("span", { class: "tag", text: "E1" }), "E1 / E2: Entry 1 / Entry 2 at the lower line (P-Final)")] : []),
    ...(channel ? [item(el("span", { class: "line channel" }), channel)] : []),
  ];
}

const passFail = (value) => (value === true ? "Pass" : "Fail");
const fixed = (value) => (isNumber(value) ? value.toFixed(2) : DASH);

/** MRI Scan values: structure, EMA signal, active SPV zone and latest PV. Empty for scans made before MRI. */
function smcItems(summary) {
  if (summary.spv_position === undefined) return [];
  const breaks = [summary.smc_event && `${summary.smc_event} (internal)`, summary.smc_swing_event && `${summary.smc_swing_event} (swing)`].filter(Boolean);
  const cross = summary.ma_signal_last ? `${summary.ma_signal_last} · ${formatDate(summary.ma_signal_date)}` : DASH;
  const spv = summary.spv_date ? formatDate(summary.spv_date) : "None active";
  const range = isNumber(summary.spv_high) ? `${formatPrice(summary.spv_high)} / ${formatPrice(summary.spv_low)}` : DASH;
  const position = summary.spv_position
    ? summary.spv_position + (summary.spv_position === "Inside" ? "" : ` (${fixed(summary.spv_distance_pct)}%)`)
    : DASH;
  const pv = summary.pv_kind ? `${summary.pv_kind} · ${formatDate(summary.pv_date)}` : "None";
  return [
    ["Internal trend", summary.smc_trend || DASH],
    ["Swing trend", summary.smc_swing_trend || DASH],
    ["Structure today", breaks.join(", ") || "No break"],
    ["EMA 9 / 20 signal", cross],
    ["Active bullish SPV", spv],
    ["SPV high / low", range],
    ["LTP vs SPV", position],
    ["Last PV", pv],
  ];
}

function rsiNote(rsi) {
  if (!isNumber(rsi)) return "";
  if (rsi < 30) return "Oversold (below 30)";
  return rsi > 70 ? "Overbought (above 70)" : "Neutral (30 to 70)";
}

function cciNote(cci) {
  if (!isNumber(cci)) return "";
  if (cci < -100) return "Below -100: oversold zone";
  return cci > 100 ? "Above 100: overbought zone" : "Between -100 and 100";
}

function cciEmaNote(cci, ema) {
  if (!isNumber(cci) || !isNumber(ema)) return "";
  return cci >= ema ? "CCI is above its EMA" : "CCI is below its EMA";
}

/** Colour class for a value against its low / high bands: "down" below, "up" above, "" between. */
function tone(value, low, high) {
  if (!isNumber(value)) return "";
  if (value < low) return "down";
  return value > high ? "up" : "";
}

/** Indicator values and screen results from the Mid Cap notebook strategy, for the latest bar. */
function indicators(summary) {
  if (summary.screen_count === undefined) return null;
  const check = (value, yes, no) => [passFail(value), value === true ? yes : no, value === true ? "pass" : "fail"];
  const items = [
    ["P/B ratio", isNumber(summary.pb_ratio) ? summary.pb_ratio.toFixed(2) : DASH,
      isNumber(summary.pb_ratio) ? `Price ${formatPrice(summary.close)} ÷ book value ${formatPrice(summary.book_value)} per share`
        + (summary.pb_ratio < 0 ? " · negative: liabilities exceed assets" : "")
        : "Book value per share not available", isNumber(summary.pb_ratio) && summary.pb_ratio < 0 ? "warn" : ""],
    ["RSI 14", fixed(summary.rsi), rsiNote(summary.rsi), tone(summary.rsi, 30, 70)],
    ["CCI 60", fixed(summary.cci), cciNote(summary.cci), tone(summary.cci, -100, 100)],
    ["CCI EMA 9", fixed(summary.cci_ma), cciEmaNote(summary.cci, summary.cci_ma), ""],
    ["ATR %", isNumber(summary.atr_pct) ? `${summary.atr_pct.toFixed(2)}%` : DASH,
      summary.volatility ? `${summary.volatility} · daily range vs price` : "Daily range vs price", "warn"],
    ["Close > EMA 7", ...check(summary.ema7_pass, "Close is above EMA 7", "Close is under EMA 7")],
    ["3-rule candle", ...check(summary.candle_3rule, "Candle rules met", "Candle rules not met")],
    ["Above prev 5 closes", ...check(summary.close_breakout, "Close beat the last 5", "Close did not beat the last 5")],
    ["Screens passed", summary.screens || "None", summary.top_pick ? "Top pick" : `${summary.screen_count || 0} of 2 (CCI up-cross, Breakout)`,
      summary.screens ? "pass" : "muted"],
  ];
  const tile = ([label, value, note, kind]) => el("div", { class: "metric" }, [
    el("dt", {}, [el("span", { text: label }), TILE_INFO[label] ? infoButton(TILE_INFO[label]) : null]),
    el("dd", {}, [
      el("span", { class: `metric-value ${kind ? `tone-${kind}` : ""}`, text: value }),
      note ? el("span", { class: "metric-note", text: note }) : null,
    ]),
  ]);
  const mri = smcItems(summary);
  return el("div", {}, [
    el("dl", { class: "indicators" }, items.map(tile)),
    mri.length ? el("h3", { class: "section-title", text: "MRI Scan" }) : null,
    mri.length ? el("dl", { class: "indicators" }, mri.map(([label, value]) => tile([label, value, "", ""]))) : null,
  ]);
}

// The swing table of the 2-hour bars and of the daily bars: same fields, daily ones prefixed d1_ in the summary.
const SWING_FRAMES = {
  twoHour: { prefix: "", title: "Swings (2-hour bars)", short: "2h", timeType: "bartime", format: formatBarTime, bar: "bar", swingsKey: "swings", barsKey: "bars_2h" },
  daily: { prefix: "d1_", title: "Swings (1-day bars)", short: "daily", timeType: "date", format: formatDate, bar: "session", swingsKey: "swings_1d", barsKey: "bars_1d" },
};

// Columns of the Swings 2h / Swings 1D tables kept in the code but not shown (remove a key here to show it again).
const HIDDEN_SWING_COLUMNS = new Set(["ang_ll_ll", "ang_ll_hl", "ll3_hh", "ll4_hh"]);
const shownColumns = (columns) => columns.filter((column) => !HIDDEN_SWING_COLUMNS.has(column.key));

const swingColumns = (frame) => [
  { key: "time", label: frame.prefix ? "Day" : "2h bar", type: frame.timeType },
  { key: "label", label: "Swing", type: "text" },
  { key: "low", label: "Low", type: "price" },
  { key: "high", label: "High", type: "price" },
  { key: "close", label: "Close", type: "price" },
  { key: "ang_ll_ll", label: "LL→LL", type: "angle", title: "LL low to next LL low, written on the first LL" },
  { key: "ang_ll_hl", label: "LL→HL", type: "angle", title: "LL low to HL low, when an HH followed" },
  { key: "signal", label: "Signal", type: "text" },
];

// Every 2-hour bar, with the columns of TBC_all_stocks_bars.csv (status, status_clean, angles, Final, flags).
const BEFORE_ENTRY = "Before Entry";
const FINAL_TITLE = "Final: Before Entry on an HH that comes right after an LH breakout (LH, LH breakout, HH); "
  + "Entry1 on the first 3-rule candle once that HH is confirmed (5 bars after it); "
  + "Entry2 on the first 3-rule candle after the Clean labels LH, HH, HL, HH in a row, once the last HH is confirmed, closing below that HH's close; "
  + "LH breakout > level when the close breaks the last LH (comma separated when a bar has more than one)";
const P_FINAL_COLUMN = { key: "p_final", label: "P-Final", type: "text",
  title: "Rising 2-hour parallel channel: Entry 1 = touch 2 of its lower line, Entry 2 = touch 3 (a bar's High must reach the middle line between two touches)" };
const barColumns = (frame, pFinal = false) => [
  { key: "time", label: frame.prefix ? "Day" : "2h bar", type: frame.timeType },
  { key: "open", label: "Open", type: "price" },
  { key: "high", label: "High", type: "price" },
  { key: "low", label: "Low", type: "price" },
  { key: "close", label: "Close", type: "price" },
  // Daily table only: on an HH, % from the close of the latest LH before it to the HH's close.
  ...(frame.prefix ? [{ key: "hh_change_pct", label: "Change %", type: "pct", title: "On an HH: (HH close - last LH close) / last LH close" }] : []),
  { key: "raw_label", label: "Status", type: "text", title: "status: the raw HH / HL / LH / LL label" },
  { key: "label", label: "Clean", type: "text", title: "status_clean: the label after the clean-up" },
  { key: "final", label: "Final", type: "text", title: FINAL_TITLE },
  ...(pFinal ? [P_FINAL_COLUMN] : []),
  { key: "ang_ll_ll", label: "LL→LL", type: "angle", title: "ang_LL_LL" },
  { key: "ang_ll_hl", label: "LL→HL", type: "angle", title: "ang_LL_HL" },
  { key: "ll3_hh", label: "3LL_HH", type: "flag" },
  { key: "ll4_hh", label: "4LL_HH", type: "flag" },
  { key: "volume", label: "Volume", type: "crore", title: "Shares traded in the bar" },
];

const ENTRY = "Entry1";
const ENTRY_2 = "Entry2";   // first 3-rule candle after LH, HH, HL, HH (stock_entry/swings.py entry_2_after_hh2)
// Final of the daily and the 2-hour table: Before Entry or Entry1, Entry2 and the LH breakout share one cell,
// comma separated when a bar has several.
const barRow = (bar) => {
  const merged = [
    bar.before_entry_1 ? BEFORE_ENTRY : (bar.entry ? ENTRY : null),
    bar.entry_2 && ENTRY_2,
    bar.lh_breakout && `LH breakout > ${formatPrice(bar.lh_breakout_level)}`,
  ].filter(Boolean);
  return { ...bar, final: merged.length ? merged.join(", ") : null };
};

/** The stock's Final status of the daily bars: the latest day whose Final cell (Swings 1D) is filled.
 *  @returns {{text: string, kind: string, date: string, sessions: number} | null} */
function dailyFinal(bars) {
  const rows = (bars || []).map(barRow);
  const at = rows.findLastIndex((row) => row.final);
  if (at < 0) return null;
  const row = rows[at];
  // chip colour: an entry is green, the Before Entry HH amber, an LH breakout alone blue
  const kind = row.entry || row.entry_2 ? "entry" : row.before_entry_1 ? "wait" : "breakout";
  return { text: row.final, kind, date: row.time, sessions: rows.length - 1 - at };
}

function finalBadge(final) {
  return el("span", { class: `badge badge-${final.kind}`, title: `Final status of the daily bars (Swings 1D), on ${formatDate(final.date, true)}` }, [
    el("span", { class: `mark ${final.kind}`, "aria-hidden": "true" }),
    el("span", { text: `${final.text} · ${formatDate(final.date)}` }),   // with its day: the status can be old
  ]);
}

/** The box at the top of the Overview: the Final status of the daily bars in a sentence. */
function finalFacts(final, summary) {
  if (!final) {
    return [summary.at_closing_high === true
      ? "No Final status: the stock is at a new closing high, so its daily swings are not analysed."
      : "No Final status in the loaded daily bars."];
  }
  const ago = final.sessions === 0 ? "the latest day" : `${plural(final.sessions, "session")} ago`;
  return [`Final status (1D): ${final.text} on ${formatDate(final.date, true)} (${ago}).`,
    "From the Final column of the Swings 1D tab: the latest day with a Before Entry, Entry1, Entry2 or LH breakout."];
}

function swingSignal(point) {
  const before = point.before_entry && `TBC Zone Entry (${point.pattern || `${point.ll_count} LL, other`})`;
  return [
    before,
    point.lh_breakout && `LH breakout > ${formatPrice(point.lh_breakout_level)}`,
    point.raw_label && point.raw_label !== point.label && `was ${point.raw_label}`,
  ].filter(Boolean).join(" · ");
}

const SOURCE_TEXT = {
  tradingview: "2-hour prices from TradingView.",
  "tradingview-nologin": "2-hour prices from TradingView without login (the sign-in failed, so the latest bars may be missing).",
  "tbc-csv": "2-hour prices from TBC_all_stocks_bars.csv (the TradingView notebook run).",
};

function swingFacts(summary, frame) {
  const get = (key) => summary[`${frame.prefix}${key}`];
  const source = SOURCE_TEXT[get("swing_source")] || null;
  const when = frame.format;
  const ago = (bars) => (isNumber(bars) ? ` (${plural(bars, frame.bar)} ago)` : "");
  const signal = (at, bars, text) => (at ? `${text} on ${when(at)}${ago(bars)}.` : null);
  return [
    get("swing_last_label")
      ? `Last swing: ${get("swing_last_label")} on ${when(get("swing_last_label_at"))}. Latest ${frame.short} bar ${when(get("swing_last_bar"))}.`
      : `No swing label in the loaded ${frame.short} bars.`,
    get("before_entry_at")
      ? `TBC Zone Entry on ${when(get("before_entry_at"))}${ago(get("before_entry_bars"))}: HH after ${plural(get("before_entry_ll"), "lower low")} → ${get("before_entry_pattern") || "not a 3LL / 4LL pattern"}.`
      : `No TBC Zone Entry (confirmed HH) in the loaded ${frame.short} bars.`,
    source,
    signal(get("ll3_hh_at"), get("ll3_hh_bars"), "3 LL then HH"),
    signal(get("ll4_hh_at"), get("ll4_hh_bars"), "4 LL then HH"),
    get("lh_breakout_at")
      ? `LH breakout on ${when(get("lh_breakout_at"))}${ago(get("lh_breakout_bars"))}: closed above the LH high of ${formatPrice(get("lh_breakout_level"))}.`
      : null,
    isNumber(get("ang_ll_ll")) ? `Latest LL→LL angle ${formatAngle(get("ang_ll_ll"))} (from the LL on ${when(get("ang_ll_ll_at"))}).` : null,
    isNumber(get("ang_ll_hl")) ? `Latest LL→HL angle ${formatAngle(get("ang_ll_hl"))} (from the LL on ${when(get("ang_ll_hl_at"))}).` : null,
  ].filter(Boolean);
}

/** TradingView.ipynb swings on 2-hour or daily bars. Nothing for scans / servers without them. */
/** Daily table only: what the Change % column means, with the latest HH's value. */
function changeFact(frame, everyBar) {
  if (!frame.prefix || !Array.isArray(everyBar)) return null;
  const latest = [...everyBar].reverse().find((bar) => isNumber(bar.hh_change_pct));
  const value = latest ? ` Latest: ${formatPercent(latest.hh_change_pct)} on the HH of ${frame.format(latest.time)}.` : "";
  return `Change % (HH days only): how far the HH's close is above the close of the last LH before it, (HH close − LH close) ÷ LH close.${value}`;
}

const CLOSING_HIGH_NOTE = "it closed at a new high, and the scan does no swing analysis for a stock at a new closing high";

/** The rising 2-hour channel and its entries in a sentence (above the Swings 2h table). */
function channelFact(summary) {
  if (summary.ch2_channel !== true) return "P-Final: Entry 1 / Entry 2 at the lower line of the rising 2-hour channel.";
  const entries = [summary.ch2_entry1_at && `Entry 1 on ${formatBarTime(summary.ch2_entry1_at)}`,
    summary.ch2_entry2_at && `Entry 2 on ${formatBarTime(summary.ch2_entry2_at)}`].filter(Boolean);
  return `Rising 2-hour channel since ${formatBarTime(summary.ch2_since)} (${plural(summary.ch2_bars, "bar")}, ${formatPrice(summary.ch2_lower)} to ${formatPrice(summary.ch2_upper)}, ${summary.ch2_zone}): `
    + `${entries.length ? entries.join(", ") : "one touch of the lower line so far, no entry yet"}. See the P-Final column.`;
}

function swingSection(summary, swings, frame, everyBar = null) {
  const bars = summary[`${frame.prefix}swing_bars`];
  if (bars === undefined) return null;
  const title = el("h3", { class: "section-title", text: frame.title });
  const allBars = Array.isArray(everyBar) && everyBar.length > 0;
  // A stock at a new closing high gets no swing summary in the scan (so it is in no swing list), but its bars are still loaded.
  const skipped = !bars && summary.at_closing_high === true;
  if (!bars && !allBars) {
    const why = skipped ? CLOSING_HIGH_NOTE : `${frame.short} prices were not available`;
    return el("div", {}, [title, el("p", { class: "dim", text: `No ${frame.short} swing data for this stock (${why}).` })]);
  }
  const table = el("table", { class: "compact" }, [el("thead"), el("tbody")]);
  const rows = allBars
    ? [...everyBar].reverse().map(barRow)   // newest first, every bar as in the CSV
    : [...(swings || [])].reverse().map((point) => ({ ...point, signal: swingSignal(point) }));
  // P-Final: 2-hour table only, for a stock in a rising 2-hour channel
  const pFinal = !frame.prefix && allBars && (summary.ch2_channel === true || everyBar.some((bar) => bar.p_final));
  renderTable(table, { rows, columns: shownColumns(allBars ? barColumns(frame, pFinal) : swingColumns(frame)) });
  return el("div", {}, [
    title,
    el("div", { class: "facts facts-none" },
      [...(bars ? swingFacts(summary, frame) : [`No swing summary for this stock today (${skipped ? CLOSING_HIGH_NOTE : "it was not part of the last scan"}). The bars and their labels are shown below.`]),
        changeFact(frame, everyBar), pFinal ? channelFact(summary) : null].filter(Boolean).map((text) => el("p", { text }))),
    rows.length ? el("div", { class: "table-wrap" }, [table]) : null,
  ]);
}

/** A row of pick-one buttons above the chart. `onChange` receives the chosen option. */
function picker(options, selectedLabel, groupLabel, onChange) {
  const buttons = options.map((option) => el("button", {
    type: "button", text: option.label, "aria-pressed": String(option.label === selectedLabel),
    onclick: (event) => {
      buttons.forEach((button) => button.setAttribute("aria-pressed", String(button === event.currentTarget)));
      onChange(option);
    },
  }));
  return el("div", { class: "ranges", role: "group", "aria-label": groupLabel }, buttons);
}

// The detail is split into tabs so each section shows on its own instead of one long scroll.
// The chosen tab is kept while moving between stocks.
let currentTab = "overview";

/** Tab bar + panels. `tabs`: [{id, label, body, onShow?}]; tabs without a body are left out. */
// Detail tabs kept in the code but not shown (turn one back on by removing its id here).
const HIDDEN_TABS = new Set(["sessions"]);

function tabbed(tabs, hidden = []) {
  const shown = tabs.filter((tab) => tab.body && !HIDDEN_TABS.has(tab.id) && !hidden.includes(tab.id));
  const active = shown.some((tab) => tab.id === currentTab) ? currentTab : shown[0].id;
  const buttons = shown.map((tab) => el("button", {
    type: "button", role: "tab", id: `detail-tab-${tab.id}`, "aria-controls": `detail-panel-${tab.id}`,
    "aria-selected": String(tab.id === active), text: tab.label,
  }));
  const panels = shown.map((tab) => el("div", {
    role: "tabpanel", id: `detail-panel-${tab.id}`, "aria-labelledby": `detail-tab-${tab.id}`,
    class: "detail-tabpanel", hidden: tab.id !== active,
  }, [tab.body]));
  const select = (index) => {
    currentTab = shown[index].id;
    buttons.forEach((button, i) => button.setAttribute("aria-selected", String(i === index)));
    panels.forEach((panel, i) => { panel.hidden = i !== index; });
    shown[index].onShow?.();
  };
  buttons.forEach((button, index) => {
    button.addEventListener("click", () => select(index));
    button.addEventListener("keydown", (event) => {
      const step = { ArrowRight: 1, ArrowLeft: -1 }[event.key];
      if (!step) return;
      event.preventDefault();
      const next = (index + step + buttons.length) % buttons.length;
      select(next);
      buttons[next].focus();
    });
  });
  return { bar: el("div", { class: "detail-tabs", role: "tablist", "aria-label": "Stock detail sections" }, buttons), panels };
}

// TradingView chart page for a Yahoo ticker, same rules as stock_entry/tradingview.py tv_symbol:
// "BAJAJ-AUTO.NS" -> NSE:BAJAJ_AUTO, "M&M.NS" -> NSE:M_M, ".BO" -> BSE.
const TV_EXCHANGES = { NS: "NSE", BO: "BSE" };
const TV_CHART_URL = "https://www.tradingview.com/chart/?symbol=";

export function tradingViewUrl(ticker) {
  const [base, suffix = ""] = String(ticker || "").toUpperCase().split(".");
  if (!base) return null;
  const symbol = `${TV_EXCHANGES[suffix] || "NSE"}:${base.replace(/[-&]/g, "_")}`;
  return TV_CHART_URL + encodeURIComponent(symbol);
}

function tradingViewButton(ticker) {
  const href = tradingViewUrl(ticker);
  if (!href) return null;
  return el("a", { class: "button-link", href, target: "_blank", rel: "noopener noreferrer",
    title: `Open ${ticker} on TradingView (new tab)`, text: "TradingView chart ↗" });
}

export function renderDetailMessage(panel, text) {
  panel.replaceChildren(el("p", { class: "empty", text }));
}

/**
 * @param {HTMLElement} panel
 * @param {{summary: object, history: object[], swings?: object[], swings_1d?: object[]}} detail
 * @returns {() => void} cleanup that stops the chart's resize tracking
 */
export function renderDetail(panel, detail, { hiddenTabs = [], twoHourChannel = false } = {}) {
  const { summary } = detail;
  // Opened from the Ascending list with a rising 2-hour channel: the chart shows the 2-hour bars, not the days.
  const twoHourBars = detail[SWING_FRAMES.twoHour.barsKey] || [];
  const twoHourChart = twoHourChannel && hasTwoHourChannel(summary) && twoHourBars.length > 1;
  const drawnChannel = chartChannel(summary, twoHourChannel);
  const dailyHistory = twoHourChart ? detail.history
    : withSwingLabels(drawnChannel ? withChannel(detail.history, drawnChannel) : detail.history, detail[SWING_FRAMES.daily.barsKey]);
  const history = twoHourChart ? twoHourHistory(twoHourBars, summary) : dailyHistory;
  const ranges = twoHourChart ? TWO_HOUR_RANGES : RANGES;
  const defaultRange = twoHourChart ? TWO_HOUR_DEFAULT_RANGE : DEFAULT_RANGE;
  const hasSwingLabels = history.some((bar) => bar.swing_label);
  const subtitle = [summary.name, summary.sector].filter(Boolean).join(" · ");
  const final = dailyFinal(detail[SWING_FRAMES.daily.barsKey]);   // replaces the breakout / wait / entry chip
  const chart = el("div", { class: "chart" });
  const table = el("table", { class: "compact" }, [el("thead"), el("tbody")]);
  const recent = dailyHistory.slice(-HISTORY_ROWS).reverse().map((bar) => ({ ...bar, status: bar.entry_status }));
  const legend = el("div", { class: "legend" });
  let visibleBars = ranges.find((range) => range.label === defaultRange).bars;
  let chartMode = DEFAULT_MODE;
  let zoom = null;   // null: the whole period; else { count, offset } (see chartzoom.js)
  const zoomInfo = el("span", { class: "zoom-info dim" });
  // Full view: the chart with its controls over the whole screen (see toggleFullView below).
  let fullView = false;
  const controls = el("div", { class: "chart-controls" });
  const chartView = el("div", { class: "chart-view" });
  const fullHeight = () => Math.max(FULL_VIEW_MIN_HEIGHT, chartView.clientHeight - controls.offsetHeight - FULL_VIEW_SPARE);
  const periodBars = () => (visibleBars ? history.slice(-visibleBars) : history);
  const drawChart = () => {
    if (!chart.clientWidth) return;   // chart tab hidden: drawn when it is shown
    const period = periodBars();
    const shown = sliceWindow(period, zoom);
    const drawn = renderChart(chart, shown, chartMode, fullView ? fullHeight() : undefined);
    legend.replaceChildren(...legendItems(drawn, drawnChannel ? drawnChannel.legend : null, hasSwingLabels, twoHourChart));  // matches what was drawn, e.g. line when OHLC is missing
    const zoomed = fitWindow(zoom, period.length).count < period.length;
    zoomInfo.textContent = zoomed
      ? `${shown.length} of ${period.length} ${twoHourChart ? "2-hour bars" : "days"} · drag to move, wheel to zoom`
      : `${twoHourChart ? "2-hour bars · " : ""}Wheel or + to zoom in`;
  };
  const setZoom = (view) => {
    zoom = view;
    drawChart();
  };
  const zoomBy = (factor) => setZoom(zoomWindow(zoom, periodBars().length, factor, 1));   // keep the latest day
  const stopZoom = attachZoom(chart, () => ({
    view: zoom, total: periodBars().length, plotLeft: PLOT_MARGIN.left,
    plotWidth: chart.clientWidth - PLOT_MARGIN.left - PLOT_MARGIN.right,
  }), setZoom);
  const zoomButtons = el("div", { class: "ranges zoom", role: "group", "aria-label": "Zoom the chart" }, [
    el("button", { type: "button", text: "−", title: "Zoom out", "aria-label": "Zoom out", onclick: () => zoomBy(1 / ZOOM_STEP) }),
    el("button", { type: "button", text: "+", title: "Zoom in", "aria-label": "Zoom in", onclick: () => zoomBy(ZOOM_STEP) }),
    el("button", { type: "button", text: "Reset", title: "Show the whole period", onclick: () => setZoom(null) }),
  ]);
  // The chart view is moved to <body> while it is full: the detail pane is a transformed sheet on phones,
  // and a fixed element inside it would only cover the sheet. The browser's own full screen is asked for
  // on top of that; where it is refused (iPhone, some embedded pages) the view still fills the window.
  const fullButton = el("button", { type: "button", class: "full-view-button" });
  const placeholder = document.createComment("chart view");
  const nativeFull = () => document.fullscreenElement || document.webkitFullscreenElement;
  const showFullState = () => {
    fullButton.textContent = fullView ? "Exit full view" : "Full view";
    fullButton.title = fullView ? "Back to the stock page (Esc)" : "Open the chart on the full screen";
    fullButton.setAttribute("aria-pressed", String(fullView));
  };
  const setFullView = (on) => {
    if (on === fullView) return;
    fullView = on;
    if (on) {
      chartView.replaceWith(placeholder);
      document.body.append(chartView);
    } else {
      placeholder.replaceWith(chartView);
    }
    chartView.classList.toggle("full", on);
    document.body.classList.toggle("chart-full-open", on);
    showFullState();
    drawChart();
  };
  const toggleFullView = () => {
    const on = !fullView;
    setFullView(on);
    const root = document.documentElement;
    const request = root.requestFullscreen || root.webkitRequestFullscreen;
    const leave = document.exitFullscreen || document.webkitExitFullscreen;
    try {
      const done = on ? (request && !nativeFull() ? request.call(root) : null) : (leave && nativeFull() ? leave.call(document) : null);
      if (done && done.catch) done.catch(() => {});   // refused: the view already fills the window
    } catch {
      // older browsers throw instead of rejecting; same fallback
    }
  };
  const onFullscreenChange = () => {
    if (!nativeFull() && fullView) setFullView(false);   // left with Esc or the browser's own button
    else drawChart();                                    // the screen size changed
  };
  const onKey = (event) => {
    if (event.key === "Escape" && fullView && !nativeFull()) setFullView(false);
  };
  const onResize = () => { if (fullView) drawChart(); };
  fullButton.addEventListener("click", toggleFullView);
  document.addEventListener("fullscreenchange", onFullscreenChange);
  document.addEventListener("webkitfullscreenchange", onFullscreenChange);
  document.addEventListener("keydown", onKey);
  window.addEventListener("resize", onResize);
  showFullState();
  zoomButtons.append(fullButton);

  const tabs = tabbed([
    { id: "overview", label: "Overview", body: el("div", {}, [
      el("div", { class: `facts facts-${final ? final.kind : "none"}` },
        finalFacts(final, summary).map((text) => el("p", { text, title: FINAL_TITLE }))),
      indicators(summary),
    ]) },
    { id: "chart", label: "Chart", onShow: () => drawChart(), body: (() => {
      controls.append(legend, el("div", { class: "pickers" }, [
        picker(MODES, DEFAULT_MODE === "candles" ? "Candles" : "Line", "Chart type", (option) => {
          chartMode = option.mode;
          drawChart();
        }),
        picker(ranges, defaultRange, "Chart period", (option) => {
          visibleBars = option.bars;
          setZoom(null);   // a new period starts unzoomed
        }),
        zoomButtons,
      ]));
      chartView.append(controls, el("div", { class: "chart-card" }, [chart, zoomInfo]));
      return el("div", {}, [chartView]);
    })() },
    { id: "swings2h", label: "Swings 2h",
      body: swingSection(summary, detail[SWING_FRAMES.twoHour.swingsKey], SWING_FRAMES.twoHour, detail[SWING_FRAMES.twoHour.barsKey]) },
    { id: "swings1d", label: "Swings 1D", body: swingSection(summary, detail[SWING_FRAMES.daily.swingsKey], SWING_FRAMES.daily, detail[SWING_FRAMES.daily.barsKey]) },
    { id: "sessions", label: `Last ${recent.length} sessions`, body: el("div", { class: "table-wrap" }, [table]) },
  ], hiddenTabs);

  panel.replaceChildren(
    el("div", { class: "detail-head" }, [
      el("div", {}, [
        el("div", { class: "detail-title" }, [
          el("h2", { text: summary.ticker }),
          final ? finalBadge(final) : null,
          tradingViewButton(summary.ticker),
        ]),
        subtitle ? el("p", { class: "detail-name", text: subtitle }) : null,
      ]),
      el("div", { class: "hero" }, [
        el("div", { class: "hero-value", text: formatPrice(summary.close) }),
        el("div", { class: signClass(summary.pct_change), text: formatPercent(summary.pct_change) }),
        el("div", { class: "dim", text: `Close on ${formatDate(summary.date, true)}` }),
      ]),
    ]),
    tabs.bar,
    ...tabs.panels,
  );
  renderTable(table, { rows: recent, columns: HISTORY_COLUMNS });
  drawChart();

  let lastWidth = chart.clientWidth;
  const observer = new ResizeObserver(() => {
    if (Math.abs(chart.clientWidth - lastWidth) < 2) return;
    lastWidth = chart.clientWidth;
    drawChart();
  });
  observer.observe(chart);
  return () => {
    observer.disconnect();
    stopZoom();
    // leave the full view with the stock: the chart must not stay over the page
    if (fullView && nativeFull()) (document.exitFullscreen || document.webkitExitFullscreen)?.call(document)?.catch?.(() => {});
    chartView.remove();
    document.body.classList.remove("chart-full-open");
    document.removeEventListener("fullscreenchange", onFullscreenChange);
    document.removeEventListener("webkitfullscreenchange", onFullscreenChange);
    document.removeEventListener("keydown", onKey);
    window.removeEventListener("resize", onResize);
  };
}
