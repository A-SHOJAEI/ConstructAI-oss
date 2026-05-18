"""Tests for the simplified IFC (STEP) parser.

Pin the 5 documented entity types (IFCPROJECT, IFCSITE,
IFCBUILDING, IFCBUILDINGSTOREY, IFCSPACE), the name extraction
heuristic (3rd quoted string after GlobalId+OwnerHistory), the
metadata header parsing, and the encoding fallback.
"""

from __future__ import annotations

from app.services.ingestion.ifc_parser import (
    _ENTITY_PATTERNS,
    IfcParseResult,
    _extract_metadata,
    _extract_name,
    parse_ifc,
)

# =========================================================================
# _ENTITY_PATTERNS — pin the 5 documented entity types
# =========================================================================


def test_entity_patterns_canonical_set():
    """Pin the 5 documented IFC entity types — refactor must NOT
    silently drop one (downstream consumers depend on coverage)."""
    assert set(_ENTITY_PATTERNS) == {
        "IFCPROJECT",
        "IFCSITE",
        "IFCBUILDING",
        "IFCBUILDINGSTOREY",
        "IFCSPACE",
    }


def test_entity_patterns_case_insensitive_compiled():
    """[contract] All patterns compiled with IGNORECASE so 'ifcproject'
    matches as well as 'IFCPROJECT'."""
    import re

    for pattern in _ENTITY_PATTERNS.values():
        assert pattern.flags & re.IGNORECASE


# =========================================================================
# _extract_name — pin the IFC IfcRoot positional layout
# =========================================================================


def test_extract_name_uses_name_position_not_description():
    """[invariant] Standard IfcRoot-derived entities have:
    pos 1=GlobalId (str), pos 2=OwnerHistory (ref #), pos 3=Name (str),
    pos 4=Description (str). So the 1st quoted string after GlobalId
    is Name (matches[1]), NOT Description (matches[2]).
    Pin so refactor doesn't silently regress to picking Description."""
    args = "'0YvctVUKr0kugbFTf53O9L',#5,'My Project Name','description',$"
    assert _extract_name(args) == "My Project Name"


def test_extract_name_unset_dollar_falls_back_to_description():
    """[fallback] If Name is '$' (unset), fall back to Description.
    Pin: better to show Description than nothing for unnamed entities."""
    args = "'0YvctVUKr',#5,'$','My Description'"
    assert _extract_name(args) == "My Description"


def test_extract_name_unset_name_and_description_returns_none():
    """Both Name AND Description are '$' -> None."""
    args = "'0YvctVUKr',#5,'$','$'"
    assert _extract_name(args) is None


def test_extract_name_empty_string_falls_back_to_description():
    """Empty Name string -> fall back to Description if available."""
    args = "'0YvctVUKr',#5,'','description'"
    assert _extract_name(args) == "description"


def test_extract_name_no_quoted_strings():
    """No quoted strings -> None."""
    assert _extract_name("$,$,$") is None


def test_extract_name_only_one_quoted_string():
    """Only GlobalId quoted -> no name to extract -> None."""
    assert _extract_name("'GUID',#5,$") is None


def test_extract_name_two_quoted_strings_uses_second():
    """[contract] 2 quoted strings (GlobalId + Name only) -> the
    second is the Name."""
    args = "'GUID','MyName'"
    assert _extract_name(args) == "MyName"


# =========================================================================
# _extract_metadata — header parsing
# =========================================================================


def test_extract_metadata_file_description():
    """File description without semicolons inside quoted strings — the
    regex's [^;]* group can't span ';'."""
    text = "FILE_DESCRIPTION(('ViewDefinition'),'2-1');"
    meta = _extract_metadata(text)
    assert "file_description" in meta
    assert "ViewDefinition" in meta["file_description"]


def test_extract_metadata_file_name():
    text = "FILE_NAME('MyBuilding.ifc','2026-04-26',('User'),('Org'),'IFC4','app','authoring');"
    meta = _extract_metadata(text)
    assert meta["file_name"] == "MyBuilding.ifc"


def test_extract_metadata_file_schema_ifc4():
    text = "FILE_SCHEMA(('IFC4'));"
    meta = _extract_metadata(text)
    assert meta["schema"] == ["IFC4"]


def test_extract_metadata_file_schema_multiple():
    text = "FILE_SCHEMA(('IFC4','IFC2X3'));"
    meta = _extract_metadata(text)
    assert "IFC4" in meta["schema"]
    assert "IFC2X3" in meta["schema"]


def test_extract_metadata_no_header_returns_empty():
    """No header section -> empty dict (don't crash)."""
    assert _extract_metadata("") == {}
    assert _extract_metadata("ENDSEC; DATA;") == {}


# =========================================================================
# parse_ifc — public API
# =========================================================================


