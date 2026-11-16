// Formatting and small DOM helpers shared by the table, the detail panel and the chart.

const PRICE = new Intl.NumberFormat("en-IN", { minimumFractionDigits: 2, maximumFractionDigits: 2 });
const MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];
export const DASH = "—";

export const isNumber = (value) => typeof value === "number" && Number.isFinite(value);
export const formatPrice = (value) => (isNumber(value) ? PRICE.format(value) : DASH);
export const formatPercent = (value) => (isNumber(value) ? `${value > 0 ? "+" : ""}${value.toFixed(2)}%` : DASH);

/** "2026-11-09" -> "09 Nov" (or "09 Nov 2026" with withYear). */
export function formatDate(iso, withYear = false) {
  const parts = typeof iso === "string" ? iso.slice(0, 10).split("-") : [];
  if (parts.length !== 3) return DASH;
  const month = MONTHS[Number(parts[1]) - 1];
  if (!month) return DASH;
  return withYear ? `${parts[2]} ${month} ${parts[0]}` : `${parts[2]} ${month}`;
}

/** "2026-11-14T09:12:00" -> "14 Nov 2026, 09:12" */
export function formatDateTime(iso) {
  if (typeof iso !== "string" || iso.length < 16) return DASH;
  return `${formatDate(iso, true)}, ${iso.slice(11, 16)}`;
}

/** 2-hour bar start "2026-11-16 09:15" -> "16 Nov, 09:15" */
export function formatBarTime(text) {
  if (typeof text !== "string" || text.length < 16) return DASH;
  return `${formatDate(text)}, ${text.slice(11, 16)}`;
}

/** Angle in degrees with sign, e.g. "-45.12°" */
export const formatAngle = (value) => (isNumber(value) ? `${value > 0 ? "+" : ""}${value.toFixed(2)}°` : DASH);

export const signClass = (value) => (!isNumber(value) || value === 0 ? "" : value > 0 ? "up" : "down");

/** Create an element. Text always goes through textContent, never innerHTML. */
export function el(tag, props = {}, children = []) {
  const node = document.createElement(tag);
  for (const [key, value] of Object.entries(props)) {
    if (value === null || value === undefined || value === false) continue;
    if (key === "text") node.textContent = value;
    else if (key === "class") node.className = value;
    else if (key.startsWith("on")) node.addEventListener(key.slice(2), value);
    else node.setAttribute(key, value === true ? "" : value);
  }
  for (const child of children) if (child) node.append(child);
  return node;
}

/** What a bar "is": entry, wait, breakout (valid = true), or nothing. */
export function statusKind(status, valid) {
  if (status === "entry") return "entry";
  if (status === "wait") return "wait";
  return valid === true ? "breakout" : "";
}

const STATUS_LABELS = { entry: "Entry", wait: "Wait", breakout: "Breakout" };

export function statusBadge(status, valid) {
  const kind = statusKind(status, valid);
  if (!kind) return el("span", { class: "dim", text: DASH });
  return el("span", { class: `badge badge-${kind}` }, [
    el("span", { class: `mark ${kind}`, "aria-hidden": "true" }),
    el("span", { text: STATUS_LABELS[kind] }),
  ]);
}
