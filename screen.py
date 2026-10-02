"""Relative-strength screener. Run: python screen.py --top 30."""
from __future__ import annotations

import argparse
import csv
from datetime import datetime
import json
import os
from pathlib import Path
import sys

import pandas as pd


ROOT = Path(__file__).resolve().parent
UNIVERSE = ROOT / "universe.csv"
RESULT_NAME = "screen_result.json"
BENCH = {"us": "SPY", "tw": "0050.TW"}
FX = "TWD=X"
MIN_BARS = 200
MIN_VOLUME = 500_000
MIN_VALUE_USD = 50_000_000
MIN_PRICE_US = 10
FUNNEL = ("universe", "data", "liquidity", "trend", "loose", "strict")
LABELS = {"universe": "範圍", "data": "資料", "liquidity": "流動性", "trend": "趨勢", "loose": "寬鬆", "strict": "嚴格"}
DISCLAIMER = "本工具依歷史價格自動篩選，僅供輔助參考，不構成投資建議。"


class ScreenError(Exception):
    pass


def load_universe(path=UNIVERSE) -> list[dict]:
    with Path(path).open(encoding="utf-8-sig", newline="") as stream:
        return [{key: row[key] for key in ("ticker", "name", "market")} for row in csv.DictReader(stream)]


def threshold(b) -> float:
    return float(2 * b) if b > 0 else 0.0


def returns(c) -> tuple[float, float]:
    return float(c.iloc[-1] / c.iloc[-22] - 1), float(c.iloc[-1] / c.iloc[-127] - 1)


def metrics(c, v, market, fx) -> dict | None:
    if len(c) < MIN_BARS:
        return None
    r1m, r6m = returns(c)
    value = float((c * v).iloc[-10:].mean())
    if market == "tw":
        value /= fx
    return {"market": market, "price": float(c.iloc[-1]),
            "sma10": float(c.iloc[-10:].mean()), "sma20": float(c.iloc[-20:].mean()),
            "sma200": float(c.iloc[-200:].mean()),
            "vols": [float(v.iloc[-n:].mean()) for n in (10, 30, 60, 90)],
            "value_usd": float(value), "r1m": r1m, "r6m": r6m}


def stage(m, bench) -> int:
    if m is None:
        return 0
    if (min(m["vols"]) < MIN_VOLUME or m["value_usd"] < MIN_VALUE_USD
            or (m["market"] == "us" and m["price"] < MIN_PRICE_US)):
        return 1
    if m["price"] < m["sma200"] or m["sma10"] < m["sma20"]:
        return 2
    if m["r1m"] < threshold(bench["r1m"]):
        return 3
    if m["r6m"] < threshold(bench["r6m"]):
        return 4
    return 5


def download(tickers) -> tuple[pd.DataFrame, pd.DataFrame]:
    import yfinance as yf

    tickers = list(tickers)

    def fetch(symbols):
        return yf.download(symbols, period="1y", interval="1d", auto_adjust=True,
                           progress=False, threads=True, group_by="column")

    def field(frame, name, symbols):
        if name not in frame:
            return pd.DataFrame(index=frame.index)
        value = frame[name]
        if isinstance(value, pd.Series):
            value = value.to_frame()
        if len(symbols) == 1 and name in value.columns:
            value = value.rename(columns={name: symbols[0]})
        return value

    data = fetch(tickers)
    if data is None or data.empty:
        raise ScreenError("下載失敗：沒有取得任何資料")
    close, volume = field(data, "Close", tickers), field(data, "Volume", tickers)
    missing = [ticker for ticker in tickers if ticker not in close or close[ticker].dropna().empty]
    if missing:
        try:
            data2 = fetch(missing)
            if data2 is not None and not data2.empty:
                close2, volume2 = field(data2, "Close", missing), field(data2, "Volume", missing)
                ok = [ticker for ticker in missing if ticker in close2 and not close2[ticker].dropna().empty]
                if ok:
                    close = close2[ok].combine_first(close.drop(columns=ok, errors="ignore"))
                    volume = volume2.reindex(columns=ok).combine_first(volume.drop(columns=ok, errors="ignore"))
        except Exception:
            pass
    return close, volume


