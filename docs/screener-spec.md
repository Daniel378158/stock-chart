# 強勢股篩選器實作規格

修改篩選規則時，要同步更新這份文件與測試。公式、索引、比較邊界與股票池須與對照實作一致。

本功能只使用 stdlib 與專案既有套件，不改變 `chart.py` 既有行為。所有自動化測試使用合成資料或假下載，不連網。

## 1. 新檔案 `universe.csv`（篩選範圍，609 檔）

- 格式：UTF-8（無 BOM）、LF 換行。欄位固定為 `ticker,name,market`。
- `market` 用小寫 `us` / `tw`，和 `chart.Stock.market` 一致。
- 美股 `name` 留空字串。台股 `name` 填下面清單提供的中文名。
- 排列順序：先美股（照下面順序），再台股（照下面順序）。
- **代碼不可增減，也不可修改**，包括 `BRK-B`、`BF-B` 的連字號（Yahoo 用 `-`）。總數必須是 609，其中美股 559、台股 50，不可重複。
- 新增 `.gitattributes`，內容為 `universe.csv text eol=lf`，避免 Windows 的 Git 在 checkout 時把換行轉成 CRLF。
- 精確格式：第一列是 `ticker,name,market`；美股列像 `MMM,,us`；台股列像 `2330.TW,臺積電,tw`；不加引號；每一列（含最後一列）都以 `\n` 結尾。
- **完整性檢查**：這份清單是複製貼上傳過來的，可能在途中損壞。寫完後執行下方的 hash 指令，`universe.csv` 的 SHA-256 必須是 `eddff1aafae3defad8d39196d11ecc2a840d1b0aba04ac8659984099a8f0e369`。如果不一致，**先停下來告訴我**，不要自行猜測修補。

```
python -c "import hashlib,sys;[print(hashlib.sha256(open(p,'rb').read().replace(b'\r\n',b'\n')).hexdigest(),p) for p in sys.argv[1:]]" universe.csv docs/backtest-findings.md
```

美股 559 檔：完整清單見 `universe.csv`。

台股 50 檔：完整清單見 `universe.csv`。

## 2. 新檔案 `screen.py`（篩選核心＋CLI）

### 常數（名稱照用）

```python
ROOT = Path(__file__).resolve().parent
UNIVERSE = ROOT / "universe.csv"
RESULT_NAME = "screen_result.json"
BENCH = {"us": "SPY", "tw": "0050.TW"}
FX = "TWD=X"            # 1 USD 可換多少 TWD
MIN_BARS = 200
MIN_VOLUME = 500_000
MIN_VALUE_USD = 50_000_000
MIN_PRICE_US = 10
FUNNEL = ("universe", "data", "liquidity", "trend", "loose", "strict")
LABELS = {"universe": "範圍", "data": "資料", "liquidity": "流動性", "trend": "趨勢", "loose": "寬鬆", "strict": "嚴格"}
DISCLAIMER = "本工具依歷史價格自動篩選，僅供輔助參考，不構成投資建議。"
class ScreenError(Exception): ...
```

### 函式（簽名與行為照寫）

以下 `c` 是單一股票的收盤價 `pd.Series`（已 `dropna()`，時間由舊到新），`v` 是同索引的成交量 Series。

- `load_universe(path=UNIVERSE) -> list[dict]`
  - 用 `csv.DictReader` 讀取，encoding 用 `utf-8-sig`，相容有 BOM 的檔案。
  - 每列回傳 `{"ticker", "name", "market"}`。

- `threshold(b) -> float`：`b > 0` 時回傳 `2 * b`，否則回傳 `0.0`。大盤下跌時，門檻就是 0。

- `returns(c) -> (r1m, r6m)`
  - `r1m = c.iloc[-1] / c.iloc[-22] - 1`
  - `r6m = c.iloc[-1] / c.iloc[-127] - 1`
  - 兩者都轉成 `float`。**索引就是 -22 和 -127**。

