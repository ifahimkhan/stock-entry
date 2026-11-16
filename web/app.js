// Page controller: loads the scan, renders tiles / tabs / table, opens the single-stock panel.

import { POLL_MS, getScan, getScanStatus, getStock, getTickers, startScan } from "./api.js";
import { renderDetail, renderDetailMessage } from "./detail.js";
import { renderCards } from "./cards.js";
import { MARKET_HOURS_TEXT, isMarketHours } from "./market.js";
import { DASH, el, formatAngle, formatBarTime, formatDate, formatDateTime, formatPercent, formatPrice, isNumber } from "./format.js";
import { renderTable, sortRows } from "./table.js";

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
const NO_SWINGS = " 2-hour prices did not load for this scan, so this list is empty: run the scan again.";
const OLD_SCAN = " This scan was made before this screen was added: run the scan again.";

// Swing lists (TradingView.ipynb on 2-hour bars). A swing label is only known 5 bars after its pivot,
// so each list keeps records up to a fixed number of 2-hour bars old (NSE has about 4 per session).
const SWING_NOTE = " Labels are confirmed 5 bars (about 1 session) after the pivot, and an HH can still turn into LH if price later closes below the previous LL.";
/** True when a record `bars` 2-hour bars old is at most `maxBars` old. */
const withinBars = (bars, maxBars) => isNumber(bars) && bars <= maxBars;
const BEFORE_ENTRY_MAX_BARS = 7;   // Before Entry list: only HHs at most this many 2-hour bars old
const LL_HH_MAX_BARS = 12;         // 3 LL -> HH and 4 LL -> HH lists: same idea, a wider window
const LH_BREAKOUT_MAX_BARS = 3;    // LH breakout list: only fresh breakouts
const barsAgo = (key) => ({ key, label: "Bars ago", type: "int", title: "2-hour bars since the record (about 4 per session)" });
const ANG_LL_LL = { key: "ang_ll_ll", label: "LL→LL", type: "angle", title: "Angle of the latest LL low to the next LL low (% change vs bars)" };
const ANG_LL_HL = { key: "ang_ll_hl", label: "LL→HL", type: "angle", title: "Angle of the latest LL low to the HL low, when an HH followed" };
const llThenHhView = (count) => ({
  id: `ll${count}`, label: `${count} LL then HH (2h)`, mark: null, needsKey: "swing_bars",
  chip: `${count} LL → HH`,
  note: (row) => `HH ${formatBarTime(row[`ll${count}_hh_at`])} after ${count} lower lows · LL→LL ${formatAngle(row.ang_ll_ll)}`,
  hint: `Exactly ${count} lower lows (LL) since the previous higher high, then a new HH (2-hour bars), in the last ${LL_HH_MAX_BARS} two-hour bars.${SWING_NOTE}`,
  filter: (row) => withinBars(row[`ll${count}_hh_bars`], LL_HH_MAX_BARS),
  columns: [TICKER, { key: `ll${count}_hh_at`, label: "HH bar", type: "bartime" }, barsAgo(`ll${count}_hh_bars`),
    CLOSE, CHANGE, ANG_LL_LL, ANG_LL_HL],
  sort: { key: `ll${count}_hh_at`, dir: "desc" },
});
const PATTERN_FILTERS = [
  { id: "all", label: "All" }, { id: "3LL-HH", label: "3LL-HH" },
  { id: "4LL-HH", label: "4LL-HH" },
];
const patternText = (row) => row.before_entry_pattern || `${row.before_entry_ll ?? "?"} LL (other)`;
const patternMatches = (row, id) => id === "all" || (id === "other" ? !row.before_entry_pattern : row.before_entry_pattern === id);
const SWING_VIEWS = [
  {
    id: "before", label: "TBC Zone Entry (2h)", mark: null, needsKey: "swing_bars", patternFilter: true,
    chip: "TBC Zone Entry",
    note: (row) => `TBC Zone Entry ${formatBarTime(row.before_entry_at)} · ${patternText(row)} · LL→HL ${formatAngle(row.ang_ll_hl)}`,
    hint: `TBC Zone Entry (the notebook's Final = "Before Entry"): a higher high (HH) that survived the clean-up (2-hour bars). Only HHs that complete a 3LL-HH or 4LL-HH pattern (3 or 4 lower lows before it), from the last ${BEFORE_ENTRY_MAX_BARS} two-hour bars.${SWING_NOTE}`,
    filter: (row) => Boolean(row.before_entry_pattern)   // 3LL-HH or 4LL-HH only
      && withinBars(row.before_entry_bars, BEFORE_ENTRY_MAX_BARS),
    columns: [TICKER, { key: "before_entry_at", label: "Zone entry", type: "bartime" }, barsAgo("before_entry_bars"),
      { key: "before_entry_pattern", label: "Pattern", type: "pattern", title: "Lower lows since the previous HH: 3LL-HH, 4LL-HH or other" },
      { key: "before_entry_close", label: "HH close", type: "price" }, CLOSE, CHANGE, ANG_LL_LL, ANG_LL_HL],
    sort: { key: "before_entry_at", dir: "desc" },
  },
  llThenHhView(3),
  llThenHhView(4),
  {
    id: "lhbreak", label: "LH breakout (2h)", mark: null, needsKey: "swing_bars",
    chip: "LH breakout",
    note: (row) => `Closed above LH high ${formatPrice(row.lh_breakout_level)} on ${formatBarTime(row.lh_breakout_at)}`,
    hint: `3 or more LLs or LHs since the last HH, then a 2-hour candle closed above the high of the last LH, in the last ${LH_BREAKOUT_MAX_BARS} two-hour bars.${SWING_NOTE}`,
    filter: (row) => withinBars(row.lh_breakout_bars, LH_BREAKOUT_MAX_BARS),
    columns: [TICKER, { key: "lh_breakout_at", label: "Breakout bar", type: "bartime" }, barsAgo("lh_breakout_bars"),
      { key: "lh_breakout_level", label: "LH high", type: "price", title: "High of the last LH that the close broke" },
      CLOSE, CHANGE, ANG_LL_LL],
    sort: { key: "lh_breakout_at", dir: "desc" },
  },
];

