// Character School — rp.js
// RP state, session setup, WebSocket, UI rendering, author's note, response style.

// ─── RP State & Logic ──────────────────────────
let rpWS = null;
let rpActiveSessionId = null;
let rpCharacters = [];      // [{filename, name}]
let rpPersonaFilename = null;
let rpResponseStyle = 'moderate';
let rpPov = null;
let rpInnerMonologue = false;
let rpAutoContinue = true;  // auto-continue truncated responses
let rpLorebooks = []; // active lorebook filenames for RP session
let schoolLorebooks = []; // active lorebook filenames for school session
const rpStyleLabels = {short:'Short', moderate:'Moderate', long:'Long', flexible:'Flexible'};
const rpStyleOrder = ['short','moderate','long','flexible'];
const rpStyleTokens = {short:'~150 words', moderate:'~150-300 words', long:'300+ words', flexible:'dynamic'};
const povLabels = {null:'Auto', 'first':'1st', 'second':'2nd', 'third':'3rd'};
const povOrder = ['first','second','third'];
const povTitles = {null:'Default (inherit from card)', 'first':'First person (I/me)', 'second':'Second person (you)', 'third':'Third person (she/he/they)'};
let schoolResponseStyle = 'moderate';
let schoolPov = null;
let schoolInnerMonologue = false;
let schoolAutoContinue = true;  // auto-continue truncated responses
let rpStackConfig = null;
let rpSystemPrompt = '';  // system prompt text from server for token estimation
let stackDragSrc = null;
let rpIsWaiting = false;
let rpCardsList = [];       // cached card list for picker
let rpSetupChars = [];      // selected filenames in setup
let rpSetupStyle = 'moderate';
let rpConsoleEvents = 0;
let rpCurrentMode = 'school';

// Character color palette for chat labels
const RP_CHAR_COLORS = ['#58a6ff', '#3fb950', '#bc8cff', '#db6d28'];

function switchMode(mode) {
  rpCurrentMode = mode;
  document.body.classList.toggle('rp-mode', mode === 'rp');
  document.getElementById('tabSchool').classList.toggle('active', mode === 'school');
  document.getElementById('tabRP').classList.toggle('active', mode === 'rp');
   // Reset token counter when switching modes
   // (token counter moved to prompt stack builder footer)
   if (mode === 'rp') {
    loadRPSessions();
    // Populate persona select in setup
    populateRPPersonaSelect();
    if (rpCharacters.length) renderRPSceneBar();
  } else if (mode === 'school') {
    loadSchoolSessions();
    if (schoolActiveSessionId) updateSchoolStyleButton();
  }
}

// ─── RP Session Setup ───────────────────────────
async function openRPSetup() {
  // Load card list if not cached
  if (rpCardsList.length === 0) {
    const resp = await fetch('/api/cards');
    rpCardsList = await resp.json();
  }
  // Render character picker
  const picker = document.getElementById('rpCharPicker');
  picker.innerHTML = rpCardsList.map(c => {
    const tags = (c.tags || []).slice(0, 3).map(t => `<span class="tags">${escapeHtml(t)}</span>`).join(' ');
    return `<div class="rp-char-pick-item" data-filename="${escapeHtml(c.filename)}" onclick="toggleRPCharPick('${escapeHtml(c.filename)}', this)">
      <div class="checkbox"></div>
      <div class="name">${escapeHtml(c.name)}</div>
      <div class="tags">${tags}</div>
    </div>`;
  }).join('');
  rpSetupChars = [];
  document.getElementById('rpSelectedCount').textContent = '0 selected';
  document.getElementById('rpStartBtn').disabled = true;
  // Reset toggles
  rpToggleSetting('turn', 'auto');
  rpToggleSetting('style', 'moderate');
  // Populate persona select
  populateRPPersonaSelect();
  document.getElementById('rpSetupOverlay').classList.add('show');
}

function closeRPSetup() {
  document.getElementById('rpSetupOverlay').classList.remove('show');
}

function populateRPPersonaSelect() {
  const sel = document.getElementById('rpPersonaSelect');
  sel.innerHTML = '<option value="">None (raw you)</option>';
  personasList.forEach(p => {
    const opt = document.createElement('option');
    opt.value = p.filename;
    opt.textContent = p.name;
    sel.appendChild(opt);
  });
}

