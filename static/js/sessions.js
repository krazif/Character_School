// Character School — sessions.js
// RP console, RP/School session lists, add/remove character mid-session.

// ─── RP Console ─────────────────────────────────
function addRPConsoleEntry(data) {
  const inner = document.getElementById('rpConsoleInner');
  const empty = inner.querySelector('.console-empty');
  if (empty) empty.remove();

  rpConsoleEvents++;
  document.getElementById('rpConsoleCount').textContent = rpConsoleEvents;

  const isReq = data.event === 'request';
  const isResp = data.event === 'response';
  const isErr = data.event === 'error';
  const isKwargs = data.event === 'request_kwargs';

  // Update token estimate in console title bar
  if (isReq && data.token_estimate !== undefined) {
    const el = document.getElementById('rpConsoleTokEst');
    if (el) el.textContent = '~' + data.token_estimate + ' tok';
  }
  if (isResp && data.usage && data.usage.prompt_tokens !== undefined) {
    const el = document.getElementById('rpConsoleTokEst');
    if (el) el.textContent = data.usage.prompt_tokens + ' tok';
  }
  const tagClass = isReq ? 'console-tag-req' : isResp ? 'console-tag-resp' : isErr ? 'console-tag-err' : 'console-tag-req';
  const tagLabel = isReq ? '→ REQ' : isResp ? '← RESP' : isErr ? '✕ ERR' : '→ KWARGS';
  const llmClass = data.llm === 'character' ? 'console-llm-char' : 'console-llm-analysis';
  const llmLabel = data.llm === 'character' ? 'CHARACTER' : 'ANALYSIS';

  let html = '';
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
    // Block markers for RP stack visualization
    const blockMarkers = data.block_markers || [];
    const blockColors = { system: '#58a6ff', head: '#f0883e', summary: '#bc8cff', custom: '#3fb950', tail: '#f85149' };
    data.messages.forEach((m, i) => {
      // Insert block separator if this message starts a new block
      if (blockMarkers.length > 0) {
        const bm = blockMarkers.find(b => b.start === i);
        if (bm) {
          const color = blockColors[bm.block] || 'var(--text-dim)';
          html += `<div class="console-block-sep" style="color:${color};border:1px solid ${color};margin-top:8px">▼ ${escapeHtml(bm.label)}</div>`;
        }
      }
      const role = m.role || '?';
      let content = m.content || '';
      html += `<div class="msg-content ${role}"><span class="msg-role">${escapeHtml(role)}</span>: ${escapeHtml(content)}</div>`;
    });
    html += `</div>`;
  } else if (isResp && data.content) {
    html += `<div class="msg-content assistant">${escapeHtml(data.content)}</div>`;
  } else if (isErr) {
    html += `<div class="msg-content" style="border-left-color:var(--red);color:var(--red)">${escapeHtml(data.message || data.content || '')}</div>`;
  } else if (isKwargs && data.kwargs) {
    html += `<pre style="margin:4px 0;font-size:11px;color:var(--text-dim);white-space:pre-wrap;word-break:break-all">${escapeHtml(JSON.stringify(data.kwargs, null, 2))}</pre>`;
  }

  if (isResp && data.usage) {
    const u = data.usage;
    html += `<span class="usage">📊 tokens: prompt=${u.prompt_tokens || '?'} completion=${u.completion_tokens || '?'} total=${u.total_tokens || '?'}</span>`;
    if (data.finish_reason) html += ` <span class="usage">finish: ${escapeHtml(data.finish_reason)}</span>`;
    html += `<br>`;
  }

  const entryDiv = document.createElement('div');
  entryDiv.className = 'console-entry';
  entryDiv.dataset.raw = JSON.stringify(data);
  entryDiv.innerHTML = html;
  inner.appendChild(entryDiv);
  trimConsoleEntries(inner);

  if (consoleOpen) {
    inner.scrollTop = inner.scrollHeight;
  }
}

// ─── RP Sessions List ───────────────────────────
async function loadRPSessions() {
  try {
    const resp = await fetch('/api/rp/sessions');
    const sessions = await resp.json();
    const list = document.getElementById('rpSessionsList');
    if (sessions.length === 0) {
      list.innerHTML = '<div class="loading" style="padding:20px;text-align:center;color:var(--text-dim)">No sessions yet. Click "+ New" to start.</div>';
      return;
    }
    list.innerHTML = sessions.map(s => {
      const active = s.id === rpActiveSessionId ? ' active' : '';
      const date = s.updated_at ? new Date(s.updated_at + 'Z').toLocaleDateString() : '';
      const style = rpStyleLabels[s.response_style] || 'Moderate';
      return `<div class="rp-session-item${active}" data-session-id="${s.id}" onclick="resumeRPSession(${s.id})">
        <span class="del-btn" onclick="event.stopPropagation(); deleteRPSession(${s.id})">✕</span>
        <span class="fork-btn" onclick="event.stopPropagation(); forkRPSession(${s.id})" title="Fork this session">🧬</span>
        <div class="title">${escapeHtml(s.title || 'Untitled')}</div>
        <div class="meta">${style} · ${date}</div>
      </div>`;
    }).join('');
  } catch (e) {
    console.error('Failed to load RP sessions:', e);
  }
}

