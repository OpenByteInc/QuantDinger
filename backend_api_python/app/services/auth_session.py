"""Shared authentication session helpers."""

from ipaddress import ip_address
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse

from flask import request

from app.utils.auth import generate_token
from app.utils.logger import get_logger

logger = get_logger(__name__)
CLIENT_TYPES = frozenset({"pc", "mobile"})


def normalize_client_type(value, default: str = "pc") -> str:
    """Normalize a client session class, falling back to the desktop slot."""
    normalized = str(value or "").strip().lower()
    if normalized in CLIENT_TYPES:
        return normalized
    return default


def get_login_client_type(data: dict = None) -> str:
    """Resolve the login client class from a request body or header."""
    body_value = (data or {}).get("client_type")
    return normalize_client_type(body_value or request.headers.get("X-Client-Type"))


def build_frontend_login_redirect(frontend_url: str, **params) -> str:
    """Build a frontend login redirect URL for OAuth flows."""
    base = (frontend_url or "").strip().rstrip("/")
    if not base:
        base = "http://localhost:8080"

    candidate = base if "://" in base else f"https://{base}"
    try:
        parsed = urlparse(candidate)
    except Exception:
        parsed = None

    origin = base
    has_real_path = False
    has_hash_route = False
    if parsed and parsed.scheme and parsed.netloc:
        origin = f"{parsed.scheme}://{parsed.netloc}".rstrip("/")
        has_hash_route = bool(parsed.fragment)
        path_part = (parsed.path or "").rstrip("/")
        has_real_path = bool(path_part)

    clean_params = {k: v for k, v in params.items() if v is not None and v != ""}
    qs = urlencode(clean_params)

    if has_hash_route:
        login_url = f"{origin}/#/user/login"
        return f"{login_url}?{qs}" if qs else login_url

    if has_real_path:
        existing_qs = dict(parse_qsl(parsed.query or "", keep_blank_values=True))
        existing_qs.update(clean_params)
        return urlunparse((
            parsed.scheme,
            parsed.netloc,
            parsed.path,
            parsed.params,
            urlencode(existing_qs),
            "",
        ))

    login_url = f"{origin}/#/user/login"
    return f"{login_url}?{qs}" if qs else login_url


def get_client_ip() -> str:
    """Get the client IP, trusting forwarding headers only from an internal proxy."""
    remote_addr = (request.remote_addr or "0.0.0.0").strip()
    try:
        remote_ip = ip_address(remote_addr)
    except ValueError:
        return remote_addr

    if not (remote_ip.is_private or remote_ip.is_loopback):
        return remote_addr

    candidate = (request.headers.get("X-Real-IP") or "").strip()
    if not candidate:
        forwarded_for = request.headers.get("X-Forwarded-For") or ""
        candidate = forwarded_for.rsplit(",", 1)[-1].strip()

    try:
        return str(ip_address(candidate)) if candidate else remote_addr
    except ValueError:
        return remote_addr


def get_user_agent() -> str:
    """Get a bounded user agent string from the request."""
    return request.headers.get("User-Agent", "")[:500]


def build_userinfo(user: dict, user_id: int, username: str, permissions: list) -> dict:
    """Build the frontend userinfo payload."""
    return {
        "id": user.get("id") or user.get("user_id", user_id),
        "username": user.get("username", username),
        "nickname": user.get("nickname", "User"),
        "email": user.get("email"),
        "avatar": user.get("avatar", "/avatar2.jpg"),
        "timezone": str(user.get("timezone") or "").strip(),
        "role": {
            "id": user.get("role", "admin"),
            "permissions": permissions,
        },
        "must_change_initial_password": must_change_initial_password(user_id),
    }


def issue_login_token(
    user: dict,
    user_id: int,
    username: str,
    permissions: list,
    client_type: str = "pc",
) -> tuple:
    """Replace one client session slot, issue JWT, and build userinfo."""
    from app.services.user_service import get_user_service

    client_type = normalize_client_type(client_type)
    service = get_user_service()
    try:
        token_version = service.get_token_version(user_id)
        client_token_version = service.increment_client_token_version(user_id, client_type)
    except Exception as exc:
        logger.warning("Failed to replace %s client session: %s", client_type, exc)
        client_token_version = 0

    if client_token_version < 1:
        return None, build_userinfo(user, user_id, username, permissions)

    token = generate_token(
        user_id=user_id,
        username=user.get("username", username),
        role=user.get("role", "admin"),
        token_version=token_version,
        client_type=client_type,
        client_token_version=client_token_version,
    )
    return token, build_userinfo(user, user_id, username, permissions)


def touch_last_login(user_id: int) -> None:
    """Best-effort last-login timestamp update."""
    try:
        from app.services.user_service import get_user_service

        get_user_service().touch_last_login(int(user_id))
    except Exception as exc:
        logger.warning("Failed to touch last_login_at for user %s: %s", user_id, exc)


def must_change_initial_password(user_id: int) -> bool:
    """Whether the UI should prompt for bootstrap password rotation."""
    try:
        from app.services.user_service import get_user_service

        return get_user_service().must_change_initial_password(int(user_id))
    except Exception:
        return False
