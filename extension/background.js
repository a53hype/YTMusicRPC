const BRIDGE_URL = "http://127.0.0.1:8765/state";
const BRIDGE_TOKEN = "ytm-rpc-local-bridge-2026-11-v1-7f5c2a9d";

// Keep browser samples in arrival order. Previously each fetch ran independently,
// so a slower request from the previous track could arrive after a newer sample.
let forwardQueue = Promise.resolve();

async function forward(payload) {
  try {
    const response = await fetch(BRIDGE_URL, {
      method: "POST",
      mode: "cors",
      credentials: "omit",
      cache: "no-store",
      headers: {
        "Content-Type": "application/json",
        "X-YTM-RPC-Token": BRIDGE_TOKEN
      },
      body: JSON.stringify(payload),
      signal: AbortSignal.timeout(3000)
    });
    if (!response.ok) {
      console.warn("YTM Discord RPC bridge rejected update:", response.status);
    }
  } catch (error) {
    console.warn("YTM Discord RPC bridge is unavailable. Is the desktop app running?", error);
  }
}

function enqueueForward(payload) {
  forwardQueue = forwardQueue
    .catch(() => {})
    .then(() => forward(payload));
  return forwardQueue;
}

chrome.runtime.onMessage.addListener((message, sender, sendResponse) => {
  if (sender.tab?.id === undefined) return;
  const tabId = sender.tab.id;

  if (message?.type === "ytm-rpc-state" && message.payload) {
    const sampledAt = Number(message.sampledAt || message.payload.sampledAt || Date.now());
    enqueueForward({
      ...message.payload,
      sampledAt,
      sequence: Number(message.sequence || 0),
      sessionId: String(message.sessionId || ""),
      tabId
    }).then(() => sendResponse({ ok: true }));
    return true;
  } else if (message?.type === "ytm-rpc-clear") {
    enqueueForward({
      action: "clear",
      sampledAt: Number(message.sampledAt || Date.now()),
      sequence: Number(message.sequence || 0),
      sessionId: String(message.sessionId || ""),
      tabId
    }).then(() => sendResponse({ ok: true }));
    return true;
  }
});

chrome.tabs.onRemoved.addListener((tabId) => {
  enqueueForward({ action: "clear", sampledAt: Date.now(), sequence: Date.now(), tabId });
});

chrome.tabs.onUpdated.addListener((tabId, changeInfo) => {
  if (typeof changeInfo.url !== "string") return;
  try {
    const url = new URL(changeInfo.url);
    if (url.hostname !== "music.youtube.com") {
      enqueueForward({ action: "clear", sampledAt: Date.now(), sequence: Date.now(), tabId });
    }
  } catch (_) {
    enqueueForward({ action: "clear", sampledAt: Date.now(), sequence: Date.now(), tabId });
  }
});
