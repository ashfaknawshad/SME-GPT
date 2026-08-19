import QuickActions from "./QuickActions";

export default function MobileShell({
  children,
  hideQuickActions = false,
}: {
  children: React.ReactNode;
  /** Suppress the floating Quick Actions button — for pages with their own
   * fixed bottom UI (e.g. a chat input bar) that would collide with it. */
  hideQuickActions?: boolean;
}) {
  // .app-page: at least a full small-viewport tall, with the landscape notch
  // insets applied. min-h-screen (= 100vh) resolved to the *large* viewport on
  // mobile — taller than what is visible — which left a phantom scroll on pages
  // with nothing to scroll.
  return (
    <div className="app-page" style={{ background: "var(--bg)", color: "var(--text-1)" }}>
      {children}
      {!hideQuickActions && <QuickActions />}
    </div>
  );
}
