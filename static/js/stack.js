// Character School — stack.js
// Stack builder UI (context management blocks).

// ─── Stack Builder ──────────────────────────────────────────────
const STACK_BLOCK_DEFS = {
  system:         { icon: '⚙️', label: 'System Prompt',  desc: 'Character cards + persona + routing', locked: true },
  head:           { icon: '📌', label: 'Head Messages',   desc: 'First N messages kept raw (sets the scene)', hasN: true },
  summary_chunk:  { icon: '📝', label: 'Summary Chunk',   desc: 'Engine-summarized msg range — read-only', hasRange: true, hasSummarize: true, hasAuto: true },
  raw_mid:        { icon: '📄', label: 'Raw Mid',         desc: 'Raw messages in a range — kept verbatim', hasRange: true },
  custom:         { icon: '✏️', label: 'Custom Inject',   desc: 'Your own system message — free text', hasText: true },
  tail:           { icon: '⏩', label: 'Raw Tail',        desc: 'Last N messages kept raw (recent context)', hasN: true },
  // backward compat (normalized to summary_chunk on load)
  early_summary:  { icon: '📝', label: 'Summary Chunk (legacy)', desc: 'Summarized range', hasRange: true, hasSummarize: true },
  late_summary:   { icon: '📝', label: 'Summary Chunk (legacy)', desc: 'Summarized range', hasRange: true, hasSummarize: true },
  summary:        { icon: '📝', label: 'Summary Chunk (legacy)', desc: 'Summarized range', hasRange: true, hasSummarize: true },
};

function getDefaultStackConfig() {
  return { blocks: [
    { type: 'system',        enabled: true, locked: true },
    { type: 'head',          enabled: true, n: 2 },
    { type: 'summary_chunk', enabled: true, start: 2, end: 5, text: '' },
    { type: 'raw_mid',       enabled: true, start: 5, end: 8 },
    { type: 'summary_chunk', enabled: true, start: 8, end: 11, text: '', auto: false },
    { type: 'custom',        enabled: false, text: '' },
    { type: 'tail',          enabled: true, n: 3 },
  ]};
}

function normalizeStackTypes(blocks) {
  for (const b of blocks) {
    if (b.type === 'early_summary' || b.type === 'late_summary' || b.type === 'summary') {
      b.type = 'summary_chunk';
    }
  }
  // Don't touch auto — toggleBlockAuto and the auto-summary system manage it.
  return blocks;
}

function openStackBuilder() {
  if (!rpActiveSessionId) { alert('Start or resume an RP session first.'); return; }
  if (!rpStackConfig) rpStackConfig = getDefaultStackConfig();
  normalizeStackTypes(rpStackConfig.blocks);
  activeStackConfig = rpStackConfig;
  activeStackWS = rpWS;
  activeStackMode = 'rp';
  renderStackBlocks();
  document.getElementById('stackOverlay').classList.add('show');
}

function openSchoolStackBuilder() {
  if (!schoolActiveSessionId) { alert('Start or resume a session first.'); return; }
  if (!schoolStackConfig) schoolStackConfig = getDefaultStackConfig();
  normalizeStackTypes(schoolStackConfig.blocks);
  activeStackConfig = schoolStackConfig;
  activeStackWS = ws;
  activeStackMode = 'school';
  renderStackBlocks();
  document.getElementById('stackOverlay').classList.add('show');
}

function closeStackBuilder() {
  document.getElementById('stackOverlay').classList.remove('show');
}

