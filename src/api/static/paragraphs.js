"use strict";
const form = document.getElementById("prepare-form");
const titleInput = document.getElementById("title");
const contentInput = document.getElementById("content");
const modeInput = document.getElementById("paragraph-mode");
const result = document.getElementById("result");
const status = document.getElementById("status");
const error = document.getElementById("error");
const submit = document.getElementById("prepare-button");
let prepared = null;
let revision = 0;
let analysis = null;
let analyzing = false;
let savedArticleId = null;  // set only while the text is exactly a confirmed article; any edit clears it
const analyzeButton = document.getElementById("analyze-button");
const analysisResult = document.getElementById("analysis-result");

function invalidate() {
  revision += 1;
  prepared = null;
  analysis = null;
  savedArticleId = null;
  analysisResult.hidden = true;
  result.hidden = true;
  error.hidden = true;
  status.textContent = "內容已變更，請重新產生段落預覽。";
}
form.addEventListener("input", invalidate);
modeInput.addEventListener("change", invalidate);
document.getElementById("sample-button").addEventListener("click", () => {
  titleInput.value = "【虛構範例】星河公司全面調漲價格";
  contentInput.value = "星河公司表示，正在評估部分產品的售價調整。\n目前仍未作出最終決定。\n\n這項評估只涉及北美市場。方案中的調整幅度為 3.5%，其他市場不在此次評估範圍。";
  modeInput.value = "blank_lines";
  invalidate();
});

function render(data) {
  const list = document.getElementById("paragraph-list");
  const nav = document.getElementById("paragraph-nav");
  list.replaceChildren();
  nav.replaceChildren();
  for (const paragraph of data.paragraphs) {
    const article = document.createElement("article");
    article.id = paragraph.id;
    article.tabIndex = -1;
    const heading = document.createElement("h3");
    heading.textContent = paragraph.id;
    const text = document.createElement("p");
    text.textContent = paragraph.text;
    article.append(heading, text);
    list.append(article);
    const button = document.createElement("button");
    button.type = "button";
    button.className = "secondary";
    button.textContent = paragraph.id;
    button.setAttribute("aria-pressed", "false");
    button.addEventListener("click", () => {
      for (const node of list.children) node.classList.remove("highlight");
      for (const node of nav.children) node.setAttribute("aria-pressed", "false");
      article.classList.add("highlight");
      button.setAttribute("aria-pressed", "true");
      article.focus({ preventScroll: true });
      article.scrollIntoView({ block: "nearest" });
    });
    nav.append(button);
  }
  document.getElementById("original-text").textContent = data.original_text;
  document.getElementById("paragraph-note").textContent = `共 ${data.paragraphs.length} 段。標題獨立保存，不包含在正文證據中。`;
  result.hidden = false;
}

form.addEventListener("submit", async (event) => {
  event.preventDefault();
  analysis = null;
  analysisResult.hidden = true;
  const currentRevision = ++revision;
  prepared = null;
  result.hidden = true;
  error.hidden = true;
  submit.disabled = true;
  status.textContent = "正在整理段落…";
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), 15000);
  try {
    const response = await fetch("/articles/prepare", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ title: titleInput.value, content: contentInput.value, paragraph_mode: modeInput.value }),
      signal: controller.signal,
    });
    const data = await response.json();
    if (currentRevision !== revision) return;
    if (!response.ok) {
      const message = typeof data.detail === "string" ? data.detail : "請確認標題及內文長度、內容與分段方式。";
      throw new Error(message);
    }
    prepared = data;
    render(data);
    status.textContent = "段落準備完成。尚未呼叫 AI 模型，資料未寫入新聞資料庫。";
  } catch (failure) {
    if (currentRevision !== revision) return;
    error.textContent = failure.name === "AbortError" ? "連線逾時，請稍後再試。" : failure.message;
    error.hidden = false;
    status.textContent = "段落準備未完成。";
  } finally {
    clearTimeout(timer);
    submit.disabled = false;
  }
});

document.getElementById("download-button").addEventListener("click", () => {
  if (!prepared) return;
  const blob = new Blob([JSON.stringify(analysis ? { ...prepared, analysis } : prepared, null, 2)], { type: "application/json;charset=utf-8" });
  const url = URL.createObjectURL(blob);
  const link = document.createElement("a");
  link.href = url;
  link.download = `article-${prepared.document_id.slice(0, 12)}.json`;
  document.body.append(link);
  link.click();
  link.remove();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
});


