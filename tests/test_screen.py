from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import sys
from types import SimpleNamespace
from unittest.mock import Mock

import numpy as np
import pandas as pd
import pytest

import screen


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    monkeypatch.setitem(sys.modules, "yfinance", SimpleNamespace(
        download=Mock(side_effect=AssertionError("Tests must not contact Yahoo"))))


@pytest.mark.parametrize("value,expected", [(.1, .2), (0, 0), (-.1, 0)])
def test_threshold(value, expected):
    assert screen.threshold(value) == expected


def test_universe_exact_snapshot_and_document_hash():
    stocks = screen.load_universe()
    assert len(stocks) == len({s["ticker"] for s in stocks}) == 609
    assert sum(s["market"] == "us" for s in stocks) == 559
    assert sum(s["market"] == "tw" for s in stocks) == 50
    assert {s["market"] for s in stocks} == {"us", "tw"}
    by_symbol = {s["ticker"]: s for s in stocks}
    assert {"AAPL", "BRK-B", "BF-B", "TSM", "ASML", "ALAB", "2330.TW"} <= by_symbol.keys()
    assert by_symbol["2330.TW"]["name"] == "臺積電"
    assert all(s["name"] == "" for s in stocks[:559])
    raw = screen.UNIVERSE.read_bytes()
    assert not raw.startswith(b"\xef\xbb\xbf") and b"\r" not in raw and raw.endswith(b"\n")
    assert hashlib.sha256(raw).hexdigest() == "eddff1aafae3defad8d39196d11ecc2a840d1b0aba04ac8659984099a8f0e369"
    appendix = (screen.ROOT / "docs/backtest-findings.md").read_bytes().replace(b"\r\n", b"\n")
    assert hashlib.sha256(appendix).hexdigest() == "21d2d5bbd849f78df82527546eaa04209bb0156de746cee284cb14c1a5e88e1c"


def test_universe_accepts_bom(tmp_path):
    path = tmp_path / "universe.csv"
    path.write_text("ticker,name,market\n2330.TW,臺積電,tw\n", encoding="utf-8-sig")
    assert screen.load_universe(path) == [{"ticker": "2330.TW", "name": "臺積電", "market": "tw"}]


@pytest.mark.parametrize("market,divisor", [("us", 1), ("tw", 32)])
def test_metrics_exact_windows_and_daily_product(market, divisor):
    c = pd.Series(np.arange(1, 202, dtype=float), index=pd.date_range("2025-01-01", periods=201))
    v = pd.Series(np.arange(1000, 1201, dtype=float), index=c.index)
    m = screen.metrics(c, v, market, 32)
    assert m["market"] == market and m["price"] == 201.0
    for n in (10, 20, 200):
        assert m[f"sma{n}"] == pytest.approx(sum(c.iloc[-n:]) / n)
    assert m["vols"] == pytest.approx([sum(v.iloc[-n:]) / n for n in (10, 30, 60, 90)])
    assert m["value_usd"] == pytest.approx(sum(a * b for a, b in zip(c.iloc[-10:], v.iloc[-10:])) / 10 / divisor)
    assert m["value_usd"] != pytest.approx(c.iloc[-10:].mean() * v.iloc[-10:].mean() / divisor, rel=1e-7)
    assert m["r1m"] == pytest.approx(201 / 180 - 1)
    assert m["r6m"] == pytest.approx(201 / 75 - 1)
    assert screen.metrics(c.iloc[-199:], v.iloc[-199:], market, 32) is None
    assert screen.metrics(c.iloc[-200:], v.iloc[-200:], market, 32) is not None
    assert all(isinstance(value, float) for key, value in m.items() if key not in ("market", "vols"))
    assert all(isinstance(value, float) for value in m["vols"])


def good_metrics():
    return {"market": "us", "price": 100., "sma10": 100., "sma20": 100., "sma200": 100.,
            "vols": [500_000.] * 4, "value_usd": 50_000_000., "r1m": .2, "r6m": .4}