function toggleRPCharPick(filename, el) {
  const idx = rpSetupChars.indexOf(filename);
  if (idx >= 0) {
    rpSetupChars.splice(idx, 1);
    el.classList.remove('selected');
    el.querySelector('.checkbox').textContent = '';
  } else {
    if (rpSetupChars.length >= 4) { alert('Maximum 4 characters'); return; }
    rpSetupChars.push(filename);
    el.classList.add('selected');
    el.querySelector('.checkbox').textContent = '✓';
  }
  document.getElementById('rpSelectedCount').textContent = rpSetupChars.length + ' selected';
  document.getElementById('rpStartBtn').disabled = rpSetupChars.length === 0;
}

function rpToggleSetting(group, value) {
  if (group === 'style') {
    rpSetupStyle = value;
    document.getElementById('rpToggleModerate').classList.toggle('active', value === 'moderate');
    document.getElementById('rpToggleLong').classList.toggle('active', value === 'long');
    document.getElementById('rpToggleShort').classList.toggle('active', value === 'short');
    document.getElementById('rpToggleFlexible').classList.toggle('active', value === 'flexible');
  }
}

function startRPSession() {
  const persona = document.getElementById('rpPersonaSelect').value || null;

  closeRPSetup();
  connectRPWS({
    characters: rpSetupChars,
    persona_filename: persona,
    response_style: rpSetupStyle,
  });
}

// ─── RP WebSocket ───────────────────────────────
function connectRPWS(startData, resumeSessionId) {
  if (rpWS) rpWS.close();
  const proto = location.protocol === 'https:' ? 'wss' : 'ws';
  rpWS = new WebSocket(proto + '://' + location.host + '/ws/rp');

  rpWS.onopen = () => {
    document.getElementById('status').textContent = 'Connected';
    document.getElementById('status').classList.add('connected');
    if (resumeSessionId) {
      rpWS.send(JSON.stringify({type: 'resume', session_id: resumeSessionId}));
    } else {
      rpWS.send(JSON.stringify({type: 'start', ...startData}));
    }
  };

  rpWS.onmessage = (event) => {
    const data = JSON.parse(event.data);
    handleRPMessage(data);
  };

  rpWS.onclose = () => {
    document.getElementById('status').textContent = 'Disconnected';
    document.getElementById('status').classList.remove('connected');
  };

  rpWS.onerror = () => {
    addRPMessage('system', 'Connection error', null, null, null, null);
  };
}

