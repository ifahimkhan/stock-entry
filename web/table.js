// A sortable table of stock rows. Column `type` picks the cell renderer.

import { DASH, el, formatDate, formatPercent, formatPrice, isNumber, signClass, statusBadge } from "./format.js";

const NUMERIC_TYPES = new Set(["price", "pct", "int", "num2", "crore"]);
const CRORE = new Intl.NumberFormat("en-IN", { maximumFractionDigits: 0 });

function cell(column, row) {
  const value = row[column.key];
  switch (column.type) {
    case "ticker":
      return el("td", {}, [
        el("span", { class: "ticker", text: row.ticker }),
        row.name ? el("span", { class: "sub", text: row.name, title: row.name }) : null,
      ]);
    case "price": return el("td", { class: "num", text: formatPrice(value) });
    case "pct": return el("td", { class: `num ${signClass(value)}`, text: formatPercent(value) });
    case "int": return el("td", { class: "num", text: isNumber(value) ? String(value) : DASH });
    case "crore": return el("td", { class: "num", text: isNumber(value) ? CRORE.format(value) : DASH });
    case "num2": return el("td", { class: "num", text: isNumber(value) ? value.toFixed(2) : DASH });
    case "screens": return el("td", { text: row.screens || DASH, class: row.screens ? "" : "dim" });
    case "flag": return el("td", { text: value === true ? "Yes" : DASH, class: value === true ? "" : "dim" });
    case "date": return el("td", { text: formatDate(value) });
    case "status": return el("td", {}, [statusBadge(row.status ?? row.entry_status, row.valid)]);
    case "valid": return el("td", { text: value === true ? "True" : value === false ? "False" : DASH, class: value === null ? "dim" : "" });
    default: return el("td", { class: "clip", text: value || DASH, title: value || null });
  }
}

/** Sorted copy of rows; empty values always go last. */
export function sortRows(rows, sort) {
  const direction = sort.dir === "asc" ? 1 : -1;
  const isEmpty = (value) => value === null || value === undefined || value === "";
  return [...rows].sort((a, b) => {
    const left = a[sort.key];
    const right = b[sort.key];
    if (isEmpty(left) || isEmpty(right)) return isEmpty(left) - isEmpty(right);
    if (typeof left === "number" && typeof right === "number") return (left - right) * direction;
    return String(left).localeCompare(String(right)) * direction;
  });
}

/**
 * @param {HTMLTableElement} table
 * @param {{rows: object[], columns: {key: string, label: string, type: string}[], sort?: {key: string, dir: string},
 *          selected?: string, onSort?: (key: string) => void, onSelect?: (row: object) => void}} options
 */
export function renderTable(table, { rows, columns, sort, selected, onSort, onSelect }) {
  const headerCells = columns.map((column) => {
    const numeric = NUMERIC_TYPES.has(column.type);
    const active = sort && sort.key === column.key;
    const th = el("th", {
      class: numeric ? "num" : "", scope: "col", title: column.title || null,
      "aria-sort": active ? (sort.dir === "asc" ? "ascending" : "descending") : null,
    });
    if (!onSort) {
      th.textContent = column.label;
      return th;
    }
    const arrow = active ? (sort.dir === "asc" ? " ▲" : " ▼") : "";
    th.append(el("button", { type: "button", text: column.label + arrow, onclick: () => onSort(column.key) }));
    return th;
  });
  table.tHead.replaceChildren(el("tr", {}, headerCells));

  const bodyRows = rows.map((row) => {
    const tr = el("tr", {}, columns.map((column) => cell(column, row)));
    if (onSelect) {
      tr.className = "clickable";
      tr.tabIndex = 0;
      tr.setAttribute("aria-selected", String(row.ticker === selected));
      tr.addEventListener("click", () => onSelect(row));
      tr.addEventListener("keydown", (event) => {
        if (event.key === "Enter" || event.key === " ") {
          event.preventDefault();
          onSelect(row);
        }
      });
    }
    return tr;
  });
  table.tBodies[0].replaceChildren(...bodyRows);
}