function renderStackBlocks() {
  const list = document.getElementById('stackBlockList');
  list.innerHTML = '';
  const blocks = activeStackConfig.blocks || [];

  // Normalize legacy types
  normalizeStackTypes(blocks);

  blocks.forEach((block, idx) => {
    const def = STACK_BLOCK_DEFS[block.type] || {};
    const enabled = block.enabled !== false;
    const locked = !!def.locked || !!block.locked;
    const isSummaryChunk = block.type === 'summary_chunk';
    const isCustom = block.type === 'custom';

    const div = document.createElement('div');
    div.className = 'stack-block ' + (enabled ? 'enabled' : 'disabled') + (locked ? ' locked' : '');
    div.draggable = !locked;
    div.dataset.idx = idx;

    let inner = '';
    inner += `<span class="drag-handle">${locked ? '' : '⋮⋮'}</span>`;
    inner += `<span class="block-icon">${def.icon || '❓'}</span>`;
    inner += `<div style="flex:1"><div class="block-label">${def.label || block.type}</div><div class="block-desc">${def.desc || ''}</div></div>`;

    if (def.hasN) {
      inner += `<input class="block-n-input" type="number" min="0" max="50" value="${block.n ?? 2}" onchange="updateBlockN(${idx}, this.value)" onclick="event.stopPropagation()" />`;
    }

    if (def.hasRange) {
      const startVal = block.start ?? 0;
      const endVal = block.end ?? 0;
      const hasText = !!block.text;
      inner += `<div class="block-range-wrap" onclick="event.stopPropagation()">`;
      inner += `<input class="block-range-input" type="number" min="0" max="999" value="${startVal}" onchange="updateBlockRange(${idx}, 'start', this.value)" placeholder="from" />`;
      inner += `<span class="block-range-arrow">→</span>`;
      inner += `<input class="block-range-input" type="number" min="0" max="999" value="${endVal}" onchange="updateBlockRange(${idx}, 'end', this.value)" placeholder="to" />`;
      if (def.hasSummarize) {
        const btnLabel = hasText ? 'Re-summarize' : 'Summarize';
        inner += `<span class="block-summarize-btn ${hasText ? 'has-text' : ''}" onclick="summarizeBlock(${idx})">${btnLabel}</span>`;
      }
      if (def.hasAuto) {
        const autoOn = !!block.auto;
        inner += `<label class="block-auto-wrap${autoOn ? ' auto-on' : ''}" onclick="event.stopPropagation()"><input type="checkbox" ${autoOn ? 'checked' : ''} onchange="toggleBlockAuto(${idx})" /> Auto</label>`;
      }
      inner += `</div>`;
    }

    if (!locked) {
      inner += `<span class="block-delete-btn" onclick="deleteBlock(${idx})" title="Delete">✕</span>`;
    }

    if (!locked) {
      inner += `<div class="block-toggle" onclick="toggleBlock(${idx})"></div>`;
    } else {
      inner += `<div class="block-toggle"></div>`;
    }

    div.innerHTML = inner;

    // ── Token display ──
    if (enabled && (def.hasRange || def.hasN || isSummaryChunk || isCustom || block.type === 'system')) {
      const allMsgs = getSessionMessages();
      const tok = estimateBlockTokens(block, allMsgs);
      const tokDiv = document.createElement('div');
      tokDiv.className = 'block-token-info';
      if (isSummaryChunk) {
        if (tok.summary > 0 && tok.raw > 0) {
          const saved = Math.round((1 - tok.summary / tok.raw) * 100);
          tokDiv.innerHTML = `<span class="tok-before">${formatTokens(tok.raw)} →</span> <span class="tok-after">${formatTokens(tok.summary)}</span> <span class="tok-saved">(−${saved}%)</span>`;
        } else if (tok.summary > 0) {
          tokDiv.innerHTML = `<span class="tok-after">${formatTokens(tok.summary)}</span> summarised`;
        } else if (tok.raw > 0) {
          tokDiv.innerHTML = `<span class="tok-before">${formatTokens(tok.raw)} raw</span> <span class="tok-unsaved">(not summarised)</span>`;
        }
      } else if (tok.raw > 0) {
        tokDiv.innerHTML = `<span class="tok-before">≈ ${formatTokens(tok.raw)} tokens</span>`;
      } else if (tok.summary > 0) {
        tokDiv.innerHTML = `<span class="tok-before">≈ ${formatTokens(tok.summary)} tokens</span>`;
      }
      if ((tok.summary > 0 || tok.raw > 0) || isSummaryChunk) {
        div.appendChild(tokDiv);
      }
    }

    // ── Inline text content for summary_chunk and custom ──
    if (isSummaryChunk && block.text) {
      const txtDiv = document.createElement('div');
      txtDiv.className = 'block-text-inline summary-text';
      txtDiv.textContent = block.text;
      txtDiv.title = block.text;
      div.appendChild(txtDiv);
    }
    if (isCustom && enabled) {
      const txtDiv = document.createElement('div');
      txtDiv.className = 'block-text-inline custom-text';
      const ta = document.createElement('textarea');
      ta.className = 'block-custom-ta';
      ta.value = block.text || '';
      ta.placeholder = 'Custom system message...';
      ta.rows = 2;
      ta.onchange = function() { block.text = this.value; };
      ta.oninput = function() { block.text = this.value; };
      ta.onclick = function(e) { e.stopPropagation(); };
      txtDiv.appendChild(ta);
      div.appendChild(txtDiv);
    }

    // Drag events
    if (!locked) {
      div.addEventListener('dragstart', (e) => {
        stackDragSrc = idx;
        div.classList.add('dragging');
        e.dataTransfer.effectAllowed = 'move';
      });
      div.addEventListener('dragend', () => {
        div.classList.remove('dragging');
        document.querySelectorAll('.stack-block').forEach(el => el.classList.remove('drag-over'));
      });
      div.addEventListener('dragover', (e) => {
        e.preventDefault();
        e.dataTransfer.dropEffect = 'move';
        div.classList.add('drag-over');
      });
      div.addEventListener('dragleave', () => {
        div.classList.remove('drag-over');
      });
      div.addEventListener('drop', (e) => {
        e.preventDefault();
        div.classList.remove('drag-over');
        if (stackDragSrc === null || stackDragSrc === idx) return;
        if (idx === 0) return; // can't drop before system
        if (stackDragSrc === 0) return; // can't move system
        const blks = activeStackConfig.blocks;
        const moved = blks.splice(stackDragSrc, 1)[0];
        const insertAt = idx > stackDragSrc ? idx - 1 : idx;
        blks.splice(insertAt, 0, moved);
        stackDragSrc = null;
        normalizeStackTypes(blks);
        renderStackBlocks();
      });
    }

    list.appendChild(div);
  });

  // Update total token counter in footer
  updateStackTotalTokens();
}

