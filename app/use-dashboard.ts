"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { readDashboard, refreshDashboard, refreshNotice } from "./dashboard-client";
import type { Data } from "./dashboard-types";

const message = (error: unknown) => error instanceof Error ? error.message : "无法连接本机服务，请重试。";

export function useDashboard() {
  const [data, setData] = useState<Data | null>(null);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const [dailyBusy, setDailyBusy] = useState(false);
  const [notice, setNotice] = useState("");
  const [refreshError,setRefreshError] = useState("");
  const latestGeneratedAt = useRef('');
  const lifecycle = useRef<AbortController | null>(null);
  const refreshController = useRef<AbortController | null>(null);

  const load = useCallback(async (signal = lifecycle.current?.signal) => {
    if (!signal || signal.aborted) return;
    try {
      const next = await readDashboard(signal);
      if (!signal.aborted) {
        if (Date.parse(next.generatedAt) > Date.parse(latestGeneratedAt.current) && !next.refresh?.lastError) setRefreshError("");
        latestGeneratedAt.current = next.generatedAt;
        setData(next); setError("");
      }
    } catch (failure) {
      if (!signal.aborted) setError(message(failure));
    }
  }, []);

  const refresh = useCallback(async () => {
    if (refreshController.current || lifecycle.current?.signal.aborted) return;
    const controller = new AbortController();
    refreshController.current = controller;
    setBusy(true);
    setRefreshError("");
    setNotice("");
    try {
      const next = await refreshDashboard(controller.signal);
      if (!controller.signal.aborted) { latestGeneratedAt.current = next.generatedAt; setData(next); setError(""); setNotice(refreshNotice(next)); }
    } catch (failure) {
      if (!controller.signal.aborted) {setNotice(message(failure));setRefreshError(message(failure));}
    } finally {
      if (refreshController.current === controller) {
        refreshController.current = null;
        if (!controller.signal.aborted) setBusy(false);
      }
    }
  }, []);

  useEffect(() => {
    const controller = new AbortController();
    lifecycle.current = controller;
    let checking = false;
    let lastDataVersion: string | null = null;
    const checkDaily = async () => {
      if (checking || controller.signal.aborted || document.hidden) return;
      checking = true;
      try {
        const response = await fetch('/api/daily-sync-status', {cache:'no-store', signal:controller.signal});
        if (!response.ok) return;
        const status = await response.json() as {channels?: Record<string, {status?:string;lastAttemptAt?:string|null;lastSuccessAt?:string|null;lastError?:string|null;updatedCount?:number}>};
        if (controller.signal.aborted) return;
        const market = status.channels?.market;
        setDailyBusy(market?.status === 'running');
        const version = JSON.stringify(Object.entries(status.channels || {}).map(([key, value]) => [key, value.lastSuccessAt,
          value.status !== 'running' ? value.lastAttemptAt : null, value.status === 'error' ? value.lastError : null]));
        if (lastDataVersion !== version) {
          lastDataVersion = version;
          await load(controller.signal);
        }
        // A scheduler result describes that daily attempt, which may precede
        // a successful manual refresh. Current errors come from dashboard.refresh.
      } catch {
        // Keep the existing snapshot when the lightweight status request fails.
      } finally { checking = false; }
    };
    // The backend owns the once-daily schedule; opening another tab only reads it.
    void Promise.resolve().then(async () => {
      if (controller.signal.aborted) return;
      await load(controller.signal);
      await checkDaily();
    });
    const timer = window.setInterval(() => void checkDaily(), 30_000);
    const onVisible = () => { if (!document.hidden) void checkDaily(); };
    document.addEventListener('visibilitychange', onVisible);
    return () => {
      controller.abort();
      window.clearInterval(timer);
      document.removeEventListener('visibilitychange', onVisible);
      refreshController.current?.abort();
      refreshController.current = null;
    };
  }, [load]);

  return { data, error, busy:busy||dailyBusy, notice, refreshError, setNotice, load, refresh };
}