function resumeRPSession(sessionId) {
  if (rpWS) rpWS.close();
  connectRPWS(null, sessionId);
}

async function deleteRPSession(sessionId) {
  if (!confirm('Delete this RP session?')) return;
  try {
    await fetch('/api/rp/sessions/' + sessionId, {method: 'DELETE'});
    if (sessionId === rpActiveSessionId) {
      // Clear active session
      rpActiveSessionId = null;
      rpCharacters = [];
      document.getElementById('rpChatMessages').innerHTML = '<div class="analysis-empty" id="rpChatEmpty">Start a new RP session to begin.</div>';
      document.getElementById('rpInputArea').style.display = 'none';
      document.getElementById('rpAuthorNoteBar').style.display = 'none';
      document.getElementById('rpSceneBar').innerHTML = '<span style="color:var(--text-dim);font-size:13px">No active session. Click "+ New" to start a roleplay.</span>';
    }
    loadRPSessions();
  } catch (e) {
    alert('Failed to delete: ' + e.message);
  }
}

async function forkRPSession(sessionId) {
  try {
    const resp = await fetch('/api/rp/sessions/' + sessionId + '/fork', {method: 'POST'});
    const result = await resp.json();
    if (result.error) { alert(result.error); return; }
    // Reload session list to show the new fork
    await loadRPSessions();
    // Optionally auto-resume the forked session
    if (result.new_session_id) {
      resumeRPSession(result.new_session_id);
    }
  } catch (e) {
    alert('Failed to fork: ' + e.message);
  }
}

// ─── School Sessions List ───────────
let schoolSessionsCollapsed = false;

function toggleSchoolSessions() {
  schoolSessionsCollapsed = !schoolSessionsCollapsed;
  const list = document.getElementById('schoolSessionsList');
  const chevron = document.getElementById('schoolSessionsChevron');
  if (schoolSessionsCollapsed) {
    list.style.display = 'none';
    chevron.textContent = '▶';
  } else {
    list.style.display = '';
    chevron.textContent = '▼';
  }
}

async function loadSchoolSessions() {
  try {
    const resp = await fetch('/api/school/sessions');
    const data = await resp.json();
    const sessions = data.sessions || [];
    const list = document.getElementById('schoolSessionsList');
    if (sessions.length === 0) {
      list.innerHTML = '<div style="padding:10px;text-align:center;color:var(--text-dim);font-size:11px">No saved sessions yet.</div>';
      return;
    }
    list.innerHTML = sessions.map(s => {
      const active = s.id === schoolActiveSessionId ? ' active' : '';
      const date = s.updated_at ? new Date(s.updated_at + 'Z').toLocaleDateString() : '';
      const personaTag = s.persona_filename ? ' · vs persona' : '';
      const styleTag = s.response_style ? ' · ' + (rpStyleLabels[s.response_style] || 'Moderate') : '';
      return `<div class="school-session-item${active}" data-session-id="${s.id}" onclick="resumeSchoolSession(${s.id})">
        <span class="del-btn" onclick="event.stopPropagation(); deleteSchoolSession(${s.id})">✕</span>
        <span class="move-btn" onclick="event.stopPropagation(); moveSchoolToRP(${s.id})" title="Move to RP">→</span>
        <span class="fork-btn" onclick="event.stopPropagation(); forkSchoolSession(${s.id})" title="Fork this session">🧬</span>
        <div class="title">${escapeHtml(s.title || 'Untitled')}</div>
        <div class="meta">${date}${personaTag}${styleTag}</div>
      </div>`;
    }).join('');
  } catch (e) {
    console.error('Failed to load school sessions:', e);
  }
}

async function resumeSchoolSession(sessionId) {
  // Fetch session metadata to load card
  try {
    const resp = await fetch('/api/school/sessions/' + sessionId);
    const data = await resp.json();
    if (data.error) { alert(data.error); return; }
    const sess = data.session;
    // Load card
    currentFilename = sess.card_filename;
    const cardResp = await fetch('/api/cards/' + sess.card_filename);
    currentCard = await cardResp.json();
    const d = currentCard.data || currentCard;
    document.getElementById('charName').textContent = d.name || sess.card_filename;
    // Set persona
    if (sess.persona_filename) {
      currentPersonaFilename = sess.persona_filename;
      try {
        const pResp = await fetch('/api/personas/' + sess.persona_filename);
        currentPersona = await pResp.json();
      } catch (e) {
        currentPersona = null;
      }
      // Update persona dropdown
      const sel = document.getElementById('personaSelect');
      if (sel) sel.value = sess.persona_filename;
    } else {
      currentPersonaFilename = null;
      currentPersona = null;
      const sel = document.getElementById('personaSelect');
      if (sel) sel.value = '';
    }
    // Enable controls
    document.getElementById('personaSelect').disabled = false;
    document.getElementById('btnPersonaEditor').disabled = false;
    // Connect WS in resume mode
    connectWS(null, null, sessionId);
    // Highlight active in sidebar
    document.querySelectorAll('.card-item').forEach(el => {
      el.classList.toggle('active', el.dataset.filename === sess.card_filename);
    });
  } catch (e) {
    alert('Failed to resume session: ' + e.message);
  }
}

