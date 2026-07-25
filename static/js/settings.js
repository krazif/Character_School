// Character School — settings.js
// Settings modal, endpoint presets, ComfyUI workflow presets.

// ─── Settings Modal ──────────────────────────────────────────────
async function openSettings() {
  try {
    const res = await fetch('/api/config');
    const cfg = await res.json();
    document.getElementById('cfgServerHost').value = cfg.server.host || '0.0.0.0';
    document.getElementById('cfgServerPort').value = cfg.server.port || 7862;
    document.getElementById('cfgChatBaseUrl').value = cfg.chat.base_url || '';
    document.getElementById('cfgChatApiKey').value = cfg.chat.api_key || '';
    document.getElementById('cfgChatModel').value = cfg.chat.model || '';
    document.getElementById('cfgChatTemp').value = cfg.chat.temperature ?? 0.8;
    document.getElementById('cfgChatMaxTok').value = cfg.chat.max_tokens ?? 2000;
    document.getElementById('cfgChatTopP').value = cfg.chat.top_p != null ? cfg.chat.top_p : '';
    document.getElementById('cfgChatTopK').value = cfg.chat.top_k != null ? cfg.chat.top_k : '';
    document.getElementById('cfgChatEnableThinking').checked = cfg.chat.enable_thinking ?? false;
    document.getElementById('cfgAnalysisBaseUrl').value = cfg.analysis.base_url || '';
    document.getElementById('cfgAnalysisApiKey').value = cfg.analysis.api_key || '';
    document.getElementById('cfgAnalysisModel').value = cfg.analysis.model || '';
    document.getElementById('cfgAnalysisTemp').value = cfg.analysis.temperature ?? 0.1;
    document.getElementById('cfgAnalysisMaxTok').value = cfg.analysis.max_tokens ?? 1500;
    document.getElementById('cfgAnalysisTopP').value = cfg.analysis.top_p != null ? cfg.analysis.top_p : '';
    document.getElementById('cfgAnalysisTopK').value = cfg.analysis.top_k != null ? cfg.analysis.top_k : '';
    document.getElementById('cfgSummaryBaseUrl').value = cfg.summary?.base_url || '';
    document.getElementById('cfgSummaryApiKey').value = cfg.summary?.api_key || '';
    document.getElementById('cfgSummaryModel').value = cfg.summary?.model || '';
    document.getElementById('cfgSummaryTemp').value = cfg.summary?.temperature ?? 0.3;
    document.getElementById('cfgSummaryMaxTok').value = cfg.summary?.max_tokens ?? 1000;
    document.getElementById('cfgSummaryTopP').value = cfg.summary?.top_p != null ? cfg.summary.top_p : '';
    document.getElementById('cfgSummaryTopK').value = cfg.summary?.top_k != null ? cfg.summary.top_k : '';
    document.getElementById('cfgCharactersDir').value = cfg.paths.characters_dir || '';
    document.getElementById('cfgPersonasDir').value = cfg.paths.personas_dir || '';
    // Image generation settings
    const ig = cfg.imagegen || {};
    document.getElementById('cfgImagegenEnabled').checked = ig.enabled || false;
    document.getElementById('cfgImagegenBaseUrl').value = ig.base_url || 'http://127.0.0.1:8188';
    document.getElementById('cfgImagegenNegative').value = ig.negative_prompt || '';
    document.getElementById('cfgImagegenWidth').value = ig.width || 512;
    document.getElementById('cfgImagegenHeight').value = ig.height || 768;
    document.getElementById('cfgImagegenSteps').value = ig.steps || 20;
    document.getElementById('cfgImagegenCfgScale').value = ig.cfg_scale || 7.0;
    document.getElementById('cfgImagegenSampler').value = ig.sampler || 'euler';
    document.getElementById('cfgImagegenScheduler').value = ig.scheduler || 'normal';
    document.getElementById('cfgImagegenSeed').value = ig.seed ?? 0;
    document.getElementById('cfgImagegenRandomSeed').checked = ig.random_seed !== false;
    document.getElementById('cfgImagegenWorkflow').value = ig.workflow ? JSON.stringify(ig.workflow, null, 2) : '';
    // Auto image generation settings
    const iga = cfg.imagegen_auto || {};
    document.getElementById('cfgImgAutoEnabled').checked = iga.enabled || false;
    document.getElementById('cfgImgAutoBaseUrl').value = iga.base_url || 'https://openrouter.ai/api/v1';
    document.getElementById('cfgImgAutoApiKey').value = iga.api_key || '';
    document.getElementById('cfgImgAutoModel').value = iga.model || 'deepseek/deepseek-v4-flash';
    document.getElementById('cfgImgAutoTemp').value = iga.temperature ?? 0.5;
    document.getElementById('cfgImgAutoMaxTok').value = iga.max_tokens || 500;
    document.getElementById('cfgImgAutoInterval').value = iga.interval || 5;
    document.getElementById('cfgImgAutoNegative').value = iga.negative_prompt || '';
    if (cfg.version) {
      document.getElementById('settingsFooter').textContent = `Character School v${cfg.version} © Krazif & Tunas`;
    }
    document.getElementById('cfgBgOverlay').addEventListener('input', liveApplyAppearance);
    // BG panel upload + URL listeners
    document.getElementById('bgPanelFile').addEventListener('change', function(e) {
      const file = e.target.files[0];
      if (file) addBgFromUpload(file);
      e.target.value = '';
    });
    document.getElementById('bgPanelUrl').addEventListener('keydown', function(e) {
      if (e.key === 'Enter') { e.preventDefault(); addBgFromUrl(); }
    });
    // Populate preset dropdowns
    window._presets = cfg.presets || {};
    populatePresetDropdowns();
    // Populate workflow presets
    _workflowPresets = cfg.workflows || {};
    populateWorkflowDropdown();
  } catch (e) {
    console.error('Failed to load config:', e);
    alert('Failed to load config: ' + e.message);
  }
  document.getElementById('settingsOverlay').classList.add('show');
}

