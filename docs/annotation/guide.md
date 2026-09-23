# 標註指南（v0.2）

目的：判斷「這篇文章的**內文**是否支持它的**標題**」。不判斷新聞在現實中是否為真，也不判斷作者有沒有惡意。標題吸睛不等於誤導；內文沒有提到不等於標題錯誤。

這份指南是第一版，試標後會依分歧修訂。修訂時更新版本號，並記錄改了什麼。

## 1. 你要標什麼

一篇文章，拆成 1～3 個「可核對的標題主張」，每個主張各標一筆判斷。

- **可核對主張**：內文可以支持或否定的陳述，例如「公司確定調漲價格」「工廠今年關閉」。
- **不是主張**：語氣詞、情緒詞（震撼、驚爆）、純粹的提問。標題是問句時，只標它預設的事實。
- 一句標題有多個獨立事實時拆開標（最多 6 個），不要合併成一個籠統的主張。
- 主張用你自己的話寫短，保留標題的關鍵詞（對象、行動、時間、範圍、確定程度），不要照抄整句標題。

## 2. 每個主張填兩個欄位

### relation（內文與主張的核心事實的關係）

| 值 | 定義 |
|---|---|
| `supports` | 內文支持主張的核心事實（誰做了什麼），即使確定程度或範圍不同 |
| `conflicts` | 內文對同一對象、同一期間，明確說了相反的事 |
| `insufficient` | 內文沒有足夠資訊判斷，也沒有明確相反 |

判斷 relation 時先**忽略**「確定」「全面」這類程度與範圍的字眼，只看核心事實。程度與範圍另外用 gap_type 記錄。

### gap_type（標題與內文的落差類型）

| 值 | 用在 | 意思 |
|---|---|---|
| `none` | supports | 標題與內文沒有實質落差 |
| `certainty` | supports | 標題把不確定的事說成確定 |
| `scope` | supports | 標題把有限範圍的事說成更廣 |
| `contradiction` | conflicts | 內文明確否定標題 |
| `insufficient` | insufficient | 內文資訊不足 |

**relation 和 gap_type 的組合是固定的**，只有下表五種是合法的，其他組合驗證程式會拒絕：

| relation | gap_type | 導出的結果 |
|---|---|---|
| supports | none | 內文支持（`supported`） |
| supports | certainty 或 scope | 重要條件省略（`missing_conditions`） |
| conflicts | contradiction | 與內文明確衝突（`contradicted`） |
| insufficient | insufficient | 資訊不足（`insufficient`） |

結果標籤**不用自己填**，由程式導出。這和 `src/ai/headline.py` 的 Gemini 輸出使用同一套名稱，所以人工標註可以直接拿來比較。

一篇有多個主張時，整篇的結果取最嚴重的：衝突 > 重要條件省略 > 資訊不足 > 支持。

## 3. 判斷規則

### 確定性（certainty）

標題用了確定的說法，內文只有可能性或過程，就標 `supports` + `certainty`。

| 標題常見字眼 | 內文常見字眼（表示還沒確定） |
|---|---|
| 確定、正式、已、拍板、宣布 | 評估中、考慮、研議、傳出、預計、可能、規劃、尚未決定、視情況 |

反過來，標題已經是保留語氣（可能、傳）而內文一樣保留，不算落差。

### 範圍（scope）

標題用了大範圍的說法，內文限定在較小範圍，就標 `supports` + `scope`。

| 標題常見字眼 | 內文常見字眼（表示範圍有限） |
|---|---|
| 全面、所有、全球、各地、全部、一律 | 部分、特定、僅、只適用、限於、某些產品、北美地區 |

### 矛盾（contradiction）

必須同時成立：**同一對象**、**同一期間**、內文有**明確相反**的陳述。三個少一個就不要標衝突，改想想是不是 `insufficient` 或 `scope`。

### 資訊不足（insufficient）

- 內文完全沒談到標題的核心事實。**沒提到不等於矛盾。**
- 標題加入了內文沒有的新事實（例如內文沒說裁員，標題寫裁員）。
- 內文與標題只是相關，但不足以判斷。
- 資訊不足時可以不引用證據；有相關段落時引用也可以。

