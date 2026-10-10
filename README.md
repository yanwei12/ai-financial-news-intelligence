# 新聞照妖鏡

貼上新聞網址或標題與內文，比對標題是否符合正文，顯示判斷理由與原文證據。

## 啟動

使用專案的 Python 虛擬環境執行：

```powershell
python -m uvicorn src.api.main:app --reload
```

開啟 http://127.0.0.1:8000/check；首頁 `/` 與舊 `/dashboard` 網址會轉到 `/check`。
在 `.env` 設定 `GEMINI_API_KEY`，可用 `GEMINI_MODEL` 指定模型。
貼上網址後按「分析標題」；目前支援 Yahoo 奇摩新聞／股市、公視新聞（news.pts.org.tw）、自由時報新聞（news.ltn.com.tw）。自由時報的其他子網域尚未支援。其他來源可切換成貼上標題與內文。
擷取不完整時會顯示提示，請補齊正文後再次分析。按分析會將文章傳送至 Google Gemini，可能使用 API 額度。

## 功能與資料

- `/check`：網址輸入；`/paragraphs`：文字輸入。
- `/articles/preview`：擷取文章；`/articles/prepare`：整理段落與引用位置。
- `/headline/analyze`：比對標題與正文，保存成功的分析結果。
- `/articles`：儲存確認的文章；`/articles/{id}`：讀取文章。
- `/news`、`/news/{id}`：保留既有 RSS 新聞的唯讀查詢。
- `/docs`：API 文件。

舊情緒分析表單、圖表、`/analyze`、`/analytics/sentiment` 與本機模型批次分析已移除。
既有 `data/news.db` 及歷史欄位保留，不刪除或重新分析舊資料。新聞照妖鏡不需要 torch 或 transformers。

## 測試

```powershell
python -m pytest tests -q
```

測試使用模擬 Gemini，不耗用 API 額度。

## 外部取證（Google Search + Gemini）

先完成標題比對，再按每項主張下的「搜尋外部證據」。也可開啟舊的分析紀錄再查證。
每次會把該篇新聞與待查主張交給 Gemini，啟用 Google Search；模型與搜尋可能各自計費。
原本的標題／正文相符程度保持不變，外部判斷另列「支持、衝突、部分支持／證據互異、證據不足」。

- 來源 URL 只接受 Gemini `groundingMetadata.groundingChunks`，證據摘要必須有 `groundingSupports` 對應，不採用模型自行撰寫的網址。
- 缺少實際搜尋詞或可引用證據時顯示「外部證據不足」；來源列表本身不算已查證。
- 引用旁可開啟來源；摘要是 AI 整理，並非來源逐字引文。搜尋引用不保證推論正確。
- 搜尋介面未提供可靠的來源發布日期，因此標為未知，不將查證時間冒充發布時間。模型說明中的日期仍需回到來源核對。
- 保存查證時間、模型、提示詞版本、搜尋詞、來源、證據及 token 用量。重新讀取歷史紀錄或下載 JSON 都會包含外部結果。
- 相同分析／主張／模型／提示詞版本 24 小時內重用快取；到期後再按按鈕才重新搜尋。歷史畫面始終顯示上次查證時間，不會自動產生費用。
- 搜尋失敗可重試，原分析與先前成功取證保留。單一程序同時只允許一個取證請求，忙碌時回 429。
- 只新增 `external_verifications` 表，不修改舊分析表結構。啟動時自動建立。

沿用 `.env` 中的 `GEMINI_API_KEY` 與 `GEMINI_MODEL`；可另設支援 Google Search 的 `GEMINI_SEARCH_MODEL`。
搜尋使用 REST `generateContent` 的 `google_search` 工具，不需要額外 SDK。

API：`POST /headline/history/{analysis_id}/claims/{claim_index}/verify`（主張索引從 0 起）。
`/headline/analyze` 與 `/headline/history/{analysis_id}` 都會回傳 `analysis_id`。
取證端點的輸入僅為已儲存的分析 ID 與主張索引，避免送入不同文章卻附加到舊分析。

Google 搜尋建議以受限 sandbox iframe 呈現；來源與摘要以文字節點呈現，不執行模型輸出的 HTML。
此原型沿用現有單機架構，沒有使用者登入與跨程序限流，公開部署需另配置。

參考：[Google Search grounding 文件](https://ai.google.dev/gemini-api/docs/generate-content/google-search)。

## 通用新聞網址擷取

先執行 `python -m pip install -r requirements.txt` 安裝 Trafilatura。
公開新聞網址不再限制來源清單；Yahoo、公視、自由時報保留專用解析，其餘使用通用正文解析。
只讀取使用者提交的單篇 HTML，遵守 robots.txt。導覽、側欄與留言不納入正文。
有付費標記的文章會拒絕；登入、驗證碼或 JavaScript 才載入正文的頁面可能需要手動貼文。
通用解析仍可能不完整，請核對原文。每次連線固定到檢查過的公開 IP，轉址也會重新檢查。
批次評估工具的來源清單限制維持不變。
