// Backend client used when the page runs inside Streamlit (streamlit_app.py copies this over api.js).
// There is no HTTP API there: each call goes to Python through the Streamlit component channel as
// {id, op, ...}, and the reply {id, success, data, error} comes back in the next "render" message.

const REPLY_TIMEOUT_MS = 90000;
export const POLL_MS = 4000;  // each progress check costs two Streamlit script runs, so check less often
const MIN_FRAME_HEIGHT = 640;
const PAGE_CHROME_HEIGHT = 150;  // browser toolbars + Streamlit header above the frame
const session = Math.random().toString(36).slice(2, 10);  // keeps ids unique if the frame reloads

let nextId = 1;
let waiting = null;            // the one request in flight: {id, resolve, reject, timer}
let queue = Promise.resolve(); // calls run one at a time: Streamlit only keeps the latest value sent
let markReady;
const ready = new Promise((resolve) => { markReady = resolve; });

function post(type, extra = {}) {
  window.parent.postMessage({ isStreamlitMessage: true, type, ...extra }, "*");
}

function onMessage(event) {
  if (event.source !== window.parent || !event.data || event.data.type !== "streamlit:render") return;
  markReady();
  const reply = event.data.args ? event.data.args.response : null;
  if (!reply || !waiting || reply.id !== waiting.id) return;  // an older reply shown again: ignore
  const { resolve, reject, timer } = waiting;
  waiting = null;
  clearTimeout(timer);
  if (reply.success === true) resolve(reply.data);
  else reject(new Error(reply.error || "The request failed."));
}

function send(op, extra) {
  return new Promise((resolve, reject) => {
    const id = `${session}-${nextId++}`;
    const timer = setTimeout(() => {
      if (!waiting || waiting.id !== id) return;
      waiting = null;
      reject(new Error("The server took too long to answer. Try again."));
    }, REPLY_TIMEOUT_MS);
    waiting = { id, resolve, reject, timer };
    post("streamlit:setComponentValue", { value: { id, op, ...extra }, dataType: "json" });
  });
}

function request(op, extra = {}) {
  const result = queue.then(() => ready).then(() => send(op, extra));
  queue = result.catch(() => {});  // a failed call must not block the ones after it
  return result;
}

function frameHeight() {
  const screenHeight = window.screen && window.screen.availHeight ? window.screen.availHeight : 0;
  return Math.max(MIN_FRAME_HEIGHT, screenHeight - PAGE_CHROME_HEIGHT);
}

window.addEventListener("message", onMessage);
post("streamlit:componentReady", { apiVersion: 1 });
post("streamlit:setFrameHeight", { height: frameHeight() });  // fixed height: the page scrolls inside the frame

export const getScan = () => request("scan");
export const getScanStatus = () => request("scan_status");
export const startScan = () => request("start_scan");
export const getTickers = () => request("tickers");
export const getStock = (symbol) => request("stock", { symbol });
