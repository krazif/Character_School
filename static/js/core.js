// Character School — core.js
// Core state, card version helpers, console, token estimation.

// ─── State ──────────────────────────────────────
let ws = null;
let currentCard = null;
let currentFilename = null;
let currentPersona = null;
let currentPersonaFilename = null;
let editorData = null;
let editorTab = 'core';
let editorVersion = 2;
let editorNormalized = null;
let editorOriginal = null;
// School session state
let schoolActiveSessionId = null;
let schoolStackConfig = null;
let schoolConsoleEvents = [];
let schoolSystemPrompt = '';  // system prompt text from server for token estimation
// Active stack builder context (shared between School and RP modes)
let activeStackConfig = null;
let activeStackWS = null;
let activeStackMode = 'rp';  // 'rp' or 'school'

// ─── Card Version Support (V1/V2/V3) ──────────────
const V3_ALIASES = { first_mes: 'first_message', mes_example: 'message_examples' };
function detectCardVersion(card) {
  const spec = card.spec || '';
  if (spec.includes('v3')) return 3;
  if (spec.includes('v2')) return 2;
  if (card.data) return 2;
  return 1;
}
function contentToString(val) {
  if (!val) return '';
  if (typeof val === 'string') return val;
  if (Array.isArray(val)) return val.map(b => (typeof b === 'object' && b.text) ? b.text : (typeof b === 'string' ? b : '')).join('\n');
  return String(val);
}
function normalizeForEditing(card) {
  const d = card.data || card;
  const normalized = {};
  for (const [key, val] of Object.entries(d)) {
    const v2Key = Object.entries(V3_ALIASES).find(([_, v3]) => v3 === key)?.[0];
    const targetKey = v2Key || key;
    if (targetKey === 'tags') {
      normalized[targetKey] = Array.isArray(val) ? val : (typeof val === 'string' ? val.split(',').map(s=>s.trim()).filter(Boolean) : val);
    } else if (targetKey === 'alternate_greetings' || targetKey === 'group_only_greetings') {
      normalized[targetKey] = Array.isArray(val) ? val.map(v => contentToString(v)) : val;
    } else {
      normalized[targetKey] = contentToString(val);
    }
  }
  return normalized;
}
function denormalizeForSaving(normalized, version, original) {
  if (version === 1) {
    const flat = {};
    for (const [key, val] of Object.entries(normalized)) {
      if (key === 'system_prompt' || key === 'post_history_instructions' || key === 'creator_notes' || key === 'alternate_greetings') continue; // V1 doesn't have these
      flat[key] = val;
    }
    return flat;
  } else if (version === 3) {
    const d = {};
    for (const [key, val] of Object.entries(normalized)) {
      const v3Key = V3_ALIASES[key] || key;
      d[v3Key] = val;
    }
    return { ...original, spec: 'chara_card_v3', spec_version: original.spec_version || '3.0', data: d };
  } else {
    return { ...original, spec: original.spec || 'chara_card_v2', spec_version: original.spec_version || '2.0', data: { ...normalized } };
  }
}
let isWaiting = false;
let personasList = [];
let personaEditorData = null;
let personaEditorFilename = null;
let consoleOpen = false;
let consoleMaximized = false;
let consoleEventCount = 0;
let consoleLimit = parseInt(localStorage.getItem('cs_console_limit')) || 0; // 0 = unlimited; shared across School + RP consoles

// Restore console limit inputs on load
function initConsoleLimit() {
  const si = document.getElementById('consoleLimitInput');
  const ri = document.getElementById('rpConsoleLimitInput');
  if (si) si.value = consoleLimit;
  if (ri) ri.value = consoleLimit;
}

function onConsoleLimitChange(val) {
  consoleLimit = Math.max(0, parseInt(val) || 0);
  localStorage.setItem('cs_console_limit', consoleLimit);
  // Sync both inputs
  const si = document.getElementById('consoleLimitInput');
  const ri = document.getElementById('rpConsoleLimitInput');
  if (si) si.value = consoleLimit;
  if (ri) ri.value = consoleLimit;
  // Immediately trim existing entries
  trimConsoleEntries(document.getElementById('consoleInner'));
  trimConsoleEntries(document.getElementById('rpConsoleInner'));
}

