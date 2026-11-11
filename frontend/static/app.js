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
  const SPEAKER_KEY = 'roomz.speaker';
  const BASE_BUFFER_SEC = 0.6;
  const LOOKAHEAD_SEC = 0.2;
  const CATCHUP_SEC = 0.35;
  const SYNC_SAMPLES = 5;
  const SYNC_GAP_MS = 40;
  const SYNC_IGNORE_MS = 5;
  const SYNC_SNAP_MS = 150;

  let ws = null;
  let reconnectTimer = null;
  let syncInterval = null;
  let scheduleTimer = null;

  let clockOffsetMs = 0;
  let clockReady = false;
  let catchupUntil = 0;
  let burstBestRtt = Infinity;
  let delayCompensationMs = 0;

  let playlist = [];
  let storedPlaylist = '';
  let storedPlaylists = [];
  let renderingPlaylistSelect = false;
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

  let libraryTracks = [];
  let libraryPanelOpen = false;
  let chatPanelOpen = false;
  let scanInProgress = false;
  let wakeLock = null;

  const $ = (id) => document.getElementById(id);
  const el = {
    wsStatus: $('ws-status'),
    syncHealth: $('sync-health'),
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
    statusBanner: $('status-banner'),
    homeError: $('home-error'),
    fileInput: $('file-input'),
    librarySearch: $('library-search'),
    uploadFolder: $('upload-folder'),
    btnUpload: $('btn-upload'),
    btnScan: $('btn-scan'),
    playlist: $('playlist'),
    btnPlaylistClear: $('btn-playlist-clear'),
    playlistSelect: $('playlist-select'),
    playlistNewName: $('playlist-new-name'),
    btnPlaylistNew: $('btn-playlist-new'),
    btnPlaylistDelete: $('btn-playlist-delete'),
    panelLibrary: $('panel-library'),
    panelChat: $('panel-chat'),
    btnMenuLibrary: $('btn-menu-library'),
    btnMenuChat: $('btn-menu-chat'),
    btnSpeaker: $('btn-speaker'),
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

  function showHomeError(message) {
    if (!el.homeError) return;
    el.homeError.textContent = message;
    el.homeError.classList.remove('is-hidden');
  }

  function hideHomeError() {
    if (el.homeError) el.homeError.classList.add('is-hidden');
  }

  async function refreshHealth() {
    if (!el.statusBanner) return;
    try {
      const response = await fetch(`${API_BASE}/health`);
      const body = await response.json();
      const problems = Array.isArray(body.problems) ? body.problems : [];
      if (!problems.length) {
        el.statusBanner.textContent = '';
        el.statusBanner.classList.add('is-hidden');
        return;
      }
      el.statusBanner.textContent = problems.join(' ');
      el.statusBanner.classList.remove('is-hidden');
    } catch (err) {
      console.error('Health check', err);
      el.statusBanner.textContent = 'Cannot reach the Roomz server.';
      el.statusBanner.classList.remove('is-hidden');
    }
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
    storedPlaylist = msg.stored_playlist || '';
    storedPlaylists = Array.isArray(msg.stored_playlists) ? msg.stored_playlists : [];
    renderStoredPlaylists();
    if (msg.stream) {
      streamRate = msg.stream.rate || streamRate;
      streamChannels = msg.stream.channels || streamChannels;
      frameSamples = msg.stream.frame_samples || frameSamples;
    }
    renderPlaylist();
    updateNowPlayingUI();
    updateMediaSession();
    syncWakeLock();
  }

  function updateMediaSession() {
    if (!('mediaSession' in navigator)) return;
    const session = navigator.mediaSession;
    session.metadata = new MediaMetadata({
      title: title || 'Roomz',
      artist: artist || 'Roomz',
    });
    session.playbackState = playbackState === 'play' ? 'playing' : 'paused';
    if (!(duration > 0)) return;
    try {
      session.setPositionState({
        duration,
        playbackRate: playbackState === 'play' ? 1 : 0,
        position: Math.min(Math.max(0, elapsed), duration),
      });
    } catch (err) {
      /* A browser rejects the position when duration is still settling. */
    }
  }

  async function syncWakeLock() {
    if (!('wakeLock' in navigator)) return;
    const want = playbackState === 'play' && document.visibilityState === 'visible';
    if (!want) {
      if (!wakeLock) return;
      try {
        await wakeLock.release();
      } catch (err) {
        /* Already released by the browser. */
      }
      wakeLock = null;
      return;
    }
    if (wakeLock) return;
    try {
      wakeLock = await navigator.wakeLock.request('screen');
      wakeLock.addEventListener('release', () => {
        wakeLock = null;
      });
    } catch (err) {
      console.error('Wake lock', err);
    }
  }

  function bindMediaSession() {
    if (!('mediaSession' in navigator) || !navigator.mediaSession.setActionHandler) return;
    const session = navigator.mediaSession;
    session.setActionHandler('play', () => {
      unlockAudio();
      sendWs({ type: 'play' });
    });
    session.setActionHandler('pause', () => sendWs({ type: 'pause' }));
    session.setActionHandler('previoustrack', () => sendWs({ type: 'previous' }));
    session.setActionHandler('nexttrack', () => sendWs({ type: 'next' }));
  }

  async function setSpeakerMode(on) {
    document.body.classList.toggle('is-speaker', on);
    if (el.btnSpeaker) el.btnSpeaker.classList.toggle('is-active', on);
    localStorage.setItem(SPEAKER_KEY, on ? '1' : '0');
    if (!on && document.fullscreenElement) {
      try {
        await document.exitFullscreen();
      } catch (err) {
        /* Fullscreen was already left. */
      }
    }
    if (on && document.fullscreenEnabled && !document.fullscreenElement) {
      try {
        await document.documentElement.requestFullscreen();
      } catch (err) {
        /* The browser refused fullscreen. The layout still applies. */
      }
    }
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
    libraryTracks = tracks || [];
    paintLibrary();
  }

  function trackMatches(track, query) {
    if (!query) return true;
    const hay = [track.title, track.artist, track.album, track.file]
      .filter(Boolean)
      .join('\n')
      .toLowerCase();
    return hay.includes(query);
  }

  function paintLibrary() {
    if (!el.libraryAllMedia) return;
    const query = (el.librarySearch && el.librarySearch.value.trim().toLowerCase()) || '';
    const tracks = libraryTracks.filter((track) => trackMatches(track, query));
    el.libraryAllMedia.innerHTML = '';
    if (!tracks.length) {
      const empty = document.createElement('p');
      empty.className = 'library-empty';
      empty.textContent = query ? 'No matches' : 'Library is empty';
      el.libraryAllMedia.appendChild(empty);
      return;
    }
    const folders = new Map();
    tracks.forEach((track) => {
      const folder = track.folder || '';
      const album = track.album || '';
      if (!folders.has(folder)) folders.set(folder, new Map());
      const albums = folders.get(folder);
      if (!albums.has(album)) albums.set(album, []);
      albums.get(album).push(track);
    });
    const folderKeys = [...folders.keys()].sort((a, b) => {
      if (a === '') return -1;
      if (b === '') return 1;
      return a.localeCompare(b);
    });
    folderKeys.forEach((folderKey) => {
      const folderBlock = folderGroup(
        folderKey === '' ? '(root)' : folderKey,
        folderKey === '' ? '/' : folderKey
      );
      const albums = folders.get(folderKey);
      [...albums.keys()].sort((a, b) => a.localeCompare(b)).forEach((albumKey) => {
        const songs = albums.get(albumKey).sort((a, b) => {
          const left = (a.title || a.file || '').toLowerCase();
          const right = (b.title || b.file || '').toLowerCase();
          return left.localeCompare(right);
        });
        const host = albumKey ? folderGroup(albumKey) : folderBlock;
        const ul = document.createElement('ul');
        ul.className = 'list library-folder-list';
        songs.forEach((track) => ul.appendChild(libraryRow(track)));
        host.appendChild(ul);
        if (albumKey) folderBlock.appendChild(host);
      });
      el.libraryAllMedia.appendChild(folderBlock);
    });
  }

  function folderGroup(label, playUri) {
    const block = document.createElement('div');
    block.className = 'library-folder';
    const header = document.createElement('div');
    header.className = 'library-folder-header';
    header.setAttribute('role', 'button');
    header.setAttribute('aria-expanded', 'true');
    header.innerHTML = `
      <span class="library-folder-chevron" aria-hidden="true">▼</span>
      <span class="library-folder-label">${escapeHtml(label)}</span>
    `;
    header.addEventListener('click', () => {
      const collapsed = block.classList.toggle('is-collapsed');
      header.setAttribute('aria-expanded', String(!collapsed));
    });
    if (playUri != null) {
      const play = document.createElement('button');
      play.type = 'button';
      play.className = 'library-play';
      play.textContent = 'Play';
      play.addEventListener('click', (event) => {
        event.stopPropagation();
        sendWs({ type: 'play_folder', uri: playUri });
      });
      header.appendChild(play);
    }
    block.appendChild(header);
    return block;
  }

  function renderStoredPlaylists() {
    if (!el.playlistSelect) return;
    renderingPlaylistSelect = true;
    const selected = storedPlaylists.includes(storedPlaylist) ? storedPlaylist : '';
    el.playlistSelect.innerHTML = '';
    const unsaved = document.createElement('option');
    unsaved.value = '';
    unsaved.textContent = 'Unsaved queue';
    el.playlistSelect.appendChild(unsaved);
    storedPlaylists.forEach((name) => {
      const option = document.createElement('option');
      option.value = name;
      option.textContent = name;
      el.playlistSelect.appendChild(option);
    });
    el.playlistSelect.value = selected;
    renderingPlaylistSelect = false;
  }

  function libraryRow(track) {
    const li = document.createElement('li');
    li.className = 'item';
    const meta = track.artist ? `<span class="item-meta">${escapeHtml(track.artist)}</span>` : '';
    li.innerHTML = `
      <span class="item-title">${escapeHtml(track.title || track.file || '—')}</span>
      ${meta}
      <span class="item-dur">${formatTime(track.duration_seconds)}</span>
      <button type="button" class="library-add">Add</button>
    `;
    li.querySelector('.library-add').addEventListener('click', (e) => {
      e.stopPropagation();
      sendWs({ type: 'playlist_add', uri: track.file });
    });
    li.addEventListener('click', (e) => {
      if (e.target.classList.contains('library-add')) return;
      sendWs({ type: 'play_uri', uri: track.file });
    });
    return li;
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
    const sample = msg.server_utc * 1000 - (msg.client_time + rtt / 2);
    if (!clockReady) {
      clockOffsetMs = sample;
      clockReady = true;
      return;
    }
    const delta = sample - clockOffsetMs;
    if (Math.abs(delta) < SYNC_IGNORE_MS) return;
    if (Math.abs(delta) > SYNC_SNAP_MS) {
      clockOffsetMs = sample;
      return;
    }
    clockOffsetMs += delta * 0.25;
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
      hideHomeError();
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
      console.error('MPD', msg.detail);
      showHomeError(String(msg.detail));
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

  function startFrame(item, when) {
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

  function schedulePending() {
    if (!audioCtx) return;
    const now = audioCtx.currentTime;
    const frameSec = frameSamples / streamRate;
    const still = [];
    let catchupAt = null;
    for (const item of pending) {
      const target = playAtFor(item.serverTs);
      const late = now - target;
      if (late > CATCHUP_SEC) continue;
      let when = target;
      if (late > 0) {
        if (catchupAt == null) catchupAt = now + 0.01;
        if (catchupAt - now > CATCHUP_SEC) continue;
        when = catchupAt;
        catchupAt += frameSec;
        catchupUntil = Math.max(catchupUntil, when);
      }
      if (when - now > LOOKAHEAD_SEC) {
        still.push(item);
        continue;
      }
      startFrame(item, when);
    }
    pending = still;
  }

  function syncLabel() {
    if (!clockReady) return 'Syncing clock';
    const frames = pending.length + scheduled.length;
    if (audioCtx && audioCtx.currentTime < catchupUntil) return 'Catching up';
    if (frames === 0) return playbackState === 'play' ? 'Buffer empty' : 'Idle';
    const ms = Math.round(frames * (frameSamples / streamRate) * 1000);
    return `Buffer ${ms} ms`;
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
    if (el.syncHealth) {
      const label = syncLabel();
      if (el.syncHealth.textContent !== label) el.syncHealth.textContent = label;
    }
    requestAnimationFrame(uiLoop);
  }

  document.addEventListener('pointerdown', unlockAudio);
  document.addEventListener('visibilitychange', () => {
    if (document.visibilityState === 'visible') startClockSync();
    syncWakeLock();
  });
  document.addEventListener('fullscreenchange', () => {
    if (document.fullscreenElement || !document.body.classList.contains('is-speaker')) return;
    document.body.classList.remove('is-speaker');
    if (el.btnSpeaker) el.btnSpeaker.classList.remove('is-active');
    localStorage.setItem(SPEAKER_KEY, '0');
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
  if (el.btnSpeaker) {
    el.btnSpeaker.addEventListener('click', () => {
      unlockAudio();
      setSpeakerMode(!document.body.classList.contains('is-speaker'));
    });
  }
  if (localStorage.getItem(SPEAKER_KEY) === '1') {
    document.body.classList.add('is-speaker');
    if (el.btnSpeaker) el.btnSpeaker.classList.add('is-active');
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
  if (el.playlistSelect) {
    el.playlistSelect.addEventListener('change', () => {
      if (renderingPlaylistSelect) return;
      const name = el.playlistSelect.value;
      sendWs(name ? { type: 'playlist_switch', name } : { type: 'playlist_detach' });
    });
  }
  if (el.btnPlaylistNew) {
    el.btnPlaylistNew.addEventListener('click', () => {
      const name = el.playlistNewName ? el.playlistNewName.value.trim() : '';
      if (!name) return;
      sendWs({ type: 'playlist_new', name });
      el.playlistNewName.value = '';
    });
  }
  if (el.btnPlaylistDelete && el.playlistSelect) {
    el.btnPlaylistDelete.addEventListener('click', () => {
      const name = el.playlistSelect.value;
      if (!name) return;
      sendWs({ type: 'playlist_delete', name });
    });
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

  if (el.librarySearch) el.librarySearch.addEventListener('input', paintLibrary);
  el.btnUpload.addEventListener('click', () => el.fileInput.click());
  el.fileInput.addEventListener('change', async () => {
    const files = el.fileInput.files;
    if (!files || !files.length) return;
    for (const file of files) {
      const fd = new FormData();
      fd.append('file', file);
      if (el.uploadFolder && el.uploadFolder.value.trim()) {
        fd.append('folder', el.uploadFolder.value.trim());
      }
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

  bindMediaSession();
  connectWs();
  refreshHealth();
  setInterval(refreshHealth, 5000);
  scheduleTimer = setInterval(schedulePending, 20);
  uiLoop();
})();
