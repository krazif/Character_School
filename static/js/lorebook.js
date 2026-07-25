// Character School — lorebook.js
// Lorebook (World Info) manager, import/export.

// ─── Lorebook Manager ─────────────────────────────────────────────
let _lorebookList = [];
let _currentLorebook = null;     // {filename, name, description, entries:{}}
let _lorebookCtx = 'rp';         // 'rp' or 'school'

async function openLorebookManager(ctx) {
  _lorebookCtx = ctx || 'rp';
  const lbl = document.getElementById('lorebookCtxLabel');
  if (lbl) lbl.textContent = _lorebookCtx === 'school' ? 'School Mode' : 'RP Mode';
  document.getElementById('lorebookOverlay').style.display = 'flex';
  await loadLorebookList();
}

function closeLorebookManager() {
  document.getElementById('lorebookOverlay').style.display = 'none';
  _currentLorebook = null;
}

async function loadLorebookList() {
  try {
    const res = await fetch('/api/lorebooks');
    _lorebookList = await res.json();
    renderLorebookList();
  } catch (e) {
    console.error('Failed to load lorebooks', e);
  }
}

function renderLorebookList() {
  const container = document.getElementById('lorebookList');
  if (!_lorebookList || _lorebookList.length === 0) {
    container.innerHTML = '<div style="color:var(--sub);text-align:center;padding:20px 10px;font-size:13px">No lorebooks yet.<br>Create one above.</div>';
    return;
  }
  let html = '';
  for (const lb of _lorebookList) {
    const isActive = (_lorebookCtx === 'rp' ? rpLorebooks : schoolLorebooks).includes(lb.filename);
    html += `<div class="lb-list-item ${_currentLorebook && _currentLorebook._filename === lb.filename ? 'active' : ''}" onclick="selectLorebook('${lb.filename}')" style="padding:8px 10px;cursor:pointer;border-bottom:1px solid var(--border);font-size:13px;display:flex;align-items:center;gap:6px">`;
    html += `<input type="checkbox" ${isActive ? 'checked' : ''} onclick="event.stopPropagation();toggleLorebookActive('${lb.filename}', this.checked)" style="accent-color:var(--accent)">`;
    html += `<span style="flex:1;overflow:hidden;text-overflow:ellipsis;white-space:nowrap">${escHtml(lb.name)}</span>`;
    if (lb.entry_count) html += `<span style="font-size:10px;color:var(--sub)">${lb.entry_count}</span>`;
    html += `</div>`;
  }
  container.innerHTML = html;
}

async function selectLorebook(filename) {
  try {
    const res = await fetch('/api/lorebooks/' + encodeURIComponent(filename));
    const data = await res.json();
    _currentLorebook = data;
    _currentLorebook._filename = filename;
    renderLorebookDetail();
    renderLorebookList(); // update active highlight
  } catch (e) {
    console.error('Failed to load lorebook', e);
  }
}

async function createLorebook() {
  const name = document.getElementById('lorebookNewName').value.trim();
  if (!name) return;
  try {
    const res = await fetch('/api/lorebooks', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({name, description: ''}),
    });
    const result = await res.json();
    document.getElementById('lorebookNewName').value = '';
    await loadLorebookList();
    await selectLorebook(result.filename);
  } catch (e) {
    console.error('Failed to create lorebook', e);
  }
}

async function deleteCurrentLorebook() {
  if (!_currentLorebook || !_currentLorebook._filename) return;
  if (!confirm('Delete this lorebook? This cannot be undone.')) return;
  const fn = _currentLorebook._filename;
  try {
    await fetch('/api/lorebooks/' + encodeURIComponent(fn), {method: 'DELETE'});
    // Remove from active lists
    rpLorebooks = rpLorebooks.filter(f => f !== fn);
    schoolLorebooks = schoolLorebooks.filter(f => f !== fn);
    if (rpActiveSessionId) {
      await fetch('/api/rp/sessions/' + rpActiveSessionId + '/lorebooks', {
        method: 'PUT', headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({lorebooks: rpLorebooks}),
      });
    }
    if (schoolActiveSessionId) {
      await fetch('/api/school/sessions/' + schoolActiveSessionId + '/lorebooks', {
        method: 'PUT', headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({lorebooks: schoolLorebooks}),
      });
    }
    _currentLorebook = null;
    await loadLorebookList();
    renderLorebookDetail();
  } catch (e) {
    console.error('Failed to delete lorebook', e);
  }
}