## 4. 邊界情況

- **摘要式省略不算落差**：標題本來就比內文短，沒寫細節是正常的。只有省略後會**改變讀者對核心事實的理解**才標 certainty 或 scope。
- **標題引述他人**：「A 說 B」要檢查 A 是不是真的這樣說。內文是別人（分析師、媒體）說的，而標題寫成公司說的，屬於矛盾或範圍問題，請在備註寫明。
- **內文自己就有出入**：前後段互相矛盾時，以最明確、最靠近主張的段落為準，並在備註說明。
- **時間**：只看內文本身。不要用內文以外的知識、新聞發布後才發生的事，或你對公司的印象。
- **數字**：只有標題與內文的數字明確不同時才標 `contradiction`（例如標題 30%、內文 3%）。不要自己驗算成長率。
- **單位或幣別不同**：先確認是不是同一回事再判斷，不確定就標 `insufficient` 並寫備註。
- **內文有條件但標題沒寫**：這正是 certainty 或 scope 的典型，前提是那個條件會改變理解。
- **並列暗示因果**：標題把兩件事並列（例如「A 帶頭漲價 15%！B 掀新賽道」），讀者容易以為兩者有因果或關聯。這時**另外拆一個主張**，寫出暗示的關聯（例如「A 的漲價與 B 的熱潮有關」），不要塞進原本的主張。判斷：
  - 內文有支持這個關聯 → `supports`（再看有沒有 certainty 或 scope）。
  - 內文沒談這個關聯，或給了**不同的原因**，但沒有明確說「與 B 無關」→ `insufficient`。備註寫出內文實際給的原因。
  - 內文明確說兩者無關、或明確否定 → `conflicts` + `contradiction`。
  - 只有標題的並列方式確實容易被這樣理解時才拆，不要把每個並列都當因果。

## 5. 選證據

- 證據是**段落**，用段落編號（P001、P002…）。編號來自 `/paragraphs` 預覽或 `prepare_article`，以 `blank_lines` 模式為準。
- 選**最少但足夠**的段落。標 certainty 或 scope 時，必須包含**寫出限制條件的那一段**。
- `conflicts` 要引用明確否定的那一段。
- 標題**不是**證據，不能引用標題。
- 段落編號只在**那個版本**的標題與內文下有效。標題或內文改動，編號可能變，所以每筆標註都記錄 `document_id`，驗證程式會檢查。
- 引用存在只代表位置對，不代表你的判斷對；判斷要靠你自己核對原文。

## 6. 檔案與流程

- 一個檔案是**一位標註者**對**一篇文章**的判斷，`.json`，格式見 `evaluation/annotation_schema.py`。
- 檔名建議 `<sample_id>.<annotator>.json`。兩人獨立標同一篇時，兩個檔案的 `sample_id` 相同、`annotator` 不同。
- `group_id`：同一事件、轉載或改寫的文章用同一個，之後切訓練、驗證、測試時整組放在一起，避免洩漏。
- **真實文章**（`is_synthetic: false`）：只存網址、`content_hash`、`document_id`、標題，**不存全文**。全文放在本機快取 `evaluation/cache/<content_hash>.json`（不進 git），格式 `{"title": ..., "body": ...}`。
- **合成案例**（`is_synthetic: true`）：文章是虛構的，全文寫在 `article.text`。合成與真實的結果一律分開報告，不可用合成案例冒充真實案例。
- 原標題不能自動當標準答案，也不要為了湊數而套用標題的說法。

收集真實文章（結果存本機快取，不進 git）：

```bash
python -m evaluation.articles discover                 # 從 Yahoo 台灣 RSS 抓一批，列出可能有落差的候選
python -m evaluation.articles fetch <文章網址>          # 只抓一篇
python -m evaluation.articles show <hash 前 8 碼>       # 顯示標題、段落編號 P001…、完整段落
python -m evaluation.articles show <hash 前 8 碼> --template evaluation/annotations/real --annotator max
```

最後一行會建立一個 `claims` 是空的標註檔，不填就過不了驗證。`discover` 的「標題線索、內文線索」只是粗篩：台灣財經標題誇大多半靠語氣與引述（「韓媒認了」「全搶進」），不靠「確定」「全面」，所以 30 篇只會抓到極少數。**候選只是提示，是否值得標要自己讀完整篇再決定。**

