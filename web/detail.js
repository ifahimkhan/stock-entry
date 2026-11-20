// Single-stock panel: headline, plain-language status, chart, and the bars as a table.

import { renderChart } from "./chart.js";
import { DASH, el, formatAngle, formatBarTime, formatDate, formatPercent, formatPrice, isNumber, signClass, statusBadge, statusKind } from "./format.js";
import { renderTable } from "./table.js";

const HISTORY_ROWS = 40;
const RANGES = [{ label: "1M", bars: 22 }, { label: "3M", bars: 66 }, { label: "6M", bars: null }];
const DEFAULT_RANGE = "3M";
const MODES = [{ label: "Candles", mode: "candles" }, { label: "Line", mode: "line" }];
const DEFAULT_MODE = "candles";
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

/** Plain sentences describing where the stock is in the breakout -> wait -> entry cycle. */
function factLines(summary) {
  const lines = [];
  if (summary.status === "entry") {
    lines.push(`Entry signal on ${formatDate(summary.date, true)} at ${formatPrice(summary.close)}: first up day after the pullback.`);
  } else if (summary.status === "wait") {
    lines.push(`Waiting ${plural(summary.wait_days, "day")} for a reversal. Entry is the first up day.`);
  } else if (summary.valid === true) {
    lines.push(`Breakout on ${formatDate(summary.date, true)}: closed above the previous 4-day high of ${formatPrice(summary.prev4_max_close)}.`);
    lines.push("Next: a down day starts the wait, then the first up day is the entry.");
  } else {
    lines.push("No signal on the latest bar.");
  }
  if (summary.status === "wait" && summary.breakout_date) {
    lines.push(`Breakout on ${formatDate(summary.breakout_date)} closed at ${formatPrice(summary.breakout_close)}; price is ${formatPercent(summary.pullback_pct)} from it.`);
  }
  if (summary.last_entry_date && summary.status !== "entry") {
    lines.push(`Last entry ${formatDate(summary.last_entry_date)} at ${formatPrice(summary.last_entry_price)}, ${formatPercent(summary.since_entry_pct)} since (${plural(summary.sessions_since_entry, "session")} ago).`);
  } else if (!summary.last_entry_date) {
    lines.push("No entry signal in the loaded history.");
  }
  return lines;
}

