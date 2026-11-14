// Page controller: loads the scan, renders tiles / tabs / table, opens the single-stock panel.

import { getScan, getScanStatus, getStock, getTickers, startScan } from "./api.js";
import { renderDetail, renderDetailMessage } from "./detail.js";
import { el, formatDate, formatDateTime, isNumber } from "./format.js";
import { renderTable, sortRows } from "./table.js";

const RECENT_SESSIONS = 5;
const MRI_SESSIONS = 10;  // how far back a confirmed SPV / PV still counts as fresh
const POLL_MS = 1500;
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
  { id: "all", label: "All caps" }, { id: "Large", label: "Large cap" },
  { id: "Mid", label: "Mid cap" }, { id: "Small", label: "Small cap" },
];
const STAGE_TEXT = { fundamentals: "Loading market cap and debt", prices: "Scanning prices" };
const NO_FUNDAMENTALS = " Market cap and debt/equity are not loaded for this scan, so those filters were skipped: run the scan again.";
const OLD_SCAN = " This scan was made before this screen was added: run the scan again.";

/** Each list: which rows it shows (given the latest market date), its columns and default sort. */
const VIEWS = [
  {
    id: "entry", label: "Entry today", mark: "entry",
    hint: "Reversal on the latest bar: first up day after a breakout and pullback.",
    filter: (row, dataDate) => row.status === "entry" && row.date === dataDate,
    columns: [TICKER, { key: "close", label: "Entry price", type: "price" }, CHANGE,
      { key: "screen_count", label: "Also passes", type: "screens" }, RSI, CCI],
    sort: { key: "screen_count", dir: "desc" },
  },
  {
    id: "recent", label: `Entries, last ${RECENT_SESSIONS} sessions`, mark: "entry",
    hint: "Entry signals from the last few sessions and how price has moved since.",
    filter: (row) => row.last_entry_date !== null && row.sessions_since_entry < RECENT_SESSIONS,
    columns: [TICKER, { key: "last_entry_date", label: "Entry date", type: "date" },
      { key: "last_entry_price", label: "Entry price", type: "price" }, CLOSE,
      { key: "since_entry_pct", label: "Since entry", type: "pct" }],
    sort: { key: "last_entry_date", dir: "desc" },
  },
  {
    id: "wait", label: "Waiting", mark: "wait",
    hint: "Breakout done, pullback running. The first up day becomes the entry: watch these.",
    filter: (row) => row.status === "wait",
    columns: [TICKER, SECTOR, CLOSE, CHANGE, { key: "wait_days", label: "Days waiting", type: "int" },
      { key: "pullback_pct", label: "From breakout", type: "pct" }],
    sort: { key: "wait_days", dir: "asc" },
  },
  {
    id: "breakout", label: "Valid today", mark: "breakout",
    hint: "Valid = True on the latest bar: up day closing above the previous 4-day high.",
    filter: (row, dataDate) => row.valid === true && row.date === dataDate,
    columns: [TICKER, SECTOR, CLOSE, CHANGE, { key: "prev4_max_close", label: "Prev 4-day high", type: "price" }],
    sort: { key: "pct_change", dir: "desc" },
  },
  {
    id: "cci", label: "CCI up-cross", mark: null, usesFundamentals: true,
    hint: "CCI crossed above its EMA 9 today while CCI ≤ 0, close above EMA 7, gap ≥ 4, debt/equity ≤ 0.4.",
    filter: (row, dataDate) => row.cci_setup === true && row.date === dataDate,
    columns: [TICKER, CLOSE, CHANGE, CCI, { key: "cci_gap", label: "Above EMA", type: "num2" }, RSI,
      { key: "atr_pct", label: "ATR %", type: "num2" }, { key: "volatility", label: "Volatility", type: "text" }],
    sort: { key: "cci", dir: "asc" },
  },
  {
    id: "screen", label: "Breakout screen", mark: null, usesFundamentals: true,
    hint: "Close above the previous 5 closes, CCI above its EMA (gap ≥ 2, CCI ≤ 100), RSI ≥ 40, 3-rule candle, close above EMA 7, debt/equity ≤ 0.3. Top pick: small cap, RSI ≥ 45, CCI ≤ 0, debt/equity ≤ 0.2.",
    filter: (row, dataDate) => row.breakout_setup === true && row.date === dataDate,
    columns: [TICKER, CLOSE, CCI, RSI, CAP, MARKET_CAP, DEBT,
      { key: "top_pick", label: "Top pick", type: "flag" }, { key: "status", label: "Entry status", type: "status" }],
    sort: { key: "market_cap_cr", dir: "desc" },
  },
  {
    id: "spv", label: "MRI SPV", mark: null, needsKey: "spv_position",
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
    hint: `Bullish PV from the MRI Scan indicator, confirmed in the last ${MRI_SESSIONS} sessions: the first fair value gap candle after the order block of an internal structure break up.`,
    filter: (row) => row.pv_kind === "PV bull" && isNumber(row.pv_sessions) && row.pv_sessions < MRI_SESSIONS,
    columns: [TICKER, { key: "pv_date", label: "PV candle", type: "date" }, CLOSE, CHANGE,
      { key: "smc_trend", label: "Trend", type: "text", title: "Internal structure: direction of the last BOS / CHoCH" },
      { key: "pv_high", label: "PV high", type: "price" }, { key: "pv_low", label: "PV low", type: "price" }],
    sort: { key: "pv_date", dir: "desc" },
  },
  {
    id: "all", label: "All stocks", mark: null,
    hint: "Every stock from the last scan.",
    filter: () => true,
    columns: [TICKER, SECTOR, CAP, MARKET_CAP, DEBT, CLOSE, CHANGE,
      { key: "status", label: "Status", type: "status" }, { key: "last_entry_date", label: "Last entry", type: "date" }],
    sort: { key: "ticker", dir: "asc" },
  },
];

