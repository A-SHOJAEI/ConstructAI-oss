"use client";

import { FolderOpen } from "lucide-react";

export function NoProjectSelected() {
  return (
    <div
      className="flex flex-col items-center justify-center py-20"
      role="status"
      aria-label="No project selected"
    >
      <div
        className="flex h-16 w-16 items-center justify-center rounded-full bg-gray-100 dark:bg-gray-800 mb-4"
        aria-hidden="true"
      >
        <FolderOpen className="h-8 w-8 text-gray-400" />
      </div>
      <h2 className="text-lg font-semibold text-gray-900 dark:text-white mb-2">
        No project selected
      </h2>
      <p className="text-sm text-gray-500 dark:text-gray-400 max-w-sm text-center">
        Select a project from the dropdown in the header to view this page.
      </p>
    </div>
  );
}