function handleRPMessage(data) {
   if (data.type === 'session_started') {
     rpActiveSessionId = data.session_id;
     rpCharacters = data.characters;
     rpPersonaFilename = data.persona;
     rpSystemPrompt = data.system_prompt || '';
     // Update currentPersona so user message labels show the persona name
    if (data.persona) {
      const p = personasList.find(p => p.filename === data.persona);
      if (p) currentPersona = p;
    } else {
      currentPersona = null;
    }
    rpResponseStyle = data.response_style;
    rpPov = data.pov || null;
    rpInnerMonologue = data.inner_monologue || false;
    rpAutoContinue = data.auto_continue !== undefined ? data.auto_continue : true;
    rpStackConfig = data.stack_config || null;
    if (rpStackConfig) rpStackConfig._systemPromptText = rpSystemPrompt;
    rpLorebooks = data.lorebooks || [];
    rpConsoleEvents = 0;
    document.getElementById('rpConsoleCount').textContent = '0';
    document.getElementById('rpConsoleInner').innerHTML = '<div class="console-empty">LLM inference events will appear here.</div>';
    // Clear summary bar from any previous session
    const oldSummaryBar = document.getElementById('rpSummaryBar');
    if (oldSummaryBar) oldSummaryBar.remove();
    renderRPSceneBar();
    // Clear virtual scroll state
    clearRPChat();
     document.getElementById('rpInputArea').style.display = 'flex';
     document.getElementById('rpAuthorNoteBar').style.display = '';
     rpIsWaiting = false;
     updateRPInputState();
     // Apply per-session bg or fall back to global
     applyBgImage(data.bg_image || localStorage.getItem('cs_bg_image') || '', parseFloat(localStorage.getItem('cs_bg_overlay')) ?? 0.85, false);
     loadRPSessions();
   } else if (data.type === 'session_resumed') {
     rpActiveSessionId = data.session_id;
     rpCharacters = data.characters;
     rpPersonaFilename = data.persona;
     rpSystemPrompt = data.system_prompt || '';
     // Update currentPersona so user message labels show the persona name
    if (data.persona) {
      const p = personasList.find(p => p.filename === data.persona);
      if (p) currentPersona = p;
    } else {
      currentPersona = null;
    }
    rpResponseStyle = data.response_style;
    rpPov = data.pov || null;
    rpInnerMonologue = data.inner_monologue || false;
    rpAutoContinue = data.auto_continue !== undefined ? data.auto_continue : true;
     rpStackConfig = data.stack_config || null;
     if (rpStackConfig) rpStackConfig._systemPromptText = rpSystemPrompt;
     rpLorebooks = data.lorebooks || [];
     // Clear summary bar from any previous session
     const oldSummaryBar = document.getElementById('rpSummaryBar');
    if (oldSummaryBar) oldSummaryBar.remove();
    // Restore saved console events for this session
    const rpConsoleInner = document.getElementById('rpConsoleInner');
    rpConsoleInner.innerHTML = '';
    rpConsoleEvents = 0;
    // Always fetch console events via REST to keep the WS resume payload tiny
    if (data.console_events_truncated) {
      fetch(`/api/rp/sessions/${data.session_id}/console_events`)
        .then(r => r.json())
        .then(d => {
          if (d.events && d.events.length) {
            d.events.forEach(evt => addRPConsoleEntry(evt));
            trimConsoleEntries(rpConsoleInner);
          } else {
            rpConsoleInner.innerHTML = '<div class="console-empty">LLM inference events will appear here.</div>';
            document.getElementById('rpConsoleCount').textContent = '0';
          }
        })
        .catch(() => {
          rpConsoleInner.innerHTML = '<div class="console-empty">LLM inference events will appear here.</div>';
          document.getElementById('rpConsoleCount').textContent = '0';
        });
    } else {
      const savedEvents = data.console_events || [];
      if (savedEvents.length) {
        savedEvents.forEach(evt => addRPConsoleEntry(evt));
        trimConsoleEntries(rpConsoleInner);
      } else {
        rpConsoleInner.innerHTML = '<div class="console-empty">LLM inference events will appear here.</div>';
        document.getElementById('rpConsoleCount').textContent = '0';
      }
    }
    renderRPSceneBar();
    // Clear virtual scroll state and populate from server messages
    clearRPChat();
    if (data.messages) {
      data.messages.forEach(m => {
        if (m.role === 'user') {
          rpMessages.push({ role: 'user', content: m.content, speaker: null, messageId: m.id, clientId: null, personaName: m.persona_name || null, imagePath: m.image_path || null, imagePrompt: m.image_prompt || null, seed: m.seed, swipes: m.swipes || null, activeSwipe: m.active_swipe || 0 });
        } else if (m.role === 'character') {
          rpMessages.push({ role: 'character', content: m.content, speaker: m.speaker, messageId: m.id, clientId: null, imagePath: m.image_path || null, imagePrompt: m.image_prompt || null, seed: m.seed, swipes: m.swipes || null, activeSwipe: m.active_swipe || 0 });
        } else if (m.role === 'system') {
          rpMessages.push({ role: 'system', content: m.content, speaker: null, messageId: m.id, clientId: null, imagePath: m.image_path || null, imagePrompt: m.image_prompt || null, seed: m.seed, swipes: m.swipes || null, activeSwipe: m.active_swipe || 0 });
        }
      });
    }
    rpVisibleCount.value = Math.min(VIRTUAL_SCROLL_BATCH, rpMessages.length);
    renderVirtualMessages('rpChatMessages', rpMessages, rpVisibleCount.value);
    initVirtualScroll('rpChatMessages', rpMessages, rpVisibleCount, rpObserver);
    document.getElementById('rpInputArea').style.display = 'flex';
    document.getElementById('rpAuthorNoteBar').style.display = '';
    rpIsWaiting = false;
    updateRPInputState();
     // Apply per-session bg or fall back to global
     applyBgImage(data.bg_image || localStorage.getItem('cs_bg_image') || '', parseFloat(localStorage.getItem('cs_bg_overlay')) ?? 0.85, false);
     // Highlight active session in sidebar
     document.querySelectorAll('.rp-session-item').forEach(el => {
       el.classList.toggle('active', parseInt(el.dataset.sessionId) === rpActiveSessionId);
     });
    } else if (data.type === 'character_message') {
      clearStreamBubble();
      if (data.regenerated) {
        // Update existing message (swipe) instead of adding new
        const target = rpMessages.find(m => m.messageId === data.message_id);
        if (target) {
          target.swipes = target.swipes || [];
          if (target.swipes.length === 0) target.swipes.push(target.content); // original becomes swipe 0
          target.swipes.push(data.content);
          target.content = data.content;
          target.truncated = !!data.truncated;
          target.activeSwipe = (data.swipe_index !== undefined) ? data.swipe_index : target.swipes.length - 1;
          renderVirtualMessages('rpChatMessages', rpMessages, rpVisibleCount.value);
        }
      } else {
        addRPMessage('character', data.content, data.character_name, data.message_id, null, data.image_path || null, data.image_prompt || null, data.seed, data.truncated);
      }
      rpIsWaiting = false;
      updateRPInputState();
  } else if (data.type === 'swipe_switched') {
    const target = rpMessages.find(m => m.messageId === data.message_id);
    if (target) {
      target.content = data.content;
      target.activeSwipe = data.active_swipe;
      renderVirtualMessages('rpChatMessages', rpMessages, rpVisibleCount.value);
    }
  } else if (data.type === 'character_delta') {
    handleStreamDelta('rp', data);
  } else if (data.type === 'character_typing') {
    showRPTyping(data.character_name);
  } else if (data.type === 'character_typing_stopped') {
    hideRPTyping();
  } else if (data.type === 'generation_stopped') {
    clearStreamBubble();
    rpIsWaiting = false;
    hideRPTyping();
    updateRPInputState();
  } else if (data.type === 'user_message_stored') {
    // Tag the RP user message in the array with the DB row id so delete works
    if (data.message_id != null) {
      const target = data.client_msg_id
        ? rpMessages.find(m => m.clientId === data.client_msg_id)
        : null;
      if (target) {
        target.messageId = data.message_id;
      }
    }
  } else if (data.type === 'summary_updated') {
    showRPSummaryBar(data.summary, data.summarized_count);
    } else if (data.type === 'settings_updated') {
      rpResponseStyle = data.response_style;
      rpPov = data.pov || null;
      rpInnerMonologue = data.inner_monologue || false;
      rpAutoContinue = data.auto_continue !== undefined ? data.auto_continue : true;
      if (data.system_prompt !== undefined) {
        rpSystemPrompt = data.system_prompt;
        if (rpStackConfig) rpStackConfig._systemPromptText = rpSystemPrompt;
      }
      renderRPSceneBar();
   } else if (data.type === 'bg_image_updated') {
     // Server confirmed per-session bg update — no action needed, UI already applied
   } else if (data.type === 'stack_updated') {
    rpStackConfig = data.stack_config;
    if (rpStackConfig) rpStackConfig._systemPromptText = rpSystemPrompt;
    if (activeStackMode === 'rp') activeStackConfig = rpStackConfig;
  } else if (data.type === 'block_summarizing') {
    // Button already shows "Summarizing…" from JS; auto triggers also send this
  } else if (data.type === 'block_summary_updated') {
    if (data.stack_config) rpStackConfig = data.stack_config;
    if (activeStackMode === 'rp') activeStackConfig = rpStackConfig;
    if (rpStackConfig) rpStackConfig._systemPromptText = rpSystemPrompt;
    summarizingBlockIdx = null;
    renderStackBlocks();
    // Show brief toast
    if (data.summary) {
      const btn = document.querySelector(`.stack-block[data-idx="${data.block_index}"] .block-summarize-btn`);
      if (btn) { btn.classList.add('has-text'); }
    }
  } else if (data.type === 'chunk_added') {
    // Auto-summary created a new summary_chunk
    if (data.stack_config) rpStackConfig = data.stack_config;
    if (activeStackMode === 'rp') activeStackConfig = rpStackConfig;
    if (rpStackConfig) rpStackConfig._systemPromptText = rpSystemPrompt;
    summarizingBlockIdx = null;
    renderStackBlocks();
    if (data.summary) {
      addConsoleEvent('auto-sum', `Auto-summarized msgs ${data.chunk_index ? '→ chunk #' + data.chunk_index : ''}: ${data.summary.substring(0, 80)}...`);
    }
  } else if (data.type === 'persona_changed') {
    rpPersonaFilename = data.persona;
    // Update currentPersona so user message labels reflect the new persona
    if (data.persona) {
      const p = personasList.find(p => p.filename === data.persona);
      if (p) currentPersona = p;
    } else {
      currentPersona = null;
    }
    renderRPSceneBar();
  } else if (data.type === 'character_added') {
    rpCharacters = data.characters;
    renderRPSceneBar();
    loadRPSessions(); // refresh sidebar title
  } else if (data.type === 'character_removed') {
    rpCharacters = data.characters;
    renderRPSceneBar();
  } else if (data.type === 'reload_sessions') {
    loadRPSessions();
  } else if (data.type === 'message_deleted') {
    // If server says delete failed (success: false), the optimistic DOM removal was
    // wrong — reload session from DB to restore the message. If success: true, the
    // optimistic splice was correct and no action is needed.
    if (data.success === false && rpActiveSessionId && rpWS && rpWS.readyState === 1) {
      rpWS.send(JSON.stringify({type: 'resume', session_id: rpActiveSessionId}));
    }
  } else if (data.type === 'error') {
    clearStreamBubble();
    addRPMessage('system', 'Error: ' + data.message, null, null, null, null);
    rpIsWaiting = false;
    updateRPInputState();
    hideRPTyping();
  } else if (data.type === 'console_event') {
    addRPConsoleEntry(data);
    queueSaveRPConsoleEvents();
  }
}

