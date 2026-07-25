// Character School — imagegen.js
// Scene generation modal, ComfyUI integration, image gallery.

let genSceneMode = 'rp'; // 'rp' or 'school'
let lastGenSceneClientId = null; // track last inserted image msg for regenerate
let genSceneHistory = []; // {image_path, seed, prompt} entries
let lastPreviewedImage = null; // {filename, prompt, seed} — currently previewed image for Insert to Chat

function openGenerateSceneModal(mode) {
  genSceneMode = mode || 'rp';
  lastGenSceneClientId = null;
  genSceneHistory = [];
  const overlay = document.getElementById('genSceneOverlay');
  overlay.style.display = 'flex';
  document.getElementById('genSceneModeLabel').textContent = genSceneMode === 'school' ? 'School Mode' : 'RP Mode';
  document.getElementById('genSceneStatus').textContent = '';
  document.getElementById('genScenePreviewWrap').style.display = 'none';
  document.getElementById('genSceneHistoryWrap').style.display = 'none';
  document.getElementById('genSceneHistory').innerHTML = '';
  document.getElementById('genSceneBtn').disabled = false;
  document.getElementById('genSceneBtn').textContent = 'Generate';
  document.getElementById('genSceneRegenBtn').style.display = 'none';
  document.getElementById('genSceneInsertBtn').style.display = 'none';
  document.getElementById('genSceneBgBtn').style.display = 'none';
  // Reset seed: if Auto is on, clear the field (random each time); otherwise roll a seed
  if (document.getElementById('genSceneAutoSeed').checked) {
    document.getElementById('genSceneSeed').value = '';
  } else {
    rollGenSceneSeed();
  }
  checkComfyUIStatus();
  autoFillGenScenePrompt();
  loadImageGallery();
}

function rollGenSceneSeed() {
  document.getElementById('genSceneSeed').value = Math.floor(Math.random() * 4294967296);
}

function onGenSceneAutoSeedChange() {
  const auto = document.getElementById('genSceneAutoSeed').checked;
  const seedInput = document.getElementById('genSceneSeed');
  const diceBtn = document.getElementById('genSceneDiceBtn');
  if (auto) {
    seedInput.value = '';
    seedInput.placeholder = 'random';
    diceBtn.style.opacity = '0.5';
  } else {
    rollGenSceneSeed();
    seedInput.placeholder = '0';
    diceBtn.style.opacity = '1';
  }
}

function getGenSceneSeedPayload() {
  const auto = document.getElementById('genSceneAutoSeed').checked;
  if (auto) return {};
  const v = parseInt(document.getElementById('genSceneSeed').value);
  if (isNaN(v)) return {};
  return { seed: v };
}

function showGenSceneSeed(seed) {
  if (seed !== undefined && seed !== null) {
    const statusEl = document.getElementById('genSceneStatus');
    const cur = statusEl.textContent.replace(/\s*\(seed:.*\)/, '');
    statusEl.textContent = cur + '  (seed: ' + seed + ')';
    // Also update the seed input so user sees what was used
    if (!document.getElementById('genSceneAutoSeed').checked) {
      document.getElementById('genSceneSeed').value = seed;
    }
  }
}

// ── Aspect ratio ──
function onGenSceneRatioChange() {
  const sel = document.getElementById('genSceneRatio').value;
  const wInput = document.getElementById('genSceneWidth');
  const hInput = document.getElementById('genSceneHeight');
  if (sel === 'custom') {
    wInput.style.display = '';
    hInput.style.display = '';
  } else {
    wInput.style.display = 'none';
    hInput.style.display = 'none';
  }
}

function getGenSceneDimensions() {
  const sel = document.getElementById('genSceneRatio').value;
  if (sel === 'custom') {
    const w = parseInt(document.getElementById('genSceneWidth').value);
    const h = parseInt(document.getElementById('genSceneHeight').value);
    const payload = {};
    if (!isNaN(w) && w > 0) payload.width = w;
    if (!isNaN(h) && h > 0) payload.height = h;
    return payload;
  }
  if (sel === 'custom' || !sel) return {};
  const parts = sel.split('x');
  return { width: parseInt(parts[0]), height: parseInt(parts[1]) };
}

