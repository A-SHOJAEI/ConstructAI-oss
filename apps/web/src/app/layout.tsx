import type { Metadata } from "next";
import { Toaster } from "sonner";
import { ErrorBoundary } from "@/components/error-boundary";
import { AuthProvider } from "@/providers/auth-provider";
import { QueryProvider } from "@/providers/query-provider";
import { ThemeProvider } from "@/providers/theme-provider";
import "./globals.css";

export const metadata: Metadata = {
  title: "ConstructAI",
  description: "AI-Powered Construction Management Platform",
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en" suppressHydrationWarning>
      <body className="min-h-screen bg-gray-50 dark:bg-gray-900 text-gray-900 dark:text-gray-100 antialiased">
        <ErrorBoundary
          fallback={
            <div className="flex items-center justify-center min-h-screen">
              <h1>Something went wrong. Please refresh the page.</h1>
            </div>
          }
        >
          <QueryProvider>
            <AuthProvider>
              <ThemeProvider>
                {children}
                <Toaster position="top-right" richColors />
              </ThemeProvider>
            </AuthProvider>
          </QueryProvider>
        </ErrorBoundary>
      </body>
    </html>
  );
}
