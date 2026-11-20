// Character School — cards.js
// Card upload/download/delete/list, character generation modal.

// ─── API ────────────────────────────────────────

// ─── Character Generation ──────────────────────
let genNsfwOn = false;
let genLastCard = null;
let genLastVersion = 2;

function openGenerateModal() {
  resetGenerateForm();
  document.getElementById('genOverlay').classList.add('show');
}

function closeGenerateModal() {
  document.getElementById('genOverlay').classList.remove('show');
}

function toggleGenNsfw() {
  genNsfwOn = !genNsfwOn;
  const toggle = document.getElementById('genNsfwToggle');
  const label = document.getElementById('genNsfwLabel');
  if (genNsfwOn) {
    toggle.classList.add('on');
    label.textContent = 'NSFW';
    label.style.color = 'var(--accent)';
  } else {
    toggle.classList.remove('on');
    label.textContent = 'SFW';
    label.style.color = 'var(--text-dim)';
  }
}

function showGenView(view) {
  // view: 'form', 'loading', 'preview'
  document.getElementById('genForm').style.display = view === 'form' ? '' : 'none';
  document.getElementById('genLoading').style.display = view === 'loading' ? '' : 'none';
  document.getElementById('genError').style.display = 'none';
  document.getElementById('genPreview').classList.toggle('show', view === 'preview');
  document.getElementById('genFooterForm').style.display = view === 'form' ? '' : 'none';
  document.getElementById('genFooterPreview').style.display = view === 'preview' ? '' : 'none';
}

function resetGenerateForm() {
  document.getElementById('genConcept').value = '';
  document.getElementById('genName').value = '';
  document.getElementById('genAge').value = '';
  document.getElementById('genPersonality').value = '';
  document.getElementById('genScenario').value = '';
  document.getElementById('genAppearance').value = '';
  document.getElementById('genVersion').value = '2';
  document.getElementById('genCreator').value = 'Richard';
  genNsfwOn = false;
  document.getElementById('genNsfwToggle').classList.remove('on');
  document.getElementById('genNsfwLabel').textContent = 'SFW';
  document.getElementById('genNsfwLabel').style.color = 'var(--text-dim)';
  genLastCard = null;
  showGenView('form');
}

async function generateCharacter() {
  const concept = document.getElementById('genConcept').value.trim();
  if (!concept) {
    const err = document.getElementById('genError');
    err.textContent = 'Please provide a concept for the character.';
    err.style.display = 'block';
    return;
  }

  const payload = {
    concept,
    name: document.getElementById('genName').value.trim(),
    age: document.getElementById('genAge').value.trim(),
    personality: document.getElementById('genPersonality').value.trim(),
    scenario: document.getElementById('genScenario').value.trim(),
    appearance: document.getElementById('genAppearance').value.trim(),
    version: parseInt(document.getElementById('genVersion').value),
    creator: document.getElementById('genCreator').value.trim(),
    nsfw: genNsfwOn,
  };
  genLastVersion = payload.version;

  showGenView('loading');

  try {
    const res = await fetch('/api/generate-character', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload),
    });
    const data = await res.json();

    // Render console events (request + response/error) in the LLM console
    if (data.console_events) {
      for (const ev of data.console_events) {
        addConsoleEntry(ev);
      }
    }

    if (data.error) {
      const err = document.getElementById('genError');
      err.textContent = data.error;
      err.style.display = 'block';
      showGenView('form');
      return;
    }

    genLastCard = data.card;
    renderGenPreview(data.card, payload.version);
    showGenView('preview');
  } catch (e) {
    const err = document.getElementById('genError');
    err.textContent = 'Failed to generate: ' + e.message;
    err.style.display = 'block';
    showGenView('form');
  }
}

function regenerateCharacter() {
  // Keep form values, just regenerate
  generateCharacter();
}

