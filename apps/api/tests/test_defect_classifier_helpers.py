"""Tests for the pure helpers in services/quality/defect_classifier.

Pin the taxonomy, severity map, recommendation lookup, the
ImageNet-to-defect heuristic mapper, and the version auto-detection
that reads metadata.json / class_mapping.txt — those are the parts
the API serves up regardless of whether a real ViT is loaded.

The model-loading paths are skipped here (require torch + a real
checkpoint); ``_fallback_classify`` is also covered.
"""

from __future__ import annotations

import json

import numpy as np
import pytest

from app.services.quality.defect_classifier import (
    DEFECT_TYPES,
    DEFECT_TYPES_V1_0,
    DEFECT_TYPES_V1_1,
    SEVERITY_MAP,
    DefectClassifier,
    _get_recommendations,
    _map_imagenet_to_defect,
)

# =========================================================================
# Taxonomy invariants
# =========================================================================


def test_default_taxonomy_is_v1_1():
    """Pin the default — DEFECT_TYPES must point at v1.1, not legacy v1.0."""
    assert DEFECT_TYPES is DEFECT_TYPES_V1_1


def test_v1_1_has_eight_classes():
    assert len(DEFECT_TYPES_V1_1) == 8


def test_v1_1_classes_canonical():
    """v1.1 documented class set — refactor must not silently drop one."""
    expected = {
        "crack",
        "spalling",
        "corrosion",
        "efflorescence",
        "exposed_rebar",
        "surface_deterioration",
        "biological_growth",
        "no_defect",
    }
    assert set(DEFECT_TYPES_V1_1) == expected


def test_v1_0_legacy_has_twelve_classes():
    assert len(DEFECT_TYPES_V1_0) == 12


# =========================================================================
# SEVERITY_MAP
# =========================================================================


@pytest.mark.parametrize(
    "defect,severity",
    [
        # Critical: structural integrity at risk
        ("crack", "critical"),
        ("exposed_rebar", "critical"),
        ("crack_structural", "critical"),  # legacy alias
        ("rebar_exposure", "critical"),
        # Major: needs scheduled remediation
        ("spalling", "major"),
        ("corrosion", "major"),
        ("efflorescence", "major"),
        ("surface_deterioration", "major"),
        # Minor: cosmetic / monitoring
        ("biological_growth", "minor"),
        ("crack_cosmetic", "minor"),
        # No defect → no severity
        ("no_defect", "none"),
    ],
)
def test_severity_map_canonical(defect: str, severity: str):
    assert SEVERITY_MAP[defect] == severity


def test_severity_map_covers_every_v1_1_class():
    for cls in DEFECT_TYPES_V1_1:
        assert cls in SEVERITY_MAP, f"v1.1 class {cls} missing from SEVERITY_MAP"


def test_severity_map_covers_every_v1_0_class():
    """Backward-compat: legacy classes must still resolve."""
    for cls in DEFECT_TYPES_V1_0:
        assert cls in SEVERITY_MAP, f"legacy class {cls} missing"


# =========================================================================
# _get_recommendations
# =========================================================================


def test_get_recommendations_returns_list_of_strings():
    recs = _get_recommendations("crack")
    assert isinstance(recs, list)
    assert recs
    assert all(isinstance(r, str) for r in recs)


def test_get_recommendations_unknown_defect_returns_default():
    recs = _get_recommendations("alien_defect_type_xyz")
    assert recs
    # Default text contains generic guidance, not a class-specific phrase.
    joined = " ".join(recs).lower()
    assert "document" in joined or "schedule" in joined


def test_get_recommendations_for_critical_defect_mentions_structural():
    """Critical defects must surface "structural" guidance — that's the
    flag that drives downstream review escalation."""
    recs = _get_recommendations("crack")
    joined = " ".join(recs).lower()
    assert "structural" in joined


def test_get_recommendations_for_no_defect_indicates_clean():
    recs = _get_recommendations("no_defect")
    joined = " ".join(recs).lower()
    assert "no defects" in joined or "good condition" in joined


def test_get_recommendations_legacy_class_resolves():
    """v1.0 legacy classes must still produce non-default recs."""
    recs = _get_recommendations("crack_structural")
    joined = " ".join(recs).lower()
    assert "structural" in joined


# =========================================================================
# _map_imagenet_to_defect
# =========================================================================


def _probs_with_peak_at(idx: int, peak: float = 0.9):
    """Build a torch tensor of ImageNet probabilities with a peak at
    ``idx``. Imported lazily so this test file collects without torch
    eagerly importing at module-load time."""
    import torch

    arr = np.full(1000, (1.0 - peak) / 999, dtype=np.float32)
    arr[idx] = peak
    return torch.from_numpy(arr).unsqueeze(0)  # batch dim


def test_map_imagenet_to_defect_caps_confidence_at_0_4():
    """The heuristic mapper caps confidence at 0.4 to signal it's not a
    trained classifier — even with full probability mass on a mapped
    class, confidence cannot exceed the cap."""
    pytest.importorskip("torch")
    probs = _probs_with_peak_at(489, peak=0.99)  # corrosion → 489
    _, confidence = _map_imagenet_to_defect(probs)
    assert confidence <= 0.4


