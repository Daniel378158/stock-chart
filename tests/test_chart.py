from datetime import datetime, timezone
import logging
from unittest.mock import Mock

import numpy as np
import pandas as pd
import pytest

import chart


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    monkeypatch.setattr(chart.yf, "Ticker", Mock(side_effect=AssertionError("測試禁止連網")))


def synthetic(n=330):
    rng = np.random.default_rng(872)
    x = np.arange(n)
    close = 90 + 0.055 * x + 7 * np.sin(x / 7) + rng.normal(0, .7, n)
    opening = close + rng.normal(-.1, .9, n)
    return pd.DataFrame({"Open": opening, "High": np.maximum(opening, close) + 1.2,
                         "Low": np.minimum(opening, close) - 1.2, "Close": close,
                         "Volume": np.full(n, 200000)}, index=pd.bdate_range("2023-01-02", periods=n))


def controlled(n=80):
    frame = pd.DataFrame({"Open": 101.8, "High": 103., "Low": 101.5, "Close": 102.,
                          "Volume": 1000., "MA60": 100., "ATR": 2.},
                         index=pd.bdate_range("2023-01-02", periods=n))
    return frame


def vendor(frame=None, metadata=None, info=None):
    obj = Mock()
    obj.history.return_value = synthetic(80) if frame is None else frame
    obj.get_history_metadata.return_value = metadata or {"exchangeTimezoneName": "Asia/Taipei"}
    obj.get_info.return_value = info or {"shortName": "公司名"}
    return obj


def test_taiwan_normalization_and_adjusted_prices(monkeypatch):
    ticker = vendor()
    factory = Mock(return_value=ticker)
    monkeypatch.setattr(chart.yf, "Ticker", factory)
    stock = chart.fetch_stock("2330")
    assert stock.symbol == "2330.TW" and stock.currency == "TWD"
    factory.assert_called_once_with("2330.TW")
    ticker.history.assert_called_once_with(period="2y", interval="1d", auto_adjust=True)
    assert stock.name == "公司名" and stock.exchange_timezone == "Asia/Taipei"


@pytest.mark.parametrize("failure", [pd.DataFrame(), RuntimeError("not found")])
def test_taiwan_fallback_to_two(monkeypatch, failure):
    primary, secondary = vendor(), vendor()
    if isinstance(failure, Exception):
        primary.history.side_effect = failure
    else:
        primary.history.return_value = failure
    factory = Mock(side_effect=[primary, secondary])
    monkeypatch.setattr(chart.yf, "Ticker", factory)
    assert chart.fetch_stock("6488", "1y").symbol == "6488.TWO"
    assert [c.args[0] for c in factory.call_args_list] == ["6488.TW", "6488.TWO"]
    secondary.history.assert_called_once_with(period="1y", interval="1d", auto_adjust=True)


@pytest.mark.parametrize("raw,expected", [("mu", ["MU"]), ("2330.tw", ["2330.TW"]),
                                         ("6488.TWO", ["6488.TWO"]), ("BRK-B", ["BRK-B"])])
def test_explicit_symbols(raw, expected):
    assert chart.symbol_candidates(raw) == expected


def test_both_taiwan_candidates_fail(monkeypatch):
    factory = Mock(return_value=vendor(pd.DataFrame()))
    monkeypatch.setattr(chart.yf, "Ticker", factory)
    with pytest.raises(ValueError, match=r"2330.TW.*2330.TWO"):
        chart.fetch_stock("2330")
    assert factory.call_count == 2


def test_successful_fallback_hides_probe_error_and_restores_logging(monkeypatch, caplog):
    logger = logging.getLogger("yfinance")
    before = logger.disabled
    first, second = vendor(), vendor()
    def failed_probe(**_):
        logger.error("intermediate TW probe error")
        raise ValueError("not found")
    first.history.side_effect = failed_probe
    monkeypatch.setattr(chart.yf, "Ticker", Mock(side_effect=[first, second]))
    assert chart.fetch_stock("6488").symbol == "6488.TWO"
    assert "intermediate TW probe error" not in caplog.text
    assert logger.disabled == before


@pytest.mark.parametrize("raw", ["", "../bad", "<script>", "MU AAPL"])
def test_invalid_symbol(raw):
    with pytest.raises(ValueError):
        chart.symbol_candidates(raw)


