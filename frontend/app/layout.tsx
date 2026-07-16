import type { Metadata } from "next";
import {
  Frank_Ruhl_Libre,
  Geist,
  Geist_Mono,
  Heebo,
  Source_Serif_4,
} from "next/font/google";
import { AuthProvider } from "@/lib/auth";
import { SWRProvider } from "@/lib/swr";
import ErrorReporting from "@/components/ErrorReporting";
import Toaster from "@/components/ui/Toaster";
import "./globals.css";

const sourceSerif = Source_Serif_4({
  subsets: ["latin"],
  style: ["normal", "italic"],
  variable: "--font-serif",
});

const geist = Geist({
  subsets: ["latin"],
  variable: "--font-sans",
});

const geistMono = Geist_Mono({
  subsets: ["latin"],
  variable: "--font-mono",
});

// Geist and Source Serif 4 carry no Hebrew glyphs, so Hebrew fell through to
// browser defaults and looked out of place. These sit behind them in the CSS
// stacks (font fallback is per-glyph): Heebo matches Geist's geometric UI
// voice, Frank Ruhl Libre is the classic Hebrew news serif for headlines.
const heebo = Heebo({
  subsets: ["hebrew"],
  variable: "--font-sans-hebrew",
});

const frankRuhl = Frank_Ruhl_Libre({
  subsets: ["hebrew"],
  variable: "--font-serif-hebrew",
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
      className={`${sourceSerif.variable} ${geist.variable} ${geistMono.variable} ${heebo.variable} ${frankRuhl.variable}`}
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
