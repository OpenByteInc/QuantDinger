"""
AI chat API routes (optional).
Currently kept as a minimal compatibility layer for legacy frontend calls.
"""

from flask import jsonify, request
from app.openapi.blueprint import HumanBlueprint as Blueprint

from app.utils.logger import get_logger

logger = get_logger(__name__)

ai_chat_blp = Blueprint('ai_chat', __name__)


@ai_chat_blp.route('/chat/message', methods=['POST'])
def chat_message():
    """
    Minimal placeholder for legacy chat.
    Return a friendly message instead of 404, so the UI can evolve gradually.
    """
    data = request.get_json() or {}
    msg = (data.get('message') or '').strip()
    if not msg:
        return jsonify({'code': 0, 'msg': 'Missing message', 'data': None}), 400
    return jsonify({
        'code': 1,
        'msg': 'success',
        'data': {
            'reply': 'Chat API is not implemented yet in local-only mode.',
            'echo': msg
        }
    })


@ai_chat_blp.route('/chat/history', methods=['GET'])
def get_chat_history():
    """Return empty history (compatibility stub)."""
    return jsonify({'code': 1, 'msg': 'success', 'data': []})


@ai_chat_blp.route('/chat/history/save', methods=['POST'])
def save_chat_history():
    """No-op save (compatibility stub)."""
    return jsonify({'code': 1, 'msg': 'success', 'data': None})



# openapi-compat: legacy import name
ai_chat_bp = ai_chat_blp
