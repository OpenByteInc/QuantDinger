"""Admin user/strategy list ID-aware search helpers."""

from app.services.user_service import UserService


def test_user_list_filter_exact_id_only():
    clause, params = UserService._build_user_list_filter(user_id=42)
    assert clause == "WHERE id = ?"
    assert params == [42]


def test_user_list_filter_search_numeric_includes_id():
    clause, params = UserService._build_user_list_filter(search="2062")
    assert "id = ?" in clause
    assert "username ILIKE ?" in clause
    assert 2062 in params


def test_user_list_filter_search_text_no_exact_id():
    clause, params = UserService._build_user_list_filter(search="alice")
    assert "id = ?" not in clause
    assert "username ILIKE ?" in clause
    assert "%alice%" in params


def test_user_list_filter_combined_id_and_search():
    clause, params = UserService._build_user_list_filter(search="bob", user_id=7)
    assert clause.startswith("WHERE id = ? AND (")
    assert params[0] == 7
