"use client";

import { useEffect } from "react";

// H-11: global-error must declare its own <html>/<body> because it replaces
// the root layout when the root layout itself throws. Kept minimal to avoid
// depending on anything that could ALSO be broken.
export default function GlobalError({
  error,
  reset,
}: {
  error: Error & { digest?: string };
  reset: () => void;
}) {
  useEffect(() => {
    console.error("GlobalError", { message: error.message, digest: error.digest });
  }, [error]);

  return (
    <html lang="en">
      <body>
        <div
          role="alert"
          style={{
            fontFamily: "system-ui, sans-serif",
            maxWidth: "40rem",
            margin: "4rem auto",
            padding: "1.5rem",
            border: "1px solid #fca5a5",
            borderRadius: "0.5rem",
            background: "#fef2f2",
            color: "#7f1d1d",
          }}
        >
          <h1 style={{ fontSize: "1.25rem", marginTop: 0 }}>The application has crashed</h1>
          <p>{error.message || "An unexpected error occurred."}</p>
          {error.digest ? (
            <p>
              Reference: <code>{error.digest}</code>
            </p>
          ) : null}
          <button
            type="button"
            onClick={() => reset()}
            style={{
              padding: "0.5rem 1rem",
              border: "1px solid #7f1d1d",
              background: "#fff",
              color: "#7f1d1d",
              cursor: "pointer",
              borderRadius: "0.25rem",
              marginTop: "1rem",
            }}
          >
            Reload
          </button>
        </div>
      </body>
    </html>
  );
}