function closeSettings() {
  document.getElementById('settingsOverlay').classList.remove('show');
}

async function saveSettings() {
  const btn = document.getElementById('settingsSaveBtn');
  btn.disabled = true;
  btn.textContent = 'Saving…';
  try {
    const payload = {
      server: {
        host: document.getElementById('cfgServerHost').value.trim(),
        port: parseInt(document.getElementById('cfgServerPort').value) || 7862,
      },
      chat: {
        base_url: document.getElementById('cfgChatBaseUrl').value.trim(),
        api_key: document.getElementById('cfgChatApiKey').value.trim(),
        model: document.getElementById('cfgChatModel').value.trim(),
        temperature: parseFloat(document.getElementById('cfgChatTemp').value) || 0.8,
        max_tokens: parseInt(document.getElementById('cfgChatMaxTok').value) || 2000,
        top_p: (() => { const v = document.getElementById('cfgChatTopP').value.trim(); return v === '' ? null : (parseFloat(v) ?? 1.0); })(),
        top_k: (() => { const v = document.getElementById('cfgChatTopK').value.trim(); return v === '' ? null : (isNaN(v) ? null : parseInt(v)); })(),
        enable_thinking: document.getElementById('cfgChatEnableThinking').checked,
      },
      analysis: {
        base_url: document.getElementById('cfgAnalysisBaseUrl').value.trim(),
        api_key: document.getElementById('cfgAnalysisApiKey').value.trim(),
        model: document.getElementById('cfgAnalysisModel').value.trim(),
        temperature: parseFloat(document.getElementById('cfgAnalysisTemp').value) || 0.1,
        max_tokens: parseInt(document.getElementById('cfgAnalysisMaxTok').value) || 1500,
        top_p: (() => { const v = document.getElementById('cfgAnalysisTopP').value.trim(); return v === '' ? null : (parseFloat(v) ?? 1.0); })(),
        top_k: (() => { const v = document.getElementById('cfgAnalysisTopK').value.trim(); return v === '' ? null : (isNaN(v) ? null : parseInt(v)); })(),
      },
      summary: {
        base_url: document.getElementById('cfgSummaryBaseUrl').value.trim(),
        api_key: document.getElementById('cfgSummaryApiKey').value.trim(),
        model: document.getElementById('cfgSummaryModel').value.trim(),
        temperature: parseFloat(document.getElementById('cfgSummaryTemp').value) || 0.3,
        max_tokens: parseInt(document.getElementById('cfgSummaryMaxTok').value) || 1000,
        top_p: (() => { const v = document.getElementById('cfgSummaryTopP').value.trim(); return v === '' ? null : (parseFloat(v) ?? 1.0); })(),
        top_k: (() => { const v = document.getElementById('cfgSummaryTopK').value.trim(); return v === '' ? null : (isNaN(v) ? null : parseInt(v)); })(),
      },
      paths: {
        characters_dir: document.getElementById('cfgCharactersDir').value.trim() || null,
        personas_dir: document.getElementById('cfgPersonasDir').value.trim() || null,
      },
      imagegen: {
        enabled: document.getElementById('cfgImagegenEnabled').checked,
        base_url: document.getElementById('cfgImagegenBaseUrl').value.trim() || 'http://127.0.0.1:8188',
        negative_prompt: document.getElementById('cfgImagegenNegative').value.trim(),
        width: parseInt(document.getElementById('cfgImagegenWidth').value) || 512,
        height: parseInt(document.getElementById('cfgImagegenHeight').value) || 768,
        steps: parseInt(document.getElementById('cfgImagegenSteps').value) || 20,
        cfg_scale: parseFloat(document.getElementById('cfgImagegenCfgScale').value) || 7.0,
        sampler: document.getElementById('cfgImagegenSampler').value.trim() || 'euler',
        scheduler: document.getElementById('cfgImagegenScheduler').value.trim() || 'normal',
        seed: parseInt(document.getElementById('cfgImagegenSeed').value) || 0,
        random_seed: document.getElementById('cfgImagegenRandomSeed').checked,
        workflow: (() => { const v = document.getElementById('cfgImagegenWorkflow').value.trim(); if (!v) return null; try { return JSON.parse(v); } catch { return null; } })(),
      },
      imagegen_auto: {
        enabled: document.getElementById('cfgImgAutoEnabled').checked,
        ...(() => {
          const f = getEndpointFields('imgAuto');
          return {
            base_url: f.base_url || 'https://openrouter.ai/api/v1',
            api_key: f.api_key || null,
            model: f.model || 'deepseek/deepseek-v4-flash',
            temperature: f.temperature ?? 0.5,
            max_tokens: f.max_tokens || 500,
          };
        })(),
        interval: parseInt(document.getElementById('cfgImgAutoInterval').value) || 5,
        negative_prompt: document.getElementById('cfgImgAutoNegative').value.trim(),
      },
    };
    const res = await fetch('/api/config', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify(payload),
    });
    const data = await res.json();
    if (res.ok) {
      closeSettings();
      // Reload character list if paths changed
      loadCardList(); // also calls loadPersonaList internally
    } else {
      alert('Failed to save: ' + (data.detail || data.message || 'Unknown error'));
    }
  } catch (e) {
    alert('Failed to save: ' + e.message);
  } finally {
    btn.disabled = false;
    btn.textContent = 'Save';
  }
}

