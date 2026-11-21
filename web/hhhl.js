// TBC Zone Entry Sequence: the Entry 1 / Entry 2 study of analysis/hh_hl_entries.py (data/hh_hl_entries.json),
// shown in the Results tab of the "TBC Zone Entry Sequence" list. Tables per timeframe and, on request, every signal.

import { el } from "./format.js";
import { renderTable, sortRows } from "./table.js";

const TIMEFRAMES = ["2h", "Daily", "Weekly"];   // monthly has about 24 bars per stock: too few for swings
const INTRADAY = "2h";   // its dates carry the bar's start time, "2026-11-16 09:15"
const GROUP_E1 = "Entry 1";
const GROUP_E2A = "Entry 2 (a) after Entry 1";

const SUMMARY_COLUMNS = [
  { key: "group", label: "Entry", type: "text" },
  { key: "bars", label: "Bars held", type: "int", title: "Forward horizon: the return is measured this many bars after the entry" },
  { key: "measured", label: "Signals", type: "int", title: "Signals with enough bars after them to measure" },
  { key: "win_rate_net_pct", label: "Win rate", type: "pctplain", title: "Share with a return above 0 after costs" },
  { key: "avg_ret_net_pct", label: "Avg return", type: "pct", title: "After costs" },
  { key: "median_ret_net_pct", label: "Median", type: "pct" },
  { key: "avg_win_net_pct", label: "Avg win", type: "pct" },
  { key: "avg_loss_net_pct", label: "Avg loss", type: "pct" },
  { key: "avg_mfe_pct", label: "Avg MFE", type: "pct", title: "Highest high within the horizon vs the entry price" },
  { key: "avg_mae_pct", label: "Avg MAE", type: "pct", title: "Lowest low within the horizon vs the entry price" },
  { key: "reliable", label: "Reliable", type: "text", title: "NO when there are too few signals for a trustworthy win rate" },
];
const COMPARISON_COLUMNS = [
  { key: "compared", label: "Compared", type: "text" },
  { key: "bars", label: "Bars held", type: "int" },
  { key: "win_rate_entry1_pct", label: "Win rate E1", type: "pctplain" },
  { key: "win_rate_entry2_pct", label: "Win rate E2", type: "pctplain" },
  { key: "avg_ret_entry1_pct", label: "Avg return E1", type: "pct" },
  { key: "avg_ret_entry2_pct", label: "Avg return E2", type: "pct" },
  { key: "avg_ret_diff_pct", label: "Difference", type: "pct", title: "Entry 2 average return minus Entry 1" },
  { key: "verdict", label: "Verdict", type: "text" },
];
const TREND_COLUMNS = [{ key: "weekly_trend", label: "Weekly trend", type: "text" }, ...SUMMARY_COLUMNS];

const signalColumns = (horizons, when) => [
  { key: "ticker", label: "Stock", type: "ticker" },
  { key: "entry_text", label: "Entry", type: "text" },
  { key: "hh2_date", label: "HH2", type: when },
  { key: "signal_date", label: "Signal", type: when, title: "The 3-rule candle" },
  { key: "entry_date", label: when === "dateyear" ? "Entry day" : "Entry bar", type: when, title: "Open of the next bar" },
  { key: "entry_price", label: "Entry price", type: "price" },
  ...horizons.map((bars, i) => ({ key: `ret${i + 1}`, label: `${bars} bars`, type: "pct", title: `Return after ${bars} bars, after costs` })),
  // the weekly trend is worked out for daily signals only
  ...(when === "dateyear" ? [{ key: "weekly_trend", label: "Weekly trend", type: "text" }] : []),
];

const panelState = { timeframe: "Daily", preferred: null, open: false, sort: { key: "signal_date", dir: "desc" } };

function tableOf(rows, columns, extra = {}) {
  const table = el("table", { class: "compact" }, [el("thead"), el("tbody")]);
  renderTable(table, { rows, columns, ...extra });
  return el("div", { class: "table-wrap" }, [table]);
}

const section = (title, body) => [el("h3", { class: "section-title", text: title }), body];
const signed = (value) => `${value >= 0 ? "+" : ""}${value.toFixed(2)}%`;

