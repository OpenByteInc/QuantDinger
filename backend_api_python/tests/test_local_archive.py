"""Local DATE/ K-line archive read, merge, and factory prefer-local."""
from __future__ import annotations

from pathlib import Path

import pytest

from app.data_sources import local_archive as archive
from app.data_sources.factory import DataSourceFactory


def _bars(start: int, n: int, step: int = 86400):
    rows = []
    for i in range(n):
        ts = start + i * step
        px = 10.0 + i
        rows.append(
            {
                "time": ts,
                "open": px,
                "high": px + 1,
                "low": px - 1,
                "close": px + 0.5,
                "volume": 1000 + i,
            }
        )
    return rows


@pytest.fixture
def date_dir(tmp_path, monkeypatch):
    root = tmp_path / "DATE"
    monkeypatch.setenv("QUANTDINGER_DATE_DIR", str(root))
    monkeypatch.setenv("QUANTDINGER_DATE_READ", "true")
    monkeypatch.setenv("QUANTDINGER_DATE_WRITE", "true")
    return root


def test_merge_write_and_read_roundtrip(date_dir: Path):
    first = _bars(1_700_000_000, 3)
    path = archive.merge_write("CNStock", "600519", "1D", first)
    assert path.is_file()
    assert "stocks" in str(path).replace("\\", "/")
    assert "CN" in str(path).replace("\\", "/")

    extra = _bars(1_700_000_000 + 2 * 86400, 2)
    extra[0]["close"] = 99.0
    archive.merge_write("CNStock", "600519", "1D", extra)
    stored = archive.read_klines("CNStock", "sh600519", "1D")
    assert [row["time"] for row in stored] == [
        1_700_000_000,
        1_700_000_000 + 86400,
        1_700_000_000 + 2 * 86400,
        1_700_000_000 + 3 * 86400,
    ]
    assert stored[2]["close"] == 99.0


def test_factory_prefers_fresh_local(date_dir: Path, monkeypatch):
    now = 1_800_000_000
    monkeypatch.setattr(archive.time, "time", lambda: now)
    local = _bars(now - 40 * 86400, 40)
    archive.merge_write("CNStock", "000001", "1D", local)

    def _boom(cls, *args, **kwargs):
        raise AssertionError("remote source should not be called")

    monkeypatch.setattr(DataSourceFactory, "_resolve_source", classmethod(_boom))

    rows = DataSourceFactory.get_kline("CNStock", "000001", "1D", 30)
    assert len(rows) == 30
    assert rows[-1]["time"] == local[-1]["time"]


def test_factory_writes_remote_rows(date_dir: Path, monkeypatch):
    remote = _bars(1_710_000_000, 5)

    class _Src:
        def get_kline(self, *_args, **_kwargs):
            return list(remote)

    monkeypatch.setattr(
        DataSourceFactory,
        "_resolve_source",
        classmethod(lambda cls, *a, **k: _Src()),
    )
    monkeypatch.setenv("QUANTDINGER_DATE_READ", "false")

    rows = DataSourceFactory.get_kline("Futures", "RB0", "1D", 5)
    assert len(rows) == 5
    stored = archive.read_klines("Futures", "RB0", "1D")
    assert len(stored) == 5
    path = archive.kline_path("Futures", "RB0", "1D")
    assert path is not None
    assert "futures" in str(path).replace("\\", "/")