async function resetDatabase() {
  if (!confirm('⚠️ This will permanently delete ALL chat sessions and RP sessions.\n\nThis cannot be undone. Are you sure?')) return;
  if (!confirm('Last chance — really wipe the entire database?')) return;
  const btn = document.getElementById('dbResetBtn');
  btn.disabled = true;
  btn.textContent = 'Wiping…';
  try {
    const res = await fetch('/api/database/reset', { method: 'POST' });
    const data = await res.json();
    if (res.ok) {
      alert('Database cleared successfully.');
      // Reset UI state
      const cm = document.getElementById('chatMessages');
      if (cm) cm.innerHTML = '';
      const ap = document.getElementById('analysisPanel');
      if (ap) ap.innerHTML = '<div style="color:var(--text-dim);text-align:center;padding:20px;font-size:13px">Select a character and start chatting to see analysis.</div>';
      const ci = document.getElementById('consoleInner');
      if (ci) ci.innerHTML = '';
      const rci = document.getElementById('rpConsoleInner');
      if (rci) rci.innerHTML = '';
      // Reload RP session list
      loadRPSessions();
      closeSettings();
    } else {
      alert('Failed: ' + (data.error || 'Unknown error'));
    }
  } catch (e) {
    alert('Failed: ' + e.message);
  } finally {
    btn.disabled = false;
    btn.textContent = 'Reset Database';
  }
}