def test_optional_metadata_failure_is_not_price_failure(monkeypatch):
    ticker = vendor()
    ticker.get_info.side_effect = RuntimeError("metadata unavailable")
    ticker.get_history_metadata.side_effect = RuntimeError("metadata unavailable")
    monkeypatch.setattr(chart.yf, "Ticker", Mock(return_value=ticker))
    result = chart.fetch_stock("MU")
    assert result.name == "" and result.market == "us"


@pytest.mark.parametrize("market,override,mode", [("tw", None, "tw"), ("us", None, "us"),
                                                ("tw", "us", "us"), ("us", "tw", "tw")])
def test_market_colors(market, override, mode):
    scheme = chart.color_scheme(market, override)
    assert scheme["mode"] == mode
    assert scheme["up"] == ("#f05b68" if mode == "tw" else "#27c69a")
    assert scheme["up"] != scheme["down"]


@pytest.mark.parametrize("market,value,digits", [("tw", 100, 1), ("tw", 99.9, 2), ("us", 150, 2)])
def test_precision(market, value, digits):
    assert chart.price_precision(market, value) == digits


@pytest.mark.parametrize("no_open", [False, True])
def test_multi_symbol_failure_continues_and_exit_one(monkeypatch, tmp_path, capsys, no_open):
    seen = []
    def fetch(symbol, period):
        seen.append((symbol, period))
        if symbol == "BAD":
            raise ValueError("查無日K資料")
        return chart.Stock("2330.TW" if symbol == "2330" else symbol, synthetic(80))
    monkeypatch.setattr(chart, "fetch_stock", fetch)
    original = chart.write_html
    monkeypatch.setattr(chart, "write_html", lambda payload: original(payload, tmp_path))
    browser = Mock(return_value=True)
    monkeypatch.setattr(chart.webbrowser, "open", browser)
    result = chart.main(["MU", "BAD", "2330", "LITE", "--period", "1y"] + (["--no-open"] if no_open else []))
    assert result == 1
    assert seen == [(s, "1y") for s in ["MU", "BAD", "2330", "LITE"]]
    assert sorted(p.name for p in tmp_path.glob("*.html")) == ["2330.TW_chart.html", "LITE_chart.html", "MU_chart.html"]
    assert browser.call_count == (0 if no_open else 3)
    assert "錯誤 [BAD]" in capsys.readouterr().err


def test_success_exit_zero(monkeypatch, tmp_path):
    monkeypatch.setattr(chart, "fetch_stock", lambda *_: chart.Stock("MU", synthetic(60)))
    original = chart.write_html
    monkeypatch.setattr(chart, "write_html", lambda payload: original(payload, tmp_path))
    assert chart.main(["MU", "--no-open"]) == 0


def test_indicators_warmup_boll_and_wilder_atr():
    raw = synthetic()
    result = chart.indicators(raw)
    for window in [5, 20, 60, 120, 250]:
        assert result[f"MA{window}"].iloc[:window-1].isna().all()
        assert result[f"MA{window}"].iloc[window-1] == pytest.approx(raw.Close.iloc[:window].mean())
    assert result.BOLL_UP.iloc[19] == pytest.approx(raw.Close.iloc[:20].mean() + 2*np.std(raw.Close.iloc[:20]))
    tr = [raw.High.iloc[0] - raw.Low.iloc[0]]
    for t in range(1, 15):
        tr.append(max(raw.High.iloc[t]-raw.Low.iloc[t], abs(raw.High.iloc[t]-raw.Close.iloc[t-1]),
                      abs(raw.Low.iloc[t]-raw.Close.iloc[t-1])))
    assert result.ATR.iloc[:13].isna().all()
    assert result.ATR.iloc[13] == pytest.approx(np.mean(tr[:14]))
    assert result.ATR.iloc[14] == pytest.approx((np.mean(tr[:14])*13+tr[14])/14)


def test_swing_requires_five_completed_right_bars():
    frame = controlled(12)
    frame.iloc[5, frame.columns.get_loc("High")] = 110
    assert not chart.swing_points(frame.iloc[:10])
    assert chart.swing_points(frame.iloc[:11]) == [(110., 5)]