_SAMPLE_IFC = """ISO-10303-21;
HEADER;
FILE_DESCRIPTION(('ViewDefinition'),'2-1');
FILE_NAME('Sample.ifc','2026-04-26',('User'),('Org'),'IFC4','app','auth');
FILE_SCHEMA(('IFC4'));
ENDSEC;
DATA;
#1=IFCPROJECT('0YvctVUKr0kugbFTf53O9L',#5,'My Project','desc',$,$,$,(#10),#3);
#2=IFCSITE('1ABC',#5,'Main Site','desc',$,$,$,$,$,$,$,$,$,$);
#3=IFCBUILDING('2DEF',#5,'Tower 42','desc',$,$,$,$,$,$,$);
#4=IFCBUILDINGSTOREY('3GHI',#5,'Level 1','desc',$,$,$,$,0.0);
#5=IFCSPACE('4JKL',#5,'Lobby','desc',$,$,$,$,$,$);
ENDSEC;
END-ISO-10303-21;
"""


def test_parse_ifc_extracts_all_5_entity_types():
    out = parse_ifc(_SAMPLE_IFC.encode())
    types = {e["type"] for e in out.entities}
    assert types == {
        "IFCPROJECT",
        "IFCSITE",
        "IFCBUILDING",
        "IFCBUILDINGSTOREY",
        "IFCSPACE",
    }


def test_parse_ifc_extracts_names():
    out = parse_ifc(_SAMPLE_IFC.encode())
    names_by_type = {e["type"]: e["name"] for e in out.entities}
    assert names_by_type["IFCPROJECT"] == "My Project"
    assert names_by_type["IFCSITE"] == "Main Site"
    assert names_by_type["IFCBUILDING"] == "Tower 42"
    assert names_by_type["IFCBUILDINGSTOREY"] == "Level 1"
    assert names_by_type["IFCSPACE"] == "Lobby"


def test_parse_ifc_includes_entity_id():
    """Each entity includes the STEP id (#1, #2, ...)."""
    out = parse_ifc(_SAMPLE_IFC.encode())
    for entity in out.entities:
        assert entity["id"].startswith("#")
        assert entity["id"][1:].isdigit()


def test_parse_ifc_includes_metadata():
    out = parse_ifc(_SAMPLE_IFC.encode())
    assert out.metadata["file_name"] == "Sample.ifc"
    assert out.metadata["schema"] == ["IFC4"]


def test_parse_ifc_includes_raw():
    """Each entity dict has the full raw line for downstream
    re-parsing."""
    out = parse_ifc(_SAMPLE_IFC.encode())
    for entity in out.entities:
        assert "raw" in entity
        assert entity["raw"].startswith(entity["id"])


def test_parse_ifc_lowercase_keywords_still_match():
    """[invariant] IGNORECASE pattern -> 'ifcproject' matches as
    well as 'IFCPROJECT'. Name=matches[1] = 'Lower Project'."""
    text = "#1=ifcproject('GUID',#5,'Lower Project','d',$,$,$,$,$);"
    out = parse_ifc(text.encode())
    assert len(out.entities) == 1
    assert out.entities[0]["type"] == "IFCPROJECT"
    assert out.entities[0]["name"] == "Lower Project"


def test_parse_ifc_empty_input_returns_empty():
    out = parse_ifc(b"")
    assert out.entities == []
    assert out.metadata == {}


def test_parse_ifc_no_entities_returns_empty_list():
    """File with only header (no DATA section entities) -> empty
    entity list, but metadata still extracted."""
    text = "FILE_NAME('Empty.ifc');\nFILE_SCHEMA(('IFC4'));\n"
    out = parse_ifc(text.encode())
    assert out.entities == []
    assert out.metadata.get("file_name") == "Empty.ifc"


def test_parse_ifc_latin1_fallback():
    """[fallback] Non-UTF8 IFC files (some authoring tools emit
    latin-1) must NOT crash — fall back to latin-1 decoding.
    Name=matches[1] should contain 'café'."""
    # 0xe9 = é in latin-1, invalid as UTF-8 single byte:
    text = b"#1=IFCPROJECT('GUID',#5,'caf\xe9 project','d',$,$,$,$,$);"
    out = parse_ifc(text)
    assert len(out.entities) == 1
    assert "café" in out.entities[0]["name"]


def test_parse_ifc_missing_name_returns_none():
    """Entity with $ at name position -> name is None."""
    text = "#1=IFCBUILDING('GUID',#5,$,$,$,$,$);"
    out = parse_ifc(text.encode())
    assert out.entities[0]["name"] is None


# =========================================================================
# IfcParseResult — dataclass defaults
# =========================================================================


def test_ifc_parse_result_defaults_independent():
    r1 = IfcParseResult()
    r2 = IfcParseResult()
    r1.entities.append({"x": 1})
    r1.metadata["k"] = "v"
    assert r2.entities == []
    assert r2.metadata == {}


def test_ifc_parse_result_default_values():
    r = IfcParseResult()
    assert r.entities == []
    assert r.metadata == {}
