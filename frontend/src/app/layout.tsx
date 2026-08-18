import "./globals.css";
import type { Metadata, Viewport } from "next";
import { Inter } from "next/font/google";
import PwaRegister from "@/components/PwaRegister";
import ConfirmHost from "@/components/ui/ConfirmHost";

const inter = Inter({ subsets: ["latin"] });

export const metadata: Metadata = {
  title: "SME-GPT",
  description: "Enterprise Document Intelligence",
  manifest: "/manifest.json",
  // No `icons` here on purpose: app/favicon.ico, app/icon.png and
  // app/apple-icon.png are Next's file conventions and are injected into <head>
  // automatically. Declaring them again would emit duplicate <link> tags.
  appleWebApp: {
    capable: true,
    title: "SME-GPT",
  },
};

export const viewport: Viewport = {
  themeColor: "#2563ff",
  // Keep the page pinned to the device width (no user pinch-zoom-out that the
  // PWA was falling into when a row was slightly too wide). initialScale/width
  // are Next defaults but set explicitly here so the intent is clear.
  width: "device-width",
  initialScale: 1,
  // interactive-widget=resizes-content: when the on-screen keyboard opens,
  // shrink the layout viewport (and 100dvh) instead of overlaying it. This is
  // what keeps a chat's fixed input bar above the keyboard and stops the
  // header from being pushed off-screen on mobile.
  interactiveWidget: "resizes-content",
};

export default function RootLayout({
  children,
}: Readonly<{
  children: React.ReactNode;
}>) {
  return (
    <html lang="en" suppressHydrationWarning>
      <head>
        {/* Apply saved theme before first paint to prevent flash */}
        <script
          dangerouslySetInnerHTML={{
            __html: `try{var t=localStorage.getItem('theme');if(t==='dark'||(!t&&window.matchMedia('(prefers-color-scheme: dark)').matches)){document.documentElement.setAttribute('data-theme','dark');}}catch(e){}`,
          }}
        />
        <link
          rel="stylesheet"
          href="https://fonts.googleapis.com/css2?family=Material+Symbols+Outlined:opsz,wght,FILL,GRAD@24,400,0,0"
        />
      </head>
      <body className={inter.className}>
        <PwaRegister />
        {children}
        <ConfirmHost />
      </body>
    </html>
  );
}