/** Legend items for the chart type actually drawn ("candles" or "line"). */
function legendItems(drawn) {
  const item = (mark, text) => el("span", { class: "badge" }, [mark, el("span", { text })]);
  const price = drawn === "candles"
    ? [item(el("span", { class: "candle up" }), "Up day (hollow)"), item(el("span", { class: "candle down" }), "Down day (filled)")]
    : [item(el("span", { class: "line" }), "Close")];
  return [
    ...price,
    item(el("span", { class: "mark breakout" }), "Breakout (valid)"),
    item(el("span", { class: "mark wait" }), "Wait"),
    item(el("span", { class: "mark entry" }), "Entry"),
    ...(drawn === "candles" ? [item(el("span", { class: "tag", text: "SPV" }), "MRI SPV / PV candle")] : []),
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
    el("dt", { text: label }),
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
const barColumns = (frame) => [
  { key: "time", label: frame.prefix ? "Day" : "2h bar", type: frame.timeType },
  { key: "open", label: "Open", type: "price" },
  { key: "high", label: "High", type: "price" },
  { key: "low", label: "Low", type: "price" },
  { key: "close", label: "Close", type: "price" },
  // Daily table only: on an HH, % from the close of the latest LH before it to the HH's close.
  ...(frame.prefix ? [{ key: "hh_change_pct", label: "Change %", type: "pct", title: "On an HH: (HH close - last LH close) / last LH close" }] : []),
  { key: "raw_label", label: "Status", type: "text", title: "status: the raw HH / HL / LH / LL label" },
  { key: "label", label: "Clean", type: "text", title: "status_clean: the label after the clean-up" },
  { key: "ang_ll_ll", label: "LL→LL", type: "angle", title: "ang_LL_LL" },
  { key: "ang_ll_hl", label: "LL→HL", type: "angle", title: "ang_LL_HL" },
  { key: "final", label: "Final", type: "text", title: frame.prefix
    ? "Final: Before Entry on a TBC Zone Entry HH; Entry on the first 3-rule candle once that HH is confirmed"
    : "Final: Before Entry on a confirmed HH (TBC Zone Entry)" },
  { key: "ll3_hh", label: "3LL_HH", type: "flag" },
  { key: "ll4_hh", label: "4LL_HH", type: "flag" },
  { key: "breakout", label: "LH_breakout", type: "text", title: "LH_breakout and LH_breakout_level" },
  { key: "volume", label: "Volume", type: "crore", title: "Shares traded in the bar" },
];

const ENTRY = "Entry";
// `daily`: Entry (first 3-rule candle after a confirmed Zone Entry HH) is shown on the daily table only
const barRow = (bar, daily) => ({
  ...bar,
  final: bar.before_entry ? BEFORE_ENTRY : (daily && bar.entry ? ENTRY : null),
  breakout: bar.lh_breakout ? `Yes > ${formatPrice(bar.lh_breakout_level)}` : null,
});

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

function swingSection(summary, swings, frame, everyBar = null) {
  const bars = summary[`${frame.prefix}swing_bars`];
  if (bars === undefined) return null;
  const title = el("h3", { class: "section-title", text: frame.title });
  if (!bars) return el("div", {}, [title, el("p", { class: "dim", text: `No ${frame.short} swing data for this stock (${frame.short} prices were not available).` })]);
  const table = el("table", { class: "compact" }, [el("thead"), el("tbody")]);
  const allBars = Array.isArray(everyBar) && everyBar.length > 0;
  const rows = allBars
    ? [...everyBar].reverse().map((bar) => barRow(bar, Boolean(frame.prefix)))   // newest first, every bar as in the CSV
    : [...(swings || [])].reverse().map((point) => ({ ...point, signal: swingSignal(point) }));
  renderTable(table, { rows, columns: allBars ? barColumns(frame) : swingColumns(frame) });
  return el("div", {}, [
    title,
    el("div", { class: "facts facts-none" },
      [...swingFacts(summary, frame), changeFact(frame, everyBar)].filter(Boolean).map((text) => el("p", { text }))),
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
function tabbed(tabs) {
  const shown = tabs.filter((tab) => tab.body);
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
export function renderDetail(panel, detail) {
  const { summary, history } = detail;
  const subtitle = [summary.name, summary.sector].filter(Boolean).join(" · ");
  const chart = el("div", { class: "chart" });
  const table = el("table", { class: "compact" }, [el("thead"), el("tbody")]);
  const recent = history.slice(-HISTORY_ROWS).reverse().map((bar) => ({ ...bar, status: bar.entry_status }));
  const legend = el("div", { class: "legend" });
  let visibleBars = RANGES.find((range) => range.label === DEFAULT_RANGE).bars;
  let chartMode = DEFAULT_MODE;
  const drawChart = () => {
    if (!chart.clientWidth) return;   // chart tab hidden: drawn when it is shown
    const drawn = renderChart(chart, visibleBars ? history.slice(-visibleBars) : history, chartMode);
    legend.replaceChildren(...legendItems(drawn));  // matches what was drawn, e.g. line when OHLC is missing
  };

  const tabs = tabbed([
    { id: "overview", label: "Overview", body: el("div", {}, [
      el("div", { class: `facts facts-${statusKind(summary.status, summary.valid) || "none"}` },
        factLines(summary).map((text) => el("p", { text }))),
      indicators(summary),
    ]) },
    { id: "chart", label: "Chart", onShow: () => drawChart(), body: el("div", {}, [
      el("div", { class: "chart-controls" }, [legend, el("div", { class: "pickers" }, [
        picker(MODES, DEFAULT_MODE === "candles" ? "Candles" : "Line", "Chart type", (option) => {
          chartMode = option.mode;
          drawChart();
        }),
        picker(RANGES, DEFAULT_RANGE, "Chart period", (option) => {
          visibleBars = option.bars;
          drawChart();
        }),
      ])]),
      el("div", { class: "chart-card" }, [chart]),
    ]) },
    { id: "swings2h", label: "Swings 2h",
      body: swingSection(summary, detail[SWING_FRAMES.twoHour.swingsKey], SWING_FRAMES.twoHour, detail[SWING_FRAMES.twoHour.barsKey]) },
    { id: "swings1d", label: "Swings 1D", body: swingSection(summary, detail[SWING_FRAMES.daily.swingsKey], SWING_FRAMES.daily, detail[SWING_FRAMES.daily.barsKey]) },
    { id: "sessions", label: `Last ${recent.length} sessions`, body: el("div", { class: "table-wrap" }, [table]) },
  ]);

  panel.replaceChildren(
    el("div", { class: "detail-head" }, [
      el("div", {}, [
        el("div", { class: "detail-title" }, [
          el("h2", { text: summary.ticker }),
          statusKind(summary.status, summary.valid) ? statusBadge(summary.status, summary.valid) : null,
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
  return () => observer.disconnect();
}
