// Thin client for the backend. Every endpoint answers {success, data, error}.

const CLIENT_HEADER = { "X-Requested-With": "stock-entry" };
export const POLL_MS = 1500;  // scan progress refresh; a local HTTP request is cheap

async function request(path, options = {}) {
  let response;
  try {
    response = await fetch(path, options);
  } catch (error) {
    throw new Error("Cannot reach the Stock Entry server. Is run.py still running?");
  }
  let body = null;
  try {
    body = await response.json();
  } catch (error) {
    throw new Error(`The server sent an unreadable reply (HTTP ${response.status}).`);
  }
  if (!body || body.success !== true) {
    throw new Error((body && body.error) || `Request failed (HTTP ${response.status}).`);
  }
  return body.data;
}

export const getScan = () => request("/api/scan");
export const getScanStatus = () => request("/api/scan/status");
export const startScan = () => request("/api/scan", { method: "POST", headers: CLIENT_HEADER });
export const getTickers = () => request("/api/tickers");
export const getStock = (symbol) => request(`/api/stocks/${encodeURIComponent(symbol)}`);
