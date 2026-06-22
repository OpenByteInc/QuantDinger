"""Shared strategy blueprint.

Strategy routes are split across modules while preserving the same blueprint
and URL prefixes registered by `app.openapi.register`.
"""
from app.openapi.blueprint import HumanBlueprint as Blueprint


strategy_blp = Blueprint('strategy', __name__)
