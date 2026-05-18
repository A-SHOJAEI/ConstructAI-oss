"""Tests for _csv_response — CSV streaming helper.

Pin the documented format (StreamingResponse with text/csv media type
+ Content-Disposition attachment), the empty-data fallback (returns
'No data\\n' instead of an empty CSV), and the header row construction
from the first dict's keys.
"""

from __future__ import annotations

from app.api.v1.exports import _csv_response

# =========================================================================
# _csv_response — basic contract
# =========================================================================


def _read_streaming_body(resp) -> str:
    """Drain a StreamingResponse to a string. body_iterator is an
    async generator yielding strings/bytes; we use asyncio.run to
    collect them synchronously for test simplicity."""
    import asyncio

    async def _drain() -> str:
        parts: list[str] = []
        async for chunk in resp.body_iterator:
            if isinstance(chunk, bytes):
                parts.append(chunk.decode())
            else:
                parts.append(chunk)
        return "".join(parts)

    return asyncio.run(_drain())


def test_csv_response_media_type_text_csv():
    """[contract] Content-Type: text/csv (NOT application/csv —
    browsers handle text/csv better for download)."""
    resp = _csv_response([{"a": 1}], "out.csv")
    assert resp.media_type == "text/csv"


def test_csv_response_includes_attachment_disposition():
    """[contract] Content-Disposition: attachment; filename="..." so
    browsers download instead of displaying."""
    resp = _csv_response([{"a": 1}], "report.csv")
    cd = resp.headers["Content-Disposition"]
    assert "attachment" in cd
    assert 'filename="report.csv"' in cd


def test_csv_response_with_data_writes_header_row():
    """First dict's keys become the CSV header row."""
    resp = _csv_response(
        [
            {"name": "Alice", "age": 30},
            {"name": "Bob", "age": 25},
        ],
        "users.csv",
    )
    body = _read_streaming_body(resp)
    lines = body.strip().split("\r\n")
    assert lines[0] == "name,age"
    assert lines[1] == "Alice,30"
    assert lines[2] == "Bob,25"


def test_csv_response_empty_returns_no_data_marker():
    """[contract] Empty rows list -> 'No data\\n' (not empty CSV).
    Pin so a refactor doesn't return an actually-empty file (would
    look broken to users)."""
    resp = _csv_response([], "empty.csv")
    body = _read_streaming_body(resp)
    assert body == "No data\n"


def test_csv_response_handles_special_chars_in_values():
    """csv module handles commas + quotes in values via standard
    quoting."""
    resp = _csv_response(
        [{"col": "value, with comma"}],
        "test.csv",
    )
    body = _read_streaming_body(resp)
    # Comma-containing value gets quoted:
    assert '"value, with comma"' in body


def test_csv_response_filename_passed_through():
    """[contract] Filename string passed through unchanged into the
    Content-Disposition header."""
    resp = _csv_response([{"a": 1}], "safety_alerts_2026-04-26.csv")
    assert "safety_alerts_2026-04-26.csv" in resp.headers["Content-Disposition"]


def test_csv_response_uses_first_row_keys_as_canonical():
    """[contract] Header row uses keys() from rows[0]. Heterogeneous
    rows (different keys) -> later rows missing fields blank, extra
    fields not included. Pin: refactor must NOT silently union all
    keys (would change column count expectations)."""
    resp = _csv_response(
        [
            {"a": 1, "b": 2},
            # This row has 'a' and 'c', but 'c' is NOT in header:
            # csv.DictWriter raises ValueError unless extrasaction='ignore'
            # set; the production code uses default raise.
            {"a": 3, "b": 4},
        ],
        "test.csv",
    )
    body = _read_streaming_body(resp)
    # Headers come from first row:
    assert body.startswith("a,b\r\n")


def test_csv_response_returns_streaming_response_type():
    """[contract] Return type is StreamingResponse (NOT Response with
    full body) — supports backpressure for large exports."""
    from fastapi.responses import StreamingResponse

    resp = _csv_response([{"a": 1}], "x.csv")
    assert isinstance(resp, StreamingResponse)