function renderGenPreview(card, version) {
  const isV2 = version === 2;
  const data = isV2 ? (card.data || card) : card;

  // V2 text fields to show in preview
  const v2Fields = [
    ['name', 'Name', 'input'],
    ['description', 'Description', 'textarea'],
    ['personality', 'Personality', 'textarea'],
    ['scenario', 'Scenario', 'textarea'],
    ['first_mes', 'First Message', 'textarea'],
    ['mes_example', 'Message Examples', 'textarea'],
    ['creator_notes', 'Creator Notes', 'textarea'],
    ['system_prompt', 'System Prompt', 'textarea'],
  ];
  const v1Fields = [
    ['name', 'Name', 'input'],
    ['description', 'Description', 'textarea'],
    ['personality', 'Personality', 'textarea'],
    ['scenario', 'Scenario', 'textarea'],
    ['first_mes', 'First Message', 'textarea'],
    ['mes_example', 'Message Examples', 'textarea'],
  ];
  const fields = isV2 ? v2Fields : v1Fields;

  let html = '';
  for (const [key, label, type] of fields) {
    const val = (data[key] || '').toString();
    if (type === 'input') {
      html += `<div class="gen-preview-field"><label>${label}</label><input type="text" id="genPrev_${key}" value="${escapeHtml(val)}" style="width:100%;background:var(--bg);border:1px solid var(--border);border-radius:6px;color:var(--text);padding:8px 10px;font-size:12px;font-family:inherit" /></div>`;
    } else {
      const rows = Math.max(3, Math.ceil(val.length / 80));
      html += `<div class="gen-preview-field"><label>${label}</label><textarea id="genPrev_${key}" rows="${rows}" style="width:100%;background:var(--bg);border:1px solid var(--border);border-radius:6px;color:var(--text);padding:8px 10px;font-size:12px;font-family:inherit;resize:vertical;min-height:50px">${escapeHtml(val)}</textarea></div>`;
    }
  }

  // Tags
  const tags = Array.isArray(data.tags) ? data.tags.join(', ') : '';
  html += `<div class="gen-preview-field"><label>Tags (comma-separated)</label><input type="text" id="genPrev_tags" value="${escapeHtml(tags)}" style="width:100%;background:var(--bg);border:1px solid var(--border);border-radius:6px;color:var(--text);padding:8px 10px;font-size:12px;font-family:inherit" /></div>`;

  document.getElementById('genPreviewFields').innerHTML = html;
}

function collectGenPreviewCard() {
  const isV2 = genLastVersion === 2;
  const data = isV2 ? (genLastCard.data || genLastCard) : genLastCard;

  const v2Fields = ['name', 'description', 'personality', 'scenario', 'first_mes', 'mes_example', 'creator_notes', 'system_prompt'];
  const v1Fields = ['name', 'description', 'personality', 'scenario', 'first_mes', 'mes_example'];
  const fields = isV2 ? v2Fields : v1Fields;

  for (const key of fields) {
    const el = document.getElementById('genPrev_' + key);
    if (el) data[key] = el.value;
  }

  // Tags
  const tagsEl = document.getElementById('genPrev_tags');
  if (tagsEl) {
    data.tags = tagsEl.value.split(',').map(t => t.trim()).filter(Boolean);
  }

  if (isV2) {
    genLastCard.data = data;
    genLastCard.spec = 'chara_card_v2';
    genLastCard.spec_version = '2.0';
  } else {
    genLastCard = data;
  }

  return genLastCard;
}

async function saveGeneratedCard() {
  if (!genLastCard) return;
  const card = collectGenPreviewCard();

  try {
    const res = await fetch('/api/generate-character/save', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ card }),
    });
    const data = await res.json();

    if (data.error) {
      const err = document.getElementById('genError');
      err.textContent = data.error;
      err.style.display = 'block';
      return;
    }

    // Refresh card list and close
    await loadCardList();
    closeGenerateModal();
    // Select the new card
    if (data.filename) {
      selectCard(data.filename);
    }
  } catch (e) {
    const err = document.getElementById('genError');
    err.textContent = 'Failed to save: ' + e.message;
    err.style.display = 'block';
  }
}

