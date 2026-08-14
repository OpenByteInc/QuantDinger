from __future__ import annotations

import importlib.util
from pathlib import Path


RELAY_PATH = Path(__file__).resolve().parents[1] / "scripts" / "opend_relay.py"


def _load_relay():
    spec = importlib.util.spec_from_file_location("opend_relay", RELAY_PATH)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def test_detect_relay_bind_host_uses_explicit_env(monkeypatch):
    module = _load_relay()
    monkeypatch.setenv("OPEND_RELAY_BIND_HOST", "10.8.0.1")
    assert module.detect_relay_bind_host() == "10.8.0.1"


def test_detect_relay_bind_host_prefers_docker0(monkeypatch):
    module = _load_relay()
    monkeypatch.delenv("OPEND_RELAY_BIND_HOST", raising=False)
    monkeypatch.setattr(module, "_ipv4_for_iface", lambda name: "172.18.0.1" if name == "docker0" else "")
    monkeypatch.setattr(module, "_resolve_ipv4", lambda _name: "")
    assert module.detect_relay_bind_host() == "172.18.0.1"


def test_detect_relay_bind_host_falls_back_to_default(monkeypatch):
    module = _load_relay()
    monkeypatch.delenv("OPEND_RELAY_BIND_HOST", raising=False)
    monkeypatch.setattr(module, "_ipv4_for_iface", lambda _name: "")
    monkeypatch.setattr(module, "_resolve_ipv4", lambda _name: "")
    assert module.detect_relay_bind_host() == "172.17.0.1"
