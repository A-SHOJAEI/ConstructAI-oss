"""Record the operator-failsafe demo backup screencasts.

Drives the Next.js demo headlessly via Playwright (chromium-headless-shell)
to record one 30-60 s WebM per segment, then converts each WebM to MP4 and
drops it into apps/api/static/demo-backups/<segment>.mp4 — where the
/api/v1/demo/backup/<segment> route auto-plays it.

Designed to be runnable on a headless ARM64 host (NVIDIA DGX Spark / GB10):
- Playwright's chromium-headless-shell needs no X server
- ffmpeg converts WebM -> H.264 MP4

Per-segment behavior:
- All segments log in as demo_session_01 PM and navigate to a relevant route
- Segments that need *only* Spark 2 (translation, daily-report, RFI list,
  safety alerts, estimating UI, intelligence UI) record the working
  feature
- The "ask" segment is API-driven (no UI page exists), so we replay the
  /api/v1/projects/{id}/ask response and screenshot the JSON output

Skip segments that route to Spark 1 vLLM with --skip-vllm if Spark 1 is
down (RFI auto-resolve, default-task /ask).

Run from the repository root:
    apps/api/.venv/bin/python bin/record-demo-backups.py
    # individual:
    apps/api/.venv/bin/python bin/record-demo-backups.py rfi safety
    # skip vllm-dependent:
    apps/api/.venv/bin/python bin/record-demo-backups.py --skip-vllm
"""

from __future__ import annotations

import argparse
import asyncio
import os
import shutil
import subprocess
import sys
from pathlib import Path

# Late-imported (avoid hard dep when running --help):
# from playwright.async_api import async_playwright

REPO_ROOT = Path(__file__).resolve().parents[1]
STATIC_DIR = REPO_ROOT / "apps" / "api" / "static" / "demo-backups"
WEBM_DIR = REPO_ROOT / "apps" / "api" / "static" / "demo-backups" / "_webm"
WEBM_DIR.mkdir(parents=True, exist_ok=True)

DEFAULT_FRONTEND = os.environ.get("DEMO_FRONTEND_URL", "http://localhost:3000")
DEFAULT_API = os.environ.get("DEMO_API_URL", "http://localhost:8000")
DEFAULT_LOGIN = os.environ.get("DEMO_LOGIN_EMAIL", "demo.pm@demo_session_01.test")
DEFAULT_PASSWORD = os.environ.get("DEMO_LOGIN_PASSWORD", "demo-password-demo_session_01")
VIEWPORT = {"width": 1280, "height": 720}

# Segments that exercise Spark 1's vLLM (gpt-oss-120b) — skip when --skip-vllm
VLLM_SEGMENTS = {"rfi-resolve", "ask"}


# ---------------------------------------------------------------------------
# Per-segment recording flow
# ---------------------------------------------------------------------------


async def _login(context, page) -> None:
    """Log in by calling the API directly and injecting cookies into the
    Playwright context, then navigating to the dashboard.

    The form-based path was unreliable in headless Chromium (the Next.js
    auth-provider's client-side redirect didn't fire deterministically),
    so we just bypass it: hit POST /auth/login from Python, capture the
    Set-Cookie tokens, hand them to the browser, then navigate.
    """
    import httpx

    async with httpx.AsyncClient(timeout=15.0) as client:
        resp = await client.post(
            f"{DEFAULT_API}/api/v1/auth/login",
            json={"email": DEFAULT_LOGIN, "password": DEFAULT_PASSWORD},
        )
        resp.raise_for_status()
        cookies = []
        for raw in resp.headers.get_list("set-cookie"):
            # Parse each Set-Cookie string into the dict Playwright wants.
            parts = [p.strip() for p in raw.split(";")]
            kv = parts[0].split("=", 1)
            if len(kv) != 2:
                continue
            name, value = kv
            cookie = {
                "name": name,
                "value": value,
                "domain": "localhost",
                "path": "/",
            }
            for attr in parts[1:]:
                a = attr.lower()
                if a == "httponly":
                    cookie["httpOnly"] = True
                elif a == "secure":
                    cookie["secure"] = True
                elif a.startswith("samesite="):
                    cookie["sameSite"] = attr.split("=", 1)[1].title()
                elif a.startswith("path="):
                    cookie["path"] = attr.split("=", 1)[1]
                elif a.startswith("max-age="):
                    cookie["expires"] = int(__import__("time").time()) + int(
                        attr.split("=", 1)[1]
                    )
            cookies.append(cookie)
        # Cookie-based auth is on localhost in dev; secure flag would prevent
        # http://localhost from sending. Strip it.
        for c in cookies:
            c.pop("secure", None)
        await context.add_cookies(cookies)

    # Now load the dashboard root; auth cookies are present.
    await page.goto(f"{DEFAULT_FRONTEND}/", wait_until="domcontentloaded")
    await page.wait_for_timeout(2000)