function trimConsoleEntries(inner) {
  if (!inner || consoleLimit <= 0) return;
  const entries = inner.querySelectorAll('.console-entry');
  const excess = entries.length - consoleLimit;
  if (excess > 0) {
    for (let i = 0; i < excess; i++) entries[i].remove();
    // Update count badges to reflect actual visible entries
    const visible = inner.querySelectorAll('.console-entry').length;
    if (inner.id === 'consoleInner') {
      consoleEventCount = visible;
      const el = document.getElementById('consoleCount');
      if (el) el.textContent = consoleEventCount;
    } else if (inner.id === 'rpConsoleInner') {
      rpConsoleEvents = visible;
      const el = document.getElementById('rpConsoleCount');
      if (el) el.textContent = rpConsoleEvents;
    }
  }
}

// ─── Console ────────────────────────────────────
function toggleConsole() {
  consoleOpen = !consoleOpen;
  // Handle both school and RP console panels
  const panels = document.querySelectorAll('#consolePanel, #rpConsolePanel');
  const chevrons = document.querySelectorAll('#consoleChevron, #rpConsoleChevron');
  panels.forEach(p => {
    if (consoleOpen) { p.classList.add('open'); } else { p.classList.remove('open'); }
  });
  chevrons.forEach(c => {
    if (consoleOpen) { c.classList.add('open'); } else { c.classList.remove('open'); }
  });
  if (consoleOpen) {
    // Scroll whichever inner is visible
    const inners = document.querySelectorAll('#consoleInner, #rpConsoleInner');
    inners.forEach(i => { i.scrollTop = i.scrollHeight; });
  }
}

function toggleConsoleMax() {
  const panels = document.querySelectorAll('#consolePanel, #rpConsolePanel');
  const maxBtns = document.querySelectorAll('#consoleMaxBtn, #rpConsoleMaxBtn');
  consoleMaximized = !consoleMaximized;
  panels.forEach(p => {
    if (consoleMaximized) { p.classList.add('maximized'); } else { p.classList.remove('maximized'); }
  });
  maxBtns.forEach(b => {
    b.textContent = consoleMaximized ? '⤓' : '⤢';
    b.title = consoleMaximized ? 'Restore' : 'Expand to maximum';
  });
  if (consoleMaximized && !consoleOpen) {
    consoleOpen = true;
    panels.forEach(p => p.classList.add('open'));
    document.querySelectorAll('#consoleChevron, #rpConsoleChevron').forEach(c => c.classList.add('open'));
  }
  setTimeout(() => {
    const inners = document.querySelectorAll('#consoleInner, #rpConsoleInner');
    inners.forEach(i => { i.scrollTop = i.scrollHeight; });
  }, 250);
}

// ─── Token estimation (~4 chars/token, +4 overhead per message) ─────────
function estimateTokens(messages) {
  if (!messages || !messages.length) return 0;
  let total = 0;
  for (const m of messages) {
    const content = m.content || '';
    total += Math.ceil(content.length / 4) + 4;
  }
  return total;
}

function getSessionMessages() {
  // Extract messages from the virtual scrolling arrays (not DOM, since not all are rendered)
  const messages = activeStackMode === 'rp' ? rpMessages : schoolMessages;
  return messages.map(m => ({ content: m.content }));
}

function estimateBlockTokens(block, allMsgs) {
  const btype = block.type;
  if (btype === 'system') {
    // System prompt is built server-side; use the stored text from session payload
    const cfg = (activeStackMode === 'school') ? schoolStackConfig : rpStackConfig;
    const text = (cfg && cfg._systemPromptText) ? cfg._systemPromptText : '';
    return { summary: text ? Math.ceil(text.length / 4) + 4 : 0, raw: 0 };
  }
  if (btype === 'custom') {
    const text = block.text || '';
    return { summary: text ? Math.ceil(text.length / 4) + 4 : 0, raw: 0 };
  }
  if (btype === 'summary_chunk') {
    const summaryTok = block.text ? Math.ceil(block.text.length / 4) + 4 : 0;
    const start = block.start ?? 0;
    const end = block.end ?? 0;
    const rawMsgs = allMsgs.slice(start, end);
    const rawTok = estimateTokens(rawMsgs);
    return { summary: summaryTok, raw: rawTok };
  }
  if (btype === 'head') {
    const n = block.n ?? 2;
    const rawMsgs = allMsgs.slice(0, n);
    return { summary: 0, raw: estimateTokens(rawMsgs) };
  }
  if (btype === 'raw_mid') {
    const start = block.start ?? 0;
    const end = block.end ?? 0;
    const rawMsgs = allMsgs.slice(start, end);
    return { summary: 0, raw: estimateTokens(rawMsgs) };
  }
  if (btype === 'tail') {
    const n = block.n ?? 3;
    const rawMsgs = allMsgs.slice(-n);
    return { summary: 0, raw: estimateTokens(rawMsgs) };
  }
  return { summary: 0, raw: 0 };
}

