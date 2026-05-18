import type { Metadata } from "next";

// M-33: Block indexing of every admin page. Auth already gates access, but
// if a URL ever leaks into a sitemap, analytics payload, or external link,
// this prevents search-engine discovery of the sensitive pages.
export const metadata: Metadata = {
  robots: {
    index: false,
    follow: false,
    googleBot: { index: false, follow: false },
  },
};

export default function AdminLayout({ children }: { children: React.ReactNode }) {
  return <>{children}</>;
}
