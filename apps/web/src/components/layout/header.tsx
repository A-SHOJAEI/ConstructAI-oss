"use client";

import Link from "next/link";
import { useAuth } from "@/hooks/use-auth";
import { ProjectSelector } from "@/components/project-selector";
import { ThemeToggle } from "@/components/theme-toggle";
import { LogOut, Menu, User } from "lucide-react";

interface HeaderProps {
  onMenuToggle?: () => void;
}

export function Header({ onMenuToggle }: HeaderProps) {
  const { user, logout } = useAuth();

  return (
    <header className="border-b border-gray-200 bg-white dark:bg-gray-800 dark:border-gray-700">
      <div className="flex h-16 items-center justify-between px-4 md:px-6">
        <div className="flex items-center gap-3">
          {onMenuToggle && (
            <button
              onClick={onMenuToggle}
              className="md:hidden p-2 rounded-lg text-gray-500 hover:bg-gray-100 dark:hover:bg-gray-700"
              aria-label="Toggle menu"
            >
              <Menu className="h-5 w-5" aria-hidden="true" />
            </button>
          )}
          <Link href="/projects" className="text-xl font-bold text-primary">
            ConstructAI
          </Link>
          {user && (
            <div className="flex-1 max-w-xs sm:max-w-sm">
              <ProjectSelector />
            </div>
          )}
        </div>
        <div className="flex items-center gap-2 md:gap-4">
          <ThemeToggle />
          {user && (
            <>
              <div className="hidden sm:flex items-center gap-2 text-sm text-gray-600 dark:text-gray-300">
                <User className="h-4 w-4" aria-hidden="true" />
                <span>{user.full_name}</span>
                <span className="rounded bg-gray-100 dark:bg-gray-700 px-2 py-0.5 text-xs text-gray-500 dark:text-gray-400">
                  {user.role}
                </span>
              </div>
              <button
                onClick={logout}
                className="flex items-center gap-1 rounded-lg px-3 py-1.5 text-sm text-gray-600 hover:bg-gray-100 hover:text-gray-900 dark:text-gray-300 dark:hover:bg-gray-700 dark:hover:text-white"
                aria-label="Logout"
              >
                <LogOut className="h-4 w-4" aria-hidden="true" />
                <span className="hidden sm:inline">Logout</span>
              </button>
            </>
          )}
        </div>
      </div>
    </header>
  );
}