async def _settle(page, ms: int = 1500) -> None:
    """Pause to let the camera stabilize between actions."""
    await page.wait_for_timeout(ms)


async def _record_segment_route(
    segment: str,
    route: str,
    description: str,
    duration_ms: int = 6000,
) -> Path | None:
    """Record a simple navigation to a route. Returns the WebM path."""
    from playwright.async_api import async_playwright

    out_dir = WEBM_DIR / segment
    if out_dir.exists():
        shutil.rmtree(out_dir)
    out_dir.mkdir(parents=True)

    print(f"  [{segment}] login + navigate {route}")
    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=True)
        context = await browser.new_context(
            viewport=VIEWPORT,
            record_video_dir=str(out_dir),
            record_video_size=VIEWPORT,
        )
        page = await context.new_page()
        try:
            await _login(context, page)
            await _settle(page, 1000)
            await page.goto(f"{DEFAULT_FRONTEND}{route}", wait_until="networkidle")
            await _settle(page, 1500)
            # Scroll a bit to show off page content
            await page.evaluate("window.scrollBy(0, 200)")
            await _settle(page, 800)
            await page.evaluate("window.scrollBy(0, 200)")
            await _settle(page, 800)
            await page.evaluate("window.scrollTo(0, 0)")
            await _settle(page, max(duration_ms - 4100, 500))
        finally:
            await context.close()
            await browser.close()

    # Find the WebM Playwright wrote
    webms = list(out_dir.glob("*.webm"))
    if not webms:
        print(f"  [{segment}] FAIL: no WebM written")
        return None
    return webms[0]


async def _record_rfi(skip_vllm: bool) -> Path | None:
    """RFI list + click into one — pure DB read, no LLM call."""
    from playwright.async_api import async_playwright

    out_dir = WEBM_DIR / "rfi"
    if out_dir.exists():
        shutil.rmtree(out_dir)
    out_dir.mkdir(parents=True)

    print(f"  [rfi] login + browse RFI list + open one")
    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=True)
        context = await browser.new_context(
            viewport=VIEWPORT,
            record_video_dir=str(out_dir),
            record_video_size=VIEWPORT,
        )
        page = await context.new_page()
        try:
            await _login(context, page)
            await _settle(page, 1000)
            await page.goto(f"{DEFAULT_FRONTEND}/rfis", wait_until="networkidle")
            await _settle(page, 1500)
            # Try to click into the first RFI row (cure-time question)
            # Different layouts: link, row, button. Prefer <a> that matches RFI-001.
            try:
                await page.locator("text=Concrete cure time").first.click(timeout=3000)
                await _settle(page, 2000)
            except Exception:
                pass
            await _settle(page, 2000)
        finally:
            await context.close()
            await browser.close()

    webms = list(out_dir.glob("*.webm"))
    return webms[0] if webms else None


async def _record_translation(skip_vllm: bool) -> Path | None:
    """Translation page — task_class='summarization' routes to Ollama 20B (Spark 2)."""
    return await _record_segment_route(
        "translation",
        "/translation",
        "Construction-domain translation (RFI/submittal)",
        duration_ms=8000,
    )


async def _record_daily_report(skip_vllm: bool) -> Path | None:
    """Daily reports page — generation routes to Ollama 20B."""
    return await _record_segment_route(
        "daily-report",
        "/reports",
        "Auto-generated daily-report narrative",
        duration_ms=8000,
    )


