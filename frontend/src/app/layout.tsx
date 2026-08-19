import "./globals.css";
import type { Metadata, Viewport } from "next";
import { Inter } from "next/font/google";
import PwaRegister from "@/components/PwaRegister";
import ViewportManager from "@/components/layout/ViewportManager";
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
    // "default" keeps the iOS status bar opaque and lets the web view start
    // below it, which is what an installed app looks like. The safe-area
    // padding in the shell is still applied — it just evaluates to 0 here, and
    // to a real inset in Safari's browser mode and in landscape on notched
    // devices. One implementation, correct in both.
    statusBarStyle: "default",
  },
};

export const viewport: Viewport = {
  themeColor: "#2563ff",
  // Keep the page pinned to the device width (no user pinch-zoom-out that the
  // PWA was falling into when a row was slightly too wide). initialScale/width
  // are Next defaults but set explicitly here so the intent is clear.
  width: "device-width",
  initialScale: 1,
  // Pinch-zoom is deliberately left enabled (no maximumScale/userScalable):
  // disabling it is an accessibility regression, and the 16px minimum font size
  // on coarse pointers in globals.css already removes the focus-zoom that made
  // the PWA feel broken.
  //
  // viewport-fit=cover is what makes env(safe-area-inset-*) resolve to real
  // values instead of 0 — without it the notch/home-indicator handling below is
  // inert. The app draws into those regions and pads itself back out.
  viewportFit: "cover",
  // interactive-widget=resizes-content: where it is honoured (Chromium), the
  // on-screen keyboard shrinks the layout viewport — and with it 100svh — so
  // the app shell resizes itself and ViewportManager measures no overlap.
  // WebKit ignores this hint, which is exactly the gap --keyboard-inset fills.
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
        <ViewportManager />
        {children}
        <ConfirmHost />
      </body>
    </html>
  );
}