const labels = { supported: "內文支持", missing_conditions: "重要條件省略", contradicted: "與內文明確衝突", insufficient: "資訊不足" };
function showAnalysis(data) {
  analysisResult.replaceChildren();
  const heading = document.createElement("h2");
  heading.textContent = labels[data.verdict];
  const summary = document.createElement("p");
  summary.textContent = data.summary;
  const meta = document.createElement("p");
  meta.className = "hint";
  meta.textContent = `${data.model_version} · ${data.cached ? "使用已保存結果" : "本次分析"} · ${data.analyzed_at}`;
  analysisResult.append(heading, summary, meta);
  for (const claim of data.claims) {
    const card = document.createElement("article");
    const title = document.createElement("h3");
    title.textContent = `${labels[claim.verdict]}：${claim.claim}`;
    const explanation = document.createElement("p");
    explanation.textContent = claim.explanation;
    card.append(title, explanation);
    for (const evidence of claim.evidence) {
      const button = document.createElement("button");
      button.type = "button";
      button.textContent = `查看證據 ${evidence.id}`;
      button.addEventListener("click", () => {
        for (const node of document.getElementById("paragraph-nav").children) {
          if (node.textContent === evidence.id) node.click();
        }
      });
      const quote = document.createElement("blockquote");
      quote.textContent = evidence.text;
      card.append(button, quote);
    }
    if (!claim.evidence.length) {
      const note = document.createElement("p");
      note.textContent = "未定位到足夠的正文證據。";
      card.append(note);
    }
    analysisResult.append(card);
  }
  const note = document.createElement("p");
  note.className = "hint";
  note.textContent = "引用位置已經程式驗證；模型判斷仍可能有誤，請核對原文與上下文。";
  analysisResult.append(note);
  analysisResult.hidden = false;
}

analyzeButton.addEventListener("click", async () => {
  if (!prepared || analyzing) return;
  // Same unit as the server and /check: characters other than whitespace.
  if (prepared.original_text.replace(/\s/g, "").length > 20000 || prepared.paragraphs.length > 400) {
    error.textContent = "第一版分析最多 20,000 字（不含空白）、400 段；正文不會被截斷。";
    error.hidden = false;
    return;
  }
  const currentRevision = revision;
  const snapshot = prepared;
  const articleId = savedArticleId;
  analyzing = true;
  analyzeButton.disabled = true;
  error.hidden = true;
  status.textContent = "Gemini 正在比對標題與正文，請稍候…";
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), 90000);
  try {
    const response = await fetch("/headline/analyze", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({title: snapshot.title, content: snapshot.original_text, paragraph_mode: snapshot.paragraph_mode, document_id: snapshot.document_id, article_id: articleId}),
      signal: controller.signal,
    });
    const data = await response.json();
    if (currentRevision !== revision) return;
    if (!response.ok) throw new Error(typeof data.detail === "string" ? data.detail : data.detail?.message || "分析失敗，請稍後再試。");
    if (data.document_id !== snapshot.document_id) throw new Error("文章版本不一致，請重新預覽。");
    analysis = data;
    showAnalysis(data);
    status.textContent = "分析完成，結果已保存。點選證據可查看原段落。";
  } catch (failure) {
    if (currentRevision !== revision) return;
    error.textContent = failure.name === "AbortError" ? "分析連線逾時；伺服器可能仍在處理，請稍後再試。" : failure.message;
    error.hidden = false;
    status.textContent = "未取得分析結果；不會把模型錯誤當作資訊不足。";
  } finally {
    clearTimeout(timer);
    analyzing = false;
    analyzeButton.disabled = false;
  }
});

async function loadSavedArticle() {
  const id = new URLSearchParams(location.search).get("article_id");
  if (!id || !/^[1-9][0-9]*$/.test(id)) return;
  const currentRevision = revision;
  status.textContent = "正在載入已確認文章…";
  try {
    const response = await fetch(`/articles/${encodeURIComponent(id)}`, {signal: AbortSignal.timeout(15000)});
    if (!response.ok) throw new Error("無法載入文章，請回到輸入頁重新確認。");
    const article = await response.json();
    if (currentRevision !== revision) return;
    titleInput.value = article.title;
    contentInput.value = article.body;
    modeInput.value = "blank_lines";
    form.requestSubmit();
    savedArticleId = article.id;  // after submit: requestSubmit does not fire the "input" event that clears it
  } catch (failure) {
    if (currentRevision !== revision) return;
    error.textContent = failure.message;
    error.hidden = false;
    status.textContent = "文章載入失敗。";
  }
}
loadSavedArticle();
