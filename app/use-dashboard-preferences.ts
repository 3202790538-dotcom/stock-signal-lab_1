"use client";

import { useMemo, useSyncExternalStore } from "react";

const CHANGE_EVENT = "northstar-preferences-change";
let savedFallback: string | undefined;
let privacyFallback: string | undefined;

function subscribe(listener: () => void) {
  window.addEventListener("storage", listener);
  window.addEventListener(CHANGE_EVENT, listener);
  return () => { window.removeEventListener("storage", listener); window.removeEventListener(CHANGE_EVENT, listener); };
}
function read(key: string, fallback: string | undefined, empty: string): string {
  if (fallback !== undefined) return fallback;
  try { return localStorage.getItem(key) ?? empty; }
  catch { return fallback ?? empty; }
}
const savedSnapshot = () => read("northstar-saved", savedFallback, "[]");
const privacySnapshot = () => read("northstar-private", privacyFallback, "false");
const emptySaved = () => "[]";
const emptyPrivacy = () => "false";

export function parseBookmarks(raw: string): string[] {
  try {
    const value: unknown = JSON.parse(raw);
    return Array.isArray(value) ? [...new Set(value.filter((item): item is string => typeof item === "string" && item.trim().length > 0))] : [];
  } catch { return []; }
}

export function useDashboardPreferences() {
  const savedRaw = useSyncExternalStore(subscribe, savedSnapshot, emptySaved);
  const privacyRaw = useSyncExternalStore(subscribe, privacySnapshot, emptyPrivacy);
  const saved = useMemo(() => parseBookmarks(savedRaw), [savedRaw]);
  const hidden = privacyRaw === "true";
  const save = (symbol: string) => {
    const current = parseBookmarks(savedSnapshot());
    const next = JSON.stringify(current.includes(symbol) ? current.filter(item => item !== symbol) : [...current, symbol]);
    try { localStorage.setItem("northstar-saved", next); savedFallback = undefined; } catch { savedFallback = next; }
    window.dispatchEvent(new Event(CHANGE_EVENT));
  };
  const privacy = () => {
    const next = String(privacySnapshot() !== "true");
    try { localStorage.setItem("northstar-private", next); privacyFallback = undefined; } catch { privacyFallback = next; }
    window.dispatchEvent(new Event(CHANGE_EVENT));
  };
  return { saved, hidden, save, privacy };
}
