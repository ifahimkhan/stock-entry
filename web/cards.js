// The stock list as tappable cards: the phone layout of the list (the table is shown on wide screens).

import { el, formatPercent, formatPrice, signClass, statusBadge, statusKind } from "./format.js";

/**
 * @param {HTMLElement} list  the <ul> to fill
 * @param {{rows: object[], selected?: string, note: (row: object) => string, onSelect: (row: object) => void}} options
 */
export function renderCards(list, { rows, selected, note, onSelect }) {
  const cards = rows.map((row) => {
    const button = el("button", {
      type: "button", class: "card", "aria-pressed": String(row.ticker === selected),
      onclick: () => onSelect(row),
    }, [
      el("span", { class: "card-top" }, [
        el("span", { class: "card-id" }, [
          el("span", { class: "ticker", text: row.ticker }),
          statusKind(row.status, row.valid) ? statusBadge(row.status, row.valid) : null,
        ]),
        el("span", { class: "card-price", text: formatPrice(row.close) }),
      ]),
      el("span", { class: "card-mid" }, [
        el("span", { class: "card-name", text: row.name || row.sector || "" }),
        el("span", { class: `card-change ${signClass(row.pct_change)}`, text: formatPercent(row.pct_change) }),
      ]),
      el("span", { class: "card-note", text: note(row) }),
    ]);
    return el("li", {}, [button]);
  });
  list.replaceChildren(...cards);
}
