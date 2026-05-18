"""Tests for the vision worker's path-jail and security helpers.

The Celery tasks themselves (frame processing) need a real GPU + model
file; not testable here. The non-task helpers — model-path resolution
and the jail check — are pure and contain real attack-surface code, so
those are pinned by tests.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from app.workers.vision_worker import _models_root, _resolve_model_path


@pytest.fixture
def fake_models_root(tmp_path, monkeypatch):
    """Stand up a temporary models root with a real file inside it.

    The resolver interprets relative paths against ``root.parent`` (the
    jail root's parent), so callers pass strings like ``"models/best.pt"``.
    To make that work in tests we use a structure ``<tmp_path>/models/...``
    and point MODELS_ROOT at the inner ``models`` directory.

    Returns ``(root, file, relative)`` — root is the jail (e.g.
    ``<tmp>/models``), file is a real artifact under it, and ``relative``
    is the canonical relative path to pass to the resolver.
    """
    root = tmp_path / "models"
    root.mkdir()
    monkeypatch.setenv("MODELS_ROOT", str(root))
    artifact = root / "best.pt"
    artifact.write_bytes(b"\x00")
    return root, artifact, "models/best.pt"


# ---- _models_root --------------------------------------------------------


def test_models_root_uses_env_override(monkeypatch, tmp_path):
    monkeypatch.setenv("MODELS_ROOT", str(tmp_path))
    assert _models_root() == tmp_path.resolve()


def test_models_root_falls_back_to_repo_relative(monkeypatch):
    monkeypatch.delenv("MODELS_ROOT", raising=False)
    root = _models_root()
    # Should land at <something>/models on disk.
    assert root.name == "models"


# ---- _resolve_model_path --------------------------------------------------


def test_resolve_model_path_accepts_file_inside_root(fake_models_root):
    _root, artifact, rel = fake_models_root
    resolved = _resolve_model_path(rel)
    assert resolved == artifact.resolve()


def test_resolve_model_path_accepts_absolute_path_inside_root(fake_models_root):
    _root, artifact, _rel = fake_models_root
    resolved = _resolve_model_path(str(artifact))
    assert resolved == artifact.resolve()


def test_resolve_model_path_rejects_escape_via_dotdot(fake_models_root, tmp_path):
    """``../`` traversal must be caught even though .. has a real
    meaning on the filesystem — the resolved path is checked against
    the jail root."""
    # Create a file outside the jail
    outside = tmp_path.parent / f"{tmp_path.name}-evil.pt"
    outside.write_bytes(b"\x00")
    try:
        with pytest.raises(ValueError, match="outside of models root"):
            _resolve_model_path(f"../../{outside.name}")
    finally:
        outside.unlink()


def test_resolve_model_path_rejects_absolute_path_outside_root(fake_models_root, tmp_path):
    """Even fully-qualified attacker-controlled paths get rejected if
    they don't sit inside the configured root."""
    outside = tmp_path.parent / f"{tmp_path.name}-other.pt"
    outside.write_bytes(b"\x00")
    try:
        with pytest.raises(ValueError, match="outside of models root"):
            _resolve_model_path(str(outside))
    finally:
        outside.unlink()


def test_resolve_model_path_rejects_missing_file(fake_models_root):
    """Path is inside the jail but no file present — operator typo or
    a bad task payload pointing at a deleted artifact. Surface as
    ValueError, don't silently return."""
    with pytest.raises(ValueError, match="does not point to a file"):
        _resolve_model_path("models/nope.pt")


def test_resolve_model_path_rejects_directory_in_root(fake_models_root):
    """A directory matching the name shouldn't be loaded as a model file."""
    root, _, _ = fake_models_root
    (root / "subdir").mkdir()
    with pytest.raises(ValueError, match="does not point to a file"):
        _resolve_model_path("models/subdir")


def test_resolve_model_path_resolves_pathlike_input(fake_models_root):
    """The signature accepts ``str | os.PathLike[str]`` — tests both."""
    _root, artifact, rel = fake_models_root
    resolved = _resolve_model_path(Path(rel))
    assert resolved == artifact.resolve()


def test_resolve_model_path_rejects_symlink_pointing_outside(fake_models_root, tmp_path):
    """A symlink in the jail pointing at an attacker-controlled file
    elsewhere is the classic symlink-escape attack — Path.resolve()
    follows the link, so the relative_to check catches it."""
    root, _, _ = fake_models_root
    outside = tmp_path.parent / f"{tmp_path.name}-target.pt"
    outside.write_bytes(b"\x00")
    link = root / "link.pt"
    try:
        # Symlink may need privileges on Windows; skip rather than fail.
        try:
            os.symlink(outside, link)
        except (OSError, NotImplementedError):
            pytest.skip("symlinks unavailable in this test environment")
        with pytest.raises(ValueError, match="outside of models root"):
            _resolve_model_path("models/link.pt")
    finally:
        if link.exists():
            link.unlink()
        if outside.exists():
            outside.unlink()