- `metrics(c, v, market, fx) -> dict | None`
  - `len(c) < MIN_BARS` 時回傳 `None`。
  - 否則回傳：
    - `market`
    - `price = c[-1]`
    - `sma10 = mean(c[-10:])`
    - `sma20 = mean(c[-20:])`
    - `sma200 = mean(c[-200:])`
    - `vols = [mean(v[-n:]) for n in (10, 30, 60, 90)]`
    - `value_usd`：先算 `mean((c * v)[-10:])`（先逐日相乘再取平均）。`market == "tw"` 時再除以 `fx`。
    - `r1m`、`r6m`（來自 `returns`）
  - 全部轉成 `float`。

- `stage(m, bench) -> int`
  - `bench` 是該市場的 `{"r1m", "r6m"}`。依序判斷，回傳第一個符合的值：
    1. `m is None` → `0`
    2. `min(vols) < MIN_VOLUME`，或 `value_usd < MIN_VALUE_USD`，或（`market == "us"` 且 `price < MIN_PRICE_US`）→ `1`
    3. `price < sma200`，或 `sma10 < sma20` → `2`（剛好相等算通過）
    4. `r1m < threshold(bench["r1m"])` → `3`
    5. `r6m < threshold(bench["r6m"])` → `4`
    6. 其他 → `5`
  - 意義：4＝寬鬆名單，5＝嚴格名單。

- `download(tickers) -> (close_df, volume_df)`
  - 函式內才 `import yfinance as yf`。
  - 呼叫：`yf.download(list, period="1y", interval="1d", auto_adjust=True, progress=False, threads=True, group_by="column")`
  - 取 `df["Close"]` 與 `df["Volume"]`，兩者都是「欄＝代碼」的 DataFrame。用一個小 helper 統一處理 yfinance 不同版本的格式：
    - `name not in df` 時，回傳 `pd.DataFrame(index=df.index)`（空欄）。
    - `df[name]` 是 Series 時，轉成單欄 DataFrame。
    - 只下載 1 個代碼、而且欄名是 `"Close"` / `"Volume"` 而非代碼時，把欄名改成該代碼。重抓時常只有 1 檔，一定會遇到這種情況。
  - 第一次下載結果是 `None` 或空的：raise `ScreenError("下載失敗：沒有取得任何資料")`。
  - Yahoo 偶爾會隨機漏掉幾檔，所以要**重抓一次**：
    - 找出 `close` 裡缺欄、或整欄 `dropna()` 後是空的代碼，只對這些代碼再呼叫一次。
    - `ok` ＝重抓後 `close2` 中有欄位、且 `dropna()` 後不為空的代碼。
    - 用重抓的資料取代舊欄位：`close = close2[ok].combine_first(close.drop(columns=ok, errors="ignore"))`。volume 要寫成 `volume2.reindex(columns=ok).combine_first(...)`，因為重抓結果可能有 Close 卻沒有 Volume 欄，直接 `volume2[ok]` 會 KeyError。
    - 整段重抓用 `try/except Exception: pass` 包起來。重抓失敗、重抓結果為空或 `ok` 為空時，保留第一次的結果，不 raise。
  - yfinance 漏檔時會在 stderr 印出錯誤訊息，這是正常現象，不用處理。
  - 只重抓一次，不重複。