// ── ComfyUI status check ──
async function checkComfyUIStatus() {
  const dot = document.getElementById('genSceneStatusDot');
  if (!dot) return;
  dot.style.background = '#999';
  dot.title = 'Checking ComfyUI...';
  try {
    const r = await fetch('/api/imagegen/status');
    const j = await r.json();
    if (j.enabled && j.reachable) {
      dot.style.background = '#4ade80';
      dot.title = 'ComfyUI connected ✓';
    } else if (j.enabled && !j.reachable) {
      dot.style.background = '#f87171';
      dot.title = 'ComfyUI not reachable ✗';
    } else {
      dot.style.background = '#facc15';
      dot.title = 'Image generation disabled in settings';
    }
  } catch {
    dot.style.background = '#f87171';
    dot.title = 'Cannot reach server';
  }
}

// ── Generation history ──
function pushGenSceneHistory(imagePath, seed, promptText) {
  genSceneHistory.push({ image_path: imagePath, seed, prompt: promptText });
  renderGenSceneHistory();
}

function renderGenSceneHistory() {
  const wrap = document.getElementById('genSceneHistoryWrap');
  const container = document.getElementById('genSceneHistory');
  if (!wrap || !container) return;
  if (genSceneHistory.length === 0) {
    wrap.style.display = 'none';
    return;
  }
  wrap.style.display = 'block';
  const apiBase = genSceneMode === 'school' ? '/api/school/images/' : '/api/rp/images/';
  container.innerHTML = '';
  genSceneHistory.forEach((entry, i) => {
    const thumb = document.createElement('div');
    thumb.style.cssText = 'flex-shrink:0;cursor:pointer;text-align:center';
    const img = document.createElement('img');
    img.src = apiBase + encodeURIComponent(entry.image_path);
    img.style.cssText = 'width:64px;height:64px;object-fit:cover;border-radius:4px;border:1px solid var(--border)';
    img.title = entry.prompt || '';
    const label = document.createElement('div');
    label.style.cssText = 'font-size:10px;color:var(--text-dim);font-family:monospace;margin-top:2px';
    label.textContent = entry.seed !== undefined && entry.seed !== null ? String(entry.seed).slice(-6) : '?';
    thumb.appendChild(img);
    thumb.appendChild(label);
    thumb.onclick = () => {
      // Fill seed from this entry
      document.getElementById('genSceneAutoSeed').checked = false;
      onGenSceneAutoSeedChange();
      document.getElementById('genSceneSeed').value = entry.seed || 0;
      // Also fill prompt
      if (entry.prompt) document.getElementById('genScenePrompt').value = entry.prompt;
    };
    container.appendChild(thumb);
  });
}

function closeGenSceneModal() {
  document.getElementById('genSceneOverlay').style.display = 'none';
}

// ── Image gallery (all uploads, grouped by date) ──
let galleryImages = [];
let galleryPage = 0;
const GALLERY_PAGE_SIZE = 50;

async function loadImageGallery() {
  const container = document.getElementById('genSceneGallery');
  if (!container) return;
  container.innerHTML = '<div style="text-align:center;color:var(--text-dim);font-size:12px;padding:20px">Loading...</div>';
  try {
    const resp = await fetch('/api/images');
    const data = await resp.json();
    galleryImages = data.images || [];
    galleryPage = 0;
    renderImageGallery();
  } catch (e) {
    container.innerHTML = '<div style="text-align:center;color:#f87171;font-size:12px;padding:20px">Failed to load gallery</div>';
  }
}

