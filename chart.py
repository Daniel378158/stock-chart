"""Adjusted daily stock charts. Usage: python chart.py MU 2330 --no-open."""
from __future__ import annotations

import argparse
from contextlib import contextmanager
from dataclasses import asdict, dataclass
from datetime import datetime, time, timezone
import html
import json
import logging
from pathlib import Path
import re
import sys
import webbrowser
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import numpy as np
import pandas as pd
import pandas_market_calendars as mcal
import yfinance as yf


@dataclass(frozen=True)
class Zone:
    lower: float
    upper: float
    score: float

    @property
    def center(self) -> float:
        return (self.lower + self.upper) / 2


@dataclass
class Stock:
    symbol: str
    frame: pd.DataFrame
    name: str = ""
    exchange_timezone: str = ""
    metadata: dict | None = None

    @property
    def market(self) -> str:
        return "tw" if self.symbol.endswith((".TW", ".TWO")) else "us"

    @property
    def currency(self) -> str:
        return "TWD" if self.market == "tw" else "USD"


def symbol_candidates(raw: str) -> list[str]:
    symbol = raw.strip().upper()
    if not re.fullmatch(r"[A-Z0-9][A-Z0-9.^=\-]{0,31}", symbol):
        raise ValueError(f"無效代碼：{raw!r}")
    if symbol.isdigit():
        return [symbol + ".TW", symbol + ".TWO"]
    return [symbol]


def clean_frame(frame: pd.DataFrame) -> pd.DataFrame:
    if frame is None or frame.empty:
        raise ValueError("查無日K資料")
    columns = ["Open", "High", "Low", "Close"]
    if not set(columns).issubset(frame.columns):
        raise ValueError("缺少 OHLC 欄位")
    frame = frame.copy().sort_index()
    frame = frame.loc[~frame.index.duplicated(keep="last")]
    frame[columns] = frame[columns].apply(pd.to_numeric, errors="coerce")
    valid = np.isfinite(frame[columns]).all(axis=1) & (frame[columns] > 0).all(axis=1)
    valid &= frame.High >= frame[["Open", "Close", "Low"]].max(axis=1)
    valid &= frame.Low <= frame[["Open", "Close", "High"]].min(axis=1)
    frame = frame.loc[valid].copy()
    if frame.empty:
        raise ValueError("沒有有效 OHLC 資料")
    if "Volume" not in frame:
        frame["Volume"] = 0
    frame["Volume"] = pd.to_numeric(frame.Volume, errors="coerce").fillna(0).clip(lower=0)
    return frame


@contextmanager
def quiet_yfinance():
    """A failed TW probe is not a user-facing error if TWO succeeds."""
    logger = logging.getLogger("yfinance")
    disabled = logger.disabled
    logger.disabled = True
    try:
        yield
    finally:
        logger.disabled = disabled


def fetch_stock(raw: str, period: str = "2y") -> Stock:
    errors = []
    for symbol in symbol_candidates(raw):
        try:
            ticker = yf.Ticker(symbol)
            with quiet_yfinance():
                frame = clean_frame(ticker.history(period=period, interval="1d", auto_adjust=True))
        except Exception as exc:
            errors.append(f"{symbol}: {exc}")
            continue
        # Optional metadata must never turn valid prices into a failed symbol.
        try:
            with quiet_yfinance():
                metadata = ticker.get_history_metadata() or {}
        except Exception:
            metadata = {}
        try:
            with quiet_yfinance():
                info = ticker.get_info() or {}
        except Exception:
            info = {}
        zone = metadata.get("exchangeTimezoneName") or info.get("exchangeTimezoneName") or ""
        return Stock(symbol, frame, str(info.get("shortName") or ""), zone, metadata)
    raise ValueError("；".join(errors))


def color_scheme(market: str, override: str | None = None) -> dict:
    mode = override or market
    red, green = "#f05b68", "#27c69a"
    return {"mode": mode, "up": red if mode == "tw" else green,
            "down": green if mode == "tw" else red}


def price_precision(market: str, value: float) -> int:
    return 1 if market == "tw" and value >= 100 else 2


def indicators(frame: pd.DataFrame) -> pd.DataFrame:
    result = frame.copy()
    for window in (5, 20, 60, 120, 250):
        result[f"MA{window}"] = result.Close.rolling(window).mean()
    sigma = result.Close.rolling(20).std(ddof=0)
    result["BOLL_MID"] = result.MA20
    result["BOLL_UP"] = result.MA20 + 2 * sigma
    result["BOLL_LOW"] = result.MA20 - 2 * sigma
    previous = result.Close.shift()
    tr = pd.concat([result.High - result.Low, (result.High - previous).abs(),
                    (result.Low - previous).abs()], axis=1).max(axis=1).to_numpy()
    atr = np.full(len(result), np.nan)
    if len(result) >= 14:
        atr[13] = tr[:14].mean()
        for i in range(14, len(result)):
            atr[i] = (atr[i - 1] * 13 + tr[i]) / 14
    result["ATR"] = atr
    return result