@pytest.mark.parametrize("changes,expected", [
    (None, 0), ({"vols": [500000, 499999, 500000, 500000]}, 1),
    ({"value_usd": 49_999_999}, 1), ({"price": 9.99}, 1),
    ({"price": 99.99}, 2), ({"sma10": 99.99}, 2),
    ({"r1m": .199999}, 3), ({"r6m": .399999}, 4), ({}, 5),
    ({"market": "tw", "price": 1, "sma200": 1}, 5),
    ({"price": 10, "sma200": 10}, 5),
])
def test_stage_all_stages_and_inclusive_boundaries(changes, expected):
    m = None if changes is None else {**good_metrics(), **changes}
    assert screen.stage(m, {"r1m": .1, "r6m": .2}) == expected


@pytest.mark.parametrize("month,six,expected", [(0, 0, 5), (-.000001, 0, 3), (0, -.000001, 4)])
def test_stage_falling_benchmark_uses_zero(month, six, expected):
    assert screen.stage({**good_metrics(), "r1m": month, "r6m": six}, {"r1m": -.1, "r6m": -.2}) == expected


def data_fixture():
    index = pd.date_range("2025-01-01", periods=420)
    us_index, tw_index = index[::2], index[1::2]
    close = pd.DataFrame(index=index)
    volume = pd.DataFrame(index=index)
    universe = []
    def add(ticker, values, market="us", shares=3_000_000):
        universe.append({"ticker": ticker, "name": "測試台股" if market == "tw" else "", "market": market})
        dates = tw_index if market == "tw" else us_index
        close[ticker] = pd.Series(values, index=dates[-len(values):])
        if shares is not None:
            volume[ticker] = pd.Series(shares, index=dates)
    universe.append({"ticker": "MISSING", "name": "", "market": "us"})
    add("EMPTY", np.full(210, np.nan))
    add("SHORT", np.linspace(50, 100, 199))
    add("LOW", np.linspace(50, 100, 210), shares=499999)
    add("DOWN", np.linspace(150, 100, 210))
    add("WEAK", np.full(210, 100.))
    loose = np.full(210, 100.)
    loose[-127] = 120
    loose[-21:] = np.linspace(101, 110, 21)
    add("LOOSE", loose)
    add("STRICT_US", np.linspace(50, 100, 210))
    add("STRICT_TW", np.linspace(500, 1300, 210), "tw")
    add("NOVOL", np.linspace(50, 100, 210), shares=None)
    add("PARTVOL", np.linspace(50, 100, 210), shares=500000)
    volume.loc[us_index[-1], "PARTVOL"] = np.nan
    close["SPY"] = pd.Series(np.linspace(100, 110, 210), index=us_index)
    close["0050.TW"] = pd.Series(np.linspace(100, 110, 210), index=tw_index)
    close["TWD=X"] = 32.
    return universe, close, volume


def test_run_mixed_sessions_funnel_missing_order_and_ranking():
    universe, close, volume = data_fixture()
    fetch = Mock(return_value=(close, volume))
    now = datetime(2026, 10, 2, 7, 0, 1, 123, tzinfo=timezone.utc)
    result = screen.run(universe, fetch, now)
    fetch.assert_called_once_with([u["ticker"] for u in universe] + ["SPY", "0050.TW", "TWD=X"])
    assert result["run_at"] == "2026-10-02T07:00:01+00:00"
    assert result["fx"] == 32
    assert result["missing"] == ["MISSING", "EMPTY"]
    assert result["funnel"] == dict(zip(screen.FUNNEL, [11, 8, 5, 4, 3, 2]))
    assert [r["ticker"] for r in result["rows"]] == ["STRICT_TW", "STRICT_US", "LOOSE"]
    assert [r["level"] for r in result["rows"]] == ["嚴格", "嚴格", "寬鬆"]
    assert result["rows"][0]["name"] == "測試台股"
    assert result["rows"][0]["value_usd"] == pytest.approx(np.linspace(500, 1300, 210)[-10:].mean() * 3e6 / 32)
    assert result["rows"][0]["r1m"] == pytest.approx(1300 / np.linspace(500, 1300, 210)[-22] - 1)
    assert result["benchmarks"]["us"] == {"ticker": "SPY", "r1m": pytest.approx(110 / np.linspace(100, 110, 210)[-22] - 1),
                                             "r6m": pytest.approx(110 / np.linspace(100, 110, 210)[-127] - 1)}
    assert result["benchmarks"]["tw"]["r1m"] == result["benchmarks"]["us"]["r1m"]


