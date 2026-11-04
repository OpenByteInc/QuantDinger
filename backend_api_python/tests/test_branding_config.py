from app.services.settings.branding import build_brand_config


def test_brand_config_does_not_control_frontend_release_version():
    config = build_brand_config()

    assert "app_version" not in config