def swing_points(frame: pd.DataFrame, radius: int = 5) -> list[tuple[float, int]]:
    """Only confirmed pivots; ties use the first extreme of a plateau."""
    highs, lows = frame.High.to_numpy(), frame.Low.to_numpy()
    points = []
    for i in range(radius, len(frame) - radius):
        if highs[i] > highs[i-radius:i].max() and highs[i] >= highs[i+1:i+radius+1].max():
            points.append((float(highs[i]), i))
        if lows[i] < lows[i-radius:i].min() and lows[i] <= lows[i+1:i+radius+1].min():
            points.append((float(lows[i]), i))
    return points


def cluster_points(points: list[tuple[float, int]], atr: float, length: int) -> list[Zone]:
    if not np.isfinite(atr) or atr <= 0 or length <= 0:
        return []
    groups: list[list[tuple[float, int]]] = []
    for point in sorted(points):
        if (groups and point[0] - groups[-1][-1][0] <= 0.5 * atr
                and point[0] - groups[-1][0][0] <= atr):
            groups[-1].append(point)
        else:
            groups.append([point])
    zones = []
    for group in groups:
        lower, upper = group[0][0], group[-1][0]
        center = (lower + upper) / 2
        half_width = max((upper - lower) / 2, 0.1 * atr)
        score = sum(0.5 + 0.5 * index / length for _, index in group)
        zones.append(Zone(center - half_width, center + half_width, score))
    return zones


def build_zones(history: pd.DataFrame) -> list[Zone]:
    if history.empty:
        return []
    return cluster_points(swing_points(history), float(history.ATR.iloc[-1]), len(history))


def select_zones(zones: list[Zone], price: float) -> tuple[list[Zone], list[Zone]]:
    def select(side):
        strongest = sorted(side, key=lambda z: (-z.score, abs(z.center - price), z.center))[:8]
        return sorted(strongest, key=lambda z: (abs(z.center - price), -z.score, z.center))[:4]
    return (select([z for z in zones if z.center < price]),
            select([z for z in zones if z.center > price]))


def make_signal(kind: str, index: int, frame: pd.DataFrame, zone: Zone,
                resistance: list[Zone]) -> dict | None:
    row = frame.iloc[index]
    entry, stop = float(row.Close), float(zone.lower - 0.5 * row.ATR)
    if entry <= stop:
        return None
    targets = [z.lower for z in resistance if z.lower > entry]
    target = min(targets) if targets else None
    return {"index": index, "time": frame.index[index].strftime("%Y-%m-%d"),
            "kind": kind, "entry": entry, "stop": stop, "target": target,
            "rr": (target - entry) / (entry - stop) if target is not None else None,
            "zone": asdict(zone)}


def analyze_signals(frame: pd.DataFrame) -> tuple[list[dict], list[Zone]]:
    """Streaming decisions: every cache update sees only frame[:t]."""
    signals: list[dict] = []
    zones: list[Zone] = []
    pending: list[tuple[int, Zone]] = []
    last_seen = {"拉回": -999, "突破回測": -999}
    if len(frame) <= 60:
        return [], build_zones(frame.iloc[:-1])
    for t in range(60, len(frame)):
        if (t - 60) % 5 == 0:
            zones = build_zones(frame.iloc[:t])
        row, previous = frame.iloc[t], frame.iloc[t-1]
        support, resistance = select_zones(zones, float(previous.Close))
        today = []
        if t - last_seen["拉回"] > 5 and row.Close > row.MA60 and row.Close > row.Open:
            for zone in support:
                if (row.Low <= zone.upper < row.Close
                        and row.Low >= zone.lower - 0.5 * row.ATR):
                    signal = make_signal("拉回", t, frame, zone, resistance)
                    if signal:
                        today.append(signal)
        remaining = []
        for breakout_index, zone in pending:
            if t - breakout_index > 10:
                continue
            if (t - last_seen["突破回測"] > 5 and row.Close > row.Open
                    and row.Low <= zone.upper + 0.25 * row.ATR and row.Close >= zone.lower):
                signal = make_signal("突破回測", t, frame, zone, resistance)
                if signal:
                    today.append(signal)
                    continue
            remaining.append((breakout_index, zone))
        pending = remaining
        # New breakouts become eligible for retesting on the NEXT candle.
        for zone in resistance:
            if previous.Close <= zone.upper < row.Close:
                if not any(existing == zone for _, existing in pending):
                    pending.append((t, zone))
        for signal in today:
            last_seen[signal["kind"]] = t
        signals.extend(today)
    return signals, zones


