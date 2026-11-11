const moneyFormats = new Map<number, Intl.NumberFormat>();

export function money(value: number | null | undefined, digits = 2): string {
  if (value == null || !Number.isFinite(value)) return "—";
  let formatter = moneyFormats.get(digits);
  if (!formatter) {
    formatter = new Intl.NumberFormat("en-US", { style: "currency", currency: "USD", minimumFractionDigits: digits, maximumFractionDigits: digits });
    moneyFormats.set(digits, formatter);
  }
  return formatter.format(value);
}
export const pct = (value: number | null, scale = 100) => value === null ? "—" : `${value >= 0 ? "+" : ""}${(value * scale).toFixed(2)}%`;
export const color = (value: number | null) => value === null ? "muted" : value >= 0 ? "up" : "down";
export const when = (value: string) => value.length <= 10 ? value : new Date(value).toLocaleString("zh-CN", { timeZone: "Asia/Shanghai", month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit", hour12: false });