function renderLorebookDetail() {
  const container = document.getElementById('lorebookDetail');
  if (!_currentLorebook) {
    container.innerHTML = '<div style="color:var(--sub);text-align:center;padding:40px 20px">Select a lorebook from the left</div>';
    return;
  }
  const lb = _currentLorebook;
  const entries = lb.entries || {};
  const entryList = Object.entries(entries).sort((a, b) => a[0] - b[0]);

  let html = '';
  // Header
  html += '<div style="margin-bottom:12px">';
  html += `<input type="text" value="${escHtml(lb.name || '')}" placeholder="Lorebook name" oninput="_currentLorebook.name=this.value;saveLorebookDebounced()" style="width:100%;font-size:15px;font-weight:bold;padding:6px 8px;border-radius:4px;border:1px solid var(--border);background:var(--surface2);color:var(--text);margin-bottom:4px">`;
  html += `<input type="text" value="${escHtml(lb.description || '')}" placeholder="Description (optional)" oninput="_currentLorebook.description=this.value;saveLorebookDebounced()" style="width:100%;font-size:12px;padding:4px 8px;border-radius:4px;border:1px solid var(--border);background:var(--surface2);color:var(--sub)">`;
  html += `<div style="margin-top:8px;display:flex;gap:6px">`;
  html += `<button class="btn-send" style="padding:4px 12px;font-size:12px" onclick="addLorebookEntry()">+ Add Entry</button>`;
  html += `<button class="btn-send" style="padding:4px 12px;font-size:12px" onclick="saveLorebook()">💾 Save</button>`;
  html += `<button class="btn-send" style="padding:4px 12px;font-size:12px" onclick="exportLorebook()">⬇ Export</button>`;
  html += `<button class="btn-reset" style="padding:4px 12px;font-size:12px;margin-left:auto" onclick="deleteCurrentLorebook()">🗑 Delete Lorebook</button>`;
  html += `</div></div>`;

  // Entries
  if (entryList.length === 0) {
    html += '<div style="color:var(--sub);text-align:center;padding:20px;font-size:13px">No entries yet. Click "Add Entry" to create one.</div>';
  } else {
    for (const [uid, entry] of entryList) {
      html += renderLorebookEntry(uid, entry);
    }
  }

  container.innerHTML = html;
}

function renderLorebookEntry(uid, entry) {
  const e = entry;
  let html = `<div class="lb-entry" style="border:1px solid var(--border);border-radius:8px;padding:10px;margin-bottom:8px;background:var(--surface2)">`;
  // Row 1: comment + disable toggle
  html += `<div style="display:flex;align-items:center;gap:8px;margin-bottom:6px">`;
  html += `<input type="text" value="${escHtml(e.comment || '')}" placeholder="Entry name/comment" oninput="updateLorebookEntryField(${uid},'comment',this.value)" style="flex:1;font-size:13px;font-weight:bold;padding:4px 8px;border-radius:4px;border:1px solid var(--border);background:var(--surface);color:var(--text)">`;
  html += `<label style="font-size:11px;color:var(--sub);display:flex;align-items:center;gap:3px;white-space:nowrap"><input type="checkbox" ${e.disable ? 'checked' : ''} onclick="updateLorebookEntryField(${uid},'disable',this.checked)" style="accent-color:var(--accent)"> Disabled</label>`;
  html += `<button class="btn-reset" style="padding:2px 8px;font-size:11px" onclick="deleteLorebookEntry(${uid})">🗑</button>`;
  html += `</div>`;
  // Row 2: keywords
  html += `<div style="display:flex;gap:6px;margin-bottom:4px">`;
  html += `<div style="flex:1"><label style="font-size:10px;color:var(--sub)">Primary keywords (comma-separated)</label><input type="text" value="${escHtml((e.key || []).join(', '))}" oninput="updateLorebookEntryField(${uid},'key',this.value.split(',').map(s=>s.trim()).filter(Boolean))" style="width:100%;font-size:12px;padding:3px 6px;border-radius:4px;border:1px solid var(--border);background:var(--surface);color:var(--text)"></div>`;
  html += `<div style="flex:1"><label style="font-size:10px;color:var(--sub)">Secondary keywords (AND-condition, optional)</label><input type="text" value="${escHtml((e.keysecondary || []).join(', '))}" oninput="updateLorebookEntryField(${uid},'keysecondary',this.value.split(',').map(s=>s.trim()).filter(Boolean))" style="width:100%;font-size:12px;padding:3px 6px;border-radius:4px;border:1px solid var(--border);background:var(--surface);color:var(--text)"></div>`;
  html += `</div>`;
  // Row 3: content
  html += `<div style="margin-bottom:4px"><label style="font-size:10px;color:var(--sub)">Content (lore text to inject when triggered)</label><textarea oninput="updateLorebookEntryField(${uid},'content',this.value)" style="width:100%;min-height:50px;font-size:12px;padding:6px;border-radius:4px;border:1px solid var(--border);background:var(--surface);color:var(--text);resize:vertical">${escHtml(e.content || '')}</textarea></div>`;
  // Row 4: toggles + order
  html += `<div style="display:flex;gap:12px;align-items:center;font-size:11px;color:var(--sub);flex-wrap:wrap">`;
  html += `<label style="display:flex;align-items:center;gap:3px"><input type="checkbox" ${e.constant ? 'checked' : ''} onclick="updateLorebookEntryField(${uid},'constant',this.checked)" style="accent-color:var(--accent)"> Constant</label>`;
  html += `<label style="display:flex;align-items:center;gap:3px"><input type="checkbox" ${e.selective ? 'checked' : ''} onclick="updateLorebookEntryField(${uid},'selective',this.checked)" style="accent-color:var(--accent)"> Selective (AND)</label>`;
  html += `<label>Order: <input type="number" value="${e.order ?? 100}" style="width:50px;font-size:11px;padding:1px 4px;border-radius:3px;border:1px solid var(--border);background:var(--surface);color:var(--text)" oninput="updateLorebookEntryField(${uid},'order',parseInt(this.value)||100)"></label>`;
  html += `<label>Position: <select style="font-size:11px;padding:1px 4px;border-radius:3px;border:1px solid var(--border);background:var(--surface);color:var(--text)" onchange="updateLorebookEntryField(${uid},'position',parseInt(this.value))"><option value="0" ${e.position===0?'selected':''}>Before system</option><option value="1" ${e.position===1?'selected':''}>After system</option><option value="2" ${(e.position===2||e.position===undefined)?'selected':''}>Before chat</option></select></label>`;
  html += `</div>`;
  html += `</div>`;
  return html;
}