/** Each list: which rows it shows (given the latest market date), its columns and default sort. */
// Lists kept in the code but not shown as chips (turn one back on by removing its id here).
const HIDDEN_VIEWS = new Set(["recent", "cci", "spv", "pv"]);
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
  listTitle: $("list-title"), listCount: $("list-count"), listHint: $("list-hint"),
  table: $("stock-table"), listEmpty: $("list-empty"), detail: $("detail"), capFilter: $("cap-filter"),
  patternFilter: $("pattern-filter"),
};

let state = {
  scan: undefined,  // undefined until the first load answers; null when there is no scan yet
  progress: { running: false }, tickers: [], viewId: "entry",
  sort: VIEWS[0].sort, query: "", selected: null, capFilter: "all", patternFilter: "all",
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
/** Rows of a list for the selected market-cap group (chip counts ignore the pattern filter). */
const capRows = (view) => viewRows(view).filter((row) => capMatches(row, state.capFilter));
/** Rows of a list for the selected market-cap group and, on Before Entry, the selected pattern. */
const rowsFor = (view) => viewRows(view).filter((row) => capMatches(row, state.capFilter)
  && (!view.patternFilter || patternMatches(row, state.patternFilter)));
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
  dom.tiles.replaceChildren(...VIEWS.map((view) => {
    const selected = state.viewId === view.id;
    const title = view.id === "all" && failed ? `${view.hint} ${failed} returned no data.` : view.hint;
    return el("button", {
      type: "button", class: "chip", "aria-pressed": String(selected), title,
      onclick: () => selectView(view.id),
    }, [
      view.mark ? el("span", { class: `mark ${view.mark}`, "aria-hidden": "true" }) : null,
      el("span", { text: view.chip }),
      el("span", { class: "chip-count", text: scan ? String(capRows(view).length) : DASH }),
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
    text: `${cap.label} ${all.filter((row) => capMatches(row, cap.id)).length}`,
    onclick: () => setState({ capFilter: cap.id }),
  })));
  dom.capFilter.hidden = !state.scan;
}

/** All / 3LL-HH / 4LL-HH buttons, only on the Before Entry list. */
function renderPatternFilter(view) {
  dom.patternFilter.hidden = !(view.patternFilter && state.scan);
  if (dom.patternFilter.hidden) return;
  const inList = viewRows(view).filter((row) => capMatches(row, state.capFilter));
  dom.patternFilter.replaceChildren(...PATTERN_FILTERS.map((option) => el("button", {
    type: "button", "aria-pressed": String(state.patternFilter === option.id),
    text: `${option.label} ${inList.filter((row) => patternMatches(row, option.id)).length}`,
    onclick: () => setState({ patternFilter: option.id }),
  })));
}

function renderList() {
  const view = currentView();
  renderCapFilter(view);
  renderPatternFilter(view);
  const inView = state.scan ? rowsFor(view) : [];
  const visible = sortRows(inView.filter((row) => matchesQuery(row, state.query)), state.sort);
  dom.listTitle.textContent = view.label;
  let hint = view.hint;
  const needsKey = view.needsKey || "screen_count";
  if (state.scan && rows().length && rows()[0][needsKey] === undefined) hint += OLD_SCAN;
  else if (view.usesFundamentals && state.scan && !rows().some((row) => row.fundamentals_known)) hint += NO_FUNDAMENTALS;
  else if (view.needsKey === "swing_bars" && rows().length && !rows().some((row) => row.swing_bars > 0)) hint += NO_SWINGS;
  dom.listHint.textContent = hint;
  const shown = state.query.trim() ? `${visible.length} of ${inView.length}` : String(inView.length);
  dom.listCount.textContent = state.scan ? `· ${shown} stock${inView.length === 1 ? "" : "s"}` : "";
  renderSortSelect(view);
  renderCards(dom.cards, { rows: visible, selected: state.selected, note: view.note, onSelect: (row) => openStock(row.ticker) });
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

function closeSheet() {
  setSheetOpen(false);
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

async function openStock(symbol) {
  const request = ++detailRequest;
  if (detailCleanup) detailCleanup();
  detailCleanup = null;
  setState({ selected: symbol });
  renderDetailMessage(dom.detail, `Loading ${symbol}…`);
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
    if (!dom.confirm.hidden) closeScanConfirm();
    else if (document.body.classList.contains("sheet-open")) closeSheet();
  });
  window.matchMedia(WIDE_LAYOUT).addEventListener("change", (event) => {
    if (event.matches) setSheetOpen(false);  // rotated / resized to the side-by-side layout
  });
  watchScroll();
  // Bookmarkable lists, e.g. /?list=screen&cap=Large opens TBC 5-Day Breakout for large caps.
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

init();
