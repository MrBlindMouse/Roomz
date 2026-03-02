/**
 * Roomz — LAN-synchronized single-stream audio client.
 * WebSocket state sync, clock sync, Web Audio playback, playlist, chat.
 */
(function () {
  'use strict';

  const WS_URL = `${location.protocol === 'https:' ? 'wss:' : 'ws:'}//${location.host}/ws`;
  const API_BASE = '';

  let ws = null;
  let reconnectTimer = null;
  let syncInterval = null;

  let clockOffsetMs = 0;
  let delayCompensationMs = 0;

  let playlist = [];
  let currentTrackId = null;
  let currentTrackFilename = null;
  let isPlaying = false;
  let positionSeconds = 0;
  let lastUpdateServerTimestamp = 0;
  let repeatMode = 0; // 0 off, 1 one, 2 all
  let shuffle = false;

  let audioContext = null;
  let currentBuffer = null;
  let currentSource = null;
  let startedAt = 0;
  let bufferOffset = 0;
  let audioFallback = null;
  let useFallback = false;
  let fallbackCorrectionInterval = null;
  let gainNode = null;

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
    fileInput: $('file-input'),
    btnUpload: $('btn-upload'),
    btnScan: $('btn-scan'),
    playlist: $('playlist'),
    chatMessages: $('chat-messages'),
    chatInput: $('chat-input'),
    chatSend: $('chat-send'),
  };

  function clientNowMs() {
    return performance.now() + clockOffsetMs;
  }

  function clientNowSeconds() {
    return clientNowMs() / 1000;
  }

  /** Compute local playback position from server state + clock + delay. */
  function computedPositionSeconds() {
    const serverTs = lastUpdateServerTimestamp;
    const now = clientNowSeconds();
    const delaySec = delayCompensationMs / 1000;
    return positionSeconds + (now - serverTs) + delaySec;
  }

  function formatTime(s) {
    if (s == null || !Number.isFinite(s) || s < 0) return '0:00';
    const m = Math.floor(s / 60);
    const sec = Math.floor(s % 60);
    return `${m}:${sec.toString().padStart(2, '0')}`;
  }

  function getTrackById(trackId) {
    return playlist.find((p) => p.track_id === trackId);
  }

  function getCurrentTrack() {
    return currentTrackId ? getTrackById(currentTrackId) : null;
  }

  function updateNowPlayingUI() {
    const track = getCurrentTrack();
    if (track) {
      el.nowTitle.textContent = track.title || track.filename || '—';
      el.nowArtist.textContent = track.artist || '—';
      const dur = track.duration_seconds;
      el.remainingTime.textContent = Number.isFinite(dur) ? formatTime(dur - positionSeconds) : '—';
    } else {
      el.nowTitle.textContent = '—';
      el.nowArtist.textContent = '—';
      el.remainingTime.textContent = '0:00';
    }
    let pos = computedPositionSeconds();
    if (useFallback && audioFallback) pos = audioFallback.currentTime;
    else if (currentSource && audioContext && startedAt > 0) pos = bufferOffset + (audioContext.currentTime - startedAt);
    el.progressTime.textContent = formatTime(pos);
    const track2 = getCurrentTrack();
    const max = track2 && Number.isFinite(track2.duration_seconds) ? track2.duration_seconds : 100;
    el.seekBar.max = max;
    el.seekBar.value = Math.min(pos, max);
  }

  function renderPlaylist() {
    el.playlist.innerHTML = '';
    playlist.forEach((item) => {
      const li = document.createElement('li');
      li.className = 'flex items-center gap-2 p-2 hover:bg-zinc-700/50';
      li.dataset.trackId = String(item.track_id);
      li.dataset.position = String(item.position);
      li.draggable = true;
      li.innerHTML = `
        <span class="text-zinc-500 w-6">${item.position + 1}</span>
        <span class="flex-1 truncate">${(item.title || item.filename || '—')}</span>
        <span class="text-zinc-500 text-sm">${formatTime(item.duration_seconds)}</span>
        <button type="button" class="remove-track p-1 rounded hover:bg-zinc-600 text-zinc-400" data-track-id="${item.track_id}">✕</button>
      `;
      li.querySelector('.remove-track').addEventListener('click', (e) => {
        e.stopPropagation();
        sendWs({ type: 'playlist_remove', track_id: item.track_id });
      });
      li.addEventListener('click', (e) => {
        if (e.target.classList.contains('remove-track')) return;
        sendWs({ type: 'set_track', track_id: item.track_id });
      });
      setupDragDrop(li);
      el.playlist.appendChild(li);
    });
  }

  function setupDragDrop(li) {
    li.addEventListener('dragstart', (e) => {
      li.classList.add('dragging');
      e.dataTransfer.setData('text/plain', li.dataset.trackId);
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
      const order = playlist.map((p) => p.track_id);
      const fromIndex = order.indexOf(fromId);
      if (fromIndex === -1 || fromIndex === toIndex) return;
      order.splice(fromIndex, 1);
      order.splice(toIndex, 0, fromId);
      sendWs({ type: 'playlist_reorder', order });
    });
  }

  function sendWs(obj) {
    if (ws && ws.readyState === WebSocket.OPEN) ws.send(JSON.stringify(obj));
  }

  function startClockSync() {
    const clientTime = performance.now();
    sendWs({ type: 'sync', client_time: clientTime });
  }

  function connectWs() {
    if (ws) ws.close();
    el.wsStatus.textContent = 'Connecting…';
    ws = new WebSocket(WS_URL);
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
      try {
        const msg = JSON.parse(event.data);
        handleWsMessage(msg);
      } catch (_) {}
    };
  }

  function handleWsMessage(msg) {
    const typ = msg.type;
    if (typ === 'state_snapshot') {
      playlist = msg.playlist || [];
      const s = msg.state || {};
      currentTrackId = s.current_track_id ?? null;
      currentTrackFilename = msg.current_track_filename ?? null;
      isPlaying = s.is_playing ?? false;
      positionSeconds = s.position_seconds ?? 0;
      lastUpdateServerTimestamp = s.last_update_server_timestamp ?? 0;
      renderPlaylist();
      applyPlaybackState();
      updateNowPlayingUI();
      return;
    }
    if (typ === 'sync_response') {
      const now = performance.now();
      const clientTime = msg.client_time;
      const serverUtc = msg.server_utc;
      const rtt = now - clientTime;
      clockOffsetMs = serverUtc * 1000 - (clientTime + rtt / 2);
      return;
    }
    if (typ === 'play' || typ === 'pause' || typ === 'seek' || typ === 'set_track') {
      positionSeconds = msg.position ?? 0;
      lastUpdateServerTimestamp = msg.server_timestamp ?? 0;
      currentTrackId = msg.track_id ?? currentTrackId;
      isPlaying = msg.is_playing ?? typ === 'play' || typ === 'set_track';
      applyPlaybackState();
      updateNowPlayingUI();
      return;
    }
    if (typ === 'chat') {
      const ul = el.chatMessages;
      const li = document.createElement('li');
      li.className = 'break-words';
      li.textContent = `[${msg.author}]: ${msg.text}`;
      ul.appendChild(li);
      ul.scrollTop = ul.scrollHeight;
      return;
    }
    if (typ === 'playlist_updated') {
      playlist = msg.playlist || [];
      renderPlaylist();
      updateNowPlayingUI();
    }
  }

  function applyPlaybackState() {
    const pos = Math.max(0, computedPositionSeconds());
    const track = getCurrentTrack();
    if (!track) {
      stopPlayback();
      return;
    }
    if (useFallback) {
      if (audioFallback) {
        audioFallback.currentTime = pos;
        if (isPlaying) audioFallback.play().catch(() => {});
        else audioFallback.pause();
      } else {
        startFallbackAudio(track.filename, pos);
      }
      return;
    }
    if (!audioContext) {
      audioContext = new (window.AudioContext || window.webkitAudioContext)();
      gainNode = audioContext.createGain();
      gainNode.connect(audioContext.destination);
      gainNode.gain.value = el.volumeSlider.value / 100;
      window._roomzGainNode = gainNode;
    }
    if (currentTrackId && currentBuffer && currentTrackFilename === track.filename) {
      if (isPlaying) {
        const duration = (currentBuffer.duration || 0) - pos;
        if (duration > 0) {
          startSourceAt(audioContext.currentTime, pos, duration);
        }
      } else {
        stopSource();
        positionSeconds = pos;
        lastUpdateServerTimestamp = clientNowSeconds();
      }
    } else {
      loadAndPlayTrack(track.filename, pos, isPlaying);
    }
  }

  function startSourceAt(when, offset, duration) {
    stopSource();
    const source = audioContext.createBufferSource();
    source.buffer = currentBuffer;
    source.connect(gainNode || audioContext.destination);
    source.start(when, offset, duration);
    currentSource = source;
    startedAt = when;
    bufferOffset = offset;
    source.onended = () => {
      if (!currentSource) return;
      currentSource = null;
      const elapsed = audioContext.currentTime - startedAt;
      const newPos = bufferOffset + elapsed;
      if (repeatMode === 1) {
        sendWs({ type: 'seek', position_seconds: 0 });
        sendWs({ type: 'play' });
      } else if (repeatMode === 2 || (playlist.length && getNextTrack())) {
        const next = getNextTrack();
        if (next) sendWs({ type: 'set_track', track_id: next.track_id });
        else sendWs({ type: 'seek', position_seconds: 0 });
      } else {
        sendWs({ type: 'pause' });
        sendWs({ type: 'seek', position_seconds: 0 });
      }
    };
  }

  function getNextTrack() {
    if (!playlist.length) return null;
    const idx = playlist.findIndex((p) => p.track_id === currentTrackId);
    const nextIdx = shuffle ? Math.floor(Math.random() * playlist.length) : (idx + 1) % playlist.length;
    return playlist[nextIdx] || null;
  }

  function getPrevTrack() {
    if (!playlist.length) return null;
    const idx = playlist.findIndex((p) => p.track_id === currentTrackId);
    const prevIdx = idx <= 0 ? playlist.length - 1 : idx - 1;
    return playlist[prevIdx] || null;
  }

  function stopSource() {
    if (currentSource) {
      try {
        currentSource.stop();
      } catch (_) {}
      currentSource = null;
    }
  }

  function stopPlayback() {
    stopSource();
    if (audioFallback) {
      audioFallback.pause();
      audioFallback.src = '';
    }
  }

  async function loadAndPlayTrack(filename, offset, play) {
    stopSource();
    const url = `${API_BASE}/music/${encodeURIComponent(filename)}`;
    try {
      const res = await fetch(url);
      if (!res.ok) throw new Error(res.statusText);
      const buf = await res.arrayBuffer();
      currentBuffer = await audioContext.decodeAudioData(buf);
      currentTrackFilename = filename;
      const duration = currentBuffer.duration - offset;
      if (play && duration > 0) {
        startSourceAt(audioContext.currentTime, offset, duration);
      } else {
        positionSeconds = offset;
        lastUpdateServerTimestamp = clientNowSeconds();
      }
    } catch (e) {
      console.warn('Web Audio failed, using fallback', e);
      useFallback = true;
      if (!audioFallback) audioFallback = new Audio();
      startFallbackAudio(filename, offset);
      if (isPlaying) audioFallback.play().catch(() => {});
    }
    updateNowPlayingUI();
  }

  function startFallbackAudio(filename, offset) {
    if (!audioFallback) audioFallback = new Audio();
    audioFallback.src = `${API_BASE}/music/${encodeURIComponent(filename)}`;
    audioFallback.currentTime = offset;
    if (isPlaying) audioFallback.play().catch(() => {});
    if (!fallbackCorrectionInterval) {
      fallbackCorrectionInterval = setInterval(() => {
        if (!audioFallback || !currentTrackId) return;
        const expected = computedPositionSeconds();
        const drift = Math.abs(audioFallback.currentTime - expected);
        if (drift > 0.5) audioFallback.currentTime = expected;
        updateNowPlayingUI();
      }, 500);
    }
  }

  function uiLoop() {
    updateNowPlayingUI();
    requestAnimationFrame(uiLoop);
  }

  document.addEventListener('visibilitychange', () => {
    if (document.visibilityState === 'visible') startClockSync();
  });
  window.addEventListener('online', () => connectWs());

  el.btnPlay.addEventListener('click', () => {
    if (isPlaying) sendWs({ type: 'pause' });
    else sendWs({ type: 'play' });
  });
  el.btnPrev.addEventListener('click', () => {
    const prev = getPrevTrack();
    if (prev) sendWs({ type: 'set_track', track_id: prev.track_id });
    else sendWs({ type: 'seek', position_seconds: 0 });
  });
  el.btnNext.addEventListener('click', () => {
    const next = getNextTrack();
    if (next) sendWs({ type: 'set_track', track_id: next.track_id });
  });
  el.btnShuffle.addEventListener('click', () => {
    shuffle = !shuffle;
    el.btnShuffle.classList.toggle('text-amber-500', shuffle);
  });
  el.btnRepeat.addEventListener('click', () => {
    repeatMode = (repeatMode + 1) % 3;
    el.btnRepeat.classList.toggle('text-amber-500', repeatMode > 0);
    el.btnRepeat.textContent = repeatMode === 1 ? '1' : repeatMode === 2 ? '∞' : '↻';
  });
  el.seekBar.addEventListener('change', () => {
    const val = parseFloat(el.seekBar.value);
    sendWs({ type: 'seek', position_seconds: val });
  });
  el.volumeSlider.addEventListener('input', () => {
    const v = el.volumeSlider.value / 100;
    if (audioFallback) audioFallback.volume = v;
    if (gainNode) gainNode.gain.value = v;
  });
  el.delaySlider.addEventListener('input', () => {
    delayCompensationMs = parseInt(el.delaySlider.value, 10);
    el.delayValue.textContent = delayCompensationMs;
  });

  el.btnUpload.addEventListener('click', () => el.fileInput.click());
  el.fileInput.addEventListener('change', async () => {
    const files = el.fileInput.files;
    if (!files?.length) return;
    for (const file of files) {
      const fd = new FormData();
      fd.append('file', file);
      try {
        const r = await fetch(`${API_BASE}/api/upload`, { method: 'POST', body: fd });
        if (!r.ok) throw new Error(await r.text());
        const data = await r.json();
        sendWs({ type: 'playlist_add', track_id: data.id });
      } catch (e) {
        console.error('Upload failed', e);
      }
    }
    el.fileInput.value = '';
  });
  el.btnScan.addEventListener('click', async () => {
    try {
      const r = await fetch(`${API_BASE}/api/scan`, { method: 'POST' });
      if (!r.ok) throw new Error(await r.text());
      const data = await r.json();
          if (data.added > 0) {
        const list = await fetch(`${API_BASE}/api/playlist`).then((res) => res.json());
        playlist = list;
        renderPlaylist();
      }
    } catch (e) {
      console.error('Scan failed', e);
    }
  });

  el.chatSend.addEventListener('click', () => {
    const text = el.chatInput.value.trim();
    if (!text) return;
    sendWs({ type: 'chat', text });
    el.chatInput.value = '';
  });
  el.chatInput.addEventListener('keydown', (e) => {
    if (e.key === 'Enter') el.chatSend.click();
  });

  connectWs();
  uiLoop();
})();
