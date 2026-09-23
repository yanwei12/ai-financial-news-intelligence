"use strict";
const $ = id => document.getElementById(id);
let mode = location.pathname === "/check" ? "url" : "text";
let busy = false;
let revision = 0;
let prepared = null;
let analysis = null;
let fetched = null;
const labels = { supported: "符合正文", missing_conditions: "部分符合，省略重要條件", contradicted: "不符合，與正文衝突", insufficient: "無法判定，正文證據不足" };

function clearResult() {
  revision += 1;
  prepared = null;
  analysis = null;
  $("analysis-result").hidden = true;
  $("advanced-result").hidden = true;
  $("advanced-result").open = false;
  $("error").hidden = true;
  $("status").textContent = "";
}
function setMode(next) {
  mode = next;
  $("url-fields").hidden = next !== "url";
  $("text-fields").hidden = next !== "text";
  $("url-input").required = next === "url";
  $("title").required = $("content").required = next === "text";
  $("to-url").setAttribute("aria-pressed", String(next === "url"));
  $("to-paste").setAttribute("aria-pressed", String(next === "text"));
}
function showError(message) {
  $("error").textContent = message;
  $("error").hidden = false;
  $("error").focus();
}
$("analysis-form").addEventListener("input", () => { if (!busy) clearResult(); });
for (const [id, next] of [["to-url", "url"], ["to-paste", "text"]]) {
  $(id).addEventListener("click", () => { clearResult(); fetched = null; setMode(next); });
}
$("sample-button").addEventListener("click", () => {
  clearResult(); fetched = null; setMode("text");
  $("title").value = "【虛構範例】星河公司全面調漲價格";
  $("content").value = "星河公司表示，正在評估部分產品的售價調整，目前仍未作出最終決定。\n\n這項評估只涉及北美市場，其他市場不在此次評估範圍。";
  $("settings").open = false;
});
async function request(path, payload, timeout = 20000) {
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), timeout);
  try {
    const response = await fetch(path, {
      method: payload === undefined ? "GET" : "POST",
      headers: { "Content-Type": "application/json" },
      body: payload === undefined ? undefined : JSON.stringify(payload), signal: controller.signal,
    });
    let data;
    try { data = await response.json(); } catch (_) { throw new Error(`伺服器未回傳可讀取的結果（HTTP ${response.status}）。請稍後再試。`); }
    if (!response.ok) {
      const detail = data.detail;
      throw new Error(typeof detail === "string" ? detail : detail?.message || `請確認輸入內容後再試（HTTP ${response.status}）。`);
    }
    return data;
  } finally { clearTimeout(timer); }
}
function paragraphMode(body) {
  if ($("paragraph-mode").value !== "auto") return $("paragraph-mode").value;
  return /\n\s*\n/.test(body.replace(/\r\n?/g, "\n")) ? "blank_lines" : "line_breaks";
}
function element(tag, text) { const node = document.createElement(tag); node.textContent = text; return node; }
function showAnalysis(data) {
  const target = $("analysis-result"); target.replaceChildren(); target.dataset.verdict = data.verdict;
  target.append(element("p", "分析結果"), element("h2", labels[data.verdict] || "無法判定"), element("p", data.summary));
  const evidenceDetails = document.createElement("details");
  evidenceDetails.append(element("summary", "查看正文證據與逐項理由"));
  for (const claim of data.claims) {
    const card = document.createElement("article");
    card.append(element("h3", claim.claim), element("p", `${labels[claim.verdict]}：${claim.explanation}`));
    for (const evidence of claim.evidence) card.append(element("blockquote", evidence.text));
    if (!claim.evidence.length) card.append(element("p", "正文中未找到足夠證據。"));
    evidenceDetails.append(card);
  }
  target.append(evidenceDetails);
  $("analysis-meta").textContent = `${data.model_version || data.model} · ${data.cached ? "已保存結果" : "本次分析"} · ${data.analyzed_at}`;
  $("paragraph-note").textContent = `引用定位共 ${prepared.paragraphs.length} 段。`;
  $("paragraph-list").replaceChildren();
  for (const paragraph of prepared.paragraphs) {
    const card = document.createElement("article");
    card.append(element("h3", paragraph.id), element("p", paragraph.text));
    $("paragraph-list").append(card);
  }
  $("original-text").textContent = prepared.original_text;
  $("advanced-result").hidden = false;
  target.hidden = false; target.focus(); target.scrollIntoView({block:"start", behavior:"smooth"});
}
$("analysis-form").addEventListener("submit", async event => {
  event.preventDefault(); if (busy) return;
  clearResult(); busy = true; $("input-fields").disabled = true; $("analyze-button").textContent = "分析中…";
  try {
    let title = $("title").value, body = $("content").value;
    if (mode === "url") {
      $("status").textContent = "正在讀取新聞…";
      fetched = await request("/articles/preview", {url: $("url-input").value.trim()}, 45000);
      title = fetched.title || ""; body = fetched.body || "";
      $("title").value = title; $("content").value = body; setMode("text");
      if (!fetched.ok || !title.trim() || !body.trim() || fetched.warnings?.length) {
        showError(fetched.ok ? `請確認或補齊正文後再按「分析標題」。${(fetched.warnings || []).join(" ")}` : `${fetched.message || "無法自動擷取這篇文章。"} 請貼上標題與完整正文，再按「分析標題」。`);
        $("status").textContent = "需要補充或確認文章內容，尚未呼叫 AI。";
        return;
      }
    }
    if (!title.trim() || !body.trim()) throw new Error("請提供標題與完整正文。");
    if (body.length > 20000) throw new Error("正文最多 20,000 字元，請確認貼上的內容；系統不會截斷原文。");
    $("status").textContent = "正在比對標題與正文…";
    prepared = await request("/articles/prepare", {title, content:body, paragraph_mode:paragraphMode(body)});
    if (prepared.paragraphs.length > 400) throw new Error("文章段落過多，請在進階設定中調整段落處理方式。");
    const data = await request("/headline/analyze", {title:prepared.title, content:prepared.original_text, paragraph_mode:prepared.paragraph_mode, document_id:prepared.document_id}, 90000);
    if (data.document_id !== prepared.document_id) throw new Error("文章版本不一致，請重新分析。");
    analysis = data; showAnalysis(data);
    $("status").textContent = "分析完成。展開證據可核對原文。";
  } catch (failure) {
    showError(failure.name === "AbortError" ? "連線逾時，請稍後再試；分析可能仍在處理中。" : failure.message);
    $("status").textContent = "本次分析未完成，輸入內容已保留。";
  } finally {
    busy = false; $("input-fields").disabled = false; $("analyze-button").textContent = "分析標題";
  }
});
$("download-button").addEventListener("click", () => {
  if (!prepared || !analysis) return;
  const url = URL.createObjectURL(new Blob([JSON.stringify({...prepared, analysis}, null, 2)], {type:"application/json;charset=utf-8"}));
  const link = document.createElement("a"); link.href = url; link.download = `article-${prepared.document_id.slice(0,12)}.json`;
  document.body.append(link); link.click(); link.remove(); setTimeout(() => URL.revokeObjectURL(url),1000);
});
setMode(mode);
async function loadSavedArticle() {
  const id = new URLSearchParams(location.search).get("article_id");
  if (!id || !/^[1-9][0-9]*$/.test(id)) return;
  const version = revision; busy = true; $("input-fields").disabled = true;
  try {
    const article = await request(`/articles/${encodeURIComponent(id)}`);
    if (version !== revision) return;
    setMode("text"); $("title").value = article.title; $("content").value = article.body;
    $("status").textContent = "文章已載入，按「分析標題」即可。";
  } catch (failure) { showError("無法載入已確認文章，請重新貼上新聞。"); }
  finally { busy = false; $("input-fields").disabled = false; }
}
loadSavedArticle();