def test_cluster_adjacency_total_width_score_and_minimum_width():
    # Each gap fits, but the fourth point must start a new group (width > ATR).
    zones = chart.cluster_points([(13.1, 8), (11., 4), (10., 0), (12., 6)], 2, 10)
    assert len(zones) == 2
    assert zones[0] == chart.Zone(10, 12, .5+.7+.8)
    assert zones[1].upper-zones[1].lower == pytest.approx(.4)
    assert zones[1].score == pytest.approx(.9)
    assert len(chart.cluster_points([(10, 1), (11.01, 2)], 2, 10)) == 2
    assert chart.cluster_points([(10, 1)], 0, 10) == []


def test_select_top_eight_before_nearest_four():
    zones = [chart.Zone(p-.1, p+.1, p) for p in range(1, 11)]
    support, resistance = chart.select_zones(zones, 0)
    assert not support
    assert [z.center for z in resistance] == [3, 4, 5, 6]
    support, resistance = chart.select_zones(zones, 11)
    assert [z.center for z in support] == [10, 9, 8, 7] and not resistance


def test_pullback_risk_target_and_five_bar_cooldown(monkeypatch):
    frame = controlled()
    for t in [60, 61, 65, 66]:
        frame.iloc[t, frame.columns.get_loc("Low")] = 100
    monkeypatch.setattr(chart, "build_zones", lambda _: [chart.Zone(99, 101, 2), chart.Zone(109, 111, 2)])
    signals, _ = chart.analyze_signals(frame)
    assert [s["index"] for s in signals] == [60, 66]
    first = signals[0]
    assert first["kind"] == "拉回"
    assert (first["entry"], first["stop"], first["target"], first["rr"]) == (102, 98, 109, 1.75)


@pytest.mark.parametrize("column,value", [("Close", 100), ("Open", 102), ("Low", 97.9), ("MA60", 103)])
def test_pullback_rejects_failed_conditions(monkeypatch, column, value):
    frame = controlled(61)
    frame.iloc[60, frame.columns.get_loc("Low")] = 100
    frame.iloc[60, frame.columns.get_loc(column)] = value
    monkeypatch.setattr(chart, "build_zones", lambda _: [chart.Zone(99, 101, 2)])
    assert chart.analyze_signals(frame)[0] == []


def test_no_overhead_resistance_means_no_target(monkeypatch):
    frame = controlled(61)
    frame.iloc[60, frame.columns.get_loc("Low")] = 100
    monkeypatch.setattr(chart, "build_zones", lambda _: [chart.Zone(99, 101, 2)])
    signal = chart.analyze_signals(frame)[0][0]
    assert signal["target"] is None and signal["rr"] is None


@pytest.mark.parametrize("retest,expected", [(61, True), (70, True), (71, False)])
def test_breakout_retest_next_bar_through_tenth_and_frozen_zone(monkeypatch, retest, expected):
    frame = controlled(75)
    frame.loc[frame.index[60]:, ["Open", "High", "Low", "Close"]] = [106, 108, 106, 107]
    frame.loc[frame.index[retest], ["Open", "Low"]] = [105, 104.5]
    # The breakout zone disappears on the next refresh; the frozen setup survives.
    monkeypatch.setattr(chart, "build_zones", lambda h: [chart.Zone(103, 105, 2)] if len(h) == 60 else [])
    signals, _ = chart.analyze_signals(frame)
    retests = [s for s in signals if s["kind"] == "突破回測"]
    assert [s["index"] for s in retests] == ([retest] if expected else [])
    assert not any(s["index"] == 60 for s in retests)
    if expected:
        assert retests[0]["zone"]["lower"] == 103 and retests[0]["stop"] == 102


def test_zones_rebuilt_every_five_bars_using_strict_prefix(monkeypatch):
    lengths = []
    def build(history):
        lengths.append(len(history))
        return []
    monkeypatch.setattr(chart, "build_zones", build)
    chart.analyze_signals(controlled(77))
    assert lengths == [60, 65, 70, 75]


@pytest.mark.parametrize("length", [1, 14, 59, 60])
def test_no_signals_with_sixty_or_fewer(length):
    assert chart.analyze_signals(chart.indicators(synthetic(length)))[0] == []


