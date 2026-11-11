/**
 * Roomz client. One PCM stream from the server; this page only shifts playout.
 * MPD owns the queue. The seek bar shows MPD's elapsed time and never drives audio.
 *
 * ponytail: browser output delay past AudioContext.outputLatency is what the
 * speaker slider is for. A hidden tab is not a speaker. Upgrade path: a native client.
 */
(function () {
  'use strict';

  const WS_URL = `${location.protocol === 'https:' ? 'wss:' : 'ws:'}//${location.host}/ws`;
  const API_BASE = '';
  const DELAY_KEY = 'roomz.delayMs';
  const BASE_BUFFER_SEC = 0.3;
  const LOOKAHEAD_SEC = 0.15;
  const SYNC_SAMPLES = 5;
  const SYNC_GAP_MS = 40;

  let ws = null;
  let reconnectTimer = null;
  let syncInterval = null;
  let scheduleTimer = null;

  let clockOffsetMs = 0;
  let clockReady = false;
  let burstBestRtt = Infinity;
  let delayCompensationMs = 0;

  let playlist = [];
  let songId = null;
  let title = '';
  let artist = '';
  let playbackState = 'stop';
  let elapsed = 0;
  let duration = 0;
  let statusServerTs = 0;
  let repeatMode = 0;
  let randomOn = false;
  let seekHold = false;

  let streamRate = 44100;
  let streamChannels = 2;
  let frameSamples = 882;

  let audioCtx = null;
  let gain = null;
  let pending = [];
  let scheduled = [];
  let lastServerTs = null;

  let libraryPanelOpen = false;
  let chatPanelOpen = false;
  let scanInProgress = false;

  const $ = (id) => document.getElementById(id);
  const el = {
    wsStatus: $('ws-status'),
    delaySlider: $('delay-slider'),
    delayValue: $('delay-value'),
    nowTitle: $('now-title'),
    nowArtist: $('now-artist'),
    progressTime: $('progress-time'),
    seekBar: $('seek-bar'),
    remainingTime: $('remaining-time'),
    btnPlay: $('btn-play'),
    btnPrev: $('btn-prev'),
    btnNext: $('btn-next'),
    btnShuffle: $('btn-shuffle'),
    btnRepeat: $('btn-repeat'),
    volumeSlider: $('volume-slider'),
    libraryBase: $('library-base'),
    libraryError: $('library-error'),
    fileInput: $('file-input'),
    btnUpload: $('btn-upload'),
    btnScan: $('btn-scan'),
    playlist: $('playlist'),
    btnPlaylistClear: $('btn-playlist-clear'),
    panelLibrary: $('panel-library'),
    panelChat: $('panel-chat'),
    btnMenuLibrary: $('btn-menu-library'),
    btnMenuChat: $('btn-menu-chat'),
    libraryAllMedia: $('library-all-media'),
    chatMessages: $('chat-messages'),
    chatAuthor: $('chat-author'),
    chatInput: $('chat-input'),
    chatSend: $('chat-send'),
  };

  function reportError(context, err, userMessage) {
    console.error(context, err);
    if (userMessage != null && userMessage !== '' && el.libraryError) {
      el.libraryError.textContent = userMessage;
      el.libraryError.classList.remove('is-hidden');
    }
  }

  function hideLibraryError() {
    if (el.libraryError) el.libraryError.classList.add('is-hidden');
  }

  function setScanning(active) {
    if (el.btnScan) {
      el.btnScan.disabled = active;
      el.btnScan.textContent = active ? 'Scanning...' : 'Scan';
    }
  }

  function clientNowMs() {
    return performance.now() + clockOffsetMs;
  }

  function clientNowSeconds() {
    return clientNowMs() / 1000;
  }

  /** Seek-bar time. Extrapolated from the last MPD status, not from the audio clock. */
  function displayElapsed() {
    if (playbackState !== 'play' || !clockReady) return Math.max(0, elapsed);
    const pos = elapsed + (clientNowSeconds() - statusServerTs);
    if (duration > 0) return Math.min(Math.max(0, pos), duration);
    return Math.max(0, pos);
  }

  function formatTime(s) {
    if (s == null || !Number.isFinite(s) || s < 0) return '0:00';
    const m = Math.floor(s / 60);
    const sec = Math.floor(s % 60);
    return `${m}:${sec.toString().padStart(2, '0')}`;
  }

  function escapeHtml(s) {
    const div = document.createElement('div');
    div.textContent = s;
    return div.innerHTML;
  }

  function updateNowPlayingUI() {
    const pos = displayElapsed();
    el.nowTitle.textContent = title || '—';
    el.nowArtist.textContent = artist || '—';
    el.progressTime.textContent = formatTime(pos);
    el.remainingTime.textContent = duration > 0 ? formatTime(duration - pos) : '—';
    if (!seekHold && el.seekBar) {
      el.seekBar.max = duration > 0 ? duration : 0;
      el.seekBar.value = duration > 0 ? Math.min(pos, duration) : 0;
    }
    el.btnPlay.textContent = playbackState === 'play' ? '⏸' : '▶';
    el.btnPlay.classList.toggle('is-playing', playbackState === 'play');
    if (el.btnShuffle) el.btnShuffle.classList.toggle('is-active', randomOn);
    if (el.btnRepeat) {
      el.btnRepeat.classList.toggle('is-active', repeatMode > 0);
      el.btnRepeat.textContent = repeatMode === 1 ? '1' : repeatMode === 2 ? '∞' : '↻';
    }
  }

  function renderPlaylist() {
    el.playlist.innerHTML = '';
    playlist.forEach((item) => {
      const li = document.createElement('li');
      li.className = 'playlist-item';
      if (item.id === songId) li.classList.add('is-current');
      li.dataset.id = String(item.id);
      li.dataset.position = String(item.position);
      li.draggable = true;
      li.innerHTML = `
        <span class="num">${item.position + 1}</span>
        <span class="playlist-item-title-wrap"><span class="title">${escapeHtml(item.title || item.file || '—')}</span></span>
        <span class="dur">${formatTime(item.duration_seconds)}</span>
        <button type="button" class="remove-track" data-id="${item.id}">✕</button>
      `;
      li.querySelector('.remove-track').addEventListener('click', (e) => {
        e.stopPropagation();
        sendWs({ type: 'playlist_remove', id: item.id });
      });
      li.addEventListener('click', (e) => {
        if (e.target.classList.contains('remove-track')) return;
        sendWs({ type: 'play_id', id: item.id });
      });
      setupDragDrop(li);
      el.playlist.appendChild(li);
    });
  }

  function setupDragDrop(li) {
    li.addEventListener('dragstart', (e) => {
      li.classList.add('dragging');
      e.dataTransfer.setData('text/plain', li.dataset.id);
      e.dataTransfer.effectAllowed = 'move';
    });
    li.addEventListener('dragend', () => li.classList.remove('dragging'));
    li.addEventListener('dragover', (e) => {
      e.preventDefault();
      e.dataTransfer.dropEffect = 'move';
      li.classList.add('drag-over');
    });
    li.addEventListener('dragleave', () => li.classList.remove('drag-over'));
    li.addEventListener('drop', (e) => {
      e.preventDefault();
      li.classList.remove('drag-over');
      const fromId = parseInt(e.dataTransfer.getData('text/plain'), 10);
      const toIndex = parseInt(li.dataset.position, 10);
      const order = playlist.map((p) => p.id);
      const fromIndex = order.indexOf(fromId);
      if (fromIndex === -1 || fromIndex === toIndex) return;
      order.splice(fromIndex, 1);
      order.splice(toIndex, 0, fromId);
      sendWs({ type: 'playlist_reorder', order });
    });
  }

  function applyStatus(msg) {
    playbackState = msg.state || 'stop';
    elapsed = msg.elapsed || 0;
    duration = msg.duration || 0;
    statusServerTs = msg.server_timestamp || clientNowSeconds();
    songId = msg.song_id ?? null;
    title = msg.title || '';
    artist = msg.artist || '';
    randomOn = !!msg.random;
    repeatMode = msg.repeat || 0;
    playlist = msg.playlist || [];
    if (msg.stream) {
      streamRate = msg.stream.rate || streamRate;
      streamChannels = msg.stream.channels || streamChannels;
      frameSamples = msg.stream.frame_samples || frameSamples;
    }
    renderPlaylist();
    updateNowPlayingUI();
  }

  async function fetchLibrary() {
    try {
      const r = await fetch(`${API_BASE}/api/library`);
      if (!r.ok) {
        const err = await r.json().catch(() => ({ detail: r.statusText }));
        const msg = (err.detail != null ? String(err.detail) : r.statusText) || "Couldn't load library";
        reportError('Fetch library failed', err, msg);
        return;
      }
      hideLibraryError();
      const data = await r.json();
      if (el.libraryBase) el.libraryBase.textContent = data.music_directory || '';
      renderAllMedia(data.tracks || []);
    } catch (e) {
      reportError('Fetch library failed', e, "Couldn't load library");
    }
  }

  function renderAllMedia(tracks) {
    if (!el.libraryAllMedia) return;
    el.libraryAllMedia.innerHTML = '';
    const byFolder = {};
    tracks.forEach((t) => {
      const key = t.folder || '';
      if (!byFolder[key]) byFolder[key] = [];
      byFolder[key].push(t);
    });
    const folderKeys = Object.keys(byFolder).sort((a, b) => {
      if (a === '') return -1;
      if (b === '') return 1;
      return a.localeCompare(b);
    });
    folderKeys.forEach((folderKey) => {
      const folderTracks = byFolder[folderKey].sort((a, b) => {
        const na = (a.title || a.file || '').toLowerCase();
        const nb = (b.title || b.file || '').toLowerCase();
        return na.localeCompare(nb);
      });
      const folderBlock = document.createElement('div');
      folderBlock.className = 'library-folder';
      const header = document.createElement('div');
      header.className = 'library-folder-header';
      header.setAttribute('role', 'button');
      header.setAttribute('aria-expanded', 'true');
      header.innerHTML = `
        <span class="library-folder-chevron" aria-hidden="true">▼</span>
        <span class="library-folder-label">${escapeHtml(folderKey === '' ? '(root)' : folderKey)}</span>
      `;
      header.addEventListener('click', () => {
        const collapsed = folderBlock.classList.toggle('is-collapsed');
        header.setAttribute('aria-expanded', String(!collapsed));
      });
      const ul = document.createElement('ul');
      ul.className = 'list library-folder-list';
      folderTracks.forEach((t) => {
        const li = document.createElement('li');
        li.className = 'item';
        li.innerHTML = `
          <span class="item-title">${escapeHtml(t.title || t.file || '—')}</span>
          <span class="item-dur">${formatTime(t.duration_seconds)}</span>
          <button type="button" class="library-add">Add</button>
        `;
        li.querySelector('.library-add').addEventListener('click', (e) => {
          e.stopPropagation();
          sendWs({ type: 'playlist_add', uri: t.file });
        });
        li.addEventListener('click', (e) => {
          if (e.target.classList.contains('library-add')) return;
          sendWs({ type: 'play_uri', uri: t.file });
        });
        ul.appendChild(li);
      });
      folderBlock.appendChild(header);
      folderBlock.appendChild(ul);
      el.libraryAllMedia.appendChild(folderBlock);
    });
  }

  function sendWs(obj) {
    if (ws && ws.readyState === WebSocket.OPEN) ws.send(JSON.stringify(obj));
  }

  function startClockSync() {
    burstBestRtt = Infinity;
    for (let i = 0; i < SYNC_SAMPLES; i++) {
      setTimeout(() => {
        sendWs({ type: 'sync', client_time: performance.now() });
      }, i * SYNC_GAP_MS);
    }
  }

  function onSyncResponse(msg) {
    const now = performance.now();
    const rtt = now - msg.client_time;
    if (!(rtt >= 0) || rtt >= burstBestRtt) return;
    burstBestRtt = rtt;
    clockOffsetMs = msg.server_utc * 1000 - (msg.client_time + rtt / 2);
    clockReady = true;
  }

  function connectWs() {
    if (ws) ws.close();
    el.wsStatus.textContent = 'Connecting…';
    ws = new WebSocket(WS_URL);
    ws.binaryType = 'arraybuffer';
    ws.onopen = () => {
      el.wsStatus.textContent = 'Connected';
      startClockSync();
      if (syncInterval) clearInterval(syncInterval);
      syncInterval = setInterval(startClockSync, 5000);
    };
    ws.onclose = () => {
      el.wsStatus.textContent = 'Disconnected';
      if (syncInterval) clearInterval(syncInterval);
      reconnectTimer = setTimeout(connectWs, 2000);
    };
    ws.onmessage = (event) => {
      if (typeof event.data !== 'string') {
        onPcm(event.data);
        return;
      }
      try {
        handleWsMessage(JSON.parse(event.data));
      } catch (err) {
        reportError('WS message parse', err);
      }
    };
  }

  function handleWsMessage(msg) {
    const typ = msg.type;
    if (typ === 'status') {
      applyStatus(msg);
      return;
    }
    if (typ === 'sync_response') {
      onSyncResponse(msg);
      return;
    }
    if (typ === 'chat') {
      const ul = el.chatMessages;
      const li = document.createElement('li');
      li.className = 'chat-message';
      li.textContent = `[${msg.author}]: ${msg.text}`;
      ul.appendChild(li);
      ul.scrollTop = ul.scrollHeight;
      return;
    }
    if (typ === 'error' && msg.detail) {
      reportError('MPD', msg.detail, String(msg.detail));
    }
  }

  function ensureAudio() {
    if (audioCtx) return;
    const Ctx = window.AudioContext || window.webkitAudioContext;
    audioCtx = new Ctx();
    gain = audioCtx.createGain();
    gain.gain.value = el.volumeSlider ? el.volumeSlider.value / 100 : 1;
    gain.connect(audioCtx.destination);
  }

  function unlockAudio() {
    ensureAudio();
    if (audioCtx.state === 'suspended') audioCtx.resume();
  }

  function playAtFor(serverTs) {
    const outputLatency = audioCtx.outputLatency || audioCtx.baseLatency || 0;
    const serverNow = clientNowSeconds();
    return (
      audioCtx.currentTime +
      (serverTs - serverNow) -
      outputLatency +
      BASE_BUFFER_SEC +
      delayCompensationMs / 1000
    );
  }

  function pcmToBuffer(arrayBuffer) {
    const view = new Int16Array(arrayBuffer, 8);
    const buffer = audioCtx.createBuffer(streamChannels, frameSamples, streamRate);
    for (let channel = 0; channel < streamChannels; channel++) {
      const out = buffer.getChannelData(channel);
      for (let i = 0; i < frameSamples; i++) {
        out[i] = view[i * streamChannels + channel] / 32768;
      }
    }
    return buffer;
  }

  function flushAudio() {
    pending = [];
    for (const item of scheduled) {
      try {
        item.src.stop();
      } catch (err) {
        /* already stopped */
      }
    }
    scheduled = [];
  }

  function schedulePending() {
    if (!audioCtx) return;
    const now = audioCtx.currentTime;
    const still = [];
    for (const item of pending) {
      const when = playAtFor(item.serverTs);
      if (when < now) continue;
      if (when - now > LOOKAHEAD_SEC) {
        still.push(item);
        continue;
      }
      const src = audioCtx.createBufferSource();
      src.buffer = item.buffer;
      src.connect(gain);
      src.start(when);
      const entry = { src, when, serverTs: item.serverTs, buffer: item.buffer };
      scheduled.push(entry);
      src.onended = () => {
        const index = scheduled.indexOf(entry);
        if (index >= 0) scheduled.splice(index, 1);
      };
    }
    pending = still;
  }

  function onPcm(arrayBuffer) {
    ensureAudio();
    const needed = 8 + frameSamples * streamChannels * 2;
    if (arrayBuffer.byteLength < needed) return;
    const serverTs = new DataView(arrayBuffer).getFloat64(0, true);
    const frameSec = frameSamples / streamRate;
    if (lastServerTs != null && Math.abs(serverTs - (lastServerTs + frameSec)) > frameSec * 1.5) {
      flushAudio();
    }
    lastServerTs = serverTs;
    pending.push({ serverTs, buffer: pcmToBuffer(arrayBuffer) });
    if (!clockReady) {
      const maxPending = Math.ceil(BASE_BUFFER_SEC / frameSec) + 2;
      if (pending.length > maxPending) pending.splice(0, pending.length - maxPending);
      return;
    }
    schedulePending();
  }

  function onDelayInput() {
    delayCompensationMs = parseInt(el.delaySlider.value, 10) || 0;
    el.delayValue.textContent = String(delayCompensationMs);
    localStorage.setItem(DELAY_KEY, String(delayCompensationMs));
    if (!audioCtx) return;
    const now = audioCtx.currentTime;
    const keep = [];
    for (const item of scheduled) {
      if (item.when > now + 0.02) {
        try {
          item.src.stop();
        } catch (err) {
          /* already started */
        }
        pending.push({ serverTs: item.serverTs, buffer: item.buffer });
      } else {
        keep.push(item);
      }
    }
    scheduled = keep;
    schedulePending();
  }

  function uiLoop() {
    updateNowPlayingUI();
    requestAnimationFrame(uiLoop);
  }

  document.addEventListener('pointerdown', unlockAudio);
  document.addEventListener('visibilitychange', () => {
    if (document.visibilityState === 'visible') startClockSync();
  });
  window.addEventListener('online', () => connectWs());
  window.addEventListener('error', (event) => {
    reportError('Uncaught error', event.error || event.message);
  });
  window.addEventListener('unhandledrejection', (event) => {
    reportError('Unhandled promise rejection', event.reason);
  });

  if (el.btnMenuLibrary && el.panelLibrary) {
    el.btnMenuLibrary.addEventListener('click', () => {
      libraryPanelOpen = !libraryPanelOpen;
      el.panelLibrary.classList.toggle('is-hidden', !libraryPanelOpen);
      el.btnMenuLibrary.classList.toggle('is-active', libraryPanelOpen);
      if (libraryPanelOpen) fetchLibrary();
    });
  }
  if (el.btnMenuChat && el.panelChat) {
    el.btnMenuChat.addEventListener('click', () => {
      chatPanelOpen = !chatPanelOpen;
      el.panelChat.classList.toggle('is-hidden', !chatPanelOpen);
      el.btnMenuChat.classList.toggle('is-active', chatPanelOpen);
    });
  }
  if (el.btnPlaylistClear) {
    el.btnPlaylistClear.addEventListener('click', () => sendWs({ type: 'playlist_clear' }));
  }

  el.btnPlay.addEventListener('click', () => {
    unlockAudio();
    sendWs({ type: playbackState === 'play' ? 'pause' : 'play' });
  });
  el.btnPrev.addEventListener('click', () => sendWs({ type: 'previous' }));
  el.btnNext.addEventListener('click', () => sendWs({ type: 'next' }));
  el.btnShuffle.addEventListener('click', () => sendWs({ type: 'random', on: !randomOn }));
  el.btnRepeat.addEventListener('click', () => {
    sendWs({ type: 'repeat', mode: (repeatMode + 1) % 3 });
  });
  el.seekBar.addEventListener('pointerdown', () => {
    seekHold = true;
  });
  el.seekBar.addEventListener('change', () => {
    seekHold = false;
    sendWs({ type: 'seek', position_seconds: parseFloat(el.seekBar.value) });
  });
  el.volumeSlider.addEventListener('input', () => {
    if (gain) gain.gain.value = el.volumeSlider.value / 100;
  });
  el.delaySlider.addEventListener('input', onDelayInput);

  const savedDelay = parseInt(localStorage.getItem(DELAY_KEY) || '0', 10);
  if (Number.isFinite(savedDelay)) {
    delayCompensationMs = Math.max(-250, Math.min(250, savedDelay));
    el.delaySlider.value = String(delayCompensationMs);
    el.delayValue.textContent = String(delayCompensationMs);
  }

  el.btnUpload.addEventListener('click', () => el.fileInput.click());
  el.fileInput.addEventListener('change', async () => {
    const files = el.fileInput.files;
    if (!files || !files.length) return;
    for (const file of files) {
      const fd = new FormData();
      fd.append('file', file);
      try {
        const r = await fetch(`${API_BASE}/api/upload`, { method: 'POST', body: fd });
        if (!r.ok) {
          const err = await r.json().catch(() => ({ detail: r.statusText }));
          reportError('Upload failed', err, (err.detail != null ? String(err.detail) : r.statusText) || 'Upload failed');
          continue;
        }
        hideLibraryError();
      } catch (e) {
        reportError('Upload failed', e, 'Upload failed');
      }
    }
    el.fileInput.value = '';
    fetchLibrary();
  });

  el.btnScan.addEventListener('click', async () => {
    if (scanInProgress) return;
    scanInProgress = true;
    setScanning(true);
    try {
      const r = await fetch(`${API_BASE}/api/scan`, { method: 'POST' });
      if (!r.ok) {
        const err = await r.json().catch(() => ({ detail: r.statusText }));
        const msg = (r.status === 409 ? (err.detail ?? 'Scan already in progress') : (err.detail != null ? String(err.detail) : r.statusText)) || 'Scan failed';
        reportError('Scan failed', err, msg);
        return;
      }
      hideLibraryError();
      await fetchLibrary();
    } catch (e) {
      reportError('Scan failed', e, 'Scan failed');
    } finally {
      scanInProgress = false;
      setScanning(false);
    }
  });

  el.chatSend.addEventListener('click', () => {
    const author = el.chatAuthor.value.trim();
    const text = el.chatInput.value.trim();
    if (!text) return;
    sendWs({ type: 'chat', author: author || undefined, text });
    el.chatInput.value = '';
  });
  el.chatInput.addEventListener('keydown', (e) => {
    if (e.key === 'Enter') el.chatSend.click();
  });

  connectWs();
  scheduleTimer = setInterval(schedulePending, 20);
  uiLoop();
})();