@pytest.mark.parametrize("ticker,mode", [("TWD=X", "absent"), ("TWD=X", "empty"), ("SPY", "absent"),
                                        ("0050.TW", "empty"), ("SPY", "short"), ("0050.TW", "short")])
def test_run_missing_required_market_data(ticker, mode):
    universe, close, volume = data_fixture()
    if mode == "absent":
        close = close.drop(columns=ticker)
    elif mode == "empty":
        close[ticker] = np.nan
    else:
        valid = close[ticker].dropna().index
        close.loc[valid[:-126], ticker] = np.nan
    with pytest.raises(screen.ScreenError, match=ticker.replace("=", "[=]")):
        screen.run(universe, lambda _: (close, volume))


def test_run_wraps_download_error_and_preserves_screen_error():
    with pytest.raises(screen.ScreenError, match="下載失敗：vendor offline"):
        screen.run([], Mock(side_effect=RuntimeError("vendor offline")))
    error = screen.ScreenError("original")
    with pytest.raises(screen.ScreenError) as caught:
        screen.run([], Mock(side_effect=error))
    assert caught.value is error


def vendor_frame(close, volume=None):
    data = {"Close": pd.DataFrame(close)}
    if volume is not None:
        data["Volume"] = pd.DataFrame(volume)
    return pd.concat(data, axis=1)


def set_download(monkeypatch, values):
    fake = Mock(side_effect=values)
    monkeypatch.setitem(sys.modules, "yfinance", SimpleNamespace(download=fake))
    return fake


def test_download_retry_only_missing_and_normalizes_single_ticker(monkeypatch):
    first = vendor_frame({"A": [10., 11.], "B": [np.nan, np.nan]}, {"A": [100, 200]})
    retry = vendor_frame({"B": [20., 21.], "C": [30., 31.]}, {"B": [300, 400], "C": [500, 600]})
    fake = set_download(monkeypatch, [first, retry])
    close, volume = screen.download(["A", "B", "C"])
    assert [c.args[0] for c in fake.call_args_list] == [["A", "B", "C"], ["B", "C"]]
    assert fake.call_args_list[0].kwargs == dict(period="1y", interval="1d", auto_adjust=True, progress=False, threads=True, group_by="column")
    assert close["A"].tolist() == [10, 11] and close["C"].tolist() == [30, 31]
    assert volume["B"].tolist() == [300, 400]
    single = pd.DataFrame({"Close": [22., 23.], "Volume": [700., 800.]})
    fake = set_download(monkeypatch, [first, single])
    close, volume = screen.download(["A", "B"])
    assert fake.call_args.args == (["B"],)
    assert close["B"].tolist() == [22, 23] and volume["B"].tolist() == [700, 800]


def test_download_retry_close_without_volume_replaces_old_column(monkeypatch):
    first = vendor_frame({"A": [10., 11.], "B": [np.nan, np.nan]}, {"B": [999, 999]})
    fake = set_download(monkeypatch, [first, pd.DataFrame({"Close": [20., 21.]})])
    close, volume = screen.download(["A", "B"])
    assert close["B"].tolist() == [20, 21]
    assert volume["B"].isna().all() and fake.call_count == 2


def test_download_no_retry_when_complete_and_single_initial_format(monkeypatch):
    fake = set_download(monkeypatch, [pd.DataFrame({"Close": [10.], "Volume": [50.]})])
    close, volume = screen.download(["A"])
    assert fake.call_count == 1 and close["A"].iloc[0] == 10 and volume["A"].iloc[0] == 50