const $ = (id) => document.getElementById(id);
const dom = {
  meta: $("scan-meta"), scanButton: $("scan-button"), progress: $("progress"), progressFill: $("progress-fill"),
  progressText: $("progress-text"), notice: $("notice"), tiles: $("tiles"), tabs: $("tabs"),
  searchForm: $("search-form"), searchInput: $("search-input"), tickerList: $("ticker-list"),
  listTitle: $("list-title"), listCount: $("list-count"), listHint: $("list-hint"),
  table: $("stock-table"), listEmpty: $("list-empty"), detail: $("detail"), capFilter: $("cap-filter"),
};

let state = {
  scan: null, progress: { running: false }, tickers: [], viewId: "entry",
  sort: VIEWS[0].sort, query: "", selected: null, capFilter: "all",
};
let pollTimer = null;
let detailCleanup = null;
let detailRequest = 0;

const setState = (changes) => {
  state = { ...state, ...changes };
  render();
};
const currentView = () => VIEWS.find((view) => view.id === state.viewId);
const rows = () => (state.scan ? state.scan.rows : []);
/** Rows of a list before the market-cap filter. */
const viewRows = (view) => rows().filter((row) => view.filter(row, state.scan.data_date));
const capMatches = (row, capId) => capId === "all" || row.cap_category === capId;
/** Rows of a list for the selected market-cap group. */
const rowsFor = (view) => viewRows(view).filter((row) => capMatches(row, state.capFilter));
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
    dom.progressText.textContent = progress.total ? `${stage}: ${progress.done} of ${progress.total} stocks` : "Starting…";
  }
  dom.meta.textContent = scan
    ? `Prices up to ${formatDate(scan.data_date, true)} · scanned ${formatDateTime(scan.scanned_at)}`
    : "No scan yet. Press Run scan to check every stock in tickers.csv.";
}