def is_intraday(stock: Stock, now: datetime | None = None) -> bool:
    now = now or datetime.now(timezone.utc)
    if now.tzinfo is None:
        raise ValueError("now 必須帶時區")
    fallback = "Asia/Taipei" if stock.market == "tw" else "America/New_York"
    try:
        exchange_zone = ZoneInfo(stock.exchange_timezone or fallback)
    except ZoneInfoNotFoundError:
        exchange_zone = ZoneInfo(fallback)
    local_now = now.astimezone(exchange_zone)
    stamp = pd.Timestamp(stock.frame.index[-1])
    last_date = stamp.tz_convert(exchange_zone).date() if stamp.tzinfo else stamp.date()
    if last_date != local_now.date() or local_now.weekday() >= 5:
        return False
    if stock.market == "tw":
        return time(9) <= local_now.time() < time(13, 30)
    # NYSE calendar also covers standard Nasdaq equity hours and early closes.
    schedule = mcal.get_calendar("NYSE").schedule(start_date=last_date, end_date=last_date)
    if schedule.empty:
        return False
    session = schedule.iloc[0]
    return bool(session.market_open <= pd.Timestamp(now) < session.market_close)


def build_payload(stock: Stock, colors: str | None = None, now: datetime | None = None) -> dict:
    frame = indicators(stock.frame)
    signals, zones = analyze_signals(frame)
    current = float(frame.Close.iloc[-1])
    support, resistance = select_zones(zones, current)
    provisional = is_intraday(stock, now)
    dates = [stamp.strftime("%Y-%m-%d") for stamp in frame.index]
    for signal in signals:
        signal["provisional"] = provisional and signal["index"] == len(frame) - 1
    grouped: dict[str, list[dict]] = {}
    for signal in signals:
        grouped.setdefault(signal["time"], []).append(signal)
    markers = []
    for day, items in grouped.items():
        label = " + ".join(dict.fromkeys(s["kind"] for s in items))
        if any(s["provisional"] for s in items):
            label += " ?"
        markers.append({"time": day, "position": "belowBar", "color": "#ffca67",
                        "shape": "arrowUp", "text": label})
    def line(column):
        return [{"time": day, "value": float(value)} for day, value in zip(dates, frame[column])
                if np.isfinite(value)]
    zone_rows = [dict(asdict(z), side=side, distance=(z.center / current - 1) * 100)
                 for side, items in (("support", support), ("resistance", resistance)) for z in items]
    return {"symbol": stock.symbol, "name": stock.name, "market": stock.market,
            "currency": stock.currency, "timezone": stock.exchange_timezone or (
                "Asia/Taipei" if stock.market == "tw" else "America/New_York"),
            "colors": color_scheme(stock.market, colors), "current": current,
            "change": current - float(frame.Close.iloc[-2]) if len(frame) > 1 else 0,
            "changePercent": (current / float(frame.Close.iloc[-2]) - 1) * 100 if len(frame) > 1 else 0,
            "precision": price_precision(stock.market, current), "provisional": provisional,
            "asof": dates[-1], "generated": datetime.now(timezone.utc).isoformat(),
            "candles": [{"time": day, **{k.lower(): float(row[k]) for k in ("Open", "High", "Low", "Close")}}
                        for day, (_, row) in zip(dates, frame.iterrows())],
            "volume": line("Volume"),
            "lines": {key: line(key) for key in ("MA5", "MA20", "MA60", "MA120", "MA250",
                                                  "BOLL_UP", "BOLL_MID", "BOLL_LOW", "ATR")},
            "zones": zone_rows, "signals": signals, "markers": markers}


def render_html(payload: dict) -> str:
    template = Path(__file__).with_name("template.html").read_text(encoding="utf-8")
    # Escape '<' to keep vendor metadata from terminating the JSON script element.
    data = json.dumps(payload, ensure_ascii=False, allow_nan=False).replace("<", "\\u003c")
    result = template.replace("__TITLE__", html.escape(payload["symbol"] + " · 日K"))
    result = result.replace("__PAYLOAD__", data)
    search = Path(__file__).with_name("search.html").read_text(encoding="utf-8")
    return result.replace("__SEARCH__", search)


def write_html(payload: dict, output_dir: Path | None = None) -> Path:
    directory = output_dir or Path(__file__).resolve().parent
    directory.mkdir(parents=True, exist_ok=True)
    result = render_html(payload)
    path = directory / f"{payload['symbol']}_chart.html"
    path.write_text(result, encoding="utf-8")
    return path.resolve()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="台美股深色互動日K（還原股價）")
    parser.add_argument("symbols", nargs="+", help="例如 MU 2330 6488.TWO")
    parser.add_argument("--period", default="2y", help="yfinance 資料期間，預設 2y")
    parser.add_argument("--no-open", action="store_true", help="只輸出 HTML")
    parser.add_argument("--colors", choices=("tw", "us"), help="漲跌色：tw 紅漲綠跌；us 綠漲紅跌")
    args = parser.parse_args(argv)
    failed = False
    for raw in args.symbols:
        try:
            stock = fetch_stock(raw, args.period)
            payload = build_payload(stock, args.colors)
            path = write_html(payload)
            print(f"{stock.symbol} → {path}")
            if not args.no_open:
                if not webbrowser.open(path.as_uri()):
                    print(f"提醒：瀏覽器未自動開啟，請手動開啟 {path}", file=sys.stderr)
        except Exception as exc:
            failed = True
            print(f"錯誤 [{raw}]：{exc}", file=sys.stderr)
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
