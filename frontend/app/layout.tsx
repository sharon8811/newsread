import type { Metadata } from "next";
import { Geist, Geist_Mono, Source_Serif_4 } from "next/font/google";
import { AuthProvider } from "@/lib/auth";
import { SWRProvider } from "@/lib/swr";
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
      className={`${sourceSerif.variable} ${geist.variable} ${geistMono.variable}`}
    >
      <body>
        <AuthProvider>
          <SWRProvider>{children}</SWRProvider>
        </AuthProvider>
        <Toaster />
      </body>
    </html>
  );
}