// ─── RP UI Rendering ────────────────────────────
function renderRPSceneBar() {
  const bar = document.getElementById('rpSceneBar');
  if (!rpCharacters.length) {
    bar.innerHTML = '<span style="color:var(--text-dim);font-size:13px">No active session.</span>';
    return;
  }
  let html = '<div class="scene-chars">';
  rpCharacters.forEach((c, i) => {
    const color = RP_CHAR_COLORS[i % RP_CHAR_COLORS.length];
    html += `<span class="char-chip" style="border-color:${color}"><span class="dot" style="background:${color}"></span>${escapeHtml(c.name)}`;
    if (rpCharacters.length > 1) {
      html += `<span class="char-chip-x" onclick="removeRPChar('${escapeHtml(c.filename)}')" title="Remove ${escapeHtml(c.name)}">×</span>`;
    }
    html += `</span>`;
  });
  if (rpCharacters.length < 4) {
    html += `<span class="char-chip char-chip-add" onclick="openRPAddCharPicker()">+ Add</span>`;
  }
  html += '</div><span class="spacer"></span>';

  // Response style dropdown
  html += `<select class="rp-pov-select" onchange="rpUpdateSetting('response_style', this.value)" title="Response length">`;
  rpStyleOrder.forEach(st => {
    html += `<option value="${st}" ${rpResponseStyle===st?'selected':''}>${rpStyleLabels[st]} (${rpStyleTokens[st]})</option>`;
  });
  html += `</select>`;

  // POV selector dropdown
  html += `<select class="rp-pov-select" onchange="rpUpdateSetting('pov', this.value || null)" title="${povTitles[rpPov]||'Perspective'}">`;
  html += `<option value="" ${!rpPov?'selected':''}>${povLabels[null]}</option>`;
  povOrder.forEach(pv => {
    html += `<option value="${pv}" ${rpPov===pv?'selected':''}>${povLabels[pv]}</option>`;
  });
  html += `</select>`;

  // Inner monologue toggle
  html += `<div class="rp-toggle-btn ${rpInnerMonologue?'active':''}" onclick="rpUpdateSetting('inner_monologue', ${!rpInnerMonologue})" title="Inner monologue (Thoughts block)">💭 ${rpInnerMonologue ? 'ON' : 'OFF'}</div>`;

  // Auto-continue toggle
  html += `<div class="rp-toggle-btn ${rpAutoContinue?'active':''}" onclick="rpUpdateSetting('auto_continue', ${!rpAutoContinue})" title="Auto-continue truncated responses">🔄 ${rpAutoContinue ? 'ON' : 'OFF'}</div>`;

  // Stack Builder button
  html += `<div class="rp-toggle-btn" onclick="openStackBuilder()" title="Customize prompt stack">🔗 Prompts Stack</div>`;

  // Lorebook button
  html += `<div class="rp-toggle-btn" onclick="openLorebookManager()" title="Manage lorebooks">📚 Lorebooks</div>`;

  // Generate Scene button (ComfyUI)
  html += `<div class="rp-toggle-btn" onclick="openGenerateSceneModal('rp')" title="Generate scene image via ComfyUI">🎨 Generate Scene</div>`;

  // Active lorebook indicator
  if (rpLorebooks && rpLorebooks.length > 0) {
    html += `<span style="font-size:11px;color:var(--accent);margin-left:4px">${rpLorebooks.length} 📚</span>`;
  }

  bar.innerHTML = html;

  // Persona selector — now lives in the input area
  const personaSel = document.getElementById('rpPersonaBar');
  if (personaSel) {
    let phtml = '<option value="">No persona</option>';
    personasList.forEach(p => {
      const sel = p.filename === rpPersonaFilename ? ' selected' : '';
      phtml += `<option value="${escapeHtml(p.filename)}"${sel}>${escapeHtml(p.name)}</option>`;
    });
    personaSel.innerHTML = phtml;
    personaSel.style.display = personasList.length > 0 ? 'block' : 'none';
  }

  // Directed character selector — always visible with Auto as first option
  const directSel = document.getElementById('rpDirectSelect');
  if (directSel) {
    directSel.style.display = 'block';
    let dhtml = '<option value="">Auto (LLM picks)</option>';
    rpCharacters.forEach(c => {
      dhtml += `<option value="${escapeHtml(c.filename)}">${escapeHtml(c.name)}</option>`;
    });
    directSel.innerHTML = dhtml;
  }
}