function renderImageGallery() {
  const container = document.getElementById('genSceneGallery');
  const countEl = document.getElementById('genSceneGalleryCount');
  if (!container) return;
  const apiBase = genSceneMode === 'school' ? '/api/school/images/' : '/api/rp/images/';

  if (galleryImages.length === 0) {
    container.innerHTML = '<div style="text-align:center;color:var(--text-dim);font-size:12px;padding:20px">No images uploaded yet</div>';
    if (countEl) countEl.textContent = '';
    return;
  }

  const totalPages = Math.ceil(galleryImages.length / GALLERY_PAGE_SIZE);
  if (galleryPage >= totalPages) galleryPage = totalPages - 1;
  if (galleryPage < 0) galleryPage = 0;

  if (countEl) countEl.textContent = `${galleryImages.length} image${galleryImages.length > 1 ? 's' : ''}`;

  // Slice current page
  const startIdx = galleryPage * GALLERY_PAGE_SIZE;
  const pageImages = galleryImages.slice(startIdx, startIdx + GALLERY_PAGE_SIZE);

  // Group by date
  const groups = {};
  pageImages.forEach(img => {
    const d = img.date || 'Unknown';
    if (!groups[d]) groups[d] = [];
    groups[d].push(img);
  });

  container.innerHTML = '';
  const sortedDates = Object.keys(groups).sort((a, b) => b.localeCompare(a));
  sortedDates.forEach(date => {
    const dateHeader = document.createElement('div');
    dateHeader.style.cssText = 'font-size:11px;font-weight:bold;color:var(--text-dim);margin:8px 0 4px 0;position:sticky;top:0;background:var(--bg);padding:2px 0';
    dateHeader.textContent = date;
    container.appendChild(dateHeader);

    const row = document.createElement('div');
    row.style.cssText = 'display:flex;flex-wrap:wrap;gap:6px';
    groups[date].forEach(img => {
      const thumb = document.createElement('div');
      thumb.style.cssText = 'position:relative;flex-shrink:0';
      const imgEl = document.createElement('img');
      imgEl.src = apiBase + encodeURIComponent(img.filename);
      imgEl.style.cssText = 'width:72px;height:72px;object-fit:cover;border-radius:4px;border:1px solid var(--border);cursor:pointer';
      imgEl.title = img.filename;
      imgEl.onclick = () => {
        previewGalleryImage(img);
      };
      const delBtn = document.createElement('button');
      delBtn.textContent = '×';
      delBtn.style.cssText = 'position:absolute;top:-4px;right:-4px;width:18px;height:18px;border-radius:50%;border:1px solid var(--border);background:var(--surface2);color:var(--text-dim);font-size:13px;line-height:1;cursor:pointer;display:none;padding:0';
      delBtn.title = 'Delete image';
      delBtn.onclick = async (e) => {
        e.stopPropagation();
        if (!confirm('Delete this image? This cannot be undone.')) return;
        try {
          const resp = await fetch('/api/images/' + encodeURIComponent(img.filename), { method: 'DELETE' });
          if (resp.ok) {
            galleryImages = galleryImages.filter(g => g.filename !== img.filename);
            genSceneHistory = genSceneHistory.filter(h => h.image_path !== img.filename);
            // Clear preview if the deleted image was being previewed
            if (lastPreviewedImage && lastPreviewedImage.filename === img.filename) {
              lastPreviewedImage = null;
              document.getElementById('genScenePreviewWrap').style.display = 'none';
              document.getElementById('genSceneInsertBtn').style.display = 'none';
              document.getElementById('genSceneBgBtn').style.display = 'none';
            }
            renderImageGallery();
            renderGenSceneHistory();
          }
        } catch (e) { console.error('Delete failed:', e); }
      };
      thumb.onmouseenter = () => { delBtn.style.display = 'block'; };
      thumb.onmouseleave = () => { delBtn.style.display = 'none'; };
      thumb.appendChild(imgEl);
      thumb.appendChild(delBtn);
      row.appendChild(thumb);
    });
    container.appendChild(row);
  });

  // Pagination controls
  if (totalPages > 1) {
    const pager = document.createElement('div');
    pager.style.cssText = 'display:flex;align-items:center;justify-content:center;gap:12px;margin-top:10px;padding:6px 0;font-size:12px;color:var(--text-dim)';

    const prevBtn = document.createElement('button');
    prevBtn.textContent = '◀ Prev';
    prevBtn.style.cssText = 'padding:3px 10px;border-radius:4px;border:1px solid var(--border);background:var(--surface2);color:var(--text);cursor:pointer;font-size:12px';
    prevBtn.disabled = galleryPage === 0;
    if (prevBtn.disabled) prevBtn.style.opacity = '0.4';
    prevBtn.onclick = () => { galleryPage--; renderImageGallery(); };

    const pageLabel = document.createElement('span');
    pageLabel.textContent = `Page ${galleryPage + 1} / ${totalPages}`;

    const nextBtn = document.createElement('button');
    nextBtn.textContent = 'Next ▶';
    nextBtn.style.cssText = 'padding:3px 10px;border-radius:4px;border:1px solid var(--border);background:var(--surface2);color:var(--text);cursor:pointer;font-size:12px';
    nextBtn.disabled = galleryPage >= totalPages - 1;
    if (nextBtn.disabled) nextBtn.style.opacity = '0.4';
    nextBtn.onclick = () => { galleryPage++; renderImageGallery(); };

    pager.appendChild(prevBtn);
    pager.appendChild(pageLabel);
    pager.appendChild(nextBtn);
    container.appendChild(pager);
  }
}

