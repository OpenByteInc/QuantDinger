from __future__ import annotations

import pytest

from app.services import ai_skill_registry


def test_installed_prompt_skill_path_cannot_escape_registry(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(ai_skill_registry, "USER_SKILLS_DIR", tmp_path)

    with pytest.raises(ValueError, match="invalid skill id"):
        ai_skill_registry._skill_path("../outside")


def test_installed_prompt_skill_path_stays_inside_registry(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(ai_skill_registry, "USER_SKILLS_DIR", tmp_path)

    assert ai_skill_registry._skill_path("safe_prompt") == (tmp_path / "safe_prompt.json").resolve()
