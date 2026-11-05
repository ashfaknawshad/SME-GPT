/**
 * PageHeading — the one page title block used by every signed-in page, so
 * titles share the same size, spacing and position everywhere.
 */
export default function PageHeading({
  title,
  subtitle,
  actions,
  className = "",
}: {
  title?: React.ReactNode;
  subtitle?: React.ReactNode;
  /** Page-level actions shown to the right of the title (wrap below on phones). */
  actions?: React.ReactNode;
  className?: string;
}) {
  if (!title && !subtitle && !actions) return null;
  return (
    <div className={`rise-in mb-6 flex shrink-0 flex-wrap items-end justify-between gap-4 ${className}`}>
      <div className="min-w-0 max-w-3xl">
        {title && (
          <h1 className="break-words text-[24px] font-extrabold tracking-tight text-[var(--text-1)] sm:text-[30px]">
            {title}
          </h1>
        )}
        {subtitle && (
          <p className="mt-1.5 text-[13px] leading-6 text-[var(--text-2)] sm:text-[14px]">{subtitle}</p>
        )}
      </div>
      {actions && <div className="flex flex-wrap items-center gap-2">{actions}</div>}
    </div>
  );
}
