"""
Experiment orchestration API routes.
"""

import json
import queue
import threading
import time

from flask import Response, g, jsonify, request
from app.openapi.blueprint import HumanBlueprint as Blueprint

from app.services.experiment.runner import ExperimentRunnerService
from app.services.billing_service import get_billing_service
from app.utils.auth import login_required
from app.utils.logger import get_logger

logger = get_logger(__name__)

experiment_blp = Blueprint('experiment', __name__)
experiment_runner = ExperimentRunnerService()


@experiment_blp.route('/ai-optimize', methods=['POST'])
@login_required
def ai_optimize():
    """
    LLM-driven multi-round optimization pipeline with SSE progress streaming.

    The endpoint returns an SSE stream. Each event is one of:
      - event: progress   (partial update per round)
      - event: done       (final result)
      - event: error      (pipeline failure)
    """
    payload = request.get_json() or {}
    if not payload:
        return jsonify({'code': 0, 'msg': 'Request body is required', 'data': None}), 400

    user_id = int(g.user_id or 1)
    billing = get_billing_service()
    ok, billing_msg = billing.check_and_consume(
        user_id=user_id,
        feature='ai_tuning',
        reference_id=f"ai_tuning_{user_id}_{int(time.time())}",
    )
    if not ok:
        return Response(
            _sse('error', {'msg': f'Insufficient credits: {billing_msg}'}),
            mimetype='text/event-stream',
            headers={'Cache-Control': 'no-cache', 'X-Accel-Buffering': 'no'},
        )

    progress_queue: queue.Queue = queue.Queue()

    def on_progress(data):
        progress_queue.put(data)

    def run():
        try:
            result = experiment_runner.run_ai_pipeline(
                user_id=user_id,
                payload=payload,
                on_progress=on_progress,
            )
            progress_queue.put({'event': '__final__', 'data': result})
        except Exception as exc:
            logger.error("ai_optimize pipeline failed", exc_info=True)
            progress_queue.put({'event': '__error__', 'msg': str(exc)})

    worker = threading.Thread(target=run, daemon=True)
    worker.start()

    def generate():
        while True:
            try:
                item = progress_queue.get(timeout=600)
            except queue.Empty:
                yield _sse('error', {'msg': 'Pipeline timeout'})
                break

            if item.get('event') == '__final__':
                yield _sse('done', item['data'])
                break
            elif item.get('event') == '__error__':
                yield _sse('error', {'msg': item.get('msg', 'Unknown error')})
                break
            else:
                yield _sse('progress', item)

    return Response(generate(), mimetype='text/event-stream',
                    headers={'Cache-Control': 'no-cache', 'X-Accel-Buffering': 'no'})


@experiment_blp.route('/structured-tune', methods=['POST'])
@login_required
def structured_tune():
    """
    Grid or random search over explicit parameterSpace (no LLM).
    Same response shape as ai-optimize-sync for IDE compatibility.
    """
    payload = request.get_json() or {}
    if not payload:
        return jsonify({'code': 0, 'msg': 'Request body is required', 'data': None}), 400

    try:
        data = experiment_runner.run_structured_tune(
            user_id=int(g.user_id or 1),
            payload=payload,
        )
        return jsonify({'code': 1, 'msg': 'success', 'data': data})
    except ValueError as exc:
        return jsonify({'code': 0, 'msg': str(exc), 'data': None}), 400
    except Exception as exc:
        logger.error("structured_tune failed", exc_info=True)
        return jsonify({'code': 0, 'msg': str(exc), 'data': None}), 400


def _sse(event: str, data) -> str:
    return f"event: {event}\ndata: {json.dumps(data, default=str, ensure_ascii=False)}\n\n"

# openapi-compat: legacy import name
experiment_bp = experiment_blp
