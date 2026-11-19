// TBC Zone Entry win rate: the replayed trades of stock_entry/zone_backtest.py, shown above the Zone Entry list.
// One panel per timeframe (1d / 2h): the win rate per pattern, the trade rules and, on request, every trade.

import { el, formatPercent, isNumber } from "./format.js";
import { renderTable, sortRows } from "./table.js";

const PATTERN_TEXT = { all: "All", "3LL-HH": "3LL-HH", "4LL-HH": "4LL-HH" };

const tradeColumns = (timeframe) => {
  const time = timeframe === "2h" ? "datetime" : "dateyear";
  return [
    { key: "ticker", label: "Stock", type: "ticker" },
    { key: "signal_at", label: "In list", type: time, title: "The bar the stock entered the Zone Entry list" },
    { key: "pattern", label: "Pattern", type: "text" },
    { key: "change_pct", label: "Change %", type: "pct", title: "HH close vs the last LH close" },
    { key: "entry_at", label: "Entry day", type: "dateyear", title: "Open of the next session" },
    { key: "entry", label: "Entry", type: "price" },
    { key: "outcome", label: "Result", type: "outcome" },
    { key: "exit", label: "Exit", type: "price" },
    { key: "return_pct", label: "Return", type: "pct" },
    { key: "sessions", label: "Sessions", type: "int", title: "Sessions held, entry day included" },
  ];
};

const SUMMARY_COLUMNS = [
  { key: "pattern_text", label: "Pattern", type: "text" },
  { key: "trades", label: "Trades", type: "int", title: "Finished trades: wins + losses + timeouts" },
  { key: "wins", label: "Wins", type: "int" },
  { key: "losses", label: "Losses", type: "int" },
  { key: "timeouts", label: "Timeouts", type: "int" },
  { key: "open", label: "Open", type: "int", title: "Still running: not in the win rate" },
  { key: "win_rate_pct", label: "Win rate", type: "pctplain", title: "wins / (wins + losses)" },
  { key: "avg_return", label: "Avg return", type: "pct", title: "Average return of the finished trades" },
];

const panelState = { open: { "1d": false, "2h": false }, sort: { key: "signal_at", dir: "desc" } };

function headline(all, results) {
  if (!all || !isNumber(all.win_rate)) return "No finished Zone Entry trades yet.";
  const target = `+${Math.round(results.target * 100)}%`;
  const stop = `-${Math.round(results.stop * 100)}%`;
  return `Win rate ${all.win_rate.toFixed(1)}% (${target} before ${stop}): ${all.wins} wins, ${all.losses} losses`
    + `, ${all.timeouts} timeouts, ${all.open} open · avg return ${formatPercent(all.avg_return)}`;
}

/** Fill `container` with the win rate of `timeframe` ("1d" / "2h"). `onOpen(ticker)` opens a stock. */
export function renderWinRate(container, results, timeframe, onOpen) {
  if (!results) {
    container.replaceChildren(el("p", { class: "dim", text: "Loading the Zone Entry win rate…" }));
    return;
  }
  const summary = results.summary.filter((row) => row.timeframe === timeframe)
    .map((row) => ({ ...row, pattern_text: PATTERN_TEXT[row.pattern] || row.pattern, win_rate_pct: row.win_rate }));
  const trades = results.trades.filter((row) => row.timeframe === timeframe);
  const all = summary.find((row) => row.pattern === "all");
  const table = el("table", { class: "compact" }, [el("thead"), el("tbody")]);
  renderTable(table, { rows: summary, columns: SUMMARY_COLUMNS });

  const open = panelState.open[timeframe];
  const toggle = el("button", {
    type: "button", text: open ? "Hide trades" : `Show trades (${trades.length})`,
    onclick: () => {
      panelState.open[timeframe] = !open;
      renderWinRate(container, results, timeframe, onOpen);
    },
  });
  let tradeTable = null;
  if (open) {
    tradeTable = el("table", { class: "compact" }, [el("thead"), el("tbody")]);
    const draw = () => renderTable(tradeTable, {
      rows: sortRows(trades, panelState.sort), columns: tradeColumns(timeframe), sort: panelState.sort,
      onSort: (key) => {
        const dir = panelState.sort.key === key && panelState.sort.dir === "desc" ? "asc" : "desc";
        panelState.sort = { key, dir };
        draw();
      },
      onSelect: (row) => onOpen(row.ticker),
    });
    draw();
  }
  container.replaceChildren(
    el("p", { class: "win-rate-head", text: headline(all, results) }),
    el("div", { class: "table-wrap" }, [table]),
    el("p", { class: "dim win-rate-rules", text: results.rules }),
    toggle,
    tradeTable ? el("div", { class: "table-wrap win-rate-trades" }, [tradeTable]) : null,
  );
}
