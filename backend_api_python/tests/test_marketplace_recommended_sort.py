from app.services import community_service
from app.services.community_service import CommunityService, _interleave_recommended_ids
from app.services.community_kpis import summarise_backtest_runs


def test_recommended_sort_places_one_recent_listing_after_each_mature_pair():
    assert _interleave_recommended_ids(
        mature_ids=[1, 2, 3, 4, 5, 6],
        new_ids=[101, 102, 103],
    ) == [1, 2, 101, 3, 4, 102, 5, 6, 103]


def test_recommended_sort_deduplicates_and_fills_from_the_available_bucket():
    assert _interleave_recommended_ids(
        mature_ids=[1, 2, 3, 4],
        new_ids=[4, 5],
    ) == [1, 2, 4, 3, 5]
    assert _interleave_recommended_ids([], [8, 7, 6]) == [8, 7, 6]
    assert _interleave_recommended_ids([8, 7, 6], []) == [8, 7, 6]


def test_marketplace_recommended_catalog_uses_hot_hot_new_order(monkeypatch):
    def listing_row(listing_id):
        return {
            'id': listing_id,
            'name': f'Listing {listing_id}',
            'description': '',
            'pricing_type': 'free',
            'price': 0,
            'vip_free': False,
            'code_hidden': False,
            'asset_type': 'indicator',
            'source_script_source_id': None,
            'source_strategy_id': None,
            'source_code_hash': None,
            'preview_image': '',
            'purchase_count': 0,
            'avg_rating': 0,
            'rating_count': 0,
            'view_count': 0,
            'created_at': None,
            'updated_at': None,
            'source_language': 'en-US',
            'name_i18n': None,
            'description_i18n': None,
            'marketplace_kpis': None,
            'marketplace_contract': None,
            'marketplace_binding_mode': '',
            'marketplace_strategy_type': '',
            'marketplace_direction_mode': '',
            'marketplace_execution_mode': 'bar',
            'marketplace_execution_frequency': '',
            'marketplace_confirmation_frequencies': '',
            'source_description': '',
            'author_id': 10,
            'author_username': 'author',
            'author_nickname': 'Author',
            'author_avatar': '',
        }

    all_assets = [
        {'id': 1, 'asset_type': 'indicator', 'purchase_count': 40, 'view_count': 100},
        {'id': 2, 'asset_type': 'indicator', 'purchase_count': 30, 'view_count': 80},
        {'id': 3, 'asset_type': 'indicator', 'purchase_count': 0, 'view_count': 2},
        {'id': 4, 'asset_type': 'indicator', 'purchase_count': 0, 'view_count': 1},
    ]
    rows_by_id = {listing_id: listing_row(listing_id) for listing_id in range(1, 5)}

    class Cursor:
        def __init__(self):
            self.current = ''

        def execute(self, query, _params=None):
            self.current = ' '.join(query.split())

        def fetchone(self):
            if 'SUM(CASE' in self.current:
                return {
                    'indicator_count': 4,
                    'script_template_count': 0,
                    'new_indicator_count': 2,
                    'new_script_template_count': 0,
                }
            if 'SELECT COUNT(*)' in self.current:
                return {'count': 4}
            return None

        def fetchall(self):
            if "INTERVAL '14 days'" in self.current:
                return [{'id': 4}, {'id': 3}]
            if 'WHERE i.id IN' in self.current:
                return [rows_by_id[3], rows_by_id[1], rows_by_id[4], rows_by_id[2]]
            if 'COALESCE(i.purchase_count, 0) as purchase_count' in self.current:
                return all_assets
            return []

        def close(self):
            return None

    class Database:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def cursor(self):
            return Cursor()

        def commit(self):
            return None

    empty_kpis = summarise_backtest_runs([])
    monkeypatch.setattr(community_service, 'get_db_connection', lambda: Database())
    monkeypatch.setattr(
        community_service,
        'fetch_market_asset_kpis',
        lambda _cur, rows, persist_missing=True: {
            int(row['id']): dict(empty_kpis) for row in rows
        },
    )

    result = CommunityService.__new__(CommunityService).get_market_indicators(
        page=1,
        page_size=24,
        sort_by='recommended',
        asset_type='indicator',
    )

    assert [item['id'] for item in result['items']] == [1, 2, 4, 3]