function headline(summary, horizons, timeframe) {
  const last = horizons[horizons.length - 1];
  const at = (group) => summary.find((row) => row.group === group && row.bars === last);
  const text = (name, row) => (row && row.measured
    ? `${name}: ${row.win_rate_net_pct.toFixed(1)}% wins, avg ${signed(row.avg_ret_net_pct)} (${row.measured} signals)` : null);
  const parts = [text("Entry 1", at(GROUP_E1)), text("Entry 2 after Entry 1", at(GROUP_E2A))].filter(Boolean);
  return parts.length ? `${timeframe}, ${last} bars held · ${parts.join(" · ")}` : `No measured ${timeframe.toLowerCase()} signals yet.`;
}

/**
 * Fill `container` with the study.
 * @param {HTMLElement} container
 * @param {object|null} results  data/hh_hl_entries.json, or null while it loads
 * @param {(ticker: string) => void} onOpen  opens a stock
 * @param {string} [preferred]  timeframe of the list the tab belongs to ("2h" or "Daily"); shown first
 */
export function renderHhHl(container, results, onOpen, preferred) {
  if (preferred && preferred !== panelState.preferred) {   // the list's 2 hours / 1 day switch moved
    panelState.preferred = preferred;
    panelState.timeframe = preferred;
  }
  if (!results) {
    container.replaceChildren(el("p", { class: "dim", text: "Loading the TBC Zone Entry Sequence study…" }));
    return;
  }
  const redraw = () => renderHhHl(container, results, onOpen, preferred);
  const { timeframe } = panelState;
  const horizons = (results.horizons || {})[timeframe] || [];
  const summary = (results.summary || []).filter((row) => row.timeframe === timeframe);
  // short verdicts, so the column is not cut off
  const comparison = (results.comparison || []).filter((row) => row.timeframe === timeframe).map((row) => ({
    ...row, verdict: (row.verdict || "").replace(" on average return;", " ·").replace("difference is ", "").replace("statistically ", ""),
  }));
  const signals = (results.signals || []).filter((row) => row.timeframe === timeframe).map((row) => ({
    ...row, ticker: row.stock,
    entry_text: row.entry_type === 1 ? "Entry 1" : `Entry 2${row.after_entry1 ? " (after Entry 1)" : ""}`,
  }));
  const picker = el("div", { class: "segmented list-tabs", role: "group", "aria-label": "Timeframe of the study" },
    TIMEFRAMES.map((name) => el("button", {
      type: "button", text: name, "aria-pressed": String(name === timeframe),
      onclick: () => {
        panelState.timeframe = name;
        redraw();
      },
    })));
  const toggle = el("button", {
    type: "button", text: panelState.open ? "Hide signals" : `Show signals (${signals.length})`,
    onclick: () => {
      panelState.open = !panelState.open;
      redraw();
      if (panelState.open) container.querySelector(".win-rate-trades")?.scrollIntoView({ block: "start", behavior: "smooth" });
    },
  });
  let signalTable = null;
  if (panelState.open) {
    signalTable = tableOf(sortRows(signals, panelState.sort), signalColumns(horizons, timeframe === INTRADAY ? "datetime" : "dateyear"), {
      sort: panelState.sort,
      onSort: (key) => {
        panelState.sort = { key, dir: panelState.sort.key === key && panelState.sort.dir === "desc" ? "asc" : "desc" };
        redraw();
      },
      onSelect: (row) => onOpen(row.ticker),
    });
    signalTable.classList.add("win-rate-trades");
  }
  const note = `${results.rules || ""} Data up to ${results.data_date || "?"}. 2h = the app's 2-hour bars (about six months, 4 bars per session). Monthly bars (about 24 per stock) are too few for swings and are left out.`;
  container.replaceChildren(...[
    picker,
    el("p", { class: "win-rate-head", text: headline(summary, horizons, timeframe) }),
    ...section("Results by entry (after costs)", tableOf(summary, SUMMARY_COLUMNS)),
    ...section("Entry 1 vs Entry 2", tableOf(comparison, COMPARISON_COLUMNS)),
    ...(timeframe === "Daily" ? section("Daily signals by weekly trend", tableOf(results.trend_split || [], TREND_COLUMNS)) : []),
    el("p", { class: "dim win-rate-rules", text: note }),
    toggle,
    signalTable,
  ].filter(Boolean));
}