async function uploadCard(event) {
  const file = event.target.files[0];
  if (!file) return;
  const formData = new FormData();
  formData.append('file', file);
  try {
    const resp = await fetch('/api/cards/upload', {method: 'POST', body: formData});
    const result = await resp.json();
    if (resp.ok) {
      alert(`Uploaded (${result.format||'json'}): ${result.name} → ${result.filename}`);
      await loadCardList();
    } else {
      alert('Upload failed: ' + (result.error || 'Unknown error'));
    }
  } catch (e) {
    alert('Upload failed: ' + e.message);
  }
  event.target.value = ''; // reset so same file can be re-uploaded
}

function downloadCardPNG(filename) {
  window.open(`/api/cards/${encodeURIComponent(filename)}/png`, '_blank');
}

function downloadCardJSON(filename) {
  window.open(`/api/cards/${encodeURIComponent(filename)}/json`, '_blank');
}

async function deleteCard(filename, name) {
  if (!confirm(`Delete "${name}"?\n\nThis will permanently remove the character card and its avatar from storage. This cannot be undone.`))
    return;
  try {
    const resp = await fetch('/api/cards/' + encodeURIComponent(filename), {method: 'DELETE'});
    const result = await resp.json();
    if (resp.ok) {
      // Clear current selection if we deleted the active card
      if (currentFilename === filename) {
        currentFilename = null;
        currentCard = null;
        document.getElementById('charName').textContent = 'No character selected';
        document.getElementById('personaSelect').disabled = true;
        document.querySelectorAll('.card-item').forEach(el => el.classList.remove('active'));
      }
      await loadCardList();
    } else {
      alert('Delete failed: ' + (result.error || 'Unknown error'));
    }
  } catch (e) {
    alert('Delete failed: ' + e.message);
  }
}

async function uploadPersona(event) {
  const file = event.target.files[0];
  if (!file) return;
  const formData = new FormData();
  formData.append('file', file);
  try {
    const resp = await fetch('/api/personas/upload', {method: 'POST', body: formData});
    const result = await resp.json();
    if (resp.ok) {
      alert(`Uploaded: ${result.name} → ${result.filename}`);
      await loadPersonaList();
    } else {
      alert('Upload failed: ' + (result.error || 'Unknown error'));
    }
  } catch (e) {
    alert('Upload failed: ' + e.message);
  }
  event.target.value = '';
}

async function loadCardList() {
  const resp = await fetch('/api/cards');
  const cards = await resp.json();
  const list = document.getElementById('cardList');
  list.innerHTML = '';
  cards.forEach(c => {
    const div = document.createElement('div');
    div.className = 'card-item';
    div.onclick = () => selectCard(c.filename);
    const vbadge = c.version ? `<span class="card-version v${c.version}">V${c.version}</span>` : '';
    div.innerHTML = `<div class="name">${escapeHtml(c.name)} ${vbadge}</div><div class="tags">${(c.tags||[]).slice(0,4).map(t=>'<span>'+escapeHtml(t)+'</span>').join('')}</div><div class="card-dl-btns"><button class="card-dl-btn" onclick="event.stopPropagation();downloadCardPNG('${c.filename}')" title="Download as PNG card">🖼️</button><button class="card-dl-btn" onclick="event.stopPropagation();downloadCardJSON('${c.filename}')" title="Download as JSON">{}</button><button class="card-dl-btn danger" onclick="event.stopPropagation();deleteCard('${c.filename}','${escapeHtml(c.name).replace(/'/g,"\\'")}')" title="Delete character">🗑️</button></div>`;
    div.dataset.filename = c.filename;
    list.appendChild(div);
  });
  // Also load personas
  await loadPersonaList();
}

