// H-11: Route-level loading skeleton. Shown during Suspense boundaries on
// navigations to any dashboard page.
export default function DashboardLoading() {
  return (
    <div
      role="status"
      aria-label="Loading"
      aria-busy="true"
      className="mx-auto flex max-w-4xl flex-col gap-4 p-6"
    >
      <div className="h-8 w-48 animate-pulse rounded bg-slate-200 dark:bg-slate-800" />
      <div className="h-32 w-full animate-pulse rounded-lg bg-slate-200 dark:bg-slate-800" />
      <div className="h-64 w-full animate-pulse rounded-lg bg-slate-200 dark:bg-slate-800" />
      <span className="sr-only">Loading page content…</span>
    </div>
  );
}
