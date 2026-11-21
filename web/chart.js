// Daily price chart (candlesticks or close line) with breakout / wait / entry marks, crosshair and tooltip.

import { el, formatDate, formatPercent, formatPrice, isNumber, signClass, statusKind } from "./format.js";

const SVG_NS = "http://www.w3.org/2000/svg";
const HEIGHT = 320;
const MARGIN = { top: 28, right: 18, bottom: 26, left: 58 };
export const PLOT_MARGIN = MARGIN;   // where the plot sits inside the chart, for zoom and pan
const Y_TICKS = 4;
const X_TICKS = 5;
const LABEL_HALF_WIDTH = 44;   // keeps the "Entry 1,234.50" label inside the plot
const LABEL_CHAR_WIDTH = 6.4;  // approximate width of one 11px bold character
const CANDLE_MAX_WIDTH = 14;
const CANDLE_MIN_WIDTH = 2;
const CANDLE_FILL_SHARE = 0.62;  // share of each day's slot taken by the candle body
const MARK_GAP = 11;             // px between a candle's high and its status mark
const MRI_GAP = 14;              // px between a candle and its SPV / PV text
const STATUS_TEXT ={ entry: "Entry", wait: "Wait", breakout: "Breakout (valid)" };

function svg(tag, attrs = {}, style = "") {
  const node = document.createElementNS(SVG_NS, tag);
  for (const [key, value] of Object.entries(attrs)) node.setAttribute(key, value);
  if (style) node.setAttribute("style", style);
  return node;
}

/** Round tick values covering [min, max]. */
function niceTicks(min, max, count) {
  const rawStep = (max - min) / count || 1;
  const magnitude = 10 ** Math.floor(Math.log10(rawStep));
  const step = [1, 2, 2.5, 5, 10].map((m) => m * magnitude).find((s) => s >= rawStep);
  const ticks = [];
  for (let value = Math.ceil(min / step) * step; value <= max + step * 1e-9; value += step) ticks.push(value);
  return ticks;
}

const hasCandle = (bar) => isNumber(bar.Open) && isNumber(bar.High) && isNumber(bar.Low);

/** Scales for the plot. Candles get one equal slot per day; the line runs edge to edge. */
function buildScales(bars, width, candles) {
  const low = Math.min(...bars.map((bar) => (candles ? bar.Low : bar.Close)));
  const high = Math.max(...bars.map((bar) => (candles ? bar.High : bar.Close)));
  const pad = (high - low) * (candles ? 0.12 : 0.08) || high * 0.02 || 1;
  const yMin = low - pad;
  const yMax = high + pad;
  const plotWidth = width - MARGIN.left - MARGIN.right;
  const plotHeight = HEIGHT - MARGIN.top - MARGIN.bottom;
  const slot = plotWidth / bars.length;
  const lineX = (index) => (bars.length === 1 ? plotWidth / 2 : (index / (bars.length - 1)) * plotWidth);
  return {
    yMin, yMax, plotWidth, plotHeight, slot,
    x: (index) => MARGIN.left + (candles ? (index + 0.5) * slot : lineX(index)),
    y: (value) => MARGIN.top + (1 - (value - yMin) / (yMax - yMin)) * plotHeight,
    indexAt: (pixelX) => {
      const ratio = (pixelX - MARGIN.left) / plotWidth;
      const index = candles ? Math.floor(ratio * bars.length) : Math.round(ratio * (bars.length - 1));
      return Math.min(bars.length - 1, Math.max(0, index));
    },
  };
}

function drawAxes(root, bars, scales, width) {
  for (const tick of niceTicks(scales.yMin, scales.yMax, Y_TICKS)) {
    const y = scales.y(tick);
    root.append(svg("line", { x1: MARGIN.left, x2: width - MARGIN.right, y1: y, y2: y }, "stroke: var(--grid); stroke-width: 1"));
    const label = svg("text", { x: MARGIN.left - 8, y: y + 4, "text-anchor": "end" });
    label.textContent = formatPrice(tick).replace(/\.00$/, "");
    root.append(label);
  }
  const baseline = HEIGHT - MARGIN.bottom;
  root.append(svg("line", { x1: MARGIN.left, x2: width - MARGIN.right, y1: baseline, y2: baseline }, "stroke: var(--axis); stroke-width: 1"));
  const count = Math.min(X_TICKS, bars.length);
  for (let i = 0; i < count; i += 1) {
    const index = count === 1 ? 0 : Math.round((i / (count - 1)) * (bars.length - 1));
    const anchor = i === 0 ? "start" : i === count - 1 ? "end" : "middle";
    const label = svg("text", { x: scales.x(index), y: baseline + 16, "text-anchor": anchor });
    label.textContent = formatDate(bars[index].Date);
    root.append(label);
  }
}

