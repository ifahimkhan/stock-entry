// The stock list as tappable cards: the phone layout of the list (the table is shown on wide screens).

import { el, formatPercent, formatPrice, signClass } from "./format.js";

/**
 * @param {HTMLElement} list  the <ul> to fill
 * @param {{rows: object[], selected?: string, note: (row: object) => string, onSelect: (row: object) => void}} options
 */
export function renderCards(list, { rows, selected, note, onSelect, pick = null }) {
  const cards = rows.map((row) => {
    const button = el("button", {
      type: "button", class: "card", "aria-pressed": String(row.ticker === selected),
      onclick: () => onSelect(row),
    }, [
      el("span", { class: "card-top" }, [
        el("span", { class: "card-id" }, [el("span", { class: "ticker", text: row.ticker })]),
        el("span", { class: "card-price", text: formatPrice(row.close) }),
      ]),
      el("span", { class: "card-mid" }, [
        el("span", { class: "card-name", text: row.name || row.sector || "" }),
        el("span", { class: `card-change ${signClass(row.pct_change)}`, text: formatPercent(row.pct_change) }),
      ]),
      el("span", { class: "card-note", text: note(row) }),
    ]);
    if (!pick) return el("li", {}, [button]);
    const box = el("input", { type: "checkbox", "aria-label": `Select ${row.ticker}`, checked: pick.isPicked(row) });
    box.addEventListener("change", () => pick.onToggle(row, box.checked));
    return el("li", { class: "card-pickable" }, [el("label", { class: "card-pick" }, [box]), button]);
  });
  list.replaceChildren(...cards);
}