def run(universe=None, fetch=None, now=None) -> dict:
    universe = load_universe() if universe is None else universe
    fetch = download if fetch is None else fetch
    now = datetime.now().astimezone() if now is None else now
    tickers = [item["ticker"] for item in universe] + list(BENCH.values()) + [FX]
    try:
        close, volume = fetch(tickers)
    except ScreenError:
        raise
    except Exception as exc:
        raise ScreenError(f"下載失敗：{exc}") from exc

    def series(ticker):
        return close[ticker].dropna() if ticker in close else pd.Series(dtype=float)

    fx_series = series(FX)
    if fx_series.empty:
        raise ScreenError("缺少匯率 TWD=X 資料")
    fx = float(fx_series.iloc[-1])
    benchmarks = {}
    for market, ticker in BENCH.items():
        c = series(ticker)
        if len(c) < 127:
            raise ScreenError(f"缺少大盤 {ticker} 資料")
        r1m, r6m = returns(c)
        benchmarks[market] = {"ticker": ticker, "r1m": r1m, "r6m": r6m}
    funnel = dict.fromkeys(FUNNEL, 0)
    funnel["universe"] = len(universe)
    rows, missing = [], []
    for item in universe:
        ticker, market = item["ticker"], item["market"]
        c = series(ticker)
        if c.empty:
            missing.append(ticker)
            continue
        v = volume[ticker].reindex(c.index).fillna(0) if ticker in volume else pd.Series(0.0, index=c.index)
        m = metrics(c, v, market, fx)
        s = stage(m, benchmarks[market])
        for key in FUNNEL[1:s + 1]:
            funnel[key] += 1
        if s >= 4:
            rows.append({"ticker": ticker, "name": item["name"], "market": market,
                         **{key: m[key] for key in ("price", "r1m", "r6m", "value_usd")},
                         "level": "嚴格" if s == 5 else "寬鬆"})
    rows.sort(key=lambda row: (row["level"] != "嚴格", -row["r1m"]))
    return {"run_at": now.isoformat(timespec="seconds"), "fx": fx, "benchmarks": benchmarks,
            "funnel": funnel, "rows": rows, "missing": missing}


def save(result, path):
    path = Path(path)
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(json.dumps(result, ensure_ascii=False, indent=1), encoding="utf-8")
    os.replace(temporary, path)


def load(path) -> dict | None:
    try:
        result = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return result if isinstance(result, dict) and isinstance(result.get("rows"), list) else None


def report(result, top) -> str:
    def percent(value):
        return f"{value * 100:+.1f}%"

    lines = [f"執行時間：{result['run_at']}　USD/TWD：{result['fx']:.4f}",
             "漏斗：" + " → ".join(f"{LABELS[key]} {result['funnel'][key]}" for key in FUNNEL)]
    for benchmark in result["benchmarks"].values():
        lines.append(f"大盤 {benchmark['ticker']}：1M {percent(benchmark['r1m'])} / 6M {percent(benchmark['r6m'])}")
    missing = result["missing"]
    lines.append(f"缺資料（{len(missing)} 檔）：" + ("、".join(missing[:10]) + ("…" if len(missing) > 10 else "") if missing else "無"))
    if result["rows"]:
        lines.append("等級\t代碼\t市場\t現價\t1M\t6M\t10 日均成交額（百萬 USD）\t名稱")
        for row in result["rows"][:top]:
            lines.append(f"{row['level']}\t{row['ticker']}\t{'台股' if row['market'] == 'tw' else '美股'}\t"
                         f"{row['price']:.2f}\t{percent(row['r1m'])}\t{percent(row['r6m'])}\t"
                         f"{row['value_usd'] / 1e6:.2f}\t{row['name']}")
        if len(result["rows"]) > top:
            lines.append(f"另有 {len(result['rows']) - top} 檔")
    else:
        lines.append("沒有符合條件的股票")
    return "\n".join(lines + [DISCLAIMER])


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="台美股強勢股篩選")
    parser.add_argument("--top", type=int, default=30)
    args = parser.parse_args(argv)
    if args.top < 1:
        print("錯誤：--top 必須 ≥1。", file=sys.stderr)
        return 1
    print("下載資料中（約 10–60 秒）…", flush=True)
    try:
        result = run()
    except ScreenError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    directory = Path(os.environ.get("STOCK_CHART_DATA_DIR", ROOT))
    directory.mkdir(parents=True, exist_ok=True)
    save(result, directory / RESULT_NAME)
    print(report(result, args.top))
    print("網頁 /screen 可看完整清單。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
