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


def test_user_list_filter_supports_admin_filters():
    clause, params = UserService._build_user_list_filter(
        role="admin",
        status="disabled",
        vip_status="active",
    )

    assert "role = ?" in clause
    assert "status = ?" in clause
    assert "vip_expires_at > NOW()" in clause
    assert params == ["admin", "disabled"]


def test_user_list_filter_ignores_unknown_enum_values():
    clause, params = UserService._build_user_list_filter(
        role="owner",
        status="pending",
        vip_status="trial",
    )

    assert clause == ""
    assert params == []


def test_manager_role_is_no_longer_assignable():
    clause, params = UserService._build_user_list_filter(role="manager")

    assert "manager" not in UserService.ROLES
    assert "manager" not in UserService.ROLE_PERMISSIONS
    assert UserService.normalize_role("manager") == "user"
    assert UserService.normalize_user_record({"id": 7, "role": "manager"}) == {
        "id": 7,
        "role": "user",
    }
    assert UserService().get_user_permissions("manager") == UserService.ROLE_PERMISSIONS["user"]
    assert clause == ""
    assert params == []


def test_user_list_order_allowlists_credits_sort():
    assert UserService._build_user_list_order("credits", "asc") == "credits ASC, id DESC"
    assert UserService._build_user_list_order("credits", "desc") == "credits DESC, id DESC"
    assert UserService._build_user_list_order("email", "asc") == "id DESC"