// ── Preset helpers ────────────────────────────────────────────────

function populatePresetDropdowns() {
  const presets = window._presets || {};
  const names = Object.keys(presets).sort();
  for (const section of ['chat', 'analysis', 'summary', 'imgAuto']) {
    const sel = document.getElementById(`cfg${section.charAt(0).toUpperCase() + section.slice(1)}Preset`);
    if (!sel) continue;
    sel.innerHTML = '<option value="">— None (manual) —</option>';
    for (const name of names) {
      sel.innerHTML += `<option value="${name}">${name}</option>`;
    }
  }
}

function getEndpointFields(section) {
  const cap = section.charAt(0).toUpperCase() + section.slice(1);
  const getVal = (id) => { const el = document.getElementById(id); return el ? el.value.trim() : ''; };
  const topP = getVal(`cfg${cap}TopP`);
  const topK = getVal(`cfg${cap}TopK`);
  return {
    base_url: getVal(`cfg${cap}BaseUrl`),
    api_key: getVal(`cfg${cap}ApiKey`),
    model: getVal(`cfg${cap}Model`),
    temperature: parseFloat(getVal(`cfg${cap}Temp`)) || 0.8,
    max_tokens: parseInt(getVal(`cfg${cap}MaxTok`)) || 2000,
    top_p: topP === '' ? null : (parseFloat(topP) ?? 1.0),
    top_k: topK === '' ? null : (isNaN(topK) ? null : parseInt(topK)),
  };
}

function setEndpointFields(section, preset) {
  const cap = section.charAt(0).toUpperCase() + section.slice(1);
  const setVal = (id, val) => { const el = document.getElementById(id); if (el) el.value = val; };
  setVal(`cfg${cap}BaseUrl`, preset.base_url || '');
  setVal(`cfg${cap}ApiKey`, preset.api_key || '');
  setVal(`cfg${cap}Model`, preset.model || '');
  setVal(`cfg${cap}Temp`, preset.temperature ?? 0.8);
  setVal(`cfg${cap}MaxTok`, preset.max_tokens ?? 2000);
  const tp = document.getElementById(`cfg${cap}TopP`);
  if (tp) tp.value = preset.top_p != null ? preset.top_p : '';
  const tk = document.getElementById(`cfg${cap}TopK`);
  if (tk) tk.value = preset.top_k != null ? preset.top_k : '';
  if (section === 'chat') {
    const et = document.getElementById('cfgChatEnableThinking');
    if (et) et.checked = preset.enable_thinking ?? false;
  }
}

async function applyPreset(section, name) {
  if (!name) return;
  // Always use unmasked preset data from /api/presets; fetch if not yet loaded
  let presets = _presetData;
  if (!Object.keys(presets).length) {
    try {
      const res = await fetch('/api/presets');
      if (res.ok) {
        _presetData = await res.json();
        presets = _presetData;
      }
    } catch (e) { /* fall through to window._presets below */ }
  }
  // Last-resort fallback (masked keys — user will need to re-enter)
  if (!Object.keys(presets).length) presets = window._presets || {};
  const preset = presets[name];
  if (!preset) return;
  setEndpointFields(section, preset);
}

// ── Preset Manager Modal ─────────────────────────────────────────

let _presetSection = 'chat'; // which section opened the manager
let _presetData = {}; // full unmasked presets from /api/presets
let _presetVisibleKeys = {}; // track which keys are visible: { presetName: true/false }

async function openPresetManager(section) {
  _presetSection = section;
  _presetVisibleKeys = {};
  try {
    const res = await fetch('/api/presets');
    if (!res.ok) throw new Error('Failed to load presets');
    _presetData = await res.json();
  } catch (e) {
    alert('Failed to load presets: ' + e.message);
    return;
  }
  renderPresetManager();
  document.getElementById('presetOverlay').classList.add('show');
}

function closePresetManager() {
  document.getElementById('presetOverlay').classList.remove('show');
}