function renderTiles() {
  const { scan } = state;
  const tile = (view, value, note) => el("button", {
    type: "button", class: "tile", "aria-pressed": String(state.viewId === view.id),
    onclick: () => selectView(view.id),
  }, [
    el("span", { class: "tile-label" }, [view.mark ? el("span", { class: `mark ${view.mark}` }) : null, el("span", { text: view.label })]),
    el("span", { class: "tile-value", text: scan ? String(value) : "—" }),
    el("span", { class: "tile-note", text: note }),
  ]);
  const failed = scan ? scan.failed.length : 0;
  const notes = {
    entry: "buy setups on the latest bar", recent: "still fresh", wait: "watchlist for tomorrow",
    breakout: "new valid = True", cci: "CCI turning up from below 0", screen: "5-close breakout + CCI + RSI",
    spv: "order block + fair value gap", pv: "first gap after order block",
    all: failed ? `${failed} returned no data` : "scanned",
  };
  dom.tiles.replaceChildren(...VIEWS.map((view) => tile(view, scan ? rowsFor(view).length : 0, notes[view.id])));
}

function renderTabs() {
  dom.tabs.replaceChildren(...VIEWS.map((view) => el("button", {
    type: "button", role: "tab", "aria-selected": String(state.viewId === view.id), text: view.label,
    onclick: () => selectView(view.id),
  })));
}

/** Large / Mid / Small buttons with the number of stocks of the current list in each group. */
function renderCapFilter(view) {
  const all = state.scan ? viewRows(view) : [];
  dom.capFilter.replaceChildren(...CAP_FILTERS.map((cap) => el("button", {
    type: "button", "aria-pressed": String(state.capFilter === cap.id),
    text: `${cap.label} ${all.filter((row) => capMatches(row, cap.id)).length}`,
    onclick: () => setState({ capFilter: cap.id }),
  })));
  dom.capFilter.hidden = !state.scan;
}

function renderList() {
  const view = currentView();
  renderCapFilter(view);
  const inView = state.scan ? rowsFor(view) : [];
  const visible = sortRows(inView.filter((row) => matchesQuery(row, state.query)), state.sort);
  dom.listTitle.textContent = view.label;
  let hint = view.hint;
  const needsKey = view.needsKey || "screen_count";
  if (state.scan && rows().length && rows()[0][needsKey] === undefined) hint += OLD_SCAN;
  else if (view.usesFundamentals && state.scan && !rows().some((row) => row.fundamentals_known)) hint += NO_FUNDAMENTALS;
  dom.listHint.textContent = hint;
  dom.listCount.textContent = state.scan ? (state.query.trim() ? `${visible.length} of ${inView.length}` : String(inView.length)) : "";
  renderTable(dom.table, {
    rows: visible, columns: view.columns, sort: state.sort, selected: state.selected,
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
}

function render() {
  renderMeta();
  renderTiles();
  renderTabs();
  renderList();
}

function selectView(viewId) {
  setState({ viewId, sort: VIEWS.find((view) => view.id === viewId).sort });
}

async function openStock(symbol) {
  const request = ++detailRequest;
  if (detailCleanup) detailCleanup();
  detailCleanup = null;
  setState({ selected: symbol });
  renderDetailMessage(dom.detail, `Loading ${symbol}…`);
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
  setState({ scan: data.scan, progress: data.progress });
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

async function onScanClick() {
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
  dom.scanButton.addEventListener("click", onScanClick);
  dom.searchForm.addEventListener("submit", onSearchSubmit);
  dom.searchInput.addEventListener("input", () => setState({ query: dom.searchInput.value }));
  // Bookmarkable lists, e.g. /?list=screen&cap=Large opens the Breakout screen for large caps.
  const params = new URLSearchParams(location.search);
  const linkedView = VIEWS.find((view) => view.id === params.get("list"));
  const linkedCap = CAP_FILTERS.find((cap) => cap.id === params.get("cap"));
  if (linkedView) state = { ...state, viewId: linkedView.id, sort: linkedView.sort };
  if (linkedCap) state = { ...state, capFilter: linkedCap.id };
  render();
  try {
    const progress = await refreshScan();
    if (progress.running) startPolling();
  } catch (error) {
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

init();
