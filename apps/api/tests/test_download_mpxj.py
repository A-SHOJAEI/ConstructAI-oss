"""Tests for the MPXJ JAR downloader.

Pin the version + Maven Central URL contract, the SSRF guard
that refuses non-Maven URLs, and the idempotency (skip download
when JAR already present).
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest

from app.services.scheduling.download_mpxj import (
    JAR_URL,
    LIB_DIR,
    MAVEN_BASE,
    MPXJ_VERSION,
    download_mpxj_jars,
)

# =========================================================================
# Constants — pin documented version and source
# =========================================================================


def test_mpxj_version_pinned():
    """[contract] MPXJ version is pinned. Pin so a refactor doesn't
    silently bump to a version with a different XER/MPP parser
    behavior — schedule import results would change."""
    assert MPXJ_VERSION == "13.4.0"


def test_maven_base_canonical():
    """[security/contract] Maven base URL pinned to repo1.maven.org —
    refactor must NOT swap to a third-party mirror without explicit
    review."""
    assert MAVEN_BASE == "https://repo1.maven.org/maven2/net/sf/mpxj/mpxj"


def test_jar_url_constructed_from_version():
    """JAR_URL embeds the version twice — both should match."""
    assert MPXJ_VERSION in JAR_URL
    assert f"{MAVEN_BASE}/{MPXJ_VERSION}/mpxj-{MPXJ_VERSION}.jar" == JAR_URL


def test_jar_url_uses_https():
    """[security] urllib.request.urlretrieve over HTTPS only — pin
    so a refactor doesn't downgrade to plaintext HTTP."""
    assert JAR_URL.startswith("https://")


def test_lib_dir_alongside_module():
    """[contract] lib/ directory next to download_mpxj.py — keeps the
    JARs colocated with the schedule_importer that loads them."""
    assert LIB_DIR.name == "lib"
    assert LIB_DIR.parent.name == "scheduling"


# =========================================================================
# download_mpxj_jars — idempotency + SSRF guard
# =========================================================================


def test_download_skips_when_jar_already_present(tmp_path: Path):
    """Idempotent: existing JAR -> skip download, return path."""
    target = tmp_path
    existing = target / f"mpxj-{MPXJ_VERSION}.jar"
    existing.write_bytes(b"already here")

    with patch("app.services.scheduling.download_mpxj.urllib.request.urlretrieve") as fake_get:
        out = download_mpxj_jars(target_dir=target)

    assert out == existing
    fake_get.assert_not_called()


def test_download_triggers_when_jar_missing(tmp_path: Path):
    """Missing JAR -> urlretrieve called with the canonical URL."""
    target = tmp_path

    def fake_retrieve(url: str, dest: str) -> None:
        Path(dest).write_bytes(b"fake jar content")

    with patch(
        "app.services.scheduling.download_mpxj.urllib.request.urlretrieve",
        side_effect=fake_retrieve,
    ) as fake_get:
        out = download_mpxj_jars(target_dir=target)

    fake_get.assert_called_once()
    args, _kwargs = fake_get.call_args
    assert args[0] == JAR_URL
    assert out.exists()
    assert out.name == f"mpxj-{MPXJ_VERSION}.jar"


def test_download_creates_target_dir_if_missing(tmp_path: Path):
    """Target dir doesn't exist -> created (mkdir parents=True)."""
    target = tmp_path / "deeply" / "nested" / "lib"
    assert not target.exists()

    def fake_retrieve(url: str, dest: str) -> None:
        Path(dest).write_bytes(b"")

    with patch(
        "app.services.scheduling.download_mpxj.urllib.request.urlretrieve",
        side_effect=fake_retrieve,
    ):
        download_mpxj_jars(target_dir=target)

    assert target.exists()


def test_download_default_target_is_lib_dir():
    """[contract] target_dir=None -> uses module-relative ``lib/``."""
    # Patch urlretrieve so we don't actually download. Patch mkdir
    # to avoid creating the real directory.
    with (
        patch("app.services.scheduling.download_mpxj.urllib.request.urlretrieve") as fake_get,
        patch.object(Path, "exists", return_value=True),
    ):
        out = download_mpxj_jars()

    # When the existing-file branch is taken, no download happens:
    fake_get.assert_not_called()
    assert out.parent == LIB_DIR


def test_download_refuses_non_maven_url(tmp_path: Path, monkeypatch):
    """[security/SSRF] If JAR_URL is overridden to a non-Maven host,
    the function raises ValueError instead of fetching. Pin so a
    config-injection vector can't redirect the download to an
    attacker-controlled URL."""
    # Patch JAR_URL to a non-Maven URL via monkeypatch (the SSRF
    # guard checks the constant at call time):
    monkeypatch.setattr(
        "app.services.scheduling.download_mpxj.JAR_URL",
        "https://attacker.example.com/mpxj-13.4.0.jar",
    )

    with pytest.raises(ValueError, match="non-Maven URL"):
        download_mpxj_jars(target_dir=tmp_path)


def test_download_returns_path_object(tmp_path: Path):
    """[contract] Return value is a pathlib.Path (not a string)."""
    existing = tmp_path / f"mpxj-{MPXJ_VERSION}.jar"
    existing.write_bytes(b"x")

    out = download_mpxj_jars(target_dir=tmp_path)
    assert isinstance(out, Path)