async function loadPersonaList() {
  const resp = await fetch('/api/personas');
  personasList = await resp.json();
  const select = document.getElementById('personaSelect');
  select.innerHTML = '<option value="">None (raw you)</option>';
  personasList.forEach(p => {
    const opt = document.createElement('option');
    opt.value = p.filename;
    opt.textContent = p.name;
    select.appendChild(opt);
  });
  // Also populate persona editor load select
  const loadSelect = document.getElementById('personaLoadSelect');
  if (loadSelect) {
    loadSelect.innerHTML = '<option value="">— Select to edit —</option>';
    personasList.forEach(p => {
      const opt = document.createElement('option');
      opt.value = p.filename;
      opt.textContent = p.name;
      loadSelect.appendChild(opt);
    });
  }
}

async function selectCard(filename) {
  currentFilename = filename;
  document.querySelectorAll('.card-item').forEach(el => {
    el.classList.toggle('active', el.dataset.filename === filename);
  });

  const resp = await fetch('/api/cards/' + filename);
  currentCard = await resp.json();
  const d = currentCard.data || currentCard;
  document.getElementById('charName').textContent = d.name || filename;

  // Enable persona selector + editor (no session yet — just load card data)
  document.getElementById('personaSelect').disabled = false;
  document.getElementById('btnPersonaEditor').disabled = false;
  document.getElementById('btnEditor').disabled = false;

  // Show the "+ New" button so user can create a session
  document.getElementById('btnNewSchoolSession').style.display = '';

  // If there's an active session from a different card, disconnect it
  if (schoolActiveSessionId && ws && ws.readyState === WebSocket.OPEN) {
    if (schoolConsoleEvents.length > 0) {
      ws.send(JSON.stringify({type: 'save_console_events', events: schoolConsoleEvents}));
    }
    ws.close();
    ws = null;
  }
}