function drawLine(root, bars, scales) {
  const path = bars.map((bar, index) => `${index === 0 ? "M" : "L"}${scales.x(index).toFixed(1)},${scales.y(bar.Close).toFixed(1)}`).join(" ");
  root.append(svg("path", { d: path }, "fill: none; stroke: var(--series); stroke-width: 2; stroke-linejoin: round; stroke-linecap: round"));
}

/** Parallel channel: the bars that carry channel_lower / channel_upper get the two lines and a dashed middle. */
function drawChannel(root, bars, scales) {
  const inside = bars.map((bar, index) => ({ bar, index })).filter(({ bar }) => isNumber(bar.channel_lower) && isNumber(bar.channel_upper));
  if (inside.length < 2) return;
  const line = (value, dash) => {
    const path = inside.map(({ bar, index }, at) => `${at === 0 ? "M" : "L"}${scales.x(index).toFixed(1)},${scales.y(value(bar)).toFixed(1)}`).join(" ");
    root.append(svg("path", { d: path, class: "channel-line" }, `fill: none; stroke: var(--channel, #7c5cff); stroke-width: 1.5;${dash ? " stroke-dasharray: 4 4; opacity: 0.6;" : ""}`));
  };
  line((bar) => bar.channel_upper, false);
  line((bar) => bar.channel_lower, false);
  line((bar) => (bar.channel_upper + bar.channel_lower) / 2, true);
}

/** One candle per day. Up days are hollow, down days are filled, so direction never relies on colour alone. */
function drawCandles(root, bars, scales) {
  const bodyWidth = Math.max(CANDLE_MIN_WIDTH, Math.min(CANDLE_MAX_WIDTH, scales.slot * CANDLE_FILL_SHARE));
  bars.forEach((bar, index) => {
    const x = scales.x(index);
    const up = bar.Close >= bar.Open;
    const color = up ? "var(--good)" : "var(--critical)";
    const top = scales.y(Math.max(bar.Open, bar.Close));
    const height = Math.max(1, Math.abs(scales.y(bar.Open) - scales.y(bar.Close)));
    root.append(svg("line", { x1: x, x2: x, y1: scales.y(bar.High), y2: scales.y(bar.Low) }, `stroke: ${color}; stroke-width: 1`));
    root.append(svg("rect", { x: x - bodyWidth / 2, y: top, width: bodyWidth, height },
      `fill: ${up ? "var(--surface)" : color}; stroke: ${color}; stroke-width: 1`));
  });
}

function drawMark(root, kind, x, y) {
  if (kind === "entry") {
    const points = `${x},${y - 8} ${x + 7},${y + 5} ${x - 7},${y + 5}`;
    root.append(svg("polygon", { points }, "fill: var(--good); stroke: var(--surface); stroke-width: 2; stroke-linejoin: round"));
  } else if (kind === "wait") {
    root.append(svg("rect", { x: x - 4.5, y: y - 4.5, width: 9, height: 9 }, "fill: var(--warning); stroke: var(--ink-2); stroke-width: 1"));
  } else if (kind === "breakout") {
    root.append(svg("circle", { cx: x, cy: y, r: 4.5 }, "fill: var(--series); stroke: var(--surface); stroke-width: 2"));
  }
}

function drawMarks(root, bars, scales, width, markY) {
  let lastEntry = -1;
  bars.forEach((bar, index) => {
    const kind = statusKind(bar.entry_status, bar.valid);
    if (!kind) return;
    drawMark(root, kind, scales.x(index), markY(bar));
    if (kind === "entry") lastEntry = index;
  });
  if (lastEntry < 0) return;
  // Direct-label only the most recent entry; older ones are read from the tooltip and the table.
  const text = `Entry ${formatPrice(bars[lastEntry].Close)}`;
  const halfWidth = Math.max(LABEL_HALF_WIDTH, text.length * LABEL_CHAR_WIDTH / 2);  // long prices need more room
  const x = Math.min(Math.max(scales.x(lastEntry), MARGIN.left + halfWidth), width - MARGIN.right - halfWidth);
  const label = svg("text", { x, y: Math.max(markY(bars[lastEntry]) - 14, 12), "text-anchor": "middle", class: "label" });
  label.textContent = text;
  root.append(label);
}

/** MRI marks on candles: "SPV" / "PV" under the low of a bullish candle, over the marks of a bearish one. */
function drawMriMarks(root, bars, scales) {
  bars.forEach((bar, index) => {
    if (!bar.mri_mark) return;
    const bullish = bar.mri_mark.endsWith("bull");
    const y = bullish ? scales.y(bar.Low) + MRI_GAP : scales.y(bar.High) - MARK_GAP - MRI_GAP;
    const label = svg("text", { x: scales.x(index), y, "text-anchor": "middle", class: "label" });
    label.textContent = bar.mri_mark.split(" ")[0];
    root.append(label);
  });
}

