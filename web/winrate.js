// TBC Zone Entry win rate: the replayed trades of stock_entry/zone_backtest.py, shown above the Zone Entry list.
// One panel per timeframe (1d / 2h), Large and Mid caps only: the win rate per cap and pattern, the trade
// rules and, on request, every trade.

import { el, formatPercent, isNumber } from "./format.js";
import { renderTable, sortRows } from "./table.js";

const CAPS = ["Large", "Mid"];   // small caps (and stocks without a market cap) are left out
const PATTERNS = ["3LL-HH", "4LL-HH"];
const FINISHED = new Set(["win", "loss", "timeout"]);

const tradeColumns = (timeframe) => {
  const time = timeframe === "2h" ? "datetime" : "dateyear";
  return [
    { key: "ticker", label: "Stock", type: "ticker" },
    { key: "cap", label: "Cap", type: "text" },
    ...(timeframe === "all" ? [{ key: "timeframe", label: "Timeframe", type: "text" }] : []),
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
  { key: "group", label: "Group", type: "text" },
  { key: "trades", label: "Trades", type: "int", title: "Finished trades: wins + losses + timeouts" },
  { key: "wins", label: "Wins", type: "int" },
  { key: "losses", label: "Losses", type: "int" },
  { key: "timeouts", label: "Timeouts", type: "int" },
  { key: "open", label: "Open", type: "int", title: "Still running: not in the win rate" },
  { key: "win_rate", label: "Win rate", type: "pctplain", title: "wins / (wins + losses)" },
  { key: "avg_return", label: "Avg return", type: "pct", title: "Average return of the finished trades" },
];

const panelState = { open: { "1d": false, "2h": false }, sort: { key: "signal_at", dir: "desc" } };

/** Win rate of a set of trades, as stock_entry/zone_backtest.py summarize computes it. */
export function tradeStats(group, trades) {
  const count = (outcome) => trades.filter((trade) => trade.outcome === outcome).length;
  const wins = count("win");
  const losses = count("loss");
  const done = trades.filter((trade) => FINISHED.has(trade.outcome) && isNumber(trade.return_pct));
  const sum = done.reduce((total, trade) => total + trade.return_pct, 0);
  return {
    group, trades: trades.filter((trade) => FINISHED.has(trade.outcome)).length, wins, losses,
    timeouts: count("timeout"), open: count("open"),
    win_rate: wins + losses ? Math.round((wins / (wins + losses)) * 1000) / 10 : null,
    avg_return: done.length ? Math.round((sum / done.length) * 100) / 100 : null,
  };
}

function summaryRows(trades) {
  return [
    tradeStats("Large + Mid", trades),
    ...PATTERNS.map((pattern) => tradeStats(`Large + Mid · ${pattern}`, trades.filter((t) => t.pattern === pattern))),
    ...CAPS.map((cap) => tradeStats(cap, trades.filter((t) => t.cap === cap))),
  ];
}

function headline(all, results) {
  if (!all || !isNumber(all.win_rate)) return "No finished Large / Mid cap Zone Entry trades yet.";
  const target = `+${Math.round(results.target * 100)}%`;
  const stop = `-${Math.round(results.stop * 100)}%`;
  return `Large + Mid caps: win rate ${all.win_rate.toFixed(1)}% (${target} before ${stop}): ${all.wins} wins, `
    + `${all.losses} losses, ${all.timeouts} timeouts, ${all.open} open · avg return ${formatPercent(all.avg_return)}`;
}

/** Fill `container` with the Large + Mid cap win rate of `timeframe` ("1d" / "2h", or "all" for both).
 *  `capOf(ticker)` gives "Large" / "Mid" / "Small" / ""; `onOpen(ticker)` opens a stock. */
export function renderWinRate(container, results, timeframe, onOpen, capOf) {
  if (!results) {
    container.replaceChildren(el("p", { class: "dim", text: "Loading the Zone Entry win rate…" }));
    return;
  }
  const trades = results.trades
    .filter((trade) => timeframe === "all" || trade.timeframe === timeframe)
    .map((trade) => ({ ...trade, cap: capOf(trade.ticker) }))
    .filter((trade) => CAPS.includes(trade.cap));
  const summary = summaryRows(trades);
  const table = el("table", { class: "compact" }, [el("thead"), el("tbody")]);
  renderTable(table, { rows: summary, columns: SUMMARY_COLUMNS });

  const open = panelState.open[timeframe];
  const toggle = el("button", {
    type: "button", text: open ? "Hide trades" : `Show trades (${trades.length})`,
    onclick: () => {
      panelState.open[timeframe] = !open;
      renderWinRate(container, results, timeframe, onOpen, capOf);
      // bring the trades into view: the section scrolls on wide screens, the page on phones
      if (!open) container.querySelector(".win-rate-trades")?.scrollIntoView({ block: "start", behavior: "smooth" });
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
  const capNote = " Large and Mid caps only, by today's market cap (small caps and stocks without a market cap are left out).";
  container.replaceChildren(...[
    el("p", { class: "win-rate-head", text: headline(summary[0], results) }),
    el("div", { class: "table-wrap" }, [table]),
    el("p", { class: "dim win-rate-rules", text: results.rules + capNote }),
    toggle,
    tradeTable ? el("div", { class: "table-wrap win-rate-trades" }, [tradeTable]) : null,
  ].filter(Boolean));   // replaceChildren would print a null as the text "null"
}