function escHtml(s) {
  return String(s).replace(/&/g,'&amp;').replace(/"/g,'&quot;').replace(/</g,'&lt;').replace(/>/g,'&gt;');
}

function renderPresetManager() {
  const body = document.getElementById('presetBody');
  const names = Object.keys(_presetData).sort();
  let html = `<button class="preset-btn-add" onclick="addPresetCard()">+ New Preset</button>`;
  for (const name of names) {
    const p = _presetData[name];
    const vis = _presetVisibleKeys[name];
    html += `<div class="preset-card" id="preset-card-${CSS.escape(name)}">
      <div class="preset-card-name">
        <input type="text" value="${escHtml(name)}" data-original="${escHtml(name)}" style="background:transparent;border:none;color:var(--accent);font-size:14px;font-weight:600;width:200px;padding:0;" />
      </div>
      <div class="preset-card-row">
        <label>Base URL</label>
        <input type="text" value="${escHtml(p.base_url || '')}" data-field="base_url" />
      </div>
      <div class="preset-card-row">
        <label>API Key</label>
        <input type="${vis ? 'text' : 'password'}" value="${escHtml(p.api_key || '')}" data-field="api_key" />
        <button class="preset-key-toggle" onclick="togglePresetKeyVisibility('${escHtml(name)}')">${vis ? '🙈 Hide' : '👁 Show'}</button>
      </div>
      <div class="preset-card-row">
        <label>Model</label>
        <input type="text" value="${escHtml(p.model || '')}" data-field="model" />
      </div>
      <div class="preset-card-row">
        <label>Temperature</label>
        <input type="number" step="0.1" value="${p.temperature ?? 0.8}" data-field="temperature" />
      </div>
      <div class="preset-card-row">
        <label>Max Tokens</label>
        <input type="number" value="${p.max_tokens ?? 2000}" data-field="max_tokens" />
      </div>
      <div class="preset-card-row">
        <label>Top P</label>
        <input type="text" value="${p.top_p != null ? p.top_p : ''}" data-field="top_p" placeholder="default" />
      </div>
      <div class="preset-card-row">
        <label>Top K</label>
        <input type="text" value="${p.top_k != null ? p.top_k : ''}" data-field="top_k" placeholder="default" />
      </div>
      ${_presetSection === 'chat' ? `<div class="preset-card-row">
        <label>Thinking</label>
        <input type="checkbox" ${p.enable_thinking ? 'checked' : ''} data-field="enable_thinking" />
      </div>` : ''}
      <div class="preset-card-actions">
        <button class="preset-btn-delete" onclick="deletePresetFromManager('${escHtml(name)}')">🗑 Delete</button>
        <button class="preset-btn-save" onclick="savePresetFromCard('${escHtml(name)}')">💾 Save</button>
      </div>
    </div>`;
  }
  body.innerHTML = html;
}

function togglePresetKeyVisibility(name) {
  _presetVisibleKeys[name] = !_presetVisibleKeys[name];
  renderPresetManager();
}

function addPresetCard() {
  const name = 'New Preset';
  let i = 1;
  while (_presetData[name + (i > 1 ? ' ' + i : '')]) i++;
  const finalName = name + (i > 1 ? ' ' + i : '');
  _presetData[finalName] = { base_url: '', api_key: '', model: '', temperature: 0.8, max_tokens: 2000, top_p: null, top_k: null };
  if (_presetSection === 'chat') _presetData[finalName].enable_thinking = false;
  _presetVisibleKeys[finalName] = true; // show key for new presets
  renderPresetManager();
}

async function savePresetFromCard(originalName) {
  const card = document.getElementById(`preset-card-${CSS.escape(originalName)}`);
  if (!card) return;
  const nameInput = card.querySelector('input[data-original]');
  const newName = nameInput.value.trim();
  if (!newName) { alert('Preset name cannot be empty.'); return; }
  const fields = {};
  card.querySelectorAll('[data-field]').forEach(el => {
    const f = el.dataset.field;
    if (f === 'enable_thinking') { fields[f] = el.checked; }
    else if (f === 'temperature') { const v = parseFloat(el.value); fields[f] = isNaN(v) ? 0.8 : v; }
    else if (f === 'max_tokens') { const v = parseInt(el.value); fields[f] = isNaN(v) ? 2000 : v; }
    else if (f === 'top_p') { const v = el.value.trim(); fields[f] = v === '' ? null : parseFloat(v); }
    else if (f === 'top_k') { const v = el.value.trim(); fields[f] = v === '' ? null : (isNaN(v) ? null : parseInt(v)); }
    else { fields[f] = el.value.trim(); }
  });
  // Build full presets object
  const presets = { ..._presetData };
  // Remove old name if renamed
  if (newName !== originalName) delete presets[originalName];
  presets[newName] = fields;
  try {
    const res = await fetch('/api/config', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ presets }),
    });
    if (res.ok) {
      _presetData = presets;
      window._presets = presets;
      populatePresetDropdowns();
      // Auto-apply to current section
      setEndpointFields(_presetSection, fields);
      const cap = _presetSection.charAt(0).toUpperCase() + _presetSection.slice(1);
      document.getElementById(`cfg${cap}Preset`).value = newName;
      renderPresetManager();
    } else {
      const data = await res.json();
      alert('Failed to save preset: ' + (data.detail || data.message || 'Unknown error'));
    }
  } catch (e) {
    alert('Failed to save preset: ' + e.message);
  }
}

