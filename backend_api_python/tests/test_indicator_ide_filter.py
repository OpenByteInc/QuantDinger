"""Indicator IDE list should exclude script strategies."""

from __future__ import annotations

from app.services.indicator_workspace import (
    is_indicator_ide_listable,
    is_script_strategy_code,
    resolve_indicator_asset_type,
)

SCRIPT_SNIPPET = """
def on_init(ctx):
    ctx.param('fast', 10)

def on_bar(ctx, bar):
    ctx.buy()
"""

INDICATOR_SNIPPET = """
my_indicator_name = "SMA"
my_indicator_description = "Simple MA"
df = df.copy()
output = {'name': my_indicator_name, 'plots': [], 'signals': []}
"""


def test_is_script_strategy_code_detects_on_bar():
    assert is_script_strategy_code(SCRIPT_SNIPPET) is True
    assert is_script_strategy_code(INDICATOR_SNIPPET) is False


def test_is_indicator_ide_listable_excludes_script_rows():
    assert is_indicator_ide_listable(code=INDICATOR_SNIPPET, asset_type="indicator") is True
    assert is_indicator_ide_listable(code=SCRIPT_SNIPPET, asset_type="indicator") is False
    assert is_indicator_ide_listable(code=INDICATOR_SNIPPET, asset_type="script_template") is False
    assert is_indicator_ide_listable(code=INDICATOR_SNIPPET, asset_type="bot_preset") is False


def test_resolve_indicator_asset_type_reclassifies_script():
    assert resolve_indicator_asset_type(INDICATOR_SNIPPET) == "indicator"
    assert resolve_indicator_asset_type(SCRIPT_SNIPPET) == "script_template"
    assert resolve_indicator_asset_type(SCRIPT_SNIPPET, "bot_preset") == "bot_preset"


def test_link_indicator_config_skips_script_code(monkeypatch):
    from app.services.indicator_workspace import link_indicator_config

    called = {"save": False}

    def _fake_save(**kwargs):
        called["save"] = True
        return 99

    monkeypatch.setattr(
        "app.services.indicator_workspace.save_user_indicator",
        lambda **kwargs: _fake_save(**kwargs),
    )

    out = link_indicator_config(1, {"indicator_code": SCRIPT_SNIPPET})
    assert called["save"] is False
    assert "indicator_id" not in out