function connectWS(filename, personaFilename, resumeSessionId) {
  // Save console events from previous session before switching
  if (schoolActiveSessionId && ws && ws.readyState === WebSocket.OPEN && schoolConsoleEvents.length > 0) {
    ws.send(JSON.stringify({type: 'save_console_events', events: schoolConsoleEvents}));
  }
  if (ws) ws.close();
  const proto = location.protocol === 'https:' ? 'wss' : 'ws';
  ws = new WebSocket(proto + '://' + location.host + '/ws/chat');

  ws.onopen = () => {
    document.getElementById('status').textContent = 'Connected';
    document.getElementById('status').classList.add('connected');
    if (resumeSessionId) {
      ws.send(JSON.stringify({type: 'resume', session_id: resumeSessionId}));
    } else {
      ws.send(JSON.stringify({type: 'start', card_filename: filename, persona_filename: personaFilename}));
    }
    isWaiting = true;
    updateInputState();
  };

  ws.onmessage = (event) => {
    const data = JSON.parse(event.data);
    if (data.type === 'character_message') {
      clearStreamBubble();
      if (data.regenerated) {
        // Update existing message (swipe) instead of adding new
        const target = schoolMessages.find(m => m.messageId === data.message_id);
        if (target) {
          target.swipes = target.swipes || [];
          if (target.swipes.length === 0) target.swipes.push(target.content); // original becomes swipe 0
          target.swipes.push(data.content);
          target.content = data.content;
          target.truncated = !!data.truncated;
          target.activeSwipe = (data.swipe_index !== undefined) ? data.swipe_index : target.swipes.length - 1;
          renderVirtualMessages('chatMessages', schoolMessages, schoolVisibleCount.value);
        }
      } else {
        addMessage('character', data.content, data.analysis, data.is_first_mes, data.message_id, null, data.image_path || null, data.image_prompt || null, data.seed, data.truncated);
      }
      isWaiting = false;
      updateInputState();
    } else if (data.type === 'swipe_switched') {
      const target = schoolMessages.find(m => m.messageId === data.message_id);
      if (target) {
        target.content = data.content;
        target.activeSwipe = data.active_swipe;
        renderVirtualMessages('chatMessages', schoolMessages, schoolVisibleCount.value);
      }
    } else if (data.type === 'user_message_stored') {
      // Server confirms the user message was stored and gives us the DB row id.
      // Match by client_msg_id to tag the right entry in the array.
      if (data.message_id != null) {
        const target = data.client_msg_id
          ? schoolMessages.find(m => m.clientId === data.client_msg_id)
          : null;
        if (target) {
          target.messageId = data.message_id;
        } else if (_lastUserMsgDiv) {
          _lastUserMsgDiv.dataset.messageId = data.message_id;
        }
      }
    } else if (data.type === 'session_started') {
      // New session created
      schoolActiveSessionId = data.session_id;
      schoolSystemPrompt = data.system_prompt || '';
      if (data.stack_config) {
        schoolStackConfig = data.stack_config;
        schoolStackConfig._systemPromptText = schoolSystemPrompt;
      }
      schoolLorebooks = data.lorebooks || [];
      schoolResponseStyle = data.response_style || 'moderate';
      schoolPov = data.pov || null;
      schoolInnerMonologue = data.inner_monologue || false;
      schoolAutoContinue = data.auto_continue !== undefined ? data.auto_continue : true;
      updateSchoolStyleButton();
      // Clear virtual scroll state
      clearSchoolChat();
      // Clear console for new session
      consoleEventCount = 0;
      schoolConsoleEvents = [];
      document.getElementById('consoleCount').textContent = '0';
      document.getElementById('consoleInner').innerHTML = '<div class="console-empty">LLM inference events will appear here.</div>';
      document.getElementById('btnReset').disabled = false;
      document.getElementById('btnReport').disabled = false;
      document.getElementById('btnEditor').disabled = false;
      document.getElementById('btnSchoolStack').disabled = false;
      document.getElementById('btnSchoolLore').disabled = false;
      document.getElementById('btnSchoolGenScene').disabled = false;
      // Show style toggle
      document.getElementById('schoolStyleToggleWrap').style.display = '';
      // Restore POV + inner monologue from session
      schoolPov = data.pov || null;
      schoolInnerMonologue = data.inner_monologue || false;
      schoolAutoContinue = data.auto_continue !== undefined ? data.auto_continue : true;
      updateSchoolStyleButton();
      // Apply per-session bg or fall back to global
      applyBgImage(data.bg_image || localStorage.getItem('cs_bg_image') || '', parseFloat(localStorage.getItem('cs_bg_overlay')) ?? 0.85, false);
      // Enable input — first_mes has no analysis, arrives instantly
      isWaiting = false;
      updateInputState();
      if (data.persona) {
        // Show persona badge
        const p = personasList.find(p => p.filename === data.persona);
        if (p) {
          currentPersona = p;
          currentPersonaFilename = data.persona;
          document.getElementById('charName').innerHTML = escapeHtml(currentCard?.data?.name || '') + `<span class="persona-badge">vs ${escapeHtml(p.name)}</span>`;
        }
      } else {
        currentPersona = null;
        currentPersonaFilename = null;
      }
      // Refresh sessions sidebar
      loadSchoolSessions();
      // Ensure sessions list is visible and scroll to new session
      if (schoolSessionsCollapsed) toggleSchoolSessions();
      setTimeout(() => {
        const item = document.querySelector('.school-session-item.active');
        if (item) item.scrollIntoView({ block: 'nearest' });
      }, 50);
    } else if (data.type === 'session_resumed') {
      // Existing session loaded
      schoolActiveSessionId = data.session_id;
      schoolSystemPrompt = data.system_prompt || '';
      if (data.stack_config) {
        schoolStackConfig = data.stack_config;
        schoolStackConfig._systemPromptText = schoolSystemPrompt;
      }
      schoolLorebooks = data.lorebooks || [];
      schoolResponseStyle = data.response_style || 'moderate';
      schoolPov = data.pov || null;
      schoolInnerMonologue = data.inner_monologue || false;
      schoolAutoContinue = data.auto_continue !== undefined ? data.auto_continue : true;
      updateSchoolStyleButton();
      consoleEventCount = 0;
      schoolConsoleEvents = [];
      document.getElementById('consoleCount').textContent = '0';
      document.getElementById('consoleInner').innerHTML = '<div class="console-empty">LLM inference events will appear here.</div>';
      document.getElementById('analysisPanel').innerHTML = '<div class="analysis-empty">Click "Analyze Character" to run analysis.</div>';
      // Clear virtual scroll state and populate from server messages
      clearSchoolChat();
      if (data.messages) {
        data.messages.forEach(m => {
          if (m.role === 'user') {
            schoolMessages.push({ role: 'user', content: m.content, analysis: null, isFirst: m.is_first_mes, messageId: m.id, clientId: null, imagePath: m.image_path || null, imagePrompt: m.image_prompt || null, seed: m.seed, swipes: m.swipes || null, activeSwipe: m.active_swipe || 0, personaName: currentPersona?.name || null });
          } else if (m.role === 'assistant') {
            schoolMessages.push({ role: 'character', content: m.content, analysis: m.analysis, isFirst: m.is_first_mes, messageId: m.id, clientId: null, imagePath: m.image_path || null, imagePrompt: m.image_prompt || null, seed: m.seed, swipes: m.swipes || null, activeSwipe: m.active_swipe || 0 });
          } else if (m.role === 'system') {
            schoolMessages.push({ role: 'system', content: m.content, analysis: null, isFirst: false, messageId: m.id, clientId: null, imagePath: m.image_path || null, imagePrompt: m.image_prompt || null, seed: m.seed, swipes: m.swipes || null, activeSwipe: m.active_swipe || 0 });
          }
        });
      }
      schoolVisibleCount.value = Math.min(VIRTUAL_SCROLL_BATCH, schoolMessages.length);
      renderVirtualMessages('chatMessages', schoolMessages, schoolVisibleCount.value);
      initVirtualScroll('chatMessages', schoolMessages, schoolVisibleCount, schoolObserver);
      // Restore console events
      if (data.console_events && data.console_events.length > 0) {
        schoolConsoleEvents = data.console_events;
        const consoleInner = document.getElementById('consoleInner');
        data.console_events.forEach(ev => addConsoleEntry(ev));
        trimConsoleEntries(consoleInner);
      }
      // Enable controls
      document.getElementById('btnReset').disabled = false;
      document.getElementById('btnReport').disabled = false;
      document.getElementById('btnEditor').disabled = false;
      document.getElementById('btnSchoolStack').disabled = false;
      document.getElementById('btnSchoolLore').disabled = false;
      document.getElementById('btnSchoolGenScene').disabled = false;
      // Show style toggle + restore response style
      document.getElementById('schoolStyleToggleWrap').style.display = '';
      if (data.response_style) {
        schoolResponseStyle = data.response_style;
      }
      schoolPov = data.pov || null;
      schoolInnerMonologue = data.inner_monologue || false;
      schoolAutoContinue = data.auto_continue !== undefined ? data.auto_continue : true;
      updateSchoolStyleButton();
      isWaiting = false;
      updateInputState();
      // Update char name display
      if (data.persona) {
        const p = personasList.find(p => p.filename === data.persona);
        if (p) {
          currentPersona = p;
          currentPersonaFilename = data.persona;
          document.getElementById('charName').innerHTML = escapeHtml(currentCard?.data?.name || '') + `<span class="persona-badge">vs ${escapeHtml(p.name)}</span>`;
        }
      } else {
        currentPersona = null;
        currentPersonaFilename = null;
        document.getElementById('charName').textContent = currentCard?.data?.name || currentFilename;
      }
      // Highlight in sidebar
      document.querySelectorAll('.school-session-item').forEach(el => {
        el.classList.toggle('active', parseInt(el.dataset.sessionId) === schoolActiveSessionId);
      });
      // Apply per-session bg or fall back to global
      applyBgImage(data.bg_image || localStorage.getItem('cs_bg_image') || '', parseFloat(localStorage.getItem('cs_bg_overlay')) ?? 0.85, false);
    } else if (data.type === 'bg_image_updated') {
      // Server confirmed per-session bg update — no action needed
    } else if (data.type === 'stack_updated') {
      // Stack config updated (e.g., auto-summary ran)
      if (data.stack_config) {
        schoolStackConfig = data.stack_config;
        schoolStackConfig._systemPromptText = schoolSystemPrompt;
        if (activeStackMode === 'school') activeStackConfig = schoolStackConfig;
      }
    } else if (data.type === 'block_summarized') {
      if (data.stack_config) {
        schoolStackConfig = data.stack_config;
        schoolStackConfig._systemPromptText = schoolSystemPrompt;
        if (activeStackMode === 'school') activeStackConfig = schoolStackConfig;
      }
      summarizingBlockIdx = null;
      const overlay = document.getElementById('stackOverlay');
      if (overlay && overlay.classList.contains('show')) {
        renderStackBlocks();
      }
      if (data.text) {
        const btn = document.querySelector(`.stack-block[data-idx="${data.block_index}"] .block-summarize-btn`);
        if (btn) { btn.classList.add('has-text'); }
      }
    } else if (data.type === 'chunk_added') {
      // Legacy: auto-summary created a new chunk (school mode) — kept for old sessions
      if (data.stack_config) {
        schoolStackConfig = data.stack_config;
        if (schoolStackConfig) schoolStackConfig._systemPromptText = schoolSystemPrompt;
        if (activeStackMode === 'school') activeStackConfig = schoolStackConfig;
      }
      const overlay = document.getElementById('stackOverlay');
      if (overlay && overlay.classList.contains('show')) {
        renderStackBlocks();
      }
    } else if (data.type === 'block_summary_updated') {
      // Auto-summary grew the anchor block (school mode)
      if (data.stack_config) {
        schoolStackConfig = data.stack_config;
        if (schoolStackConfig) schoolStackConfig._systemPromptText = schoolSystemPrompt;
        if (activeStackMode === 'school') activeStackConfig = schoolStackConfig;
      }
      summarizingBlockIdx = null;
      const overlay = document.getElementById('stackOverlay');
      if (overlay && overlay.classList.contains('show')) {
        renderStackBlocks();
      }
      if (data.summary) {
        const btn = document.querySelector(`.stack-block[data-idx="${data.block_index}"] .block-summarize-btn`);
        if (btn) { btn.classList.add('has-text'); }
      }
    } else if (data.type === 'persona_updated') {
      // Persona changed on current session (no new session created)
      if (data.system_prompt !== undefined) {
        schoolSystemPrompt = data.system_prompt;
        if (schoolStackConfig) schoolStackConfig._systemPromptText = schoolSystemPrompt;
      }
      if (data.persona) {
        const p = personasList.find(p => p.filename === data.persona);
        if (p) {
          currentPersona = p;
          currentPersonaFilename = data.persona;
          document.getElementById('charName').innerHTML = escapeHtml(currentCard?.data?.name || '') + `<span class="persona-badge">vs ${escapeHtml(p.name)}</span>`;
        }
      } else {
        currentPersona = null;
        currentPersonaFilename = null;
        document.getElementById('charName').textContent = currentCard?.data?.name || currentFilename;
      }
      loadSchoolSessions();
    } else if (data.type === 'analysis_result') {
      // Manual analysis result from "Analyze Character" button
      if (data.analysis) {
        updateAnalysis(data.analysis);
      } else {
        document.getElementById('analysisPanel').innerHTML = '<div class="analysis-empty">Analysis returned no results.</div>';
      }
    } else if (data.type === 'reset_complete') {
      schoolActiveSessionId = data.session_id;
      if (data.system_prompt !== undefined) {
        schoolSystemPrompt = data.system_prompt;
      }
      if (data.stack_config) {
        schoolStackConfig = data.stack_config;
        schoolStackConfig._systemPromptText = schoolSystemPrompt;
      }
      consoleEventCount = 0;
      schoolConsoleEvents = [];
      document.getElementById('consoleCount').textContent = '0';
      document.getElementById('consoleInner').innerHTML = '<div class="console-empty">LLM inference events will appear here.</div>';
      document.getElementById('chatMessages').innerHTML = '';
      document.getElementById('analysisPanel').innerHTML = '<div class="analysis-empty">Click "Analyze Character" to run analysis.</div>';
      clearSchoolChat();
      isWaiting = false;
      updateInputState();
      loadSchoolSessions();
    } else if (data.type === 'message_deleted') {
      // If server says delete failed (success: false), the optimistic DOM removal was
      // wrong — reload session from DB to restore the message. If success: true, the
      // optimistic splice was correct and no action is needed.
      if (data.success === false && schoolActiveSessionId && ws && ws.readyState === 1) {
        ws.send(JSON.stringify({type: 'resume', session_id: schoolActiveSessionId}));
      }
    } else if (data.type === 'report') {
      showReport(data.content);
    } else if (data.type === 'error') {
      clearStreamBubble();
      addMessage('system', 'Error: ' + data.message, null, false, null, null, null);
      isWaiting = false;
      hideSchoolTyping();
      updateInputState();
    } else if (data.type === 'console_event') {
      addConsoleEntry(data);
      schoolConsoleEvents.push(data);
    } else if (data.type === 'character_delta') {
      handleStreamDelta('school', data);
    } else if (data.type === 'character_typing') {
      showSchoolTyping('character', data.character_name);
    } else if (data.type === 'character_typing_stopped') {
      // Character done — analysis is now manual, just hide typing
      hideSchoolTyping();
    } else if (data.type === 'analysis_typing') {
      showSchoolTyping('analyzer');
    } else if (data.type === 'analysis_typing_stopped') {
      hideSchoolTyping();
    } else if (data.type === 'generation_stopped') {
      clearStreamBubble();
      isWaiting = false;
      hideSchoolTyping();
      updateInputState();
      addMessage('system', '⏹ Generation stopped', null, false, null, null, null);
    } else if (data.type === 'response_style_updated') {
      schoolResponseStyle = data.style || 'moderate';
      schoolPov = data.pov || null;
      schoolInnerMonologue = data.inner_monologue || false;
      schoolAutoContinue = data.auto_continue !== undefined ? data.auto_continue : true;
      if (data.system_prompt !== undefined) {
        schoolSystemPrompt = data.system_prompt;
        if (schoolStackConfig) schoolStackConfig._systemPromptText = schoolSystemPrompt;
      }
      updateSchoolStyleButton();
    } else if (data.type === 'settings_updated') {
      schoolPov = data.pov || null;
      schoolInnerMonologue = data.inner_monologue || false;
      schoolAutoContinue = data.auto_continue !== undefined ? data.auto_continue : true;
      if (data.response_style) schoolResponseStyle = data.response_style;
      if (data.system_prompt !== undefined) {
        schoolSystemPrompt = data.system_prompt;
        if (schoolStackConfig) schoolStackConfig._systemPromptText = schoolSystemPrompt;
      }
      updateSchoolStyleButton();
    }
  };

  ws.onclose = () => {
    document.getElementById('status').textContent = 'Disconnected';
    document.getElementById('status').classList.remove('connected');
  };

  ws.onerror = (e) => {
    addMessage('system', 'Connection error', null, false, null, null, null);
  };
}

