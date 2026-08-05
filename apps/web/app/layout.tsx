import type { Metadata } from "next";
import { GeistMono } from "geist/font/mono";
import { GeistSans } from "geist/font/sans";
import "@radix-ui/themes/styles.css";
import "./globals.css";

import { AppThemeProvider } from "@/components/theme-provider";

export const metadata: Metadata = {
  title: "Agentic RAG",
  description: "面向南京大学新生事务的可信问答助手",
};

export default function RootLayout({ children }: Readonly<{ children: React.ReactNode }>) {
  return (
    <html lang="zh-CN" suppressHydrationWarning>
      <body className={`${GeistSans.variable} ${GeistMono.variable}`}>
        <AppThemeProvider>{children}</AppThemeProvider>
      </body>
    </html>
  );
}