驗證所有標註：

```bash
python -m evaluation.validate_annotations evaluation/annotations
```

會檢查格式、relation 與 gap_type 的組合、證據編號是否存在、`document_id` 和內容雜湊是否吻合，最後分開列出真實與合成的結果數量。

兩位標註者（或 AI 草稿與人工審核版）各放一個資料夾，比對分歧：

```bash
python -m evaluation.compare evaluation/annotations/drafts evaluation/annotations/real
```

主張用「引用的證據段落＋文字相似度」自動配對，所以每一組都要人工看過。輸出只列出哪裡不同，不判斷誰對；分歧是兩人討論、修訂指南的材料。

切分訓練、驗證、測試（同一事件的文章一定在同一份）：

```bash
python -m evaluation.split evaluation/annotations/real
python -m evaluation.split evaluation/annotations/real --export evaluation/cache/exports
```

一組（`group_id`）落在哪一份，只取決於 `group_id` 本身與固定的 salt，跟其他資料無關。所以之後再加標註，**不會把已經在測試集裡的東西搬走，也不會把別的東西搬進去**，測試集一旦有內容就固定了。代價是組數很少時某一份可能是空的，報告會直接說出來。真實文章的全文只允許匯出到 `evaluation/cache/`（不進 git）。

用標註評估一個方法（目前有規則基準 `rules-v0`）：

```bash
python -m evaluation.evaluate --gold evaluation/annotations/real --predictor rules
```

- 標準答案（`--gold`）若含 AI 草稿（標註者名稱含 claude 或 draft）會被拒絕，除非加 `--allow-draft-gold`，此時數字只代表流程可運作，**不是基準**。
- 規則基準的字詞表被指紋鎖住，看過結果後不能偷偷加字；要改就升版本號，重新評估。
- 標註少於 30 筆時，報告會註明數字太雜訊，只能看流程與錯誤案例，不能拿來排名方法。
- `--split validation` 用來選方法、`--split test` **只在最後用一次**：每看一次測試集、每依它改一次，它就少一分誠實。

## 7. 範例

以下是虛構的合成案例（公司與新聞皆為虛構），放在 `evaluation/annotations/examples/`。

| 標題 | 內文重點 | relation | gap_type | 結果 |
|---|---|---|---|---|
| 星河公司確定調漲價格 | 仍在評估，尚未決定 | supports | certainty | 重要條件省略 |
| 星河公司全面漲價 | 只適用北美部分產品線 | supports | scope | 重要條件省略 |
| 星河公司今年將關閉工廠 | 執行長說今年不會關閉 | conflicts | contradiction | 與內文明確衝突 |
| 星河公司宣布新產品 | 公司今天宣布推出新產品 | supports | none | 內文支持 |
| 星河公司即將裁員 | 只有公司背景，沒談裁員 | insufficient | insufficient | 資訊不足 |

第六筆（`syn-06-implied-cause`）示範「並列暗示因果」：

| 標題 | 內文重點 | 主張 | relation | gap_type |
|---|---|---|---|---|
| 星河公司帶頭漲價15%！新材料掀AI封裝新賽道 | 漲價原因是匯率與原物料成本，對象是面板用材料；AI封裝用的是另一個市場 | 星河公司宣布漲價15% | supports | none |
| （同上） | （同上） | 漲價與AI封裝熱潮有關 | insufficient | insufficient |

## 8. 修訂紀錄

- **v0.2**：新增「並列暗示因果」規則（第 4 節）與範例。起因是試標一篇標題把面板玻璃漲價和 AI 封裝並列，內文給的漲價原因是匯率與成本，我原本誤標成範圍落差。
- **v0.1**：第一版。

## 9. 目前的限制

- 這份指南尚未經過試標，規則可能太寬或太嚴，要依實際分歧修訂。
- 只針對確定性與範圍。其他類型的誤導（例如引述斷章取義、圖表數字）目前只用備註記錄，不另設類別。
- 證據單位是段落，不是句子。段落很長時，證據可能包含不相關的句子。
