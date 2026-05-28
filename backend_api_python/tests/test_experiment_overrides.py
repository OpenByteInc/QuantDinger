from app.services.experiment.overrides import enrich_experiment_overrides
from app.services.indicator_params import IndicatorParamsParser


def test_enrich_indicator_params_nested():
    raw = {
        "indicator_params.sma_short": 11,
        "indicator_params.sma_long": 35,
        "leverage": 2,
    }
    out = enrich_experiment_overrides(raw)
    assert out["indicatorParams"] == {"sma_short": 11, "sma_long": 35}
    assert out["indicator_params.sma_short"] == 11
    assert out["leverage"] == 2


def test_apply_defaults_to_code_updates_param_line():
    code = (
        "# @param sma_short int 14 短期均线周期\n"
        "# @param sma_long int 28 长期均线周期\n"
        "sma_short_period = int(params.get('sma_short', 14))\n"
    )
    patched = IndicatorParamsParser.apply_defaults_to_code(
        code, {"sma_short": 11, "sma_long": 35}
    )
    assert "# @param sma_short int 11" in patched
    assert "# @param sma_long int 35" in patched
    assert "params.get('sma_short', 14)" in patched
