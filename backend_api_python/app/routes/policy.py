"""
Policy / capability discovery routes.

Read-only endpoints that expose backend policy matrices to the frontend so
the UI does not have to hard-code its own copy of broker x market rules.
The frontend fetches these once at app boot and caches them in
sessionStorage; nothing here is per-user, so caching is safe.
"""
from flask import jsonify
from app.openapi.blueprint import HumanBlueprint as Blueprint

from app.services.broker_market_policy import to_dict as broker_market_policy_dict


policy_blp = Blueprint('policy', __name__)


@policy_blp.route('/broker-market', methods=['GET'])
def get_broker_market_policy():
    """Return the full broker x market x market_type compatibility matrix.

    Response shape (kept stable for the frontend):
      {
        "code": 1,
        "data": {
          "broker_markets": {
              "ibkr":   {"USStock": ["spot"]},
              "mt5":    {"Forex":   ["spot"]},
              "alpaca": {"USStock": ["spot"], "Crypto": ["spot"]},
              "binance": {"Crypto":  ["spot", "swap"]},
              ...
          },
          "long_only_brokers": ["alpaca", "ibkr"],
          "bot_type_markets": {
              "grid":       ["Crypto", "Forex"],
              "martingale": ["Crypto"],
              "dca":        ["Crypto", "Forex", "USStock"],
              "trend":      ["Crypto", "Forex", "USStock"]
          },
          "live_market_categories": ["Crypto", "Forex", "USStock"]
        }
      }
    """
    return jsonify({"code": 1, "data": broker_market_policy_dict()})

# openapi-compat: legacy import name
policy_bp = policy_blp
