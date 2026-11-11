import type { Bar } from "./dashboard-types";

type PriceKey = "close" | "ma20" | "ma50" | "ma200";
const priceKeys: PriceKey[] = ["close", "ma20", "ma50", "ma200"];

export function sparkPath(history: Bar[]): string {
  const values = history.slice(-22).map(bar => bar.close);
  if (!values.length) return "";
  const low = Math.min(...values), high = Math.max(...values);
  return values.map((value, index) => `${index ? "L" : "M"}${index / Math.max(1, values.length - 1) * 100},${27 - (value - low) / (high - low || 1) * 24}`).join(" ");
}

export function chartGeometry(data: Bar[], showAverages: boolean) {
  const values = data.flatMap(bar => [bar.close, ...(showAverages ? [bar.ma20, bar.ma50, bar.ma200] : [])]).filter((value): value is number => value !== null && Number.isFinite(value));
  const low = values.length ? Math.min(...values) * .98 : 0;
  const high = values.length ? Math.max(...values) * 1.02 : 1;
  const x = (index: number) => 10 + index / Math.max(1, data.length - 1) * 810;
  const y = (value: number) => 20 + (high - value) / (high - low || 1) * 255;
  const paths = Object.fromEntries(priceKeys.map(key => {
    let started = false;
    return [key, data.map((bar, index) => {
      const value = bar[key];
      if (value === null || !Number.isFinite(value)) { started = false; return ""; }
      const point = `${started ? "L" : "M"}${x(index).toFixed(2)},${y(value).toFixed(2)}`;
      started = true;
      return point;
    }).join(" ")];
  })) as Record<PriceKey, string>;
  const tickIndices = data.length ? [...new Set([0, Math.floor(data.length / 3), Math.floor(data.length * 2 / 3), data.length - 1])] : [];
  return { x, y, low, high, paths, tickIndices, volumeMax: Math.max(1, ...data.map(bar => bar.volume ?? 0)) };
}
