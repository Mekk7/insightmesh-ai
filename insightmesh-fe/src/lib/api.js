// src/lib/api.js
// Shared API client. Single source of truth for the backend base URL.
import axios from "axios";

export const api = axios.create({
  baseURL: import.meta.env.VITE_API_BASE_URL || "/api",
  timeout: Number(import.meta.env.VITE_API_TIMEOUT ?? 0),
});

// ---------- History ----------
export const fetchHistory = async ({ limit = 20, offset = 0, userMode = null, onlySuccessful = false } = {}) => {
  const params = { limit, offset };
  if (userMode) params.user_mode = userMode;
  if (onlySuccessful) params.only_successful = "true";
  const { data } = await api.get("/insightmesh/history", { params });
  return data;
};

export const fetchRun = async (runId) => {
  const { data } = await api.get(`/insightmesh/history/${runId}`);
  return data;
};

export const deleteRun = async (runId) => {
  const { data } = await api.delete(`/insightmesh/history/${runId}`);
  return data;
};

export const searchRuns = async (q, limit = 20) => {
  const { data } = await api.get("/insightmesh/history/search", { params: { q, limit } });
  return data;
};

export const fetchStats = async () => {
  const { data } = await api.get("/insightmesh/history/stats");
  return data;
};

export const clearCache = async () => {
  const { data } = await api.post("/insightmesh/history/cache/clear");
  return data;
};

// ---------- Skeptic vs Advocate debate ----------
export const runDebate = async (finalReport, question = null, signal = undefined) => {
  const { data } = await api.post(
    "/insightmesh/debate",
    { report: finalReport, question: question || null },
    { signal }
  );
  return data;
};

// ---------- Paste reviews (universal, unblockable source) ----------
export const runPaste = async ({ text, product, strictness = "normal" }, signal = undefined) => {
  const { data } = await api.post(
    "/insightmesh/paste",
    { text, product, strictness },
    { signal }
  );
  return data?.final_report || data;
};

// ---------- Export ----------
export const exportUrlMd = (runId) => `${api.defaults.baseURL}/insightmesh/export/run/${runId}.md`;
export const exportUrlHtml = (runId) => `${api.defaults.baseURL}/insightmesh/export/run/${runId}.html`;

export const exportReportMd = async (finalReport) => {
  const { data } = await api.post(
    "/insightmesh/export/report.md",
    { final_report: finalReport },
    { responseType: "blob" }
  );
  return data; // Blob
};

export const exportReportHtml = async (finalReport) => {
  const { data } = await api.post(
    "/insightmesh/export/report.html",
    { final_report: finalReport },
    { responseType: "blob" }
  );
  return data; // Blob
};

export const downloadBlob = (blob, filename) => {
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = filename;
  document.body.appendChild(a);
  a.click();
  a.remove();
  URL.revokeObjectURL(url);
};

// ---------- SSE pipeline streaming ----------
/**
 * Stream a pipeline run, calling onEvent({type, ...}) for each progress event,
 * and resolving with the final report. Uses fetch + ReadableStream so we can
 * POST a JSON body (EventSource only supports GET).
 */
export const streamPipeline = async (body, onEvent, signal, onComplete) => {
  const url = `${api.defaults.baseURL}/insightmesh/run_pipeline/stream`;
  const res = await fetch(url, {
    method: "POST",
    headers: { "Content-Type": "application/json", Accept: "text/event-stream" },
    body: JSON.stringify(body),
    signal,
  });
  if (!res.ok || !res.body) {
    const t = await res.text().catch(() => "");
    throw new Error(`stream failed: HTTP ${res.status} ${t}`);
  }

  const reader = res.body.getReader();
  const decoder = new TextDecoder();
  let buf = "";
  let finalReport = null;

  // SSE message parser: events are separated by blank line, fields are "key: value\n"
  const flush = () => {
    let idx;
    while ((idx = buf.indexOf("\n\n")) >= 0) {
      const block = buf.slice(0, idx);
      buf = buf.slice(idx + 2);
      let eventName = "message";
      const dataLines = [];
      for (const line of block.split("\n")) {
        if (line.startsWith("event:")) eventName = line.slice(6).trim();
        else if (line.startsWith("data:")) dataLines.push(line.slice(5).trim());
      }
      if (!dataLines.length) continue;
      let parsed;
      try { parsed = JSON.parse(dataLines.join("\n")); } catch { parsed = { raw: dataLines.join("\n") }; }
      if (eventName === "complete" || eventName === "enriched") {
        // `complete` = the fast dashboard (deep signals deferred). `enriched` = the
        // same report with deep signals filled in by the background pass. Fire
        // onComplete for BOTH so the UI renders immediately on `complete` and then
        // seamlessly swaps in the deepened version on `enriched` — instead of the
        // caller having to wait for the stream to close.
        finalReport = parsed.final_report || parsed;
        try { onComplete && onComplete(finalReport, eventName === "enriched"); } catch (e) { /* swallow */ }
      } else if (eventName === "error") {
        const err = new Error(parsed.message || "stream error");
        err.payload = parsed;
        throw err;
      } else {
        try { onEvent && onEvent({ type: parsed.stage || eventName, ...parsed }); } catch (e) { /* swallow */ }
      }
    }
  };

  while (true) {
    const { value, done } = await reader.read();
    if (done) break;
    buf += decoder.decode(value, { stream: true });
    flush();
  }
  buf += decoder.decode(); // final flush
  flush();

  if (!finalReport) throw new Error("Stream ended without a complete event");
  return finalReport;
};