- `run(universe=None, fetch=None, now=None) -> dict`
  - 預設值：`universe = load_universe()`、`fetch = download`、`now = datetime.now().astimezone()`。
  - **只呼叫一次** `fetch(全部 universe 代碼 + ["SPY", "0050.TW"] + ["TWD=X"])`。
    - `ScreenError` 原樣往外拋。
    - 其他例外包成 `ScreenError(f"下載失敗：{e}")`。
  - 取單一代碼序列時，一律用 `frame[ticker].dropna()`。美股和台股的交易日不同，聯集索引中會有 NaN，**必須逐檔 dropna**。代碼不在欄位中或 dropna 後為空，視為沒有資料。
  - FX 沒有資料：raise `ScreenError("缺少匯率 TWD=X 資料")`。`fx` 取最後一筆收盤。
  - 兩個大盤任一沒有資料，或 `len < 127`：raise `ScreenError(f"缺少大盤 {ticker} 資料")`。
    - 其他情況：`benchmarks[market] = {"ticker", "r1m", "r6m"}`。
  - 漏斗計數：
    - `funnel` 初始全為 0，`funnel["universe"] = len(universe)`。
    - 每檔股票：
      - 沒有收盤資料：加入 `missing`（保留 universe 原順序），不計入任何後續階段。
      - 有資料：`v = volume[ticker].reindex(c.index).fillna(0)`；若 volume 沒有這欄，就用全 0。
      - 算出 `s = stage(metrics(...), benchmarks[market])` 後，`FUNNEL[1 : s + 1]` 的每一項各加 1。
      - 因此 `data` 計數＝有 ≥200 根的股票數，後面各階段為累積通過數。
  - 每檔 `s >= 4` 時輸出一列：`{"ticker", "name", "market", "price", "r1m", "r6m", "value_usd", "level"}`，其中 `level` 為 `"嚴格"`（s==5）或 `"寬鬆"`（s==4）。
  - 排序：`rows.sort(key=lambda r: (r["level"] != "嚴格", -r["r1m"]))`，嚴格在前，再依 1M 報酬由高到低。
  - 回傳：`{"run_at": now.isoformat(timespec="seconds"), "fx": fx, "benchmarks": {...}, "funnel": {...}, "rows": [...], "missing": [...]}`。

- `save(result, path)`
  - 原子寫入：先寫到同目錄的 `path.name + ".tmp"`，再 `os.replace`。
  - 用 `json.dumps(..., ensure_ascii=False, indent=1)`，UTF-8。

- `load(path) -> dict | None`
  - 檔案不存在、無法讀取、不是 UTF-8、JSON 壞掉、不是 dict，或 `rows` 不是 list 時，回傳 `None`，不 raise。
  - 實作上 catch `(OSError, ValueError)`。`UnicodeDecodeError` 和 `json.JSONDecodeError` 都是 `ValueError` 的子類別。

- `report(result, top) -> str`：純文字報表，依序包含：
  - 執行時間與 USD/TWD 匯率
  - 「漏斗：範圍 609 → 資料 … → 嚴格 …」
  - 兩個大盤的 1M、6M
  - 缺資料清單：最多列 10 個，超過加「…」
  - 表格：等級、代碼、市場、現價、1M、6M、10 日均成交額（百萬 USD）、名稱。前 `top` 列，超過時提示「另有 N 檔」。
  - 沒有任何結果時顯示「沒有符合條件的股票」。
  - 最後一行是 `DISCLAIMER`。
  - 百分比格式：`f"{x*100:+.1f}%"`。

- `main(argv=None) -> int`
  - argparse 參數：`--top`，預設 30，必須 ≥1，否則印錯誤並回傳 1。
  - 印「下載資料中（約 10–60 秒）…」，然後 `run()`。`ScreenError` 時印到 stderr 並回傳 1。
  - 存檔到 `Path(os.environ.get("STOCK_CHART_DATA_DIR", ROOT)) / RESULT_NAME`，目錄不存在就建立。
  - 印出 `report`，並提示網頁 `/screen` 可看完整清單。
  - 加上 `if __name__ == "__main__": raise SystemExit(main())`。

## 3. `server.py` 整合（沿用現有寫法）

- `StockServer.__init__` 新增參數 `screener=None`：
  - `self.screener = screener or screen.run`
  - `self.screen_path = self.directory / screen.RESULT_NAME`
  - `self.screen_lock = threading.Lock()`
