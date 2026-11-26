import unittest
import os
from contextlib import contextmanager
from unittest.mock import Mock, patch

from flask import Flask
from app.services.settings import advertisements as service
from app.routes.settings import advertisements as route


def example(**changes):
    return {"id": "one", "name": "Partner", "image_url": "https://example.com/ad.png",
            "target_url": "https://example.com/signup", "position": "sidebar",
            "sort_order": 10, "enabled": True, **changes}


class AdvertisementTests(unittest.TestCase):
    @unittest.skipUnless(os.getenv('ADS_DB_TEST') == '1', 'Requires a PostgreSQL test connection')
    def test_database_roundtrip_in_temporary_table(self):
        from app.utils.db import get_db_connection
        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute('CREATE TEMP TABLE qd_advertisement_config (id SMALLINT PRIMARY KEY, items JSONB, revision INTEGER, updated_at TIMESTAMPTZ) ON COMMIT DROP')

            @contextmanager
            def connection():
                yield conn

            try:
                with patch.object(service, 'get_db_connection', connection), patch.object(conn, 'commit'):
                    self.assertEqual(service.read_advertisements(), {'items': [], 'revision': 0})
                    saved = service.save_advertisements([example()], 0)
                    self.assertEqual(service.read_advertisements(), saved)
                    with self.assertRaisesRegex(ValueError, 'conflict'):
                        service.save_advertisements([], 0)
                    self.assertEqual(service.save_advertisements([], 1), {'items': [], 'revision': 2})
                    self.assertEqual(service.public_advertisements(), [])
            finally:
                conn.rollback()

    def test_validation(self):
        self.assertEqual(service.validate_items([example()]), [example()])
        invalid = [example(name=""), example(position="other"), example(sort_order=True),
                   example(sort_order=10000), example(enabled="true"), example(target_url="javascript:alert(1)"),
                   example(image_url="https://u:p@example.com/x"), example(image_url="data:image/png;base64,a")]
        for item in invalid:
            with self.assertRaises(ValueError):
                service.validate_items([item])
        for items in [None, {}, [example(), example()], [example(id=str(i)) for i in range(41)]]:
            with self.assertRaises(ValueError):
                service.validate_items(items)

    def test_public_only_active_sorted(self):
        items = [example(), example(id="two", sort_order=0), example(id="off", enabled=False)]
        with patch.object(service, 'read_advertisements', return_value={"items": items}):
            self.assertEqual([item['id'] for item in service.public_advertisements()], ['two', 'one'])

    def test_save_revision_and_conflict(self):
        conn = Mock()
        cursor = conn.cursor.return_value
        cursor.fetchone.return_value = {"revision": 3}

        @contextmanager
        def connection():
            yield conn

        with patch.object(service, 'get_db_connection', connection):
            self.assertEqual(service.save_advertisements([example()], 2)['revision'], 3)
            conn.commit.assert_called_once()
            self.assertEqual(cursor.execute.call_args.args[1][1], 2)
            cursor.fetchone.return_value = None
            with self.assertRaisesRegex(ValueError, 'conflict'):
                service.save_advertisements([], 2)
            self.assertEqual(conn.commit.call_count, 1)

    def test_routes_require_admin_and_reject_bad_payload(self):
        app = Flask(__name__)
        app.add_url_rule('/ads', view_func=route, methods=['GET', 'PUT'])
        client = app.test_client()
        self.assertEqual(client.get('/ads').status_code, 401)
        with patch('app.utils.auth.verify_token', return_value={"user_id": 2, "_verified_user_role": "user"}):
            self.assertEqual(client.put('/ads', headers={"Authorization": "Bearer test"}, json={}).status_code, 403)
        with patch('app.utils.auth.verify_token', return_value={"user_id": 1, "_verified_user_role": "admin"}):
            headers = {"Authorization": "Bearer test"}
            self.assertEqual(client.put('/ads', headers=headers, json=[]).status_code, 400)
            with patch.object(service, 'read_advertisements', return_value={"items": [], "revision": 0}):
                self.assertEqual(client.get('/ads', headers=headers).json['data']['revision'], 0)
            with patch.object(service, 'save_advertisements', side_effect=ValueError('advertisements.conflict')):
                self.assertEqual(client.put('/ads', headers=headers, json={}).status_code, 409)


if __name__ == '__main__':
    unittest.main()
