from __future__ import annotations

import tempfile
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from app._version import FALLBACK_VERSION, normalize_version, resolve_app_version


@contextmanager
def local_temp_dir() -> Iterator[Path]:
    base = Path(__file__).resolve().parents[1] / ".test-tmp"
    base.mkdir(exist_ok=True)
    try:
        with tempfile.TemporaryDirectory(dir=base) as name:
            yield Path(name)
    finally:
        try:
            base.rmdir()
        except OSError:
            pass


def test_normalize_version_accepts_common_tag_formats():
    assert normalize_version("v3.0.23") == "3.0.23"
    assert normalize_version("refs/tags/v3.0.23") == "3.0.23"
    assert normalize_version("refs/heads/main") == "main"
    assert normalize_version("3.0.23") == "3.0.23"


def test_explicit_app_version_wins():
    with local_temp_dir() as tmp_path:
        (tmp_path / "VERSION").write_text("3.0.10\n", encoding="utf-8")

        assert (
            resolve_app_version(
                {"APP_VERSION": "v3.1.0", "GIT_TAG": "v3.0.99"},
                repo_root=tmp_path,
                app_root=tmp_path,
                use_git=False,
            )
            == "3.1.0"
        )


def test_build_stamp_is_used_after_explicit_env():
    with local_temp_dir() as tmp_path:
        (tmp_path / "BUILD_VERSION").write_text("3.1.5\n", encoding="utf-8")

        assert resolve_app_version({}, repo_root=tmp_path, app_root=tmp_path, use_git=False) == "3.1.5"


def test_git_tag_env_is_used_when_app_version_absent():
    with local_temp_dir() as tmp_path:
        assert (
            resolve_app_version(
                {"GITHUB_REF": "refs/tags/v3.2.1"},
                repo_root=tmp_path,
                app_root=tmp_path,
                use_git=False,
            )
            == "3.2.1"
        )


def test_floating_tag_falls_back_to_version_file():
    with local_temp_dir() as tmp_path:
        (tmp_path / "VERSION").write_text("3.0.22\n", encoding="utf-8")

        assert (
            resolve_app_version({"GIT_TAG": "latest"}, repo_root=tmp_path, app_root=tmp_path, use_git=False)
            == "3.0.22"
        )


def test_branch_ref_falls_back_to_version_file():
    with local_temp_dir() as tmp_path:
        (tmp_path / "VERSION").write_text("3.0.22\n", encoding="utf-8")

        assert (
            resolve_app_version({"GITHUB_REF": "refs/heads/main"}, repo_root=tmp_path, app_root=tmp_path, use_git=False)
            == "3.0.22"
        )


def test_missing_sources_return_dev_fallback():
    with local_temp_dir() as tmp_path:
        assert resolve_app_version({}, repo_root=tmp_path, app_root=tmp_path, use_git=False) == FALLBACK_VERSION
