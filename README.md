# 台美股互動日K

Python 3.10+。深色行情介面，使用 **Lightweight Charts 4.2.3 CDN**。網站有搜尋首頁與動態圖表頁，圖表資料集中保存在一個 SQLite 快取；`chart.py` CLI 仍可另外輸出獨立 HTML。圖表函式庫需連網載入。

## Docker 啟動

安裝並啟動 Docker Desktop 後，在本目錄執行：

```powershell
docker compose up --build -d
```

打開 `http://127.0.0.1:8765/` 進入首頁搜尋股票。`compose.yaml` 僅映射到主機本機位址，`charts.sqlite3` 快取保存在 `chart-data` volume；瀏覽器搜尋歷史仍存在瀏覽器 localStorage。關閉服務：`docker compose down`（不刪除資料卷）。若本機已在執行 `python server.py`，先將該服務停止以釋放 8765 埠。

### AI 對話

右側「AI 對話」可詢問目前股票的支撐壓力、近期訊號和指標。這項功能使用 [OpenAI Responses API](https://platform.openai.com/docs/api-reference/responses)，需要自己的 OpenAI API 金鑰；ChatGPT 訂閱不等於 API 額度。將 `.env.example` 複製成 `.env`，在 `OPENAI_API_KEY=` 後填入金鑰，再執行 `docker compose up --build -d`。金鑰只由伺服器讀取，前端不會收到；`.env` 已排除於 Git 和 Docker 映像之外。可以用 `OPENAI_MODEL` 調整模型，預設 `gpt-4.1-mini`。

若直接執行 `python server.py`，請先在該終端的環境變數設定 `OPENAI_API_KEY`。獨立開啟的 `*_chart.html` 沒有伺服器，AI 對話無法使用。問題與目前圖表的精簡摘要會送到 OpenAI API；對話只存在當前頁面，重新整理後清除。回答以圖表快照為依據，沒有即時行情或新聞存取能力，不構成投資建議。

也可不使用 Compose：

```powershell
docker build -t stock-chart .
docker run --rm -p 127.0.0.1:8765:8765 -v stock-chart-data:/app/charts stock-chart
```

GitHub Actions 會在 `main` 的推送及 PR 上執行 pytest、建置映像並啟動容器檢查搜尋首頁；此流程只驗證映像，不會將映像推送到任何 registry。

## 執行（Windows PowerShell）

```powershell
cd C:\Users\danie\stock-chart
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements-dev.txt
python chart.py MU
python chart.py MU 2330 LITE
python chart.py 6488 2330.TW 6488.TWO --period 5y --no-open
python chart.py AAPL 2330 --colors tw
python -m pytest -q
```

已建立 `.venv` 時，不必重新建立或安裝。若 PowerShell 不允許啟用腳本，可直接使用：

```powershell
.\.venv\Scripts\python.exe chart.py MU 2330
.\.venv\Scripts\python.exe -m pytest -q
```

- 美股直接輸入代碼，轉成大寫；純數字依序試 `.TW`、`.TWO`。已指定後綴就只嘗試該代碼。
- 預設 `--period 2y`，接受 yfinance 支援的期間，如 `6mo`、`1y`、`5y`、`max`。
- 每檔輸出到本工具目錄，例如 `MU_chart.html`、`2330.TW_chart.html`；重跑同檔會更新 HTML。
- 預設開啟每個成功輸出的檔案；`--no-open` 只輸出。單檔失敗不阻斷後續代碼，任一檔失敗時程式退出碼為 1，全成功為 0。
- 台股預設紅漲綠跌，美股預設綠漲紅跌；`--colors tw|us` 可統一覆寫。支撐帶固定綠色、壓力帶固定紅色。
- 公司名使用 `shortName`，抓不到時只顯示代碼。台股 TWD、美股 USD；美股兩位小數，台股各個價格值 ≥100 時一位，否則兩位。

## 強勢股篩選

從固定的 609 檔台美股快照，篩出相對大盤強勢且趨勢、流動性合格的候選股。範圍由 S&P 500、Nasdaq-100 額外 15 檔、41 檔大型 ADR 與台灣 50 組成（美股 559、台股 50）。這不是即時成分股名單；成分股變動時要手動更新 `universe.csv`，並同步更新規格、完整性雜湊及測試。

| 階段 | 累積通過條件 |
|---|---|
| 資料 | 每檔收盤序列分別移除 NaN 後至少 200 根日線 |
| 流動性 | 10／30／60／90 日均量皆 ≥50 萬股；10 日均成交額 ≥5,000 萬美元；美股價 ≥10 美元 |
| 趨勢 | 收盤 ≥SMA200，且 SMA10 ≥SMA20 |
| 寬鬆 | 1M 報酬 ≥2 倍同期大盤報酬 |
| 嚴格 | 再加上 6M 報酬 ≥2 倍同期大盤報酬 |

大盤使用美股 `SPY`、台股 `0050.TW`；當對應期間大盤報酬 ≤0，該期間門檻為 0。1M 使用最後收盤除以倒數第 22 筆，6M 除以倒數第 127 筆，再減 1。成交額先逐日以「收盤 × 成交量」計算，再取最近 10 筆平均；台股除以 `TWD=X` 最後匯率換算美元。剛好達到所有門檻都算通過；嚴格名單也包含在寬鬆階段的漏斗數字內。

網頁：從首頁點「強勢股篩選」，或開啟 `http://127.0.0.1:8765/screen`，按「執行篩選」。可依等級／市場篩選，點欄位標題排序；數字第一次點擊由高到低，包含 6M。點代碼先搜尋個股，再開啟其圖表。執行通常約 10–60 秒，資料來源較慢時可能更久。CLI：

```powershell
python screen.py --top 30
# 使用專案虛擬環境：
.\.venv\Scripts\python.exe screen.py --top 30
# 使用正在執行的容器，與網頁共用結果：
docker compose exec stock-chart python screen.py --top 30
```

結果存放在 `STOCK_CHART_DATA_DIR/screen_result.json`；未設定環境變數時存放於專案根目錄。Docker 使用 `/app/charts/screen_result.json`，由既有 `chart-data` volume 保存。網頁載入時顯示上次結果，執行成功後原子取代；失敗保留舊結果。CLI 與網頁要使用相同資料目錄，才能共用結果。功能提供手動網頁與 CLI 執行入口，不會自行在背景啟動每日篩選。

篩選與個股下載共用 `search_lock`，**執行期間個股搜尋與行情更新會暫停等待**。同時再次要求篩選回傳 409；下載失敗回傳 502。前端以純文字 DOM 輸出資料，不將 API 內容拼入 HTML。

限制：Yahoo 資料可能延遲或漏檔，漏掉的代碼會自動重抓一次，仍沒有收盤資料者列在缺資料清單。少於 200 根的股票不通過「資料」階段。美台交易日分別逐檔計算；本功能照指定公式使用下載結果最後一筆，未額外移除盤中日線。台積電占 0050 權重很高，2330 幾乎不可能跑贏 2 倍上漲的 0050，結構上很難入選。**名單只是候選清單，不是買進訊號。**

另一套相同規則的 2022–2026 回測顯示，名單內依 6M 漲幅排序有參考價值；進榜天數長短與「等拉回再買」都沒有明顯優勢。該回測有倖存者偏差，期間多為多頭，僅供參考，非本專案重新驗證的績效。詳細數字、建議的出場規則與案例見 [回測結論](docs/backtest-findings.md)，完整實作規格見 [篩選器規格](docs/screener-spec.md)。

本工具依歷史價格自動篩選，僅供輔助參考，不構成投資建議。

## 圖表與計算規則

### 網頁搜尋與搜尋紀錄

要直接在頁面上搜尋股票，啟動本機網站：

```powershell
cd C:\Users\danie\stock-chart
.\.venv\Scripts\python.exe server.py
```

瀏覽器會開啟 `http://127.0.0.1:8765/` 首頁。輸入 `MU`、`2330`、`6488` 或完整後綴後按 Enter／搜尋，網站會抓取行情並切換至 `/chart/代碼`；查詢失敗會保留原圖並顯示原因。網站搜尋不建立每檔 HTML，同一代碼只更新資料庫內的一筆快取。伺服器也接受 `--period 5y`、`--colors tw`、`--port 8766`、`--no-open`，按 Ctrl+C 結束。

網站載入時會檢查一次行情，之後頁面可見時每 2 分鐘檢查；「立即更新」可手動檢查。後端同一代碼至少間隔 60 秒才再次向 Yahoo 抓取。來源有新資料時圖表自動重新載入；如果正在 AI 對話，會顯示「載入新行情」按鈕，避免打斷輸入。標題顯示資料日期、盤中未確認狀態及台北時間的抓取時間。這是定期更新 Yahoo 的日 K，**不保證交易所等級的即時報價**；獨立開啟的 HTML 仍是靜態快照。

成功搜尋會保存最近 20 個代碼（依正規化代碼去重、最新在前），點選紀錄可重新查詢最新行情。可用 × 移除單筆或「清除全部」。紀錄存放於這個瀏覽器、這個網站位址的 localStorage；重新整理或關閉再開仍保留，無痕模式／清除瀏覽資料／改用不同埠號則不共用。

原本 `python chart.py MU` 的獨立 HTML 輸出照常使用；直接開啟檔案時仍可看圖，**網頁搜尋需經由上述本機網站**。服務僅綁定本機位址，沒有開放到區域網路。啟動新版服務時會把既有 `*_chart.html` 匯入 SQLite 快取，舊檔與舊網址仍保留；不會自動刪除原檔。之後從網站搜尋不會再產生這類檔案。

主圖含 MA5/20/60/120/250、BOLL(20,2)、S/R 色帶與訊號標記，下方有成交量及 ATR(14)。可用滑鼠縮放、平移、十字游標看價；可切換 MA、BOLL、S/R，或返回最後 150 根。MA120/250 與色帶不參與主價格軸自動縮放。

訊號以簡潔圖示表示：藍色圓點＝拉回、金色箭頭＝突破回測、紫色方塊＝同日兩類訊號；盤中保留 `?`。滑鼠移入訊號日期可看提示，點選該日 K 線會打開右側「訊號解讀」。也可從最近訊號卡或日期選單選取歷史訊號並定位圖表。解讀分頁顯示當時的區間、進場／停損／目標、R:R、風險距離與 ATR，並提供兩種訊號的結構示意圖與規則說明；原有行情資料留在「行情總覽」。工具列的「訊號」可隱藏圖上標記。

OHLC 透過 `Ticker.history(interval="1d", auto_adjust=True)` 取得。均線採簡單平均，BOLL 採 20 根收盤母體標準差（`ddof=0`）；ATR 採 Wilder：第一根 TR=高−低，首 14 根 TR 平均為種子，之後 `(前 ATR×13+TR)/14`，不足暖機期留空。

波段點使用左右各 5 根；必須等右邊 5 根完成才確認。同價平台取第一個符合左側嚴格極值的點。高、低波段點共同按價格排序，以相鄰差 ≤0.5×ATR 且整群寬 ≤1×ATR 合併；區上下緣為群最低、最高價，不足 0.2×ATR 時以中心向兩側擴張。

強度為 `Σ(0.5+0.5×波段點零起算索引/當時計算資料長度)`。中心低於參考價為支撐，高於為壓力，等於時不屬於任一側。每側先選強度前 8 區，再選最近 4 區；同分用距離、價格確保結果穩定。

## 訊號的時間定義

- 只有第 61 根起計算訊號；資料 ≤60 根不算。以零起算索引 `t=60,65,70,…` 更新區間，嚴格只讀 `frame[:t]`；聚類 ATR 與分數分母也來自這份歷史。
- 五根之間沿用區間幾何與分數；每根先依**前一根收盤**分類／篩選支撐壓力，避免突破當根將原壓力重新歸類而漏掉突破。頁面表格依最新收盤分類，顯示最近一次已計算區間。
- 拉回：`close>MA60`、`low≤支撐上緣`、`close>上緣`、`close>open`、`low≥下緣−0.5×ATR`。
- 收盤突破：前收盤 `≤壓力上緣` 且本收盤 `>上緣`。凍結該區上下緣，自**下一根至第 10 根（含）**，滿足 `low≤上緣+0.25×ATR`、`close≥下緣`、`close>open` 即為突破回測。觸發後消耗該次突破設定；重算價位區不改變已凍結設定。
- 進場取訊號當根收盤，停損取 `區下緣−0.5×當根 ATR`。目標取當時已篩選壓力區中，下緣高於進場且最近的一區。`R:R=(目標−進場)/(進場−停損)`；無上方目標則目標、R:R 都為「—」。
- 同類訊號後的第 1–5 根不再觸發，最早第 6 根可再觸發。同日多區／多類可保留各筆交易資料，但合併為一個圖上標記；側欄列最近 6 筆。
- 圖上的色帶是**最新區間快照**，不是各歷史日期的區間回放。歷史標記使用當時快照，沒有拿今日區間回填。

## 盤中狀態

使用 yfinance `exchangeTimezoneName` 與最後一根日K的交易日期。沒有時區資訊時按市場回退：台股 `Asia/Taipei` 09:00–13:30，美股 `America/New_York` 一般 09:30–16:00。美股透過本地交易日曆處理夏令時間、假日與提早收盤，不額外連網。台股需有當日 K 且為平日、位於指定時段才判為盤中。

最後一根處於盤中時，其訊號標 `?` 並顯示「盤中未確認」。HTML 保留產生當下的狀態，收盤後請重新執行更新。Yahoo 資料可能延遲；時間判定不能保證行情供應商已完成最終修訂。

## 驗證

`python -m pytest -q` 全部使用合成 OHLC、mock 行情來源及本地日曆，不連網。包含代碼回退、多檔部分失敗、顏色、指標、群寬、打分、5 根冷卻、10 根回測邊界、區間快照、盤中時段及非空訊號的截斷／未來資料修改不變性測試。

強勢股篩選測試涵蓋 609 檔清單與文件 SHA-256、200 根／成交量／成交額／趨勢邊界、1M 與 6M 精確索引、美台不同交易日、台幣換算、漏斗及名單排序、只重抓一次與單檔下載格式、損壞結果檔、原子寫入、CLI、API 跨來源拒絕、409 併發鎖及失敗保留舊結果。Docker CI smoke test 只讀取 `/screen` 確認頁面存在，不呼叫會下載行情的 `POST /api/screen`。

這裡的防偷看保證是**同一份固定還原行情**下，移除／修改未來資料不改變過去訊號。Yahoo 之後因除權息調整歷史價格或修正資料，重新下載可能得到不同歷史值；此工具不保存當年供應商版本，也不將它宣稱為可成交回測。

不構成投資建議。

參考：[yfinance](https://ranaroussi.github.io/yfinance/reference/api/yfinance.download.html)、[Lightweight Charts 4.2](https://tradingview.github.io/lightweight-charts/docs/4.2)、[交易日曆](https://pandas-market-calendars.readthedocs.io/en/latest/usage.html)。圖表使用 TradingView Lightweight Charts™（Apache-2.0），頁面保留 TradingView 連結歸屬。
