// Zoom and pan for the price chart: which slice of the chosen period (1M / 3M / 6M) is drawn.
// A window is { count, offset }: `count` bars are shown, ending `offset` bars before the latest one.

export const MIN_BARS = 10;
export const ZOOM_STEP = 0.7;   // one zoom-in step keeps 70% of the bars; zoom-out divides by it

const clamp = (value, low, high) => Math.min(high, Math.max(low, value));

/** The window made valid for `total` bars: null (or too wide) means every bar. */
export function fitWindow(view, total) {
  if (!view || total <= MIN_BARS) return { count: total, offset: 0 };
  const count = clamp(Math.round(view.count), MIN_BARS, total);
  return { count, offset: clamp(Math.round(view.offset), 0, total - count) };
}

/** The bars of `bars` inside `view`. */
export function sliceWindow(bars, view) {
  const { count, offset } = fitWindow(view, bars.length);
  return bars.slice(bars.length - offset - count, bars.length - offset);
}

/** Zoom by `factor` (< 1 zooms in) keeping the bar under `anchor` (0 = left edge, 1 = right edge) in place. */
export function zoomWindow(view, total, factor, anchor = 0.5) {
  const { count, offset } = fitWindow(view, total);
  const nextCount = clamp(Math.round(count * factor), Math.min(MIN_BARS, total), total);
  const start = total - offset - count;
  const pinned = start + clamp(anchor, 0, 1) * count;   // bar index under the anchor
  const nextStart = Math.round(pinned - clamp(anchor, 0, 1) * nextCount);
  return fitWindow({ count: nextCount, offset: total - nextStart - nextCount }, total);
}

/** Move the window by `bars` (positive = back in time). */
export function panWindow(view, total, bars) {
  const { count, offset } = fitWindow(view, total);
  return fitWindow({ count, offset: offset + bars }, total);
}

/**
 * Wheel zoom and drag-to-pan on `element`. `getState()` gives { view, total, plotLeft, plotWidth } in
 * element pixels; `onChange(view)` receives the new window. Returns a function that removes the handlers.
 */
export function attachZoom(element, getState, onChange) {
  let drag = null;
  let frame = 0;
  const apply = (view) => {
    cancelAnimationFrame(frame);
    frame = requestAnimationFrame(() => onChange(view));
  };
  const anchorAt = (clientX) => {
    const { plotLeft, plotWidth } = getState();
    const x = clientX - element.getBoundingClientRect().left;
    return plotWidth > 0 ? (x - plotLeft) / plotWidth : 0.5;
  };
  const onWheel = (event) => {
    if (!event.deltaY) return;
    event.preventDefault();   // the wheel zooms the chart instead of scrolling the page
    const { view, total } = getState();
    apply(zoomWindow(view, total, event.deltaY < 0 ? ZOOM_STEP : 1 / ZOOM_STEP, anchorAt(event.clientX)));
  };
  const onDown = (event) => {
    if (event.button !== 0) return;
    const { view, total } = getState();
    drag = { x: event.clientX, view: fitWindow(view, total), moved: false };
  };
  const onMove = (event) => {
    if (!drag) return;
    const { total, plotWidth } = getState();
    const dx = event.clientX - drag.x;
    if (!drag.moved && Math.abs(dx) < 4) return;
    if (!drag.moved) {
      drag.moved = true;
      element.setPointerCapture?.(event.pointerId);
      element.classList.add("dragging");
    }
    const barWidth = plotWidth / drag.view.count;
    apply(panWindow(drag.view, total, Math.round(dx / barWidth)));   // drag right = older bars
  };
  const onUp = () => {
    drag = null;
    element.classList.remove("dragging");
  };
  element.addEventListener("wheel", onWheel, { passive: false });
  element.addEventListener("pointerdown", onDown);
  element.addEventListener("pointermove", onMove);
  element.addEventListener("pointerup", onUp);
  element.addEventListener("pointercancel", onUp);
  return () => {
    element.removeEventListener("wheel", onWheel);
    element.removeEventListener("pointerdown", onDown);
    element.removeEventListener("pointermove", onMove);
    element.removeEventListener("pointerup", onUp);
    element.removeEventListener("pointercancel", onUp);
  };
}
