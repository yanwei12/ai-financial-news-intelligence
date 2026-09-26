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

### 分析端防護與紀錄

| 項目 | 行為 | 設定（環境變數） |
| --- | --- | --- |
| 每日分析上限 | 每個 UTC 日最多 N 次**新的**模型呼叫，超過回 429 `daily_limit`；已快取的結果不受影響。失敗的呼叫不計數（但可能已產生費用）。 | `HEADLINE_DAILY_LIMIT`，預設 50 |
| 請求頻率限制 | 每個來源位址每分鐘：`/articles/preview` 20 次、`/articles` 60 次、`/headline/analyze` 6 次；超過回 429 `rate_limited` 與 `Retry-After`。記憶體內、單一程序有效。 | `RATE_LIMIT_PREVIEW`、`RATE_LIMIT_CONFIRM`、`RATE_LIMIT_ANALYZE`；`0` 表示關閉 |
| 代理後的來源位址 | 預設用連線位址。只有放在自己控制的代理後面才可設為 `1`，否則 `X-Forwarded-For` 可被偽造。 | `TRUST_PROXY_HEADERS=1` |
| 說明不引入文章外資訊 | 模型的主張、說明、摘要裡的**數字與英文名稱**必須出現在標題或內文，否則整筆結果被拒絕（`ungrounded_output`，不快取）。只能擋數字與名稱，擋不了「只用文章裡的字卻下錯結論」。提示詞版本 `headline-paragraphs-v2`。 | — |

**關聯已確認文章**：以 `/paragraphs?article_id=ID` 載入已儲存文章後分析時，請求會帶 `article_id`。伺服器會確認送出的文字與那篇已存文章完全相同才建立關聯（否則 404 或 409），存在新表 `article_analyses`（不需要遷移舊表）。查詢某篇文章的所有分析：`GET /articles/{id}/analyses`。在分析頁手動修改文字後，關聯會自動取消。

**用量與耗時**：每筆分析記錄 `latency_ms` 與供應商回報的 token 用量。

```bash
python -m scripts.usage_report
python -m scripts.usage_report --input-price 0.30 --output-price 2.50
```

價格（每百萬 token）由你自己提供，程式不內建任何價格；只讀資料庫，不呼叫模型。

模型只比對本文，不做外部事實查核；引用存在不代表語意判斷正確，尚待人工評估。程式依據 [Google 結構化輸出文件](https://ai.google.dev/gemini-api/docs/generate-content/structured-output) 使用 REST JSON Schema，並於本機再次驗證。

測試：`python -m pytest tests -q`（使用模擬 Gemini，不耗用額度）。


## 通用新聞網址擷取

先執行 `python -m pip install -r requirements.txt` 安裝 Trafilatura。
公開新聞網址不再限制來源清單；Yahoo、公視、自由時報保留專用解析，其餘使用通用正文解析。
只讀取使用者提交的單篇 HTML，遵守 robots.txt。導覽、側欄與留言不納入正文。
有付費標記的文章會拒絕；登入、驗證碼或 JavaScript 才載入正文的頁面可能需要手動貼文。
通用解析仍可能不完整，請核對原文。每次連線固定到檢查過的公開 IP，轉址也會重新檢查。
批次評估工具的來源清單限制維持不變。