function tooltipContent(bar, candles) {
  const kind = statusKind(bar.entry_status, bar.valid);
  const ohlc = candles && el("div", { class: "tip-ohlc" }, [
    el("span", { text: `O ${formatPrice(bar.Open)}` }), el("span", { text: `H ${formatPrice(bar.High)}` }),
    el("span", { text: `L ${formatPrice(bar.Low)}` }), el("span", { text: `C ${formatPrice(bar.Close)}` }),
  ]);
  return [
    el("div", { class: "tip-date", text: formatDate(bar.Date, true) }),
    el("div", { class: "tip-value", text: formatPrice(bar.Close) }),
    ohlc,
    el("div", { class: signClass(bar.pct_change), text: `${formatPercent(bar.pct_change)} vs previous close` }),
    kind && el("div", { class: "badge" }, [el("span", { class: `mark ${kind}` }), el("span", { text: STATUS_TEXT[kind] })]),
    [bar.mri_mark && `MRI ${bar.mri_mark}`, bar.smc_event, bar.ma_signal && `EMA ${bar.ma_signal}`].some(Boolean)
      && el("div", { class: "tip-date", text: [bar.mri_mark && `MRI ${bar.mri_mark}`, bar.smc_event, bar.ma_signal && `EMA ${bar.ma_signal}`].filter(Boolean).join(" · ") }),
  ];
}

function addHover(container, root, bars, scales, width, candles) {
  const crosshair = svg("line", { y1: MARGIN.top, y2: HEIGHT - MARGIN.bottom, visibility: "hidden" }, "stroke: var(--axis); stroke-width: 1");
  const focus = svg("circle", { r: 5, visibility: "hidden" }, "fill: none; stroke: var(--ink); stroke-width: 1.5");
  const tooltip = el("div", { class: "tooltip", hidden: true });
  const hitArea = svg("rect", { x: MARGIN.left, y: 0, width: scales.plotWidth, height: HEIGHT }, "fill: transparent");
  root.append(crosshair, focus, hitArea);
  container.append(tooltip);

  const hide = () => {
    crosshair.setAttribute("visibility", "hidden");
    focus.setAttribute("visibility", "hidden");
    tooltip.hidden = true;
  };
  hitArea.addEventListener("pointerleave", hide);
  hitArea.addEventListener("pointermove", (event) => {
    const bounds = root.getBoundingClientRect();
    const index = scales.indexAt((event.clientX - bounds.left) * (width / bounds.width));
    const x = scales.x(index);
    const y = scales.y(bars[index].Close);
    crosshair.setAttribute("x1", x);
    crosshair.setAttribute("x2", x);
    crosshair.setAttribute("visibility", "visible");
    focus.setAttribute("cx", x);
    focus.setAttribute("cy", y);
    focus.setAttribute("visibility", candles ? "hidden" : "visible");
    tooltip.replaceChildren(...tooltipContent(bars[index], candles).filter(Boolean));
    tooltip.hidden = false;
    const scale = bounds.width / width;
    const flip = x > width / 2;
    tooltip.style.left = flip ? "" : `${x * scale + 14}px`;
    tooltip.style.right = flip ? `${(width - x) * scale + 14}px` : "";
    tooltip.style.top = `${Math.max(4, y * scale - 40)}px`;
  });
}

/**
 * Draw the chart into `container`.
 * @param {HTMLElement} container
 * @param {Array<{Date: string, Open: number|null, High: number|null, Low: number|null, Close: number,
 *                pct_change: number|null, valid: boolean|null, entry_status: string}>} history
 * @param {"candles"|"line"} mode  candles fall back to the line when open/high/low are missing
 * @returns {"candles"|"line"|null} the mode actually drawn, or null when there are too few bars
 */
export function renderChart(container, history, mode = "candles") {
  const bars = history.filter((bar) => isNumber(bar.Close));
  container.replaceChildren();
  if (bars.length < 2) {
    container.append(el("p", { class: "empty", text: "Not enough price bars to draw a chart." }));
    return null;
  }
  const candles = mode === "candles" && bars.every(hasCandle);
  const width = Math.max(320, Math.round(container.clientWidth));
  const scales = buildScales(bars, width, candles);
  const kind = candles ? "Daily candlestick chart" : "Closing price";
  const root = svg("svg", {
    viewBox: `0 0 ${width} ${HEIGHT}`, height: HEIGHT, role: "img",
    "aria-label": `${kind} from ${formatDate(bars[0].Date, true)} to ${formatDate(bars[bars.length - 1].Date, true)} with breakout, wait and entry marks. The same data is in the table below.`,
  });
  drawAxes(root, bars, scales, width);
  if (candles) drawCandles(root, bars, scales);
  else drawLine(root, bars, scales);
  drawChannel(root, bars, scales);
  // On candles the marks sit above the day's high so they never cover the candle.
  drawMarks(root, bars, scales, width, (bar) => (candles ? scales.y(bar.High) - MARK_GAP : scales.y(bar.Close)));
  if (candles) drawMriMarks(root, bars, scales);
  container.append(root);
  addHover(container, root, bars, scales, width, candles);
  return candles ? "candles" : "line";
}