function autoFillGenScenePrompt() {
  let prompt = '';
  const lastMsgs = genSceneMode === 'school' ? schoolMessages : rpMessages;
  // Find last non-empty message content for scene context
  let lastContent = '';
  for (let i = lastMsgs.length - 1; i >= 0; i--) {
    if (lastMsgs[i].content && lastMsgs[i].content.trim()) {
      lastContent = lastMsgs[i].content.trim();
      break;
    }
  }
  // Extract appearance/description from character cards
  if (genSceneMode === 'school') {
    const d = currentCard?.data || currentCard;
    const charName = d?.name || 'Character';
    const desc = d?.description || '';
    prompt = `Scene featuring ${charName}`;
    if (desc) {
      // Extract first 200 chars of description for appearance hints
      prompt += `. ${desc.substring(0, 200)}`;
    }
  } else {
    // RP mode: use last speaker's character data
    const lastSpeaker = lastMsgs.length > 0 ? lastMsgs[lastMsgs.length - 1].speaker : null;
    if (lastSpeaker && rpCharacters.length > 0) {
      const char = rpCharacters.find(c => c.name === lastSpeaker);
      if (char) {
        prompt = `Scene featuring ${lastSpeaker}`;
        // We don't have full card data in rpCharacters, just name+filename
        // Use the last message content as scene context
      } else {
        prompt = `Scene featuring ${lastSpeaker || 'characters'}`;
      }
    } else if (rpCharacters.length > 0) {
      prompt = `Scene featuring ${rpCharacters.map(c => c.name).join(', ')}`;
    } else {
      prompt = 'Scene';
    }
  }
  // Append scene context from last message (truncated)
  if (lastContent) {
    const sceneHint = lastContent.substring(0, 300).replace(/\n/g, ' ');
    prompt += `. Scene context: ${sceneHint}`;
  }
  prompt += `. High quality, detailed, cinematic lighting.`;
  document.getElementById('genScenePrompt').value = prompt;
}

async function doAutogenPrompt() {
  const statusEl = document.getElementById('genSceneStatus');
  const btn = document.getElementById('genSceneAutogenBtn');
  const lastMsgs = genSceneMode === 'school' ? schoolMessages : rpMessages;
  const recent = lastMsgs.slice(-3).map(m => ({
    role: m.role || m.speaker || 'user',
    speaker: m.speaker || m.name || '',
    content: m.content || '',
  }));
  if (recent.length === 0) {
    statusEl.textContent = '⚠ No messages to analyze';
    return;
  }
  btn.disabled = true;
  btn.textContent = 'Thinking...';
  statusEl.textContent = '⏳ Asking auto-gen LLM for a scene prompt...';
  try {
    const r = await fetch('/api/imagegen/auto-prompt', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ messages: recent }),
    });
    const j = await r.json();
    if (!j.success) {
      statusEl.textContent = '✗ ' + (j.error || 'Autogen failed');
    } else if (j.skipped || !j.prompt) {
      statusEl.textContent = '⏭ LLM said SKIP — not visually interesting enough';
    } else {
      document.getElementById('genScenePrompt').value = j.prompt;
      statusEl.textContent = '✓ Prompt generated by LLM';
    }
  } catch (err) {
    statusEl.textContent = '✗ Error: ' + err.message;
  }
  btn.disabled = false;
  btn.textContent = '✨ Autogen';
}