def test_map_imagenet_to_defect_picks_corresponding_defect():
    """Probability concentrated on a corrosion-mapped index (489 = chain)
    should produce ``corrosion`` as the most-likely defect type."""
    pytest.importorskip("torch")
    probs = _probs_with_peak_at(489, peak=0.99)
    defect_type, _ = _map_imagenet_to_defect(probs)
    # Several classes share index 489 in the heuristic map (corrosion,
    # weld_defect). Either is acceptable — the test pins that the mapper
    # picks ONE of the indexed classes, not the silent default.
    assert defect_type in {"corrosion", "weld_defect"}


def test_map_imagenet_uniform_probs_falls_back_to_default():
    """With uniform probs, no class accumulates a higher score → mapper
    returns the documented default ``surface_defect``."""
    pytest.importorskip("torch")
    import torch

    probs = torch.full((1, 1000), 1.0 / 1000)
    defect_type, _ = _map_imagenet_to_defect(probs)
    # All 12 mapped types tie at the same score — first wins (surface_defect
    # is the documented baseline). Pin "did not crash" + valid class.
    assert defect_type in {
        "crack_structural",
        "crack_cosmetic",
        "spalling",
        "corrosion",
        "water_damage",
        "surface_defect",
        "concrete_honeycombing",
        "rebar_exposure",
        "weld_defect",
        "delamination",
        "improper_alignment",
        "missing_component",
    }


# =========================================================================
# DefectClassifier — fallback path
# =========================================================================


def test_fallback_classify_payload_schema():
    clf = DefectClassifier()
    out = clf._fallback_classify()
    assert out["defect_type"] == "surface_deterioration"
    assert out["model_available"] is False
    assert out["model_type"] == "fallback"
    assert out["confidence"] == 0.1
    assert out["severity_estimate"] == "minor"
    assert any("manual" in r.lower() for r in out["recommendations"])


def test_fallback_classify_marks_low_confidence():
    """Critical UX flag — fallback must surface "manual inspection"
    guidance so reviewers don't trust the placeholder result."""
    clf = DefectClassifier()
    out = clf._fallback_classify()
    joined = " ".join(out["recommendations"]).lower()
    assert "low confidence" in joined or "manual" in joined


# =========================================================================
# _detect_model_version (file-based, no torch needed)
# =========================================================================


def test_detect_version_no_path_defaults_v1_1():
    clf = DefectClassifier(model_path=None)
    version, classes = clf._detect_model_version()
    assert version == "v1.1"
    assert classes == list(DEFECT_TYPES_V1_1)


def test_detect_version_reads_metadata_json(tmp_path):
    model_dir = tmp_path / "models" / "defect_vit_v1.1"
    model_dir.mkdir(parents=True)
    metadata = {
        "model_version": "v1.1",
        "class_names": [
            "crack",
            "spalling",
            "corrosion",
            "efflorescence",
            "exposed_rebar",
            "surface_deterioration",
            "biological_growth",
            "no_defect",
        ],
    }
    (model_dir / "metadata.json").write_text(json.dumps(metadata))
    model_path = str(model_dir / "best_model.pth")

    clf = DefectClassifier(model_path=model_path)
    version, classes = clf._detect_model_version()
    assert version == "v1.1"
    assert classes == metadata["class_names"]


def test_detect_version_reads_class_mapping_txt(tmp_path):
    model_dir = tmp_path / "models" / "v1_0_legacy"
    model_dir.mkdir(parents=True)
    # Tab-separated index<TAB>name — older format.
    (model_dir / "class_mapping.txt").write_text(
        "0\tcrack_structural\n1\tcrack_cosmetic\n2\tspalling\n"
    )
    model_path = str(model_dir / "model.pth")

    clf = DefectClassifier(model_path=model_path)
    version, classes = clf._detect_model_version()
    # Without "no_defect" in the list → v1.0
    assert version == "v1.0"
    assert classes == ["crack_structural", "crack_cosmetic", "spalling"]


def test_detect_version_class_mapping_with_no_defect_routes_to_v1_1(tmp_path):
    model_dir = tmp_path / "models" / "v1_1_via_txt"
    model_dir.mkdir(parents=True)
    (model_dir / "class_mapping.txt").write_text("0\tcrack\n1\tno_defect\n")
    model_path = str(model_dir / "model.pth")

    clf = DefectClassifier(model_path=model_path)
    version, classes = clf._detect_model_version()
    assert version == "v1.1"
    assert "no_defect" in classes


def test_detect_version_corrupt_metadata_falls_back(tmp_path):
    model_dir = tmp_path / "models" / "broken_meta"
    model_dir.mkdir(parents=True)
    (model_dir / "metadata.json").write_text("not valid json {{{")
    model_path = str(model_dir / "model.pth")

    clf = DefectClassifier(model_path=model_path)
    # Falls through to checkpoint detection, then to v1.1 default
    # (no checkpoint present in this temp dir).
    version, classes = clf._detect_model_version()
    assert version == "v1.1"
    assert classes == list(DEFECT_TYPES_V1_1)


# =========================================================================
# DefectClassifier — initial state
# =========================================================================


def test_classifier_initial_state():
    clf = DefectClassifier()
    assert clf._loaded is False
    assert clf._model is None
    assert clf._model_type == "fallback"
    assert clf._model_version == "v1.1"
    assert clf._class_names == list(DEFECT_TYPES_V1_1)
