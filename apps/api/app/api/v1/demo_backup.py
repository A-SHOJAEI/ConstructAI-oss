"""Demo-day failsafe screencasts.

If a live demo segment misbehaves, the operator clicks the bookmarked URL
``/api/v1/demo/backup/<segment>`` to play a pre-recorded MP4 walkthrough.
The route serves an HTML auto-play page that streams the MP4 from the
``apps/api/static/demo-backups/`` directory if present, otherwise displays
a placeholder explaining how to record one.

This is intentionally outside any tenant scope — it's an operator tool, not
a customer feature. No auth gate is applied (only the operator hits these
URLs) but they're under ``/api/v1/`` so existing security headers apply.
"""

from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse, HTMLResponse

router = APIRouter()

# Demo segments registered for backup playback. Add new segments by adding
# an entry here and dropping <segment>.mp4 into the static directory.
SEGMENTS = {
    "rfi": "RFI auto-resolution agent (Stage 1-3 pipeline)",
    "estimating": "Quantity takeoff + cost lookup",
    "safety": "PPE detection + safety alerts",
    "orchestrator": "Multi-agent orchestration (cross-agent handoff)",
    "ask": "Ask ConstructAI — natural language Q&A against project corpus",
    "translation": "Construction-domain translation (RFI / submittal)",
    "daily-report": "Auto-generated daily report narrative",
}

STATIC_DIR = Path(__file__).resolve().parents[3] / "static" / "demo-backups"


def _placeholder_html(segment: str, label: str) -> str:
    """HTML page shown when no MP4 has been recorded for this segment."""
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <title>Demo backup — {segment}</title>
  <style>
    body {{ font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
            background: #1a1a1a; color: #eee; padding: 4em; line-height: 1.6; }}
    h1 {{ font-size: 1.5em; }}
    code {{ background: #2a2a2a; padding: 0.15em 0.4em; border-radius: 3px; }}
    a {{ color: #4dd0e1; }}
    .placeholder {{ background: #2a2a2a; padding: 2em; border-radius: 8px;
                    border: 1px dashed #555; margin: 2em 0; }}
  </style>
</head>
<body>
  <h1>Backup screencast — {segment}</h1>
  <p>{label}</p>
  <div class="placeholder">
    <p><strong>No backup MP4 has been recorded for this segment yet.</strong></p>
    <p>To record one (60–90 s of the happy path):</p>
    <pre>ffmpeg -f x11grab -framerate 30 -video_size 1920x1080 \\
  -i :0.0 -t 90 -c:v libx264 -preset fast \\
  apps/api/static/demo-backups/{segment}.mp4</pre>
    <p>Or use OBS Studio. After recording, refresh this page — the MP4 will
       auto-play.</p>
  </div>
  <p>Other segments:
    {" &middot; ".join(f'<a href="/api/v1/demo/backup/{s}">{s}</a>' for s in SEGMENTS)}
  </p>
</body>
</html>"""


def _player_html(segment: str, label: str) -> str:
    """Auto-play HTML for a recorded MP4."""
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <title>Demo backup — {segment}</title>
  <style>
    html, body {{ margin: 0; padding: 0; background: #000; }}
    .wrap {{ max-width: 100vw; max-height: 100vh; }}
    video {{ width: 100vw; height: 100vh; object-fit: contain; }}
    .label {{ position: fixed; top: 1em; left: 1em; color: #fff;
              background: rgba(0,0,0,0.6); padding: 0.4em 0.8em;
              border-radius: 4px; font-family: sans-serif; font-size: 14px; }}
  </style>
</head>
<body>
  <div class="label">{label}</div>
  <div class="wrap">
    <video src="/api/v1/demo/backup/{segment}.mp4" controls autoplay></video>
  </div>
</body>
</html>"""


# Note: route order matters. `/backup/{segment}.mp4` MUST be declared before
# `/backup/{segment}`, otherwise FastAPI matches `safety.mp4` as the bare
# segment value and the .mp4 streamer is never reached.


@router.get("/backup/{segment}.mp4")
async def backup_mp4(segment: str) -> FileResponse:
    """Stream the recorded MP4 file for `segment`."""
    if segment not in SEGMENTS:
        raise HTTPException(404, f"Unknown segment: {segment}")
    mp4 = STATIC_DIR / f"{segment}.mp4"
    if not (mp4.exists() and mp4.stat().st_size > 0):
        raise HTTPException(404, f"No backup recorded for {segment} yet")
    return FileResponse(
        mp4,
        media_type="video/mp4",
        headers={"Cache-Control": "no-store"},
    )


@router.get("/backup/{segment}", response_class=HTMLResponse)
async def backup_landing(segment: str) -> HTMLResponse:
    """Serve the player for `segment`, or a placeholder if MP4 missing."""
    if segment not in SEGMENTS:
        raise HTTPException(404, f"Unknown segment: {segment}")
    label = SEGMENTS[segment]
    mp4 = STATIC_DIR / f"{segment}.mp4"
    if mp4.exists() and mp4.stat().st_size > 0:
        return HTMLResponse(_player_html(segment, label))
    return HTMLResponse(_placeholder_html(segment, label))


@router.get("/backup", response_class=HTMLResponse)
async def backup_index() -> HTMLResponse:
    """List all registered demo segments + their record status."""
    rows = []
    for seg, label in SEGMENTS.items():
        mp4 = STATIC_DIR / f"{seg}.mp4"
        status = (
            f"<span style='color:#4caf50'>recorded ({mp4.stat().st_size // 1024} KB)</span>"
            if mp4.exists() and mp4.stat().st_size > 0
            else "<span style='color:#ef5350'>NOT recorded</span>"
        )
        rows.append(
            f"<tr><td><a href='/api/v1/demo/backup/{seg}'>{seg}</a></td>"
            f"<td>{label}</td><td>{status}</td></tr>"
        )
    return HTMLResponse(
        f"""<!DOCTYPE html><html><head><title>Demo backup index</title>
<style>body{{font-family:sans-serif;padding:2em;background:#fafafa;}}
table{{border-collapse:collapse;}}td{{padding:0.4em 1em;border-bottom:1px solid #ddd;}}</style>
</head><body>
<h1>Demo backup screencasts</h1>
<table><thead><tr><th>segment</th><th>covers</th><th>status</th></tr></thead>
<tbody>{"".join(rows)}</tbody></table>
<p>Drop MP4s in <code>{STATIC_DIR}</code> to populate.</p>
</body></html>"""
    )
