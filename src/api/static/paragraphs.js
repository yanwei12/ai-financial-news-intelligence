"use strict";
const $ = id => document.getElementById(id);
let mode = location.pathname === "/check" ? "url" : "text";
let busy = false;
let revision = 0;
let prepared = null;
let analysis = null;
let fetched = null;
let savedArticleId = null;
let selectedHistory = null;
let historyOffset = 0;
let historyLoading = false;
const labels = { misleading: "可能誤導", supported: "相符", missing_conditions: "部分相符", contradicted: "不相符", insufficient: "證據不足" };
const meanings = {
  misleading: "標題有重要歧義，正文只支持其中一種合理讀法，可能造成誤解。",
  supported: "正文支持標題的主要說法。",
  missing_conditions: "標題省略了正文中的重要條件或限制。",
  contradicted: "標題的說法與正文存在衝突。",
  insufficient: "正文資訊不足，無法判定標題是否相符。",
};

function clearResult(clearArticle = true) {
  if (clearArticle) savedArticleId = null;
  selectedHistory = null;
  markHistory();
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
const externalLabels = {supported: "外部資料支持", contradicted: "外部資料衝突", mixed: "部分支持／證據互異", insufficient: "外部證據不足"};
function renderExternal(target, claim, index, data) {
  target.replaceChildren();
  const result = claim.external_verification;
  const completed = result && ["completed", "no_evidence"].includes(result.status);
  target.append(element("h4", `外部取證 · ${completed ? externalLabels[result.verdict] || "無法判定" : "尚未搜尋"}`));
  if (completed) {
    target.append(element("p", `查證時間：${formatHistoryTime(result.checked_at)} · ${result.model_version || result.model}`));
    target.append(element("p", "以下是 AI 依搜尋資料整理的證據摘要，並非來源逐字引文。來源發布日期未由搜尋介面提供，請開啟來源核對；查證時間不代表新聞發布時間。"));
    for (const item of result.evidence || []) {
      const paragraph = element("p", item.text);
      for (const id of item.source_ids) {
        const source = result.sources.find(s => s.id === id);
        if (!source) continue;
        const link = element("a", ` [${id + 1}] ${source.title}`);
        try {
          const url = new URL(source.url);
          if (url.protocol !== "https:" || url.username || url.password) continue;
          link.href = url.href;
        } catch (_) { continue; }
        link.target = "_blank"; link.rel = "noopener noreferrer"; paragraph.append(link);
      }
      target.append(paragraph);
    }
    const report = element("details", "");
    report.append(element("summary", "查看完整 AI 取證說明與搜尋詞"));
    const text = element("p", result.report); text.className = "external-report";
    report.append(text, element("p", `搜尋詞：${(result.search_queries || []).join("、") || "未提供"}`));
    target.append(report);
    if (result.search_entry_point) {
      const frame = document.createElement("iframe");
      frame.title = "Google 搜尋建議"; frame.className = "search-suggestions";
      frame.setAttribute("sandbox", "allow-popups allow-popups-to-escape-sandbox");
      frame.referrerPolicy = "no-referrer";
      // Isolated provider widget: never allow scripts, same-origin access, forms or top navigation.
      frame.srcdoc = `<meta http-equiv="Content-Security-Policy" content="default-src 'none'; style-src 'unsafe-inline'; img-src https:; base-uri 'none'; form-action 'none'"><base target="_blank">` + result.search_entry_point;
      target.append(frame);
    }
  } else target.append(element("p", "搜尋其他公開資料，檢查這項主張的支持或衝突證據。會將新聞與主張傳送至 Google Gemini，並可能產生模型與搜尋費用。"));
  const button = element("button", completed ? "再次取證（24 小時內使用快取）" : "搜尋外部證據");
  button.type = "button"; button.className = "secondary";
  button.disabled = !data.analysis_id;
  const status = element("p", ""); status.setAttribute("role", "status");
  button.addEventListener("click", async () => {
    if (busy) { status.textContent = "請等待目前工作完成後再試。"; return; }
    busy = true; button.disabled = true; $("input-fields").disabled = true;
    const currentRevision = revision;
    status.textContent = "正在搜尋外部來源並整理證據…";
    try {
      const verified = await request(`/headline/history/${encodeURIComponent(data.analysis_id)}/claims/${index}/verify`, {}, 115000);
      if (currentRevision !== revision || analysis !== data) return;
      claim.external_verification = verified;
      renderExternal(target, claim, index, data);
    } catch (error) {
      status.textContent = error.name === "AbortError" ? "外部搜尋逾時，原分析與先前取證仍保留。請稍後再試。" : error.message;
    } finally {
      busy = false; button.disabled = false; $("input-fields").disabled = false;
    }
  });
  target.append(button, status);
}

function showAnalysis(data) {
  const target = $("analysis-result"); target.replaceChildren(); target.dataset.verdict = data.verdict;
  const eyebrow = element("p", "標題與內文相符程度"); eyebrow.className = "eyebrow";
  const heading = element("h2", labels[data.verdict] || "無法判定"); heading.id = "verdict-heading";
  target.setAttribute("aria-labelledby", heading.id);
  const meaning = element("p", meanings[data.verdict] || "請查看分析理由。"); meaning.className = "verdict-meaning";
  const title = element("p", prepared.title); title.className = "analyzed-title";
  const reason = element("p", data.summary); reason.className = "verdict-reason";
  target.append(eyebrow, heading, meaning, title, element("h3", "判斷理由"), reason);
  const modern = data.claims.every(claim => claim.headline_meaning && claim.body_meaning);
  if (!modern || data.prompt_version !== "headline-comparison-v3-ambiguity") {
    const notice = element("p", "這是舊版分析，尚未使用新版標題歧義規則。可重新分析；會使用 API 額度，原紀錄仍會保留。");
    notice.className = "legacy-notice"; target.append(notice);
    const upgrade = element("button", "重新分析，檢查標題歧義"); upgrade.type = "button";
    upgrade.addEventListener("click", () => { if (!busy) $("analysis-form").requestSubmit(); });
    target.append(upgrade);
  }
  target.append(element("h3", modern ? "主張與證據對照" : "原版主張與理由"));
  const note = element("p", "以下對照為 AI 整理；展開原文可核對引用及前後段落。引用存在不代表判斷必然正確。");
  note.className = "hint"; target.append(note);
  data.claims.forEach((claim, index) => {
    const card = document.createElement("article"); card.className = "claim-card";
    card.append(element("h3", `${index + 1}. ${claim.claim} · ${labels[claim.verdict] || "無法判定"}`));
    if (claim.headline_meaning && claim.body_meaning) {
      const grid = element("div", ""); grid.className = "claim-comparison";
      for (const [label, text] of [
        [claim.interpretation === "implied" ? "標題可能暗示" : "標題明確說", claim.headline_meaning],
        ["正文實際說（AI 整理）", claim.body_meaning],
        [claim.verdict === "supported" ? "如何相符" : claim.verdict === "insufficient" ? "缺少什麼證據" : "差在哪裡", claim.explanation],
      ]) {
        const column = element("div", ""); column.append(element("h4", label), element("p", text)); grid.append(column);
      }
      card.append(grid);
    } else card.append(element("p", claim.explanation));
    const details = document.createElement("details");
    details.append(element("summary", "查看原文與上下文"));
    const cited = new Set(claim.evidence.map(item => item.id));
    const positions = new Set();
    prepared.paragraphs.forEach((p, i) => {
      if (cited.has(p.id)) for (let j = Math.max(0, i - 1); j <= Math.min(prepared.paragraphs.length - 1, i + 1); j++) positions.add(j);
    });
    if (!cited.size) details.append(element("p", "正文中未找到足夠引用證據，可展開下方完整原文自行核對。"));
    let previous = -1;
    for (const i of [...positions].sort((a, b) => a - b)) {
      if (previous >= 0 && i > previous + 1) details.append(element("p", "⋯ 中間段落省略，可於完整原文查看 ⋯"));
      const paragraph = prepared.paragraphs[i];
      const block = element("div", ""); block.className = cited.has(paragraph.id) ? "context-cited" : "context-neighbor";
      block.append(element("strong", cited.has(paragraph.id) ? "引用原文" : "相鄰段落（上下文）"), element("blockquote", paragraph.text));
      details.append(block); previous = i;
    }
    card.append(details);
    const external = element("div", ""); external.className = "external-placeholder";
    renderExternal(external, claim, index, data);
    card.append(external); target.append(card);
  });
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
  clearResult(false); busy = true; $("input-fields").disabled = true; $("analyze-button").textContent = "分析中…";
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
    const data = await request("/headline/analyze", {title:prepared.title, content:prepared.original_text, paragraph_mode:prepared.paragraph_mode, document_id:prepared.document_id, article_id:savedArticleId}, 90000);
    if (data.document_id !== prepared.document_id) throw new Error("文章版本不一致，請重新分析。");
    analysis = data; showAnalysis(data);
    loadHistory(true);
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
    savedArticleId = article.id;
    setMode("text"); $("title").value = article.title; $("content").value = article.body;
    $("status").textContent = "文章已載入，按「分析標題」即可。";
  } catch (failure) { showError("無法載入已確認文章，請重新貼上新聞。"); }
  finally { busy = false; $("input-fields").disabled = false; }
}
loadSavedArticle();