@pytest.mark.parametrize("value", [None, pd.DataFrame()])
def test_download_empty_initial_raises(monkeypatch, value):
    fake = set_download(monkeypatch, [value])
    with pytest.raises(screen.ScreenError, match="沒有取得任何資料"):
        screen.download(["A"])
    assert fake.call_count == 1


@pytest.mark.parametrize("retry", [None, pd.DataFrame(), RuntimeError("offline"),
                                  pd.DataFrame({"Open": [2.]}), pd.DataFrame({"Close": [np.nan]})])
def test_download_bad_retry_keeps_original(monkeypatch, retry):
    first = vendor_frame({"A": [10., 11.]}, {"A": [100., 200.]})
    fake = set_download(monkeypatch, [first, retry])
    close, volume = screen.download(["A", "B"])
    pd.testing.assert_frame_equal(close, first["Close"])
    pd.testing.assert_frame_equal(volume, first["Volume"])
    assert fake.call_count == 2


def test_download_no_close_fields_returns_empty_columns_after_retry(monkeypatch):
    frame = pd.DataFrame({"Open": [1.]})
    fake = set_download(monkeypatch, [frame, frame])
    close, volume = screen.download(["A"])
    assert close.empty and volume.empty and fake.call_count == 2
    assert close.index.equals(frame.index)


def sample_result():
    universe, close, volume = data_fixture()
    return screen.run(universe, lambda _: (close, volume), datetime(2026, 10, 2, tzinfo=timezone.utc))


def test_save_load_round_trip_atomic_replace(tmp_path, monkeypatch):
    result = sample_result()
    path = tmp_path / screen.RESULT_NAME
    replace = Mock(wraps=screen.os.replace)
    monkeypatch.setattr(screen.os, "replace", replace)
    screen.save(result, path)
    assert screen.load(path) == result
    replace.assert_called_once_with(path.with_name(path.name + ".tmp"), path)
    assert not list(tmp_path.glob("*.tmp"))
    assert "測試台股" in path.read_text(encoding="utf-8")


@pytest.mark.parametrize("data", [b"{", b"\xff\xfe", b"[]", b'{}', b'{"rows": {}}', b'null'])
def test_load_invalid_returns_none(tmp_path, data):
    path = tmp_path / "result.json"
    assert screen.load(path) is None
    path.write_bytes(data)
    assert screen.load(path) is None
    assert screen.load(tmp_path) is None


def test_report_table_truncation_missing_and_empty():
    result = sample_result()
    result["missing"] = [f"BAD{i:02}" for i in range(12)]
    report = screen.report(result, 1)
    assert "漏斗：範圍 11 → 資料 8 → 流動性 5 → 趨勢 4 → 寬鬆 3 → 嚴格 2" in report
    for value in ("USD/TWD", "SPY", "0050.TW", "STRICT_TW", "另有 2 檔", "BAD09", "…", "+"):
        assert value in report
    assert "BAD10" not in report and report.endswith(screen.DISCLAIMER)
    result["rows"] = []
    assert "沒有符合條件的股票" in screen.report(result, 30)


def test_main_saves_configured_directory(tmp_path, monkeypatch, capsys):
    result = sample_result()
    monkeypatch.setattr(screen, "run", Mock(return_value=result))
    target = tmp_path / "new-directory"
    monkeypatch.setenv("STOCK_CHART_DATA_DIR", str(target))
    assert screen.main(["--top", "1"]) == 0
    assert screen.load(target / screen.RESULT_NAME) == result
    output = capsys.readouterr().out
    assert "下載資料中" in output and "/screen" in output and "另有 2 檔" in output


def test_main_errors_do_not_download_for_invalid_top(monkeypatch, capsys):
    fake = Mock(side_effect=screen.ScreenError("test error"))
    monkeypatch.setattr(screen, "run", fake)
    assert screen.main(["--top", "0"]) == 1
    fake.assert_not_called()
    assert "--top" in capsys.readouterr().err
    assert screen.main([]) == 1
    assert "test error" in capsys.readouterr().err
