// Single-stock panel: headline, plain-language status, chart, and the bars as a table.

import { renderChart } from "./chart.js";
import { DASH, el, formatDate, formatPercent, formatPrice, isNumber, signClass, statusBadge } from "./format.js";
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

/** Indicator values and screen results from the Mid Cap notebook strategy, for the latest bar. */
function indicators(summary) {
  if (summary.screen_count === undefined) return null;
  const items = [
    ["RSI 14", fixed(summary.rsi)],
    ["CCI 60", fixed(summary.cci)],
    ["CCI EMA 9", fixed(summary.cci_ma)],
    ["ATR %", isNumber(summary.atr_pct) ? `${summary.atr_pct.toFixed(2)} (${summary.volatility})` : DASH],
    ["Close > EMA 7", passFail(summary.ema7_pass)],
    ["3-rule candle", passFail(summary.candle_3rule)],
    ["Above prev 5 closes", passFail(summary.close_breakout)],
    ["Screens passed", summary.top_pick ? `${summary.screens} (top pick)` : summary.screens || "None"],
  ];
  return el("dl", { class: "indicators" }, [...items, ...smcItems(summary)].map(([label, value]) => el("div", {}, [
    el("dt", { text: label }), el("dd", { text: value }),
  ])));
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

export function renderDetailMessage(panel, text) {
  panel.replaceChildren(el("p", { class: "empty", text }));
}

/**
 * @param {HTMLElement} panel
 * @param {{summary: object, history: object[]}} detail
 * @returns {() => void} cleanup that stops the chart's resize tracking
 */
export function renderDetail(panel, { summary, history }) {
  const subtitle = [summary.name, summary.sector].filter(Boolean).join(" · ");
  const chart = el("div", { class: "chart" });
  const table = el("table", { class: "compact" }, [el("thead"), el("tbody")]);
  const recent = history.slice(-HISTORY_ROWS).reverse().map((bar) => ({ ...bar, status: bar.entry_status }));
  const legend = el("div", { class: "legend" });
  let visibleBars = RANGES.find((range) => range.label === DEFAULT_RANGE).bars;
  let chartMode = DEFAULT_MODE;
  const drawChart = () => {
    const drawn = renderChart(chart, visibleBars ? history.slice(-visibleBars) : history, chartMode);
    legend.replaceChildren(...legendItems(drawn));  // matches what was drawn, e.g. line when OHLC is missing
  };

  panel.replaceChildren(
    el("div", { class: "detail-head" }, [
      el("div", {}, [
        el("h2", { text: summary.ticker }),
        subtitle ? el("p", { class: "detail-name", text: subtitle }) : null,
        el("p", { class: "detail-name" }, [statusBadge(summary.status, summary.valid)]),
      ]),
      el("div", { class: "hero" }, [
        el("div", { class: "hero-value", text: formatPrice(summary.close) }),
        el("div", { class: signClass(summary.pct_change), text: formatPercent(summary.pct_change) }),
        el("div", { class: "dim", text: `Close on ${formatDate(summary.date, true)}` }),
      ]),
    ]),
    el("div", { class: "facts" }, factLines(summary).map((text) => el("p", { text }))),
    indicators(summary),
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
    chart,
    el("h3", { class: "history-title", text: `Last ${recent.length} sessions` }),
    el("div", { class: "table-wrap" }, [table]),
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