function addRPMessage(role, content, speaker, messageId, clientId, imagePath, imagePrompt, seed, truncated) {
  // Remove empty placeholder
  const empty = document.getElementById('rpChatEmpty');
  if (empty) empty.remove();

  // Push to array — stamp persona name per-message so old labels don't change when persona switches
  const personaName = role === 'user' ? (currentPersona?.name || null) : null;
  const msg = { role, content, speaker, messageId, clientId, personaName, imagePath, imagePrompt, seed, truncated, swipes: null, activeSwipe: 0 };
  rpMessages.push(msg);

  // Render
  renderVirtualMessages('rpChatMessages', rpMessages, rpVisibleCount.value);

  // Init observer if not yet set up
  if (!rpObserver) {
    initVirtualScroll('rpChatMessages', rpMessages, rpVisibleCount, rpObserver);
  }
}

function showRPTyping(characterName) {
  hideRPTyping(); // remove any existing
  const container = document.getElementById('rpChatMessages');
  const div = document.createElement('div');
  div.className = 'rp-typing';
  div.id = 'rpTypingIndicator';
  const name = characterName ? escapeHtml(characterName) : 'Someone';
  div.innerHTML = `${name} is responding <span class="dots"><span>●</span><span>●</span><span>●</span></span>`;
  container.appendChild(div);
  container.scrollTop = container.scrollHeight;
}