async function doGenerateScene() {
  const promptText = document.getElementById('genScenePrompt').value.trim();
  if (!promptText) {
    document.getElementById('genSceneStatus').textContent = '⚠ Prompt is empty';
    return;
  }
  const negativeText = document.getElementById('genSceneNegative').value.trim();
  const btn = document.getElementById('genSceneBtn');
  const statusEl = document.getElementById('genSceneStatus');

  btn.disabled = true;
  btn.textContent = 'Generating...';
  statusEl.textContent = '⏳ Calling ComfyUI...';

  try {
    const body = { prompt: promptText, negative_prompt: negativeText, ...getGenSceneSeedPayload(), ...getGenSceneDimensions() };
    const r = await fetch('/api/generate-image', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    });
    const j = await r.json();
    if (!j.success) {
      statusEl.textContent = '✗ ' + (j.error || 'Generation failed');
      btn.disabled = false;
      btn.textContent = 'Generate';
      return;
    }
    statusEl.textContent = '✓ Image generated!';
    showGenSceneSeed(j.seed);
    pushGenSceneHistory(j.image_path, j.seed, promptText);
    // Show preview
    const apiBase = genSceneMode === 'school' ? '/api/school/images/' : '/api/rp/images/';
    const preview = document.getElementById('genScenePreview');
    preview.src = apiBase + encodeURIComponent(j.image_path);
    document.getElementById('genScenePreviewWrap').style.display = 'block';

    // Set as previewed image (no auto-insert to chat)
    lastPreviewedImage = { filename: j.image_path, prompt: promptText, seed: j.seed };
    document.getElementById('genSceneInsertBtn').style.display = '';
    document.getElementById('genSceneBgBtn').style.display = '';

    btn.disabled = false;
    btn.textContent = 'Generate';
    // Show regenerate button after successful generation
    document.getElementById('genSceneRegenBtn').style.display = '';
  } catch (err) {
    statusEl.textContent = '✗ Error: ' + err.message;
    btn.disabled = false;
    btn.textContent = 'Generate';
  }
}

// ─── VN Image Balloon Builder ──────────────────
function buildImageBalloon(imagePath, apiBase, index, deleteFn, promptText, seed) {
  const balloon = document.createElement('div');
  balloon.className = 'msg-image-balloon';
  const img = document.createElement('img');
  img.src = apiBase + encodeURIComponent(imagePath);
  img.alt = 'scene image';
  img.onclick = () => openImageLightbox(img.src, promptText, seed);
  balloon.appendChild(img);
  const delLink = document.createElement('a');
  delLink.className = 'balloon-delete';
  delLink.textContent = 'delete';
  delLink.onclick = (e) => { e.stopPropagation(); deleteFn(index); };
  balloon.appendChild(delLink);
  return balloon;
 }

async function doRegenerateScene() {
  const promptText = document.getElementById('genScenePrompt').value.trim();
  if (!promptText) {
    document.getElementById('genSceneStatus').textContent = '⚠ Prompt is empty';
    return;
  }
  const negativeText = document.getElementById('genSceneNegative').value.trim();
  const btn = document.getElementById('genSceneRegenBtn');
  const statusEl = document.getElementById('genSceneStatus');

  btn.disabled = true;
  btn.textContent = 'Regenerating...';
  statusEl.textContent = '⏳ Calling ComfyUI...';

  try {
    const body = { prompt: promptText, negative_prompt: negativeText, ...getGenSceneSeedPayload(), ...getGenSceneDimensions() };
    const r = await fetch('/api/generate-image', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    });
    const j = await r.json();
    if (!j.success) {
      statusEl.textContent = '✗ ' + (j.error || 'Generation failed');
      btn.disabled = false;
      btn.textContent = '↻ Regenerate';
      return;
    }
    statusEl.textContent = '✓ Image regenerated!';
    showGenSceneSeed(j.seed);
    pushGenSceneHistory(j.image_path, j.seed, promptText);
    // Show preview
    const apiBase = genSceneMode === 'school' ? '/api/school/images/' : '/api/rp/images/';
    const preview = document.getElementById('genScenePreview');
    preview.src = apiBase + encodeURIComponent(j.image_path);
    document.getElementById('genScenePreviewWrap').style.display = 'block';

    // Set as previewed image (no auto-insert to chat)
    lastPreviewedImage = { filename: j.image_path, prompt: promptText, seed: j.seed };
    document.getElementById('genSceneInsertBtn').style.display = '';
    document.getElementById('genSceneBgBtn').style.display = '';

    btn.disabled = false;
    btn.textContent = '↻ Regenerate';
  } catch (err) {
    statusEl.textContent = '✗ Error: ' + err.message;
    btn.disabled = false;
    btn.textContent = '↻ Regenerate';
  }
}