function addLorebookEntry() {
  if (!_currentLorebook) return;
  if (!_currentLorebook.entries) _currentLorebook.entries = {};
  const uids = Object.keys(_currentLorebook.entries).map(Number);
  const nextUid = uids.length > 0 ? Math.max(...uids) + 1 : 0;
  _currentLorebook.entries[String(nextUid)] = {
    uid: nextUid,
    key: [],
    keysecondary: [],
    comment: '',
    content: '',
    constant: false,
    selective: false,
    order: 100,
    position: 0,
    disable: false,
    extensions: {},
  };
  saveLorebook();
  renderLorebookDetail();
}

function deleteLorebookEntry(uid) {
  if (!_currentLorebook || !_currentLorebook.entries) return;
  delete _currentLorebook.entries[String(uid)];
  saveLorebook();
  renderLorebookDetail();
}

function updateLorebookEntryField(uid, field, value) {
  if (!_currentLorebook || !_currentLorebook.entries) return;
  const entry = _currentLorebook.entries[String(uid)];
  if (entry) entry[field] = value;
  saveLorebookDebounced();
}

let _lorebookSaveTimer = null;
function saveLorebookDebounced() {
  if (_lorebookSaveTimer) clearTimeout(_lorebookSaveTimer);
  _lorebookSaveTimer = setTimeout(() => saveLorebook(), 1000);
}

async function saveLorebook() {
  if (!_currentLorebook || !_currentLorebook._filename) return;
  const fn = _currentLorebook._filename;
  // Strip internal _filename key before saving
  const data = JSON.parse(JSON.stringify(_currentLorebook));
  delete data._filename;
  try {
    await fetch('/api/lorebooks/' + encodeURIComponent(fn), {
      method: 'PUT',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify(data),
    });
  } catch (e) {
    console.error('Failed to save lorebook', e);
  }
}

// ─── Lorebook Import / Export ──────────────────────────────────────

async function importLorebookFile(input) {
  const file = input.files[0];
  if (!file) return;
  try {
    const text = await file.text();
    const data = JSON.parse(text);
    if (!data.entries || typeof data.entries !== 'object') {
      alert('Invalid lorebook file: missing or invalid "entries" object');
      input.value = '';
      return;
    }
    const res = await fetch('/api/lorebooks/import', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify(data),
    });
    const result = await res.json();
    if (result.error) {
      alert('Import failed: ' + result.error);
    } else {
      await loadLorebookList();
      await selectLorebook(result.filename);
    }
  } catch (e) {
    alert('Import failed: ' + e.message);
  }
  input.value = '';
}

async function exportLorebook() {
  if (!_currentLorebook || !_currentLorebook._filename) return;
  const fn = _currentLorebook._filename;
  try {
    const res = await fetch('/api/lorebooks/' + encodeURIComponent(fn) + '/export');
    if (!res.ok) {
      alert('Export failed: ' + (await res.text()));
      return;
    }
    const blob = await res.blob();
    const safeName = fn.replace(/\.json$/, '');
    const a = document.createElement('a');
    a.href = URL.createObjectURL(blob);
    a.download = safeName + '.json';
    document.body.appendChild(a);
    a.click();
    document.body.removeChild(a);
    URL.revokeObjectURL(a.href);
  }  catch (e) {
    alert('Export failed: ' + e.message);
  }
}

async function toggleLorebookActive(filename, checked) {
  const arr = _lorebookCtx === 'rp' ? rpLorebooks : schoolLorebooks;
  if (checked) {
    if (!arr.includes(filename)) arr.push(filename);
  } else {
    const idx = arr.indexOf(filename);
    if (idx >= 0) arr.splice(idx, 1);
  }
  // Persist to session
  const sessionId = _lorebookCtx === 'rp' ? rpActiveSessionId : schoolActiveSessionId;
  if (!sessionId) return;
  const endpoint = _lorebookCtx === 'rp'
    ? '/api/rp/sessions/' + sessionId + '/lorebooks'
    : '/api/school/sessions/' + sessionId + '/lorebooks';
  try {
    await fetch(endpoint, {
      method: 'PUT',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({lorebooks: arr}),
    });
  } catch (e) {
    console.error('Failed to update session lorebooks', e);
  }
  renderLorebookList();
  if (_lorebookCtx === 'rp') renderRPSceneBar();
}