function updateStackTotalTokens() {
  const blocks = activeStackConfig.blocks || [];
  const msgs = getSessionMessages();
  let rawTotal = 0;    // everything except summary chunks (raw messages instead)
  let summarTotal = 0; // everything including summary chunks
  blocks.forEach(block => {
    if (block.enabled === false) return;
    const tok = estimateBlockTokens(block, msgs);
    const btype = block.type;
    if (btype === 'summary_chunk') {
      // Raw: the raw messages it covers (sent if no summarization)
      // Summarized: just the summary text (replaces those raw messages)
      rawTotal += (tok.raw || 0);
      summarTotal += (tok.summary || 0);
    } else {
      // all other blocks shared across both totals
      rawTotal += (tok.summary || 0) + (tok.raw || 0);
      summarTotal += (tok.summary || 0) + (tok.raw || 0);
    }
  });
  const el = document.getElementById('stackTotalTokens');
  if (el) el.textContent = `Raw: ${formatTokens(rawTotal)} · Summarized: ${formatTokens(summarTotal)} tokens`;
}

function toggleBlock(idx) {
  const block = activeStackConfig.blocks[idx];
  if (!block || block.locked) return;
  block.enabled = !block.enabled;
  renderStackBlocks();
}

function updateBlockN(idx, val) {
  const block = activeStackConfig.blocks[idx];
  if (!block) return;
  block.n = Math.max(0, Math.min(50, parseInt(val) || 0));
}

function updateBlockRange(idx, field, val) {
  const block = activeStackConfig.blocks[idx];
  if (!block) return;
  block[field] = Math.max(0, parseInt(val) || 0);
}

let summarizingBlockIdx = null;
function toggleBlockAuto(idx) {
  const block = activeStackConfig.blocks[idx];
  if (!block) return;
  block.auto = !block.auto;
  // Only one summary_chunk can have auto at a time
  if (block.auto) {
    for (let i = 0; i < activeStackConfig.blocks.length; i++) {
      if (i !== idx && activeStackConfig.blocks[i].type === 'summary_chunk') {
        activeStackConfig.blocks[i].auto = false;
      }
    }
  }
  renderStackBlocks();
}

function summarizeBlock(idx) {
  if (!activeStackWS || activeStackWS.readyState !== WebSocket.OPEN) { alert('Not connected.'); return; }
  summarizingBlockIdx = idx;
  activeStackWS.send(JSON.stringify({ type: 'summarize_block', block_index: idx }));
  const btn = document.querySelector(`.stack-block[data-idx="${idx}"] .block-summarize-btn`);
  if (btn) { btn.classList.add('summarizing'); btn.textContent = 'Summarizing…'; }
}

function saveStackConfig() {
  // Sync custom block textareas back into block.text
  document.querySelectorAll('.block-custom-ta').forEach(ta => {
    // oninput already syncs, but just in case
  });
  if (activeStackWS && activeStackWS.readyState === WebSocket.OPEN) {
    activeStackWS.send(JSON.stringify({ type: 'update_stack', stack_config: activeStackConfig }));
  } else if (activeStackMode === 'school' && schoolActiveSessionId) {
    // Fallback: save via REST
    fetch('/api/school/sessions/' + schoolActiveSessionId + '/stack', {
      method: 'PUT', headers: {'Content-Type':'application/json'},
      body: JSON.stringify({stack_config: activeStackConfig})
    });
  }
}

function resetStackConfig() {
  const sysText = activeStackConfig ? activeStackConfig._systemPromptText : '';
  activeStackConfig = getDefaultStackConfig();
  activeStackConfig._systemPromptText = sysText;
  if (activeStackMode === 'rp') {
    rpStackConfig = activeStackConfig;
  } else {
    schoolStackConfig = activeStackConfig;
  }
  renderStackBlocks();
}

function addStackBlock(type) {
  const newBlock = type === 'summary_chunk'
    ? { type: 'summary_chunk', enabled: true, start: 0, end: 0, text: '', auto: false }
    : { type: 'custom', enabled: true, text: '' };
  // Insert before tail if tail exists, otherwise at end
  const blocks = activeStackConfig.blocks;
  const tailIdx = blocks.findIndex(b => b.type === 'tail');
  const insertAt = tailIdx >= 0 ? tailIdx : blocks.length;
  blocks.splice(insertAt, 0, newBlock);
  normalizeStackTypes(blocks);
  renderStackBlocks();
}

function deleteBlock(idx) {
  const block = activeStackConfig.blocks[idx];
  if (!block || block.locked) return;
  if (block.type === 'system') return;
  // Only allow deleting summary_chunk and custom
  if (block.type !== 'summary_chunk' && block.type !== 'custom') return;
  activeStackConfig.blocks.splice(idx, 1);
  normalizeStackTypes(activeStackConfig.blocks);
  renderStackBlocks();
}

