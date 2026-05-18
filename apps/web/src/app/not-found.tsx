import Link from "next/link";

// H-11: Global 404 page. Handles any unknown route below the root.
export default function NotFound() {
  return (
    <div
      role="alert"
      className="mx-auto flex min-h-[50vh] max-w-xl flex-col items-center justify-center gap-4 p-6 text-center"
    >
      <h1 className="text-4xl font-bold text-slate-900 dark:text-slate-100">404</h1>
      <p className="text-lg text-slate-700 dark:text-slate-300">We couldn&apos;t find that page.</p>
      <Link
        href="/projects"
        className="inline-flex items-center justify-center rounded-md bg-blue-600 px-4 py-2 text-sm font-medium text-white hover:bg-blue-700 focus:outline-none focus:ring-2 focus:ring-blue-400"
      >
        Back to projects
      </Link>
    </div>
  );
}