async function deleteSchoolSession(sessionId) {
  if (!confirm('Delete this session? This cannot be undone.')) return;
  try {
    await fetch('/api/school/sessions/' + sessionId, {method: 'DELETE'});
    if (sessionId === schoolActiveSessionId) {
      // Clear current session
      schoolActiveSessionId = null;
      schoolStackConfig = null;
      document.getElementById('chatMessages').innerHTML = '<div class="analysis-empty" id="chatEmpty">Select a character card to begin testing.</div>';
      document.getElementById('analysisPanel').innerHTML = '<div class="analysis-empty">Click "Analyze Character" to run analysis.</div>';
      document.getElementById('btnReset').disabled = true;
      document.getElementById('btnReport').disabled = true;
      document.getElementById('btnEditor').disabled = true;
      document.getElementById('btnSchoolStack').disabled = true;
      document.getElementById('btnSchoolLore').disabled = true;
      document.getElementById('btnSchoolGenScene').disabled = true;
      if (ws) ws.close();
    }
    loadSchoolSessions();
  } catch (e) {
    alert('Failed to delete session: ' + e.message);
  }
}

async function forkSchoolSession(sessionId) {
  try {
    const resp = await fetch('/api/school/sessions/' + sessionId + '/fork', {method: 'POST'});
    const result = await resp.json();
    if (result.error) { alert(result.error); return; }
    await loadSchoolSessions();
    if (result.new_session_id) {
      resumeSchoolSession(result.new_session_id);
    }
  } catch (e) {
    alert('Failed to fork: ' + e.message);
  }
}

async function moveSchoolToRP(sessionId) {
  if (!confirm('Move this school session to RP mode? This creates a new RP session with the same character, persona, and message history. The school session is NOT deleted.')) return;
  try {
    const resp = await fetch('/api/school/sessions/' + sessionId + '/move-to-rp', {method: 'POST'});
    const result = await resp.json();
    if (result.error) { alert(result.error); return; }
    await loadRPSessions();
    // Switch to RP tab and resume the new session
    switchMode('rp');
    resumeRPSession(result.rp_session_id);
  } catch (e) {
    alert('Failed to move: ' + e.message);
  }
}

// ─── Add/Remove Character mid-session ────────────────
async function openRPAddCharPicker() {
  // Load card list if not cached
  if (rpCardsList.length === 0) {
    const resp = await fetch('/api/cards');
    rpCardsList = await resp.json();
  }
  const existing = rpCharacters.map(c => c.filename);
  const available = rpCardsList.filter(c => !existing.includes(c.filename));
  if (available.length === 0) {
    alert('No more characters available to add.');
    return;
  }
  // Build a lightweight dropdown/picker
  const picker = document.createElement('div');
  picker.className = 'rp-add-char-dropdown';
  picker.innerHTML = available.map(c => {
    const tags = (c.tags || []).slice(0, 3).map(t => `<span class="tags">${escapeHtml(t)}</span>`).join(' ');
    return `<div class="rp-add-char-item" onclick="addRPChar('${escapeHtml(c.filename)}')">
      <span class="name">${escapeHtml(c.name)}</span>
      <span class="tags">${tags}</span>
    </div>`;
  }).join('');
  // Position near the + Add button
  const addBtn = document.querySelector('.char-chip-add');
  if (!addBtn) return;
  const rect = addBtn.getBoundingClientRect();
  picker.style.position = 'fixed';
  picker.style.top = (rect.bottom + 4) + 'px';
  picker.style.left = rect.left + 'px';
  picker.id = 'rpAddCharDropdown';
  // Close on outside click
  picker.onclick = (e) => e.stopPropagation();
  document.body.appendChild(picker);
  setTimeout(() => {
    document.addEventListener('click', closeRPAddCharPicker, {once: true});
  }, 0);
}

function closeRPAddCharPicker() {
  const dd = document.getElementById('rpAddCharDropdown');
  if (dd) dd.remove();
}

function addRPChar(filename) {
  closeRPAddCharPicker();
  if (rpWS && rpWS.readyState === WebSocket.OPEN) {
    rpWS.send(JSON.stringify({type: 'add_character', filename}));
  }
}

function removeRPChar(filename) {
  if (!confirm('Remove this character from the session? Their existing messages will remain.')) return;
  if (rpWS && rpWS.readyState === WebSocket.OPEN) {
    rpWS.send(JSON.stringify({type: 'remove_character', filename}));
  }
}
