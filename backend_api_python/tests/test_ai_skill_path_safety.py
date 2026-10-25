from __future__ import annotations

import json
import re

import pytest

from app.services import ai_skill_registry


def test_installed_prompt_skill_path_cannot_escape_registry(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(ai_skill_registry, "USER_SKILLS_DIR", tmp_path)

    with pytest.raises(ValueError, match="invalid skill id"):
        ai_skill_registry._stored_skill_path("../outside")


def test_installed_prompt_skill_path_stays_inside_registry(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(ai_skill_registry, "USER_SKILLS_DIR", tmp_path)

    path = ai_skill_registry._new_skill_path()
    assert path.parent == tmp_path.resolve()
    assert re.fullmatch(r"[0-9a-f]{32}\.json", path.name)


def test_installed_prompt_skill_finds_legacy_filename(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(ai_skill_registry, "USER_SKILLS_DIR", tmp_path)
    legacy = tmp_path / "safe_prompt.json"
    legacy.write_text(json.dumps({"id": "safe_prompt"}), encoding="utf-8")

    assert ai_skill_registry._stored_skill_path("safe_prompt") == legacy.resolve()


def test_installed_prompt_skill_lifecycle_uses_opaque_filename(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(ai_skill_registry, "USER_SKILLS_DIR", tmp_path)
    payload = {
        "id": "safe_prompt",
        "kind": "prompt",
        "label": {"en": "Safe"},
        "prompt_template": "Review {symbol_label}",
    }

    ai_skill_registry.install_prompt_skill(payload)
    path = ai_skill_registry._stored_skill_path("safe_prompt")
    assert path is not None
    assert path.parent == tmp_path.resolve()
    assert path.name != "safe_prompt.json"

    updated = ai_skill_registry.set_skill_enabled("safe_prompt", False)
    assert updated["enabled"] is False
    ai_skill_registry.delete_installed_skill("safe_prompt")
    assert not path.exists()