async def _record_safety(skip_vllm: bool) -> Path | None:
    return await _record_segment_route(
        "safety",
        "/safety/alerts",
        "PPE detection + safety alerts",
        duration_ms=7000,
    )


async def _record_estimating(skip_vllm: bool) -> Path | None:
    return await _record_segment_route(
        "estimating",
        "/estimating",
        "Quantity takeoff + cost lookup",
        duration_ms=7000,
    )


async def _record_orchestrator(skip_vllm: bool) -> Path | None:
    return await _record_segment_route(
        "orchestrator",
        "/intelligence",
        "Multi-agent orchestration (cross-agent handoff)",
        duration_ms=7000,
    )


async def _record_ask(skip_vllm: bool) -> Path | None:
    """No /ask UI page in the dashboard. Show the corpus-citation power
    via the documents page, since /ask itself is API-only and the
    underlying response routes to vLLM by default (skipped on --skip-vllm).
    """
    if skip_vllm:
        print("  [ask] skipping per --skip-vllm (Spark 1 down) — using documents page navigation only")
    return await _record_segment_route(
        "ask",
        "/documents",
        "Ask ConstructAI — corpus citations against UFGS / OSHA",
        duration_ms=7000,
    )


SEGMENT_RECORDERS = {
    "rfi": _record_rfi,
    "estimating": _record_estimating,
    "safety": _record_safety,
    "orchestrator": _record_orchestrator,
    "ask": _record_ask,
    "translation": _record_translation,
    "daily-report": _record_daily_report,
}


# ---------------------------------------------------------------------------
# WebM -> MP4 conversion
# ---------------------------------------------------------------------------


def _convert(webm: Path, segment: str) -> Path:
    out = STATIC_DIR / f"{segment}.mp4"
    cmd = [
        "ffmpeg", "-y", "-loglevel", "error",
        "-i", str(webm),
        "-c:v", "libx264", "-preset", "fast", "-crf", "23",
        "-pix_fmt", "yuv420p",
        "-movflags", "+faststart",
        str(out),
    ]
    print(f"  ffmpeg -> {out}")
    subprocess.run(cmd, check=True)
    return out


# ---------------------------------------------------------------------------
# Driver
# ---------------------------------------------------------------------------


async def run(segments: list[str], skip_vllm: bool) -> None:
    print(f"recording {len(segments)} segment(s): {', '.join(segments)}")
    for seg in segments:
        if seg not in SEGMENT_RECORDERS:
            print(f"  [{seg}] unknown segment; skip")
            continue
        if skip_vllm and seg in VLLM_SEGMENTS and seg != "ask":
            print(f"  [{seg}] skipping per --skip-vllm")
            continue
        recorder = SEGMENT_RECORDERS[seg]
        try:
            webm = await recorder(skip_vllm)
            if webm is None:
                print(f"  [{seg}] no video produced; skip")
                continue
            mp4 = _convert(webm, seg)
            print(f"  [{seg}] OK -> {mp4} ({mp4.stat().st_size // 1024} KB)")
        except Exception as exc:
            print(f"  [{seg}] FAIL: {type(exc).__name__}: {exc}")

    print(f"\nMP4s in {STATIC_DIR}")
    for f in sorted(STATIC_DIR.glob("*.mp4")):
        print(f"  {f.name:<24} {f.stat().st_size // 1024:>6} KB")


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("segments", nargs="*", help="segments to record (default: all)")
    ap.add_argument(
        "--skip-vllm",
        action="store_true",
        help="skip segments that need Spark 1 vLLM (rfi-resolve)",
    )
    args = ap.parse_args()
    segs = args.segments or list(SEGMENT_RECORDERS.keys())

    # sanity: chromium downloaded
    try:
        from playwright.async_api import async_playwright  # noqa: F401
    except ImportError:
        print("ERROR: playwright not installed. Run: pip install playwright && playwright install chromium")
        sys.exit(1)

    asyncio.run(run(segs, args.skip_vllm))


if __name__ == "__main__":
    main()
