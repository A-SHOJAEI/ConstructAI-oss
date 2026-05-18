"use client";

import { useState } from "react";

interface HelpArticle {
  id: string;
  title: string;
  content: string;
  category: string;
}

const HELP_ARTICLES: HelpArticle[] = [
  {
    id: "getting-started",
    title: "Getting Started",
    category: "Basics",
    content:
      "Welcome to ConstructAI! Start by selecting a project from the dropdown in the header. " +
      "All dashboards and data are scoped to the active project. Use the sidebar to navigate " +
      "between Safety, Documents, Scheduling, and other modules.",
  },
  {
    id: "safety-monitoring",
    title: "Safety Monitoring",
    category: "Safety",
    content:
      "The Safety dashboard shows real-time alerts from camera feeds. AI-powered detection " +
      "identifies PPE violations, zone intrusions, and unsafe behaviors. Configure safety zones " +
      "in the Zones section and manage cameras in the Cameras section.",
  },
  {
    id: "document-upload",
    title: "Uploading Documents",
    category: "Documents",
    content:
      "Upload PDFs, IFC files, CSV data, and DOCX documents from the Documents page. " +
      "Documents are automatically processed, classified, and indexed for AI-powered search. " +
      "Use the Ask feature to query your documents with natural language.",
  },
  {
    id: "document-compare",
    title: "Comparing Documents",
    category: "Documents",
    content:
      "Navigate to Documents > Compare to diff two document versions. Select the base " +
      "document (A) and comparison document (B), then click Compare. The tool highlights " +
      "additions, removals, and modifications between versions.",
  },
  {
    id: "scheduling",
    title: "Schedule Management",
    category: "Scheduling",
    content:
      "Import schedules from Primavera P6 (.xer, .pmxml) or MS Project (.mpp, .xml) formats. " +
      "The CPM engine calculates critical path, float values, and DCMA 14-point compliance. " +
      "Use What-If analysis to model schedule changes before committing them.",
  },
  {
    id: "voice-commands",
    title: "Voice Commands",
    category: "Advanced",
    content:
      "Use the voice recorder to ask questions about your project documents hands-free. " +
      "Audio is transcribed using AI and matched against your indexed documents for answers.",
  },
  {
    id: "keyboard-shortcuts",
    title: "Keyboard Shortcuts",
    category: "Tips",
    content:
      "Press Shift+? to see all available keyboard shortcuts. Common shortcuts include: " +
      "g+p for Projects, g+s for Safety, g+d for Documents, and / to focus search.",
  },
  {
    id: "dark-mode",
    title: "Dark Mode",
    category: "Tips",
    content:
      "Toggle between light, dark, and system theme modes using the theme button in the " +
      "header. Your preference is saved and persists across sessions.",
  },
];

export function HelpPanel({ open, onClose }: { open: boolean; onClose: () => void }) {
  const [search, setSearch] = useState("");
  const [selectedArticle, setSelectedArticle] = useState<HelpArticle | null>(null);

  if (!open) return null;

  const filtered = search
    ? HELP_ARTICLES.filter(
        (a) =>
          a.title.toLowerCase().includes(search.toLowerCase()) ||
          a.content.toLowerCase().includes(search.toLowerCase()),
      )
    : HELP_ARTICLES;

  const categories = [...new Set(filtered.map((a) => a.category))];

  return (
    <div className="fixed inset-y-0 right-0 z-50 flex">
      {/* Backdrop */}
      <div className="fixed inset-0 bg-black/20" onClick={onClose} />

      {/* Panel */}
      <div className="relative ml-auto flex w-96 flex-col bg-white shadow-xl dark:bg-gray-800">
        {/* Header */}
        <div className="flex items-center justify-between border-b p-4 dark:border-gray-700">
          <h2 className="text-lg font-semibold text-gray-900 dark:text-white">Help</h2>
          <button
            onClick={onClose}
            className="text-gray-400 hover:text-gray-600 dark:hover:text-gray-200"
            aria-label="Close help panel"
          >
            <svg className="h-5 w-5" fill="none" viewBox="0 0 24 24" stroke="currentColor">
              <path
                strokeLinecap="round"
                strokeLinejoin="round"
                strokeWidth={2}
                d="M6 18L18 6M6 6l12 12"
              />
            </svg>
          </button>
        </div>

        {/* Search */}
        <div className="border-b p-3 dark:border-gray-700">
          <input
            type="text"
            placeholder="Search help articles..."
            value={search}
            onChange={(e) => {
              setSearch(e.target.value);
              setSelectedArticle(null);
            }}
            className="w-full rounded-md border border-gray-300 px-3 py-2 text-sm dark:border-gray-600 dark:bg-gray-700 dark:text-white"
          />
        </div>

        {/* Content */}
        <div className="flex-1 overflow-y-auto p-4">
          {selectedArticle ? (
            <div>
              <button
                onClick={() => setSelectedArticle(null)}
                className="mb-3 text-sm text-blue-600 hover:underline dark:text-blue-400"
              >
                &larr; Back to articles
              </button>
              <h3 className="text-base font-semibold text-gray-900 dark:text-white">
                {selectedArticle.title}
              </h3>
              <span className="mt-1 inline-block rounded bg-gray-100 px-2 py-0.5 text-xs text-gray-600 dark:bg-gray-700 dark:text-gray-300">
                {selectedArticle.category}
              </span>
              <p className="mt-3 text-sm leading-relaxed text-gray-600 dark:text-gray-300">
                {selectedArticle.content}
              </p>
            </div>
          ) : (
            <div className="space-y-4">
              {categories.map((cat) => (
                <div key={cat}>
                  <h4 className="mb-2 text-xs font-semibold uppercase text-gray-400">{cat}</h4>
                  <ul className="space-y-1">
                    {filtered
                      .filter((a) => a.category === cat)
                      .map((article) => (
                        <li key={article.id}>
                          <button
                            onClick={() => setSelectedArticle(article)}
                            className="w-full rounded px-3 py-2 text-left text-sm text-gray-700 hover:bg-gray-100 dark:text-gray-300 dark:hover:bg-gray-700"
                          >
                            {article.title}
                          </button>
                        </li>
                      ))}
                  </ul>
                </div>
              ))}
              {filtered.length === 0 && (
                <p className="text-center text-sm text-gray-400">
                  No articles found for &ldquo;{search}&rdquo;
                </p>
              )}
            </div>
          )}
        </div>

        {/* Footer */}
        <div className="border-t p-3 text-center dark:border-gray-700">
          <a
            href={`mailto:${process.env.NEXT_PUBLIC_SUPPORT_EMAIL || "support@constructai.dev"}`}
            className="text-sm text-blue-600 hover:underline dark:text-blue-400"
          >
            Contact Support
          </a>
        </div>
      </div>
    </div>
  );
}
