import Link from "next/link";

// M-34: removed the "View Projects" link — it pointed to /projects which
// requires auth, so anonymous clicks bounced through the login redirect.
// Single "Get Started" CTA is clearer and avoids the redirect loop.
export default function Home() {
  return (
    <main className="flex min-h-screen flex-col items-center justify-center p-8">
      <div className="max-w-2xl text-center">
        <h1 className="mb-4 text-5xl font-bold tracking-tight text-gray-900 dark:text-white">
          ConstructAI
        </h1>
        <p className="mb-8 text-xl text-gray-600 dark:text-gray-300">
          AI-Powered Construction Management Platform
        </p>
        <p className="mb-12 text-gray-500 dark:text-gray-400">
          Streamline your construction projects with eleven coordinated AI agents covering
          estimation, scheduling, safety, quality, and more.
        </p>
        <div className="flex gap-4 justify-center">
          <Link
            href="/login"
            className="rounded-lg bg-primary px-6 py-3 text-white font-semibold hover:bg-primary-dark transition-colors"
          >
            Get Started
          </Link>
        </div>
      </div>
    </main>
  );
}