def test_truncated_data_preserves_all_past_signals_no_lookahead():
    raw = synthetic(420)
    full, _ = chart.analyze_signals(chart.indicators(raw))
    assert len(full) >= 3, "Non-vacuous: fixture must actually generate signals"
    for length in [61, 89, 130, 201, 299, 361]:
        truncated, _ = chart.analyze_signals(chart.indicators(raw.iloc[:length]))
        assert truncated == [s for s in full if s["index"] < length]
    # Adversarial future prices must also leave past entries/stops/targets intact.
    mutated = raw.copy()
    mutated.iloc[201:, :4] *= 5
    changed, _ = chart.analyze_signals(chart.indicators(mutated))
    assert [s for s in changed if s["index"] < 201] == [s for s in full if s["index"] < 201]


@pytest.mark.parametrize("symbol,tz,day,now,expected", [
    ("2330.TW", "Asia/Taipei", "2026-09-29", "2026-09-29T01:00:00+00:00", True),
    ("2330.TW", "Asia/Taipei", "2026-09-29", "2026-09-29T05:29:00+00:00", True),
    ("2330.TW", "Asia/Taipei", "2026-09-29", "2026-09-29T05:30:00+00:00", False),
    ("2330.TW", "Asia/Taipei", "2026-09-28", "2026-09-29T03:00:00+00:00", False),
    ("MU", "America/New_York", "2026-07-06", "2026-07-06T13:30:00+00:00", True),
    ("MU", "America/New_York", "2026-07-06", "2026-07-06T20:00:00+00:00", False),
    ("MU", "America/New_York", "2026-01-05", "2026-01-05T14:00:00+00:00", False),
    ("MU", "America/New_York", "2026-01-05", "2026-01-05T14:30:00+00:00", True),
    ("MU", "America/New_York", "2026-11-27", "2026-11-27T17:59:00+00:00", True),
    ("MU", "America/New_York", "2026-11-27", "2026-11-27T18:00:00+00:00", False),
    ("MU", "America/New_York", "2026-12-25", "2026-12-25T15:00:00+00:00", False),
])
def test_session_detection_dst_holidays_and_early_close(symbol, tz, day, now, expected):
    frame = synthetic(1)
    frame.index = pd.DatetimeIndex([day], tz=tz)
    assert chart.is_intraday(chart.Stock(symbol, frame, exchange_timezone=tz), datetime.fromisoformat(now)) is expected


def test_multiple_same_day_signals_one_marker_and_provisional(monkeypatch):
    stock = chart.Stock("MU", synthetic(62))
    first = {"index": 60, "time": stock.frame.index[60].strftime("%Y-%m-%d"), "kind": "拉回"}
    last = {"index": 61, "time": stock.frame.index[61].strftime("%Y-%m-%d"), "kind": "拉回"}
    monkeypatch.setattr(chart, "analyze_signals", lambda _: ([first, last, {**last, "kind": "突破回測"}], []))
    monkeypatch.setattr(chart, "is_intraday", lambda *_: True)
    payload = chart.build_payload(stock)
    assert len(payload["markers"]) == 2
    assert payload["markers"][-1]["text"] == "拉回 + 突破回測 ?"
    assert not payload["signals"][0]["provisional"]
    assert payload["signals"][-1]["provisional"]


def test_html_cdn_and_vendor_metadata_escaping(tmp_path):
    payload = chart.build_payload(chart.Stock("MU", synthetic(80), '</script><script>alert(1)</script>'))
    output = chart.write_html(payload, tmp_path)
    contents = output.read_text(encoding="utf-8")
    assert "lightweight-charts@4.2.3/" in contents
    assert '</script><script>alert(1)</script>' not in contents
    assert "不構成投資建議" in contents
    assert "__PAYLOAD__" not in contents


def test_clean_frame_rejects_bad_prices_and_deduplicates():
    frame = synthetic(5)
    frame.iloc[1, frame.columns.get_loc("High")] = np.inf
    frame.iloc[2, frame.columns.get_loc("Low")] = -1
    duplicated = pd.concat([frame, frame.iloc[[4]]])
    cleaned = chart.clean_frame(duplicated)
    assert len(cleaned) == 3 and cleaned.index.is_unique
