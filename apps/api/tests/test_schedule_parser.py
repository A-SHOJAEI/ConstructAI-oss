"""Tests for the schedule (CSV/TSV) parser.

Pin delimiter auto-detection (csv.Sniffer for ,;\t|), the
header-row contract, blank-row skipping, and the encoding
fallback (utf-8 -> latin-1).
"""

from __future__ import annotations

from app.services.ingestion.schedule_parser import (
    ScheduleParseResult,
    _detect_delimiter,
    _parse_csv,
    parse_schedule,
)

# =========================================================================
# _detect_delimiter — pin the 4 documented delimiters
# =========================================================================


def test_detect_comma():
    assert _detect_delimiter("a,b,c\n1,2,3") == ","


def test_detect_semicolon():
    assert _detect_delimiter("a;b;c\n1;2;3") == ";"


def test_detect_tab():
    assert _detect_delimiter("a\tb\tc\n1\t2\t3") == "\t"


def test_detect_pipe():
    assert _detect_delimiter("a|b|c\n1|2|3") == "|"


def test_detect_unknown_falls_back_to_comma():
    """[fallback] Sniffer can't determine -> comma. Pin: refactor
    must not raise on ambiguous input."""
    out = _detect_delimiter("singleline")
    assert out == ","


def test_detect_empty_falls_back_to_comma():
    assert _detect_delimiter("") == ","


# =========================================================================
# _parse_csv — header + tasks
# =========================================================================


def test_parse_csv_basic():
    text = "Task,Duration,Predecessor\nFoundation,30,\nStructure,60,Foundation"
    out = _parse_csv(text)
    assert out.columns == ["Task", "Duration", "Predecessor"]
    assert out.row_count == 2
    assert out.tasks[0] == {
        "Task": "Foundation",
        "Duration": "30",
        "Predecessor": "",
    }
    assert out.tasks[1] == {
        "Task": "Structure",
        "Duration": "60",
        "Predecessor": "Foundation",
    }


def test_parse_csv_strips_whitespace_in_columns_and_values():
    """Header column names AND cell values are .strip()'d."""
    text = "  Task  ,  Duration  \n  Foundation  ,  30  "
    out = _parse_csv(text)
    assert out.columns == ["Task", "Duration"]
    assert out.tasks[0] == {"Task": "Foundation", "Duration": "30"}


def test_parse_csv_skips_blank_rows():
    """[edge case] Blank rows (all-whitespace cells) are skipped —
    don't create empty task entries."""
    text = "Task,Duration\nFoundation,30\n   ,   \nStructure,60\n,\n"
    out = _parse_csv(text)
    # 2 non-blank rows + header:
    assert out.row_count == 2
    assert [t["Task"] for t in out.tasks] == ["Foundation", "Structure"]


def test_parse_csv_short_row_pads_with_empty_strings():
    """[edge case] Row shorter than header -> missing cells are ''.
    Don't crash on IndexError."""
    text = "Task,Duration,Predecessor\nFoundation"
    out = _parse_csv(text)
    assert out.row_count == 1
    assert out.tasks[0] == {"Task": "Foundation", "Duration": "", "Predecessor": ""}


def test_parse_csv_empty_input_returns_empty_result():
    out = _parse_csv("")
    assert out.tasks == []
    assert out.columns == []
    assert out.row_count == 0


def test_parse_csv_only_header_returns_no_tasks():
    """Header but no data rows -> columns set, tasks empty."""
    out = _parse_csv("Task,Duration")
    assert out.columns == ["Task", "Duration"]
    assert out.tasks == []
    assert out.row_count == 0


def test_parse_csv_tsv_input():
    """Tab-separated input -> auto-detected and parsed correctly."""
    text = "Task\tDuration\nFoundation\t30\nStructure\t60"
    out = _parse_csv(text)
    assert out.columns == ["Task", "Duration"]
    assert out.row_count == 2


def test_parse_csv_semicolon_input():
    """European-style semicolon-separated CSVs (common in EU)."""
    text = "Task;Duration\nFoundation;30"
    out = _parse_csv(text)
    assert out.columns == ["Task", "Duration"]
    assert out.tasks[0] == {"Task": "Foundation", "Duration": "30"}


def test_parse_csv_quoted_field_with_comma():
    """csv module handles quoted fields with embedded delimiters."""
    text = 'Task,Description\nFoundation,"30 days, includes excavation"'
    out = _parse_csv(text)
    assert out.tasks[0]["Description"] == "30 days, includes excavation"


# =========================================================================
# parse_schedule — public API
# =========================================================================


def test_parse_schedule_returns_result():
    file_bytes = b"Task,Duration\nFoundation,30"
    out = parse_schedule(file_bytes, "schedule.csv")
    assert isinstance(out, ScheduleParseResult)
    assert out.row_count == 1


def test_parse_schedule_utf8_decoding():
    """UTF-8 BOM-free content decodes cleanly."""
    file_bytes = b"Task,Description\nFoundation,Concrete pour"
    out = parse_schedule(file_bytes, "schedule.csv")
    assert out.tasks[0]["Description"] == "Concrete pour"


def test_parse_schedule_utf8_with_unicode():
    """UTF-8 unicode characters (em-dash, accented letters) survive."""
    file_bytes = "Task,Note\nFoundation,Pour — caveat: rebar".encode()
    out = parse_schedule(file_bytes, "schedule.csv")
    assert "—" in out.tasks[0]["Note"]


def test_parse_schedule_latin1_fallback():
    """[fallback] Latin-1-only bytes (like windows-1252 'naive') don't
    decode as UTF-8 — parser MUST fall back instead of crashing."""
    # 0xe9 is é in latin-1, invalid as utf-8 single byte:
    file_bytes = b"Task,Note\nFoundation,caf\xe9"
    out = parse_schedule(file_bytes, "schedule.csv")
    assert "café" in out.tasks[0]["Note"]


def test_parse_schedule_empty_bytes():
    out = parse_schedule(b"", "schedule.csv")
    assert out.row_count == 0
    assert out.columns == []


# =========================================================================
# ScheduleParseResult — dataclass defaults
# =========================================================================


def test_schedule_parse_result_default_factory_independent():
    """[invariant] dataclass uses default_factory so multiple
    instances don't share the same list (mutable default trap)."""
    r1 = ScheduleParseResult()
    r2 = ScheduleParseResult()
    r1.tasks.append({"a": 1})
    assert r2.tasks == []
    assert r1.tasks != r2.tasks


def test_schedule_parse_result_defaults_zero():
    r = ScheduleParseResult()
    assert r.tasks == []
    assert r.columns == []
    assert r.row_count == 0
