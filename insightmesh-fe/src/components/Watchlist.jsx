// insightmesh-fe/src/components/Watchlist.jsx
// Personal watchlist — pinned products the user wants to keep an eye on.
// LocalStorage-based right now (pre-auth). When real auth lands, swap the
// storage adapter for a backend call; the API surface stays identical.
import React, { useEffect, useState } from "react";

const STORAGE_KEY = "insightmesh_watchlist_v1";

const _read = () => {
  try {
    const raw = localStorage.getItem(STORAGE_KEY);
    if (!raw) return [];
    const parsed = JSON.parse(raw);
    return Array.isArray(parsed) ? parsed : [];
  } catch {
    return [];
  }
};

const _write = (items) => {
  try { localStorage.setItem(STORAGE_KEY, JSON.stringify(items)); } catch {}
};

export const useWatchlist = () => {
  const [items, setItems] = useState(_read());

  useEffect(() => {
    const onStorage = (e) => {
      if (e.key === STORAGE_KEY) setItems(_read());
    };
    window.addEventListener("storage", onStorage);
    return () => window.removeEventListener("storage", onStorage);
  }, []);

  const has = (query) =>
    items.some((it) => (it.query || "").toLowerCase() === (query || "").toLowerCase());

  const add = (entry) => {
    const next = [
      { ...entry, added_at: new Date().toISOString() },
      ...items.filter((it) => (it.query || "").toLowerCase() !== (entry.query || "").toLowerCase()),
    ].slice(0, 24);
    setItems(next);
    _write(next);
  };

  const remove = (query) => {
    const next = items.filter((it) => (it.query || "").toLowerCase() !== (query || "").toLowerCase());
    setItems(next);
    _write(next);
  };

  const toggle = (entry) => {
    if (has(entry.query)) remove(entry.query);
    else add(entry);
  };

  return { items, has, add, remove, toggle };
};

// Heart button for pinning a product
export function WatchlistButton({ query, snapshot, className = "" }) {
  const wl = useWatchlist();
  const isOn = wl.has(query);
  return (
    <button
      onClick={() => wl.toggle({ query, ...snapshot })}
      title={isOn ? "Remove from watchlist" : "Add to watchlist"}
      className={`inline-flex items-center gap-1.5 rounded-full border px-2.5 py-1 text-[11px] transition ${
        isOn
          ? "border-rose-700/60 bg-rose-950/30 text-rose-200 hover:bg-rose-900/40"
          : "border-zinc-700 bg-zinc-900 text-zinc-300 hover:bg-zinc-800"
      } ${className}`}
    >
      <span>{isOn ? "♥" : "♡"}</span>
      <span>{isOn ? "Watching" : "Watch"}</span>
    </button>
  );
}

// Card listing the watchlist (shown on the dashboard empty state)
export default function Watchlist({ onOpen }) {
  const wl = useWatchlist();
  if (wl.items.length === 0) return null;

  return (
    <div className="rounded-2xl border border-zinc-800/80 bg-zinc-900/60 p-5">
      <div className="mb-3 flex items-center justify-between gap-2">
        <div>
          <h3 className="text-base font-medium text-zinc-100">Your watchlist</h3>
          <p className="mt-0.5 text-xs text-zinc-400">Products you're keeping an eye on</p>
        </div>
        <span className="rounded-full bg-rose-900/30 px-2 py-0.5 text-[11px] text-rose-200">{wl.items.length}</span>
      </div>
      <div className="grid grid-cols-2 gap-2 md:grid-cols-3">
        {wl.items.map((item) => {
          const sent = typeof item.avg_sentiment === "number" ? item.avg_sentiment : null;
          const ago = item.added_at ? Math.floor((Date.now() - new Date(item.added_at).getTime()) / 86400000) : null;
          return (
            <div
              key={item.query}
              className="flex flex-col items-start gap-1 rounded-lg border border-zinc-800 bg-zinc-900/40 p-2.5 text-left hover:border-zinc-700 hover:bg-zinc-800"
            >
              <button
                onClick={() => onOpen && onOpen(item)}
                className="text-left flex-1 w-full"
              >
                <span className="block truncate text-xs text-zinc-200">{item.query}</span>
                <div className="mt-1 flex items-center gap-2 text-[10px] text-zinc-500">
                  {sent != null && <span>{sent.toFixed(1)}★</span>}
                  {ago != null && <span>· {ago === 0 ? "today" : `${ago}d ago`}</span>}
                </div>
              </button>
              <button
                onClick={(e) => { e.stopPropagation(); wl.remove(item.query); }}
                className="self-end text-[10px] text-zinc-600 hover:text-rose-300"
                title="Remove"
              >
                ✕
              </button>
            </div>
          );
        })}
      </div>
    </div>
  );
}
