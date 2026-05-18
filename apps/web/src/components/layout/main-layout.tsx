"use client";

import { ReactNode, useState } from "react";
import { Header } from "./header";
import { Sidebar } from "./sidebar";
import { ErrorBoundary } from "@/components/error-boundary";
import { Breadcrumbs } from "@/components/breadcrumbs";
import { AskWidget } from "@/components/ask-widget";

interface MainLayoutProps {
  children: ReactNode;
}

export function MainLayout({ children }: MainLayoutProps) {
  const [sidebarOpen, setSidebarOpen] = useState(false);

  return (
    <div className="flex min-h-screen flex-col bg-gray-50 dark:bg-gray-900">
      {/* Skip navigation link for keyboard users */}
      <a href="#main-content" className="skip-to-content">
        Skip to main content
      </a>
      <Header onMenuToggle={() => setSidebarOpen((o) => !o)} />
      <div className="flex flex-1">
        <Sidebar open={sidebarOpen} onClose={() => setSidebarOpen(false)} />
        <main id="main-content" className="flex-1 p-4 md:p-6 overflow-auto" tabIndex={-1}>
          <Breadcrumbs />
          <ErrorBoundary>{children}</ErrorBoundary>
        </main>
      </div>
      {/* Global "Ask ConstructAI" widget — collapsible, project-scoped,
          available on every dashboard page. */}
      <AskWidget />
    </div>
  );
}