function formatHistoryTime(value) {
  const date = new Date(value);
  return value && !Number.isNaN(date.getTime()) ? date.toLocaleString("zh-TW", {hour12: false}) : "時間未記錄";
}
function markHistory() {
  for (const button of $("history-list").querySelectorAll("button")) {
    if (button.dataset.id === selectedHistory) button.setAttribute("aria-current", "true");
    else button.removeAttribute("aria-current");
  }
}
async function loadHistory(reset = false) {
  if (historyLoading) return;
  historyLoading = true;
  $("history-more").disabled = true;
  $("history-retry").hidden = true;
  $("history-status").textContent = "正在載入紀錄…";
  try {
    const data = await request(`/headline/history?offset=${reset ? 0 : historyOffset}&limit=20`);
    if (reset) $("history-list").replaceChildren();
    for (const item of data.items) {
      const button = element("button", ""); button.type = "button"; button.className = "history-item";
      button.dataset.id = item.id;
      const title = element("strong", item.title);
      const badge = element("span", labels[item.verdict] || "未分類"); badge.className = "history-verdict";
      badge.dataset.verdict = item.verdict;
      const time = element("time", formatHistoryTime(item.analyzed_at));
      button.append(title, badge, time);
      button.addEventListener("click", () => openHistory(item.id));
      $("history-list").append(button);
    }
    historyOffset = data.next_offset;
    $("history-more").hidden = historyOffset === null;
    $("history-status").textContent = $("history-list").children.length ? "" : "還沒有分析紀錄。完成第一次分析後會自動保存。";
    markHistory();
  } catch (_) {
    $("history-status").textContent = "無法載入紀錄，請重試。";
    $("history-retry").hidden = false;
  } finally { historyLoading = false; $("history-more").disabled = false; }
}
async function openHistory(id) {
  if (busy) return;
  busy = true; $("input-fields").disabled = true;
  $("status").textContent = "正在讀取已保存分析…";
  try {
    const data = await request(`/headline/history/${encodeURIComponent(id)}`);
    if (!data.prepared?.original_text || !Array.isArray(data.prepared.paragraphs)) throw new Error("這筆舊紀錄缺少原文快照，無法完整還原。");
    clearResult(); fetched = null;
    prepared = data.prepared; analysis = data; selectedHistory = id;
    $("title").value = prepared.title; $("content").value = prepared.original_text;
    $("url-input").value = ""; $("paragraph-mode").value = prepared.paragraph_mode;
    setMode("text"); markHistory(); showAnalysis(data);
    $("status").textContent = `已載入 ${formatHistoryTime(data.analyzed_at)} 的分析紀錄，未呼叫 AI。修改後可另行分析。`;
  } catch (error) { showError(error.message); $("status").textContent = "紀錄讀取失敗，原有內容已保留。"; }
  finally { busy = false; $("input-fields").disabled = false; }
}
$("new-analysis").addEventListener("click", () => {
  if (busy) return;
  clearResult(); fetched = null; $("analysis-form").reset(); setMode("url");
  $("url-input").focus(); $("analysis-form").scrollIntoView({block: "start"});
});
$("history-more").addEventListener("click", () => loadHistory());
$("history-retry").addEventListener("click", () => loadHistory(true));
loadHistory(true);
