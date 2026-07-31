import type { Metadata } from "next";
import {
  Geist,
  Geist_Mono,
  Noto_Sans_Hebrew,
  Source_Serif_4,
} from "next/font/google";
import { AuthProvider } from "@/lib/auth";
import { SWRProvider } from "@/lib/swr";
import ErrorReporting from "@/components/ErrorReporting";
import Toaster from "@/components/ui/Toaster";
import "./globals.css";

// adjustFontFallback: false because next/font's synthesized fallback faces
// (local Arial / Times New Roman, no unicode-range) swallow Hebrew glyphs
// before Noto Sans Hebrew is reached. The current Turbopack build ignores the
// option and emits them anyway, so globals.css also orders the stacks by
// literal family name with the "* Fallback" faces after Noto Sans Hebrew.
const sourceSerif = Source_Serif_4({
  subsets: ["latin"],
  style: ["normal", "italic"],
  variable: "--font-serif",
  adjustFontFallback: false,
});

const geist = Geist({
  subsets: ["latin"],
  variable: "--font-sans",
  adjustFontFallback: false,
});

const geistMono = Geist_Mono({
  subsets: ["latin"],
  variable: "--font-mono",
  adjustFontFallback: false,
});

// Geist and Source Serif 4 carry no Hebrew glyphs, so Hebrew fell through to
// browser defaults and looked out of place. Noto Sans Hebrew sits behind them
// in every CSS stack (font fallback is per-glyph) — one Hebrew face for
// headlines, reader body, and UI alike, matching modern Hebrew news sites.
const notoSansHebrew = Noto_Sans_Hebrew({
  subsets: ["hebrew"],
  variable: "--font-hebrew",
});

export const metadata: Metadata = {
  title: "NewsRead",
  description:
    "The social news reader — discover, summarize, and share articles with your take attached.",
};

export default function RootLayout({
  children,
}: Readonly<{ children: React.ReactNode }>) {
  return (
    <html
      lang="en"
      className={`${sourceSerif.variable} ${geist.variable} ${geistMono.variable} ${notoSansHebrew.variable}`}
    >
      <body>
        <AuthProvider>
          <SWRProvider>{children}</SWRProvider>
        </AuthProvider>
        <ErrorReporting />
        <Toaster />
      </body>
    </html>
  );
}
