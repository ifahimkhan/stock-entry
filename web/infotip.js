// An "i" button that shows a small table in a tooltip box: on hover, keyboard focus, or tap.
// The box is placed on the page itself (position: fixed), so a scrolling table cannot cut it off.

import { el } from "./format.js";

const GAP = 6;      // px between the button and the box
const MARGIN = 8;   // px kept free at the window edges
let open = null;    // { box, button } of the box on screen, if any

function hide() {
  if (!open) return;
  open.box.remove();
  open.button.setAttribute("aria-expanded", "false");
  open = null;
}

/** Below the button when there is room, else above; never outside the window. */
function place(box, button) {
  const at = button.getBoundingClientRect();
  const { width, height } = box.getBoundingClientRect();
  const left = Math.max(MARGIN, Math.min(at.left, window.innerWidth - width - MARGIN));
  const below = at.bottom + GAP;
  const top = below + height + MARGIN <= window.innerHeight ? below : Math.max(MARGIN, at.top - GAP - height);
  box.style.left = `${left}px`;
  box.style.top = `${top}px`;
}

function show(button, info) {
  if (open && open.button === button) return;
  hide();
  const box = el("div", { class: "info-tip", role: "tooltip" }, [
    info.title ? el("p", { class: "info-tip-title", text: info.title }) : null,
    ...(info.lines || []).map((text) => el("p", { class: "info-tip-line", text })),
    info.rows ? el("table", {}, [
      el("thead", {}, [el("tr", {}, info.columns.map((text) => el("th", { scope: "col", text })))]),
      el("tbody", {}, info.rows.map((cells) => el("tr", {}, cells.map((text) => el("td", { text }))))),
    ]) : null,
  ]);
  document.body.append(box);
  place(box, button);
  button.setAttribute("aria-expanded", "true");
  open = { box, button };
}

document.addEventListener("keydown", (event) => { if (event.key === "Escape") hide(); });
document.addEventListener("click", (event) => { if (open && !open.button.contains(event.target)) hide(); });
window.addEventListener("scroll", hide, true);
window.addEventListener("resize", hide);

/**
 * @param {{label?: string, title?: string, lines?: string[], columns?: string[], rows?: string[][]}} info
 *   label: what the button explains (for screen readers; default: the title); lines: sentences in the box;
 *   columns and rows: a table in the box
 * @returns {HTMLButtonElement}
 */
export function infoButton(info) {
  const button = el("button", { type: "button", class: "info-button", text: "i", "aria-label": info.label || info.title || "More information", "aria-expanded": "false" });
  button.addEventListener("mouseenter", () => show(button, info));
  button.addEventListener("mouseleave", hide);
  button.addEventListener("focus", () => show(button, info));
  button.addEventListener("blur", hide);
  button.addEventListener("click", (event) => {
    event.stopPropagation();   // a tap shows the box; it never sorts the column or opens a stock
    show(button, info);
  });
  return button;
}
