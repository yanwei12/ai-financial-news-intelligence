# 新聞照妖鏡

**一個協助讀者看懂新聞標題、核對正文與外部證據的 AI 新聞分析平台。**

新聞標題有時會省略重要條件，或讓讀者產生與正文不同的理解。我們希望讓讀者不只看到 AI 的結論，也能直接查看判斷理由、原文段落與外部來源，自己核對新聞在說什麼。

專案從財經新聞分析出發，目前也支援一般公開新聞。

## 可以做什麼？

- **比對標題與正文**：拆解標題中的主張，判斷是否相符、缺少條件、互相矛盾或證據不足。
- **查看原文證據**：顯示引用段落與前後文，方便回到文章核對。
- **搜尋外部證據**：透過 Gemini 與 Google Search 尋找公開資料，另列支持或衝突的證據與來源連結。
- **保留分析紀錄**：重新查看過去的分析，或下載 JSON 結果。

## 怎麼使用？

1. 貼上新聞網址，或直接貼上標題與完整內文。
2. 按「分析標題」，查看主張、判斷理由與正文證據。
3. 想進一步核對時，按該項主張下的「搜尋外部證據」。

標題符合正文，不代表新聞內容一定正確；外部取證也會獨立呈現結果。AI 的整理仍可能有誤，請搭配原文與來源閱讀。需要登入、付費或動態載入的新聞，可能需手動貼上正文。

## 快速啟動：Docker

先安裝並啟動 Docker Desktop（或 Docker Engine 與 Compose），在專案根目錄執行。

### 1. 設定 API 金鑰

第一次使用時複製設定範本；已有 `.env` 可直接編輯，不需覆蓋。

```bash
cp .env.example .env
```

在 `.env` 填入 Google Gemini API 金鑰：

```dotenv
GEMINI_API_KEY=你的金鑰
GEMINI_MODEL=gemini-3.8-flash
# 外部搜尋可選擇另一個支援 Google Search 的模型，未設定時沿用上面的模型。
# GEMINI_SEARCH_MODEL=gemini-3.8-flash
```

分析會將輸入新聞傳送給 Google Gemini；外部取證還會使用 Google Search，可能產生 API 費用。請勿將 `.env` 提交至 GitHub。

### 2. 建置並啟動

```bash
docker compose up -d --build
```

開啟 [新聞照妖鏡](http://localhost:8000/check)，API 文件位於 [Swagger UI](http://localhost:8000/docs)。

```bash
docker compose logs -f   # 查看執行紀錄
docker compose down      # 停止服務，保留資料
```

預設只提供本機存取。若 8000 已被占用，可在 `.env` 加上 `APP_PORT=8001`，再以該連接埠開啟頁面。程式更新後，重新執行建置與啟動指令即可。

只想打包映像檔：

```bash
docker build -t news-mirror:latest .
```

映像檔不包含 API 金鑰或本機資料庫；Compose 會在啟動時讀入 `.env`，並掛載資料儲存空間。

## 本機開發

使用 Python 3.11 以上版本：

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
python -m uvicorn src.api.main:app --reload
```

Windows PowerShell 可用 `python -m venv .venv` 建立環境，再以 `.venv\Scripts\Activate.ps1` 啟用。API 金鑰同樣設定在 `.env`。

執行測試：

```bash
python -m pip install -r requirements-dev.txt
python -m pytest tests -q
```

測試使用模擬 Gemini，不耗用 API 額度。

## 資料怎麼儲存？

使用 **SQLite + SQLAlchemy**，啟動時自動建立需要的資料表。

| 資料表 | 儲存內容 |
| --- | --- |
| `headline_analyses` | 標題分析結果、輸入原文快照、引用段落、模型資訊與用量 |
| `external_verifications` | 每項主張的外部取證結果、來源連結、搜尋詞、查證時間與用量 |
| `articles` | 透過文章儲存 API 確認的文章與來源資訊 |
| `news` | 早期 RSS 收集的新聞與歷史欄位 |

目前主介面的分析紀錄主要寫入 `headline_analyses`，外部取證另存於 `external_verifications`。結果以 JSON 保存，方便還原當時的內容。

- **本機開發**：資料庫放在專案的 `data/news.db`。
- **Docker**：資料庫放在容器的 `/app/data/news.db`，由 Compose 的 `news-data` volume 持久保存；與本機開發資料庫分開。
- **快取**：相同文章、模型與提示詞版本會重用標題分析；外部取證快取 24 小時，到期後需再按按鈕才重新搜尋。

`docker compose down` 不會刪除資料；`docker compose down -v` 會刪除 volume 與其中的資料庫，請勿用於一般停止服務。

## 技術架構

前端使用 HTML、CSS 與 JavaScript，後端使用 FastAPI，透過 Gemini 進行分析、Google Search 進行外部取證。新聞正文由專用解析器及 Trafilatura 擷取。

主要程式位於 `src/api`（網頁與 API）、`src/ai`（AI 分析）、`src/ingestion`（新聞擷取）、`src/processing`（文字整理）與 `src/database`（資料儲存）。

目前適合本機研究與展示，尚未提供使用者登入或多人資料隔離。