async function deletePresetFromManager(name) {
  if (!confirm(`Delete preset "${name}"?`)) return;
  try {
    const res = await fetch(`/api/presets/${encodeURIComponent(name)}`, { method: 'DELETE' });
    if (res.ok) {
      delete _presetData[name];
      window._presets = { ..._presetData };
      populatePresetDropdowns();
      renderPresetManager();
    } else {
      const data = await res.json();
      alert('Failed to delete preset: ' + (data.detail || data.message || 'Unknown error'));
    }
  } catch (e) {
    alert('Failed to delete preset: ' + e.message);
  }
}

// ── ComfyUI Workflow Presets ──────────────────────────────────────

let _workflowPresets = {}; // cached workflow presets from /api/workflows

function populateWorkflowDropdown() {
  const sel = document.getElementById('cfgImagegenWorkflowPreset');
  if (!sel) return;
  sel.innerHTML = '<option value="">— None (manual) —</option>';
  for (const name of Object.keys(_workflowPresets).sort()) {
    sel.innerHTML += `<option value="${escapeHtml(name)}">${escapeHtml(name)}</option>`;
  }
}

async function loadWorkflowPresets() {
  try {
    const res = await fetch('/api/workflows');
    if (res.ok) _workflowPresets = await res.json();
  } catch (e) { /* ignore */ }
  populateWorkflowDropdown();
}

function applyWorkflowPreset(name) {
  if (!name) return;
  const wf = _workflowPresets[name];
  if (!wf) return;
  const ta = document.getElementById('cfgImagegenWorkflow');
  if (ta) ta.value = wf ? JSON.stringify(wf, null, 2) : '';
}

async function saveWorkflowPresetAs() {
  const name = prompt('Save workflow as (name):');
  if (!name || !name.trim()) return;
  const ta = document.getElementById('cfgImagegenWorkflow');
  let workflow = null;
  if (ta && ta.value.trim()) {
    try {
      workflow = JSON.parse(ta.value);
    } catch (e) {
      alert('Invalid JSON in workflow textarea: ' + e.message);
      return;
    }
  }
  try {
    const res = await fetch('/api/workflows', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ name: name.trim(), workflow }),
    });
    if (res.ok) {
      await loadWorkflowPresets();
      document.getElementById('cfgImagegenWorkflowPreset').value = name.trim();
    } else {
      const data = await res.json();
      alert('Failed to save workflow: ' + (data.detail || data.message || 'Unknown error'));
    }
  } catch (e) {
    alert('Failed to save workflow: ' + e.message);
  }
}

async function deleteWorkflowPreset() {
  const sel = document.getElementById('cfgImagegenWorkflowPreset');
  if (!sel || !sel.value) { alert('Select a workflow to delete first.'); return; }
  const name = sel.value;
  if (!confirm(`Delete workflow "${name}"?`)) return;
  try {
    const res = await fetch(`/api/workflows/${encodeURIComponent(name)}`, { method: 'DELETE' });
    if (res.ok) {
      await loadWorkflowPresets();
    } else {
      const data = await res.json();
      alert('Failed to delete workflow: ' + (data.detail || data.message || 'Unknown error'));
    }
  } catch (e) {
    alert('Failed to delete workflow: ' + e.message);
  }
}