- `GET /screen`：回傳 `screen.html`（`text/html; charset=utf-8`）。
- `GET /api/screen`：回傳 `{"result": screen.load(self.server.screen_path)}`，尚未執行過時為 `null`。
- `POST /api/screen`：
  - 一樣先經過 `trusted_request()`，跨來源請求回 403。
  - body 必須是 `application/json` 的空物件 `{}`。`Content-Length` 必須在 1–512 之間，否則回 400。
  - 用 `self.server.screen_lock.acquire(blocking=False)` 防止重複執行。拿不到鎖時回 `409 {"error": "篩選正在執行中，請稍候。"}`。
  - 拿到鎖後，在 `with self.server.search_lock:` 內呼叫 `self.server.screener()`。
    - 原因：yfinance 共用 logging 狀態，現有程式已用 search_lock 序列化下載。執行期間個股搜尋會等待，請在 README 註明。
  - 成功：`screen.save(...)`，回 `200 {"result": result}`。
  - `ScreenError` 時回 `502 {"error": str(e)}`；其他例外回 `502 {"error": "篩選失敗，請稍後重試。"}`。**失敗時不可覆蓋舊的結果檔**。
  - 用 `try/finally` 確保 `screen_lock.release()`。
- `do_POST` 的路由在現有判斷前加上 `/api/screen`。`do_GET` 在 chart 路由之前加上 `/screen` 和 `/api/screen`。
- `respond()` 的 header（no-store、nosniff）沿用即可。

## 4. 新檔案 `screen.html`（網頁）

- 獨立頁面，`lang="zh-Hant"`。沿用 `home.html` 的深色風格與 CSS 變數（`--bg`、`--panel`、`--line`、`--ink`、`--muted`、`--gold`）。header 有回首頁的連結。
- 載入時 `GET /api/screen` 顯示上次結果。沒有結果時，提示「尚未執行，按下『執行篩選』」。
- 按鈕 `id="screen-run"`「執行篩選」：
  - 送出 `POST /api/screen`，headers 帶 `Content-Type: application/json`，body 為 `"{}"`。
  - 執行中停用按鈕，並顯示「下載約 609 檔資料，約 10–60 秒…」。
  - 409、502 時顯示伺服器回傳的錯誤訊息。
- 摘要區顯示：
  - 執行時間、USD/TWD 匯率
  - 兩個大盤的 1M、6M，以及換算後的門檻（`threshold`，0 時寫「0（大盤下跌）」）
  - 漏斗「範圍 → 資料 → 流動性 → 趨勢 → 寬鬆 → 嚴格」的各階段數字
  - 缺資料檔數（可展開清單）
- 表格欄位：等級、代碼、名稱（美股空白時顯示「—」）、市場（美股／台股）、現價、1M、6M、10 日均成交額（百萬 USD）。
  - 篩選切換：等級（全部／嚴格／寬鬆）、市場（全部／美股／台股）。
  - 點欄位標題可排序。預設順序同伺服器（嚴格在前、1M 由高到低）。**6M 欄要能排序**，回測顯示名單內 6M 漲幅越高，後續表現越好。
    - 數字欄（現價、1M、6M、成交額）第一次點擊要**由高到低**，文字欄第一次點擊由小到大。再點同一欄就反轉方向。
    - 目前排序的欄位標題後面加上 ` ▲` 或 ` ▼`，並設定 `aria-sort`（`ascending`、`descending` 或 `none`）。
- 點代碼：
  - 送出 `POST /api/search`，body 為 `{"symbol": ticker}`；成功後 `location.href = 回傳的 url`。
  - 失敗時顯示錯誤。
  - 原因：`/chart/<SYM>` 只對已搜尋過的股票存在。
- **所有資料一律用 `textContent` 或 `createElement` 輸出，不可把 API 資料拼進 `innerHTML`。**
- 頁尾放上 DISCLAIMER，並說明「名單是候選清單，不是買進訊號」。