function hideRPTyping() {
  const existing = document.getElementById('rpTypingIndicator');
  if (existing) existing.remove();
}

// ── School mode typing indicators ──
function showSchoolTyping(kind, characterName) {
  hideSchoolTyping();
  if (kind === 'character') {
    // Show in chat area
    const container = document.getElementById('chatMessages');
    const div = document.createElement('div');
    div.className = 'rp-typing';
    div.id = 'schoolCharTyping';
    const name = characterName ? escapeHtml(characterName) : 'Character';
    div.innerHTML = `${name} is responding <span class="dots"><span>●</span><span>●</span><span>●</span></span>`;
    container.appendChild(div);
    container.scrollTop = container.scrollHeight;
  } else if (kind === 'analyzer') {
    // Show in analysis panel
    const container = document.getElementById('analysisPanel');
    // Keep the empty placeholder text if present, but replace with typing indicator
    const div = document.createElement('div');
    div.className = 'rp-typing';
    div.id = 'schoolAnalyzerTyping';
    div.style.margin = '10px 14px';
    div.innerHTML = `Analyzer is thinking <span class="dots"><span>●</span><span>●</span><span>●</span></span>`;
    container.appendChild(div);
    container.scrollTop = container.scrollHeight;
  }
}

function hideSchoolTyping() {
  const c = document.getElementById('schoolCharTyping');
  if (c) c.remove();
  const a = document.getElementById('schoolAnalyzerTyping');
  if (a) a.remove();
}

function showRPSummaryBar(summary, count) {
  let bar = document.getElementById('rpSummaryBar');
  if (!bar) {
    bar = document.createElement('div');
    bar.className = 'rp-summary-bar';
    bar.id = 'rpSummaryBar';
    const inputArea = document.getElementById('rpInputArea');
    inputArea.parentNode.insertBefore(bar, inputArea);
  }
  bar.innerHTML = `<span class="label">📝 Summary</span> <span>(${count} msgs compressed)</span> <span style="flex:1"></span> <span style="font-size:11px;max-width:400px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap">${escapeHtml(summary.substring(0, 100))}...</span>`;
  bar.onclick = () => { alert(summary); };
}

let pendingRPImage = null;

function onRPImageSelected(e) {
  const file = e.target.files[0];
  if (!file) return;
  pendingRPImage = file;
  const img = document.getElementById('rpImagePreview');
  img.src = URL.createObjectURL(file);
  document.getElementById('rpImagePreviewWrap').style.display = 'block';
  document.getElementById('rpMsgInput').focus();
}

