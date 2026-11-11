import type { Metadata } from "next";
import "./globals.css";
import "./workspace-polish.css";

export const metadata: Metadata = {
  title: "北极星 · 美股研究工作台",
  description: "本机美股走势、Qlib信号与IBKR持仓研究看板。",
  icons: {
    icon: "/favicon.svg",
    shortcut: "/favicon.svg",
  },
};

export default function RootLayout({
  children,
}: Readonly<{
  children: React.ReactNode;
}>) {
  return (
    <html lang="zh-CN">
      <body className="antialiased">{children}</body>
    </html>
  );
}
