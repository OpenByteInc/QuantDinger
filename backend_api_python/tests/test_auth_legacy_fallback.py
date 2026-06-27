def test_legacy_admin_fallback_requires_single_user_mode(client, monkeypatch):
    import app.routes.auth as auth_route
    import app.services.user_service as user_service_mod

    class DummySecurity:
        def verify_turnstile(self, token, ip_address):
            return True, "ok"

        def check_login_allowed(self, username, ip_address):
            return True, ""

        def record_login_attempt(self, *args, **kwargs):
            return True

        def log_security_event(self, *args, **kwargs):
            return True

    class DummyUsers:
        def authenticate(self, username, password, update_last_login=False):
            return None

    monkeypatch.setattr("app.services.security_service.get_security_service", lambda: DummySecurity())
    monkeypatch.setattr(user_service_mod, "get_user_service", lambda: DummyUsers())
    monkeypatch.setattr(auth_route, "_is_single_user_mode", lambda: False)

    def fail_legacy(username, password):
        raise AssertionError("legacy auth should not run")

    monkeypatch.setattr(auth_route, "authenticate_legacy", fail_legacy)

    resp = client.post("/api/auth/login", json={"username": "testadmin", "password": "testpass123"})
    assert resp.status_code == 401