function clearRPImagePreview() {
  pendingRPImage = null;
  document.getElementById('rpImageUpload').value = '';
  document.getElementById('rpImagePreview').src = '';
  document.getElementById('rpImagePreviewWrap').style.display = 'none';
}

async function uploadRPImage(file) {
  const form = new FormData();
  form.append('file', file);
  const r = await fetch('/api/rp/upload', {method: 'POST', body: form});
  if (!r.ok) throw new Error('RP upload failed');
  const j = await r.json();
  return j.image_path;
}

// Continue a reply that hit the length limit — the same path as /continue.
function continueRPMessage() {
  if (rpIsWaiting || !rpWS || rpWS.readyState !== WebSocket.OPEN) return;
  rpWS.send(JSON.stringify({ type: 'continue_message' }));
  rpIsWaiting = true;
  updateRPInputState();
}

async function sendRPMessage() {
  const input = document.getElementById('rpMsgInput');
  const text = input.value.trim();
  if ((!text && !pendingRPImage) || !rpWS || rpWS.readyState !== WebSocket.OPEN) return;

  // Check for /continue command
  if (text === '/continue' || text === '/cont') {
    input.value = '';
    rpWS.send(JSON.stringify({type: 'continue_message'}));
    rpIsWaiting = true;
    updateRPInputState();
    return;
  }

  let imagePath = null;
  if (pendingRPImage) {
    try {
      imagePath = await uploadRPImage(pendingRPImage);
    } catch (err) {
      alert('Image upload failed: ' + err.message);
      return;
    }
    clearRPImagePreview();
  }

  const clientId = 'rp_' + (++_clientMsgCounter) + '_' + Date.now();
  // ── Author's Note: append to message if non-empty ──
  const noteEl = document.getElementById('rpAuthorNoteInput');
  let noteText = noteEl ? noteEl.value.trim() : '';
  let sendContent = text;
  if (noteText) {
    sendContent = text + "\n\n[Author's Note: " + noteText + ']';
  }
  addRPMessage('user', sendContent, null, null, clientId, imagePath);

  const directedTo = document.getElementById('rpDirectSelect')?.value || null;
  rpWS.send(JSON.stringify({type: 'user_message', content: sendContent, directed_to: directedTo || null, client_msg_id: clientId, image_path: imagePath}));

  input.value = '';
  if (noteEl) noteEl.value = '';
  // Image-only: no LLM wait state
  if (text || !imagePath) {
    rpIsWaiting = true;
  }
  updateRPInputState();
}

function handleRPKey(e) {
  if (e.key === 'Enter' && !e.shiftKey) {
    e.preventDefault();
    sendRPMessage();
  }
}

function updateRPInputState() {
  document.getElementById('rpBtnSend').disabled = rpIsWaiting;
  document.getElementById('rpMsgInput').disabled = rpIsWaiting;
  document.getElementById('rpBtnStop').style.display = rpIsWaiting ? '' : 'none';
  document.getElementById('rpAttachBtn').disabled = rpIsWaiting || !rpActiveSessionId;
  const noteEl = document.getElementById('rpAuthorNoteInput');
  if (noteEl) noteEl.disabled = rpIsWaiting;
  if (!rpIsWaiting) document.getElementById('rpMsgInput').focus();
}

// ── Author's Note toggle + key handlers ──
function toggleSchoolAuthorNote() {
  const bar = document.getElementById('schoolAuthorNoteBar');
  const arrow = document.getElementById('schoolAuthorNoteArrow');
  if (!bar) return;
  const expanded = bar.classList.toggle('expanded');
  arrow.textContent = expanded ? '▾' : '▸';
  if (expanded) {
    const field = document.getElementById('schoolAuthorNoteInput');
    if (field && !field.disabled) field.focus();
  }
}

function toggleRPAuthorNote() {
  const bar = document.getElementById('rpAuthorNoteBar');
  const arrow = document.getElementById('rpAuthorNoteArrow');
  if (!bar) return;
  const expanded = bar.classList.toggle('expanded');
  arrow.textContent = expanded ? '▾' : '▸';
  if (expanded) {
    const field = document.getElementById('rpAuthorNoteInput');
    if (field && !field.disabled) field.focus();
  }
}

function handleAuthorNoteKey(e, mode) {
  if (e.key === 'Enter' && !e.shiftKey) {
    e.preventDefault();
    if (mode === 'school') sendMessage();
    else if (mode === 'rp') sendRPMessage();
  }
}