function formatTokens(n) {
  if (n === 0) return '0';
  if (n >= 1000) return (n / 1000).toFixed(1) + 'k';
  return String(n);
}

function addConsoleEntry(data) {
  const inner = document.getElementById('consoleInner');
  // Remove empty placeholder
  const empty = inner.querySelector('.console-empty');
  if (empty) empty.remove();

  consoleEventCount++;
  document.getElementById('consoleCount').textContent = consoleEventCount;

  const isReq = data.event === 'request';
  const isResp = data.event === 'response';
  const isErr = data.event === 'error';
  const isKwargs = data.event === 'request_kwargs';
  const isTrim = data.event === 'style_trim';

  // Update token estimate in console title bar
  if (isReq && data.token_estimate !== undefined) {
    const el = document.getElementById('consoleTokEst');
    if (el) el.textContent = '~' + data.token_estimate + ' tok';
  }
  if (isResp && data.usage && data.usage.prompt_tokens !== undefined) {
    const el = document.getElementById('consoleTokEst');
    if (el) el.textContent = data.usage.prompt_tokens + ' tok';
  }
  const tagClass = isReq ? 'console-tag-req' : isResp ? 'console-tag-resp' : isErr ? 'console-tag-err' : isTrim ? 'console-tag-trim' : 'console-tag-req';
  const tagLabel = isReq ? '→ REQ' : isResp ? '← RESP' : isErr ? '✕ ERR' : isTrim ? '✂ TRIM' : '→ KWARGS';
  const llmClass = data.llm === 'character' ? 'console-llm-char' : 'console-llm-analysis';
  const llmLabel = data.llm === 'character' ? 'CHARACTER' : 'ANALYSIS';

  let html = `<div class="console-entry">`;
  html += `<span class="ts">${escapeHtml(data.timestamp || '')}</span>`;
  if (data.label) {
    html += ` <span style="color:var(--yellow);font-size:11px;font-weight:600">[${escapeHtml(data.label)}]</span>`;
  }
  html += ` <span class="tag ${tagClass}">${tagLabel}</span>`;
  html += ` <span class="${llmClass}">${llmLabel}</span>`;
  if (data.model) html += ` <span class="model">${escapeHtml(data.model)}</span>`;
  if (data.temperature !== undefined) html += ` <span class="model">temp=${data.temperature}</span>`;
  if (data.max_tokens) html += ` <span class="model">max=${data.max_tokens}</span>`;
  html += `<br>`;

  if (isReq && data.messages) {
    const estTokens = estimateTokens(data.messages);
     // (live token counter moved to prompt stack builder footer)
     html += `<div style="margin:4px 0">`;
     data.messages.forEach(m => {
      const role = m.role || '?';
      let content = m.content || '';
      html += `<div class="msg-content ${role}"><span class="msg-role">${escapeHtml(role)}</span>: ${escapeHtml(content)}</div>`;
    });
    html += `</div>`;
  } else if (isResp && data.content) {
    let content = data.content;
    html += `<div class="msg-content assistant">${escapeHtml(content)}</div>`;
  } else if (isErr) {
    html += `<div class="msg-content" style="border-left-color:var(--red);color:var(--red)">${escapeHtml(data.message || data.content || '')}</div>`;
  } else if (isTrim) {
    html += `<div class="msg-content" style="border-left-color:var(--yellow);color:var(--yellow)">${escapeHtml(data.message || '')}</div>`;
  } else if (isKwargs && data.kwargs) {
    html += `<pre style="margin:4px 0;font-size:11px;color:var(--text-dim);white-space:pre-wrap;word-break:break-all">${escapeHtml(JSON.stringify(data.kwargs, null, 2))}</pre>`;
  }

  if (isResp && data.usage) {
    const u = data.usage;
    html += `<span class="usage">📊 tokens: prompt=${u.prompt_tokens || '?'} completion=${u.completion_tokens || '?'} total=${u.total_tokens || '?'}</span>`;
    if (data.finish_reason) html += ` <span class="usage">finish: ${escapeHtml(data.finish_reason)}</span>`;
    html += `<br>`;
  }

  html += `</div>`;
  inner.insertAdjacentHTML('beforeend', html);
  trimConsoleEntries(inner);

  // Auto-scroll to bottom if console is open
  if (consoleOpen) {
    inner.scrollTop = inner.scrollHeight;
  }
}

