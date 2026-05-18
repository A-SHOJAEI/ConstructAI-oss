"use client";

import { useEffect } from "react";

// H-11: Route-level error boundary. Without this, any unhandled throw inside
// a dashboard page kills the whole segment with no recovery affordance.
export default function DashboardError({
  error,
  reset,
}: {
  error: Error & { digest?: string };
  reset: () => void;
}) {
  useEffect(() => {
    // Structured log — picked up by the browser console; avoid leaking
    // full stack traces to analytics unless/until we wire a sink.

    console.error("DashboardError", { message: error.message, digest: error.digest });
  }, [error]);

  return (
    <div
      role="alert"
      aria-live="assertive"
      className="mx-auto flex max-w-xl flex-col gap-4 rounded-lg border border-red-200 bg-red-50 p-6 dark:border-red-900 dark:bg-red-950"
    >
      <h2 className="text-lg font-semibold text-red-900 dark:text-red-100">
        Something went wrong loading this page
      </h2>
      <p className="text-sm text-red-800 dark:text-red-200">
        {error.message || "An unexpected error occurred."}
      </p>
      {error.digest ? (
        <p className="text-xs text-red-700 dark:text-red-300">
          Reference: <code className="font-mono">{error.digest}</code>
        </p>
      ) : null}
      <div className="flex gap-2">
        <button
          type="button"
          onClick={() => reset()}
          className="inline-flex items-center justify-center rounded-md border border-red-300 bg-white px-4 py-2 text-sm font-medium text-red-900 hover:bg-red-100 focus:outline-none focus:ring-2 focus:ring-red-400"
        >
          Try again
        </button>
        <a
          href="/projects"
          className="inline-flex items-center justify-center rounded-md border border-transparent bg-red-700 px-4 py-2 text-sm font-medium text-white hover:bg-red-800 focus:outline-none focus:ring-2 focus:ring-red-500"
        >
          Back to projects
        </a>
      </div>
    </div>
  );
}