function stopRPGeneration() {
  if (rpWS && rpWS.readyState === WebSocket.OPEN) {
    rpWS.send(JSON.stringify({ type: 'stop' }));
  }
}

function onRPPersonaChange() {
  const filename = document.getElementById('rpPersonaBar').value || null;
  rpPersonaFilename = filename;
  // Update currentPersona immediately so the next user message label is correct
  if (filename) {
    const p = personasList.find(p => p.filename === filename);
    if (p) currentPersona = p;
  } else {
    currentPersona = null;
  }
  if (rpWS && rpWS.readyState === WebSocket.OPEN) {
    rpWS.send(JSON.stringify({type: 'set_persona', persona_filename: filename}));
  }
}

let rpConsoleSaveTimer = null;

function collectRPConsoleEvents() {
  const entries = document.querySelectorAll('#rpConsoleInner > .console-entry');
  const events = [];
  entries.forEach(entry => {
    try {
      const raw = entry.getAttribute('data-raw') || '';
      const data = JSON.parse(raw);
      if (data && data.event) events.push(data);
    } catch (e) {}
  });
  return events;
}

function queueSaveRPConsoleEvents() {
  if (!rpActiveSessionId || !rpWS || rpWS.readyState !== WebSocket.OPEN) return;
  if (rpConsoleSaveTimer) clearTimeout(rpConsoleSaveTimer);
  rpConsoleSaveTimer = setTimeout(() => {
    const events = collectRPConsoleEvents();
    rpWS.send(JSON.stringify({ type: 'save_console_events', events: events }));
  }, 500);
}

function onRPDirectChange() {
  // Just updates the selected target — no server call needed
}

function rpUpdateSetting(key, value) {
  // update local state immediately for responsive UI
  if (key === 'pov') rpPov = value || null;
  else if (key === 'inner_monologue') rpInnerMonologue = !!value;
  else if (key === 'auto_continue') rpAutoContinue = !!value;
  else if (key === 'response_style') rpResponseStyle = value;
  renderRPSceneBar();
  if (rpWS && rpWS.readyState === WebSocket.OPEN) {
    const update = {type: 'update_settings'};
    update[key] = value;
    rpWS.send(JSON.stringify(update));
  }
}

// ─── School Response Style ──────────────────────
function updateSchoolStyleButton() {
  // Populate and update response style dropdown
  const styleSel = document.getElementById('schoolStyleSelect');
  if (styleSel) {
    if (styleSel.options.length === 0) {
      rpStyleOrder.forEach(st => {
        const opt = document.createElement('option');
        opt.value = st;
        opt.textContent = `${rpStyleLabels[st]} (${rpStyleTokens[st]})`;
        styleSel.appendChild(opt);
      });
    }
    styleSel.value = schoolResponseStyle || 'moderate';
  }
  // Update POV dropdown
  const povSelect = document.getElementById('schoolPovSelect');
  if (povSelect) {
    // Populate options if empty
    if (povSelect.options.length === 0) {
      const autoOpt = document.createElement('option');
      autoOpt.value = '';
      autoOpt.textContent = povLabels[null];
      povSelect.appendChild(autoOpt);
      povOrder.forEach(pv => {
        const opt = document.createElement('option');
        opt.value = pv;
        opt.textContent = povLabels[pv];
        povSelect.appendChild(opt);
      });
    }
    povSelect.value = schoolPov || '';
  }
  // Update inner monologue toggle
  const imBtn = document.getElementById('schoolImToggle');
  if (imBtn) {
    imBtn.textContent = `💭 ${schoolInnerMonologue ? 'ON' : 'OFF'}`;
    imBtn.classList.toggle('active', schoolInnerMonologue);
  }
  // Update auto-continue toggle
  const acBtn = document.getElementById('schoolAcToggle');
  if (acBtn) {
    acBtn.textContent = `🔄 ${schoolAutoContinue ? 'ON' : 'OFF'}`;
    acBtn.classList.toggle('active', schoolAutoContinue);
  }
}

function schoolUpdateSetting(key, value) {
  if (key === 'pov') schoolPov = value || null;
  else if (key === 'inner_monologue') schoolInnerMonologue = !!value;
  else if (key === 'auto_continue') schoolAutoContinue = !!value;
  else if (key === 'response_style') schoolResponseStyle = value;
  updateSchoolStyleButton();
  if (ws && ws.readyState === WebSocket.OPEN) {
    const update = {type: 'update_settings'};
    update[key] = value;
    ws.send(JSON.stringify(update));
  }
}