function insertPreviewedToChat() {
  if (!lastPreviewedImage) return;
  const { filename, prompt, seed } = lastPreviewedImage;
  const promptText = prompt || '';

  if (genSceneMode === 'school') {
    const clientId = 'gen_' + (++_clientMsgCounter) + '_' + Date.now();
    lastGenSceneClientId = clientId;
    addMessage('user', '', null, false, null, clientId, filename, promptText);
    if (ws && ws.readyState === WebSocket.OPEN) {
      ws.send(JSON.stringify({ type: 'user_message', content: '', client_msg_id: clientId, image_path: filename, image_prompt: promptText, seed: seed }));
    }
  } else {
    const clientId = 'gen_' + (++_clientMsgCounter) + '_' + Date.now();
    lastGenSceneClientId = clientId;
    addRPMessage('user', '', null, null, clientId, filename, promptText);
    if (rpWS && rpWS.readyState === WebSocket.OPEN) {
      rpWS.send(JSON.stringify({ type: 'user_message', content: '', directed_to: null, client_msg_id: clientId, image_path: filename, image_prompt: promptText, seed: seed }));
    }
  }

  // Update preview to confirm insertion
  document.getElementById('genSceneStatus').textContent = '✓ Inserted to chat!';
  // Keep insert button visible in case user wants to insert again
}

// ── Send previewed ComfyUI image to background panel ──
async function sendPreviewedToBackground() {
  if (!lastPreviewedImage) return;
  const { filename } = lastPreviewedImage;

  try {
    // Move the file from generated pool to bg pool (renames on disk)
    const resp = await fetch('/api/images/' + encodeURIComponent(filename) + '/to-background', { method: 'POST' });
    const data = await resp.json();
    if (data.error) {
      document.getElementById('genSceneStatus').textContent = '✗ ' + data.error;
      return;
    }

    // Add to bg list, apply, and persist to session
    const list = getBgList();
    if (!list.includes(data.url)) {
      list.push(data.url);
      setBgList(list);
    }
    applyBgImage(data.url, null);
    persistSessionBg(data.url);
    renderBgPanel();

    // Reload gallery so the moved image disappears from generated thumbnails
    loadImageGallery();

    // Clear preview since the source file no longer exists at old path
    lastPreviewedImage = null;
    document.getElementById('genScenePreviewWrap').style.display = 'none';
    document.getElementById('genSceneInsertBtn').style.display = 'none';
    document.getElementById('genSceneBgBtn').style.display = 'none';
    document.getElementById('genSceneRegenBtn').style.display = 'none';

    document.getElementById('genSceneStatus').textContent = '✓ Moved to background!';
  } catch (err) {
    document.getElementById('genSceneStatus').textContent = '✗ Error: ' + err.message;
  }
}

// ── Gallery thumbnail click: fill fields + show preview ──
function previewGalleryImage(img) {
  const apiBase = genSceneMode === 'school' ? '/api/school/images/' : '/api/rp/images/';

  // Fill prompt field if available
  if (img.prompt) {
    document.getElementById('genScenePrompt').value = img.prompt;
  }
  if (img.negative_prompt) {
    document.getElementById('genSceneNegative').value = img.negative_prompt;
  }
  // Fill seed if available
  if (img.seed !== null && img.seed !== undefined) {
    document.getElementById('genSceneAutoSeed').checked = false;
    onGenSceneAutoSeedChange();
    document.getElementById('genSceneSeed').value = img.seed;
  }

  // Show preview
  const preview = document.getElementById('genScenePreview');
  preview.src = apiBase + encodeURIComponent(img.filename);
  document.getElementById('genScenePreviewWrap').style.display = 'block';

  // Set as last previewed image
  lastPreviewedImage = { filename: img.filename, prompt: img.prompt || '', seed: img.seed };
  document.getElementById('genSceneInsertBtn').style.display = '';
  document.getElementById('genSceneBgBtn').style.display = '';
  document.getElementById('genSceneRegenBtn').style.display = '';

  // Update status
  document.getElementById('genSceneStatus').textContent = '';
}

// ─── Boot (runs after all scripts loaded) ───────
loadCardList();
loadSchoolSessions();
