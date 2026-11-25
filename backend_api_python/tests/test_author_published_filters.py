from datetime import datetime

from app.services import community_service
from app.services.community_service import CommunityService


class _Cursor:
    def __init__(self):
        self.executions = []
        self.current_query = ''

    def execute(self, query, params=None):
        self.current_query = ' '.join(query.split())
        self.executions.append((self.current_query, tuple(params or ())))

    def fetchone(self):
        return {'count': 1}

    def fetchall(self):
        return [{
            'id': 42,
            'name': 'Adaptive Trend',
            'description': 'Trend overlay',
            'preview_image': '',
            'pricing_type': 'paid',
            'price': 1000,
            'vip_free': True,
            'purchase_count': 5,
            'avg_rating': 4.8,
            'rating_count': 3,
            'view_count': 20,
            'review_status': 'approved',
            'review_note': '',
            'asset_type': 'indicator',
            'source_description': '',
            'revenue': 4000,
            'created_at': datetime(2026, 11, 20, 8, 0, 0),
            'updated_at': datetime(2026, 11, 24, 9, 30, 0),
        }]

    def close(self):
        return None


class _Database:
    def __init__(self, cursor):
        self._cursor = cursor

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def cursor(self):
        return self._cursor


def test_author_published_applies_server_side_filters_and_updated_sort(monkeypatch):
    cursor = _Cursor()
    monkeypatch.setattr(community_service, 'get_db_connection', lambda: _Database(cursor))

    result = CommunityService().get_author_published(
        user_id=7,
        keyword='trend',
        asset_type='indicator',
        review_status='approved',
        sort_by='updated_at',
        sort_order='desc',
    )

    count_query, count_params = cursor.executions[0]
    list_query, list_params = cursor.executions[1]
    assert 'i.name ILIKE ?' in count_query
    assert "COALESCE(i.asset_type, 'indicator') = ?" in count_query
    assert "i.review_status = 'approved' OR i.review_status IS NULL" in count_query
    assert count_params == (7, '%trend%', '%trend%', '%trend%', 'indicator')
    assert 'ORDER BY COALESCE(i.updated_at, i.created_at) DESC' in list_query
    assert list_params[-2:] == (20, 0)
    assert result['items'][0]['created_at'] == '2026-11-20T08:00:00'
    assert result['items'][0]['updated_at'] == '2026-11-24T09:30:00'


def test_author_published_rejects_untrusted_sort_expressions(monkeypatch):
    cursor = _Cursor()
    monkeypatch.setattr(community_service, 'get_db_connection', lambda: _Database(cursor))

    CommunityService().get_author_published(
        user_id=7,
        sort_by='updated_at; DROP TABLE qd_indicator_codes',
        sort_order='sideways',
    )

    list_query = cursor.executions[1][0]
    assert 'DROP TABLE' not in list_query
    assert 'ORDER BY COALESCE(i.updated_at, i.created_at) DESC' in list_query
