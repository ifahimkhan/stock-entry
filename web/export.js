// CSV export of the stocks ticked in a list. The file is named after the export date (India time),
// e.g. 2026-11-19.csv; the browser adds " (1)" when a file of that name already exists.

const IST_DATE = new Intl.DateTimeFormat("en-CA", { timeZone: "Asia/Kolkata", year: "numeric", month: "2-digit", day: "2-digit" });

/** Today's date in India as YYYY-MM-DD. */
export const exportDate = (now = new Date()) => IST_DATE.format(now);

const csvValue = (value) => {
  if (value === null || value === undefined) return "";
  const text = String(value);
  return /[",\r\n]/.test(text) ? `"${text.replace(/"/g, '""')}"` : text;
};

/** CSV text: a header row of `label`s, then one row per item with `value(row)` of each field. */
export function toCsv(rows, fields) {
  const lines = [fields.map((field) => csvValue(field.label)).join(",")];
  for (const row of rows) lines.push(fields.map((field) => csvValue(field.value(row))).join(","));
  return lines.join("\r\n") + "\r\n";
}

/** Save `text` as `filename` (UTF-8 with a byte-order mark, so Excel shows "→" and "°" correctly). */
export function downloadCsv(filename, text) {
  const blob = new Blob(["﻿", text], { type: "text/csv;charset=utf-8" });
  const url = URL.createObjectURL(blob);
  const link = document.createElement("a");
  link.href = url;
  link.download = filename;
  link.style.display = "none";
  document.body.append(link);
  link.click();
  link.remove();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}