## 5. 首頁入口、Docker、CI、ignore

- `home.html`：在 header 的 `<nav>` 加上連到 `/screen` 的「強勢股篩選」連結。可在「快速開始」區塊旁加一張入口卡片，風格一致。不要破壞 `__SEARCH__`、`__RECENT__` 佔位字串。
- `Dockerfile`：`COPY` 清單加入 `screen.py universe.csv screen.html`。漏掉的話，容器內會 404 或 500。
- `.gitignore`、`.dockerignore`：加上 `screen_result.json` 和 `screen_result.json.tmp`。
- `ci.yml` smoke test：多檢查 `curl --silent --fail http://127.0.0.1:18765/screen | grep -q 'screen-run'`。**不要在 CI 呼叫 `POST /api/screen`**，因為它會連網。

## 6. 測試（全部不連網）

新增 `tests/test_screen.py`，至少涵蓋：

1. `threshold`：正數加倍；0 與負數都回傳 0。
2. `load_universe`：實際讀取 repo 的 `universe.csv`。
   - 共 609 檔，美股 559、台股 50，代碼不重複，`market` 只有 `us`/`tw`。
   - 包含 `AAPL`、`BRK-B`、`TSM`、`ASML`、`ALAB`、`2330.TW`，且 `2330.TW` 的名稱是「臺積電」。
3. `metrics`：用合成序列驗證 sma、vols、`value_usd`（台股要除以 fx）、`r1m` 與 `r6m` 的精確索引；`199` 根回傳 `None`，`200` 根則有值。
4. `stage`：參數化測試每一個結果 0–5，含邊界：
   - 量剛好 500000 通過；499999 不通過。
   - 美股價 9.99 不通過；台股價格低不受限。
   - `price == sma200` 通過。
   - 大盤 r1m 為負時，門檻是 0。
5. `run`：用假 fetch 回傳合成 close 與 volume DataFrame（含 SPY、0050.TW、TWD=X，以及美台交易日不同、互有 NaN 的情況）。驗證漏斗計數、rows 內容與排序、`missing`、大盤數字。
   - 缺 FX、缺大盤或大盤不足 127 根時，raise `ScreenError`，訊息含代碼。
   - fetch 拋一般例外時，包成 `ScreenError`。
6. `download`（monkeypatch 一個假的 `yfinance` 模組或 `yf.download`）：
   - 缺資料的代碼只重抓一次，且只對缺的代碼重抓。
   - 重抓只有 1 檔、而且回傳的欄名是 `"Close"` / `"Volume"` 時，仍能正確補回。
   - 沒有缺資料時不重抓。
   - 第一次結果為空時 raise。
   - 重抓結果為空時保留原結果。
7. `save`/`load`：來回一致；壞 JSON、非 UTF-8 位元組（例如 `b"\xff\xfe"`）、`rows` 不是 list、檔案不存在都回傳 `None`；沒有殘留 `.tmp`。
8. `report`：包含漏斗、大盤、列、缺資料與 DISCLAIMER。
9. `main`：
   - 正常時存檔到 `STOCK_CHART_DATA_DIR`，回傳 0。
   - `ScreenError` 時回傳 1。
   - `--top 0` 時回傳 1。

在 `tests/test_server.py` 新增（用 `StockServer(..., screener=假函式)` 注入）：

- `GET /screen` 回 200 html，且含 `screen-run`。
- `GET /api/screen` 首次回 `{"result": null}`。
- `POST /api/screen` 成功：回 200，並寫入 `screen_result.json`，之後 `GET /api/screen` 讀得到。
- 假 screener raise `ScreenError`：回 502，舊結果檔不被覆蓋。
- 執行中再送一次：回 409。可用 `threading.Event` 卡住假 screener 來測試。
- 跨來源 Origin 回 403。
- body 非 `{}`，或 Content-Type 不是 JSON：回 400。
- 首頁含 `/screen` 連結。
