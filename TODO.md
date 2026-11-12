# TODO

**Product:** One song, same moment, in every room on the LAN. Each device only adjusts its own playout delay and volume.

**Done when:** Someone can open the page on each speaker, tap once, leave the phone on a counter, and music stays in sync without babysitting tabs.

---

## Headless speaker (e.g. Raspberry Pi)

**Goal:** A fixed speaker without a browser tab or screen — same timestamped PCM stream, local playout delay, always on.

| Gap | Direction |
|-----|-----------|
| Browser uses ~176 KB/s stereo PCM and needs a tab | Small native client: WebSocket `/ws`, binary frames from `app/stream.py`, clock sync, output on ALSA/Pulse |
| Phone speaker mode still needs wake lock / Media Session | Pi (or similar) runs as a service; delay and volume in a local config file |
| Bandwidth at scale | Optional later: lighter codec on the wire. Only if many headless + browser clients need it |

**Touch:** new client package or binary; reuse existing frame format and `sync` / `sync_response` messages.

---

## Remote delay and volume

**Goal:** Hear that the kitchen is early; fix it from the couch without walking there.

- Server registry: device name, `delay_ms`, optional volume
- UI: this device vs others (read-only or admin)
- Play, pause, seek, and queue stay global via MPD; only delay and volume are per-device

**Touch:** `app/main.py` WebSocket, `frontend/static/app.js`, small persistence (not playlist data).

---

## Guests on the LAN

**Goal:** Friends can queue a track; they cannot wipe the house queue by mistake.

- Soft roles: read-only guest vs host (env token or PIN on destructive actions)
- Confirm dialogs or hide: `playlist_clear`, stored-playlist delete, upload

**Touch:** `app/main.py` `_run_command`, frontend controls.

---

## Announcements

**Goal:** Short voice or chime over the music without stopping MPD playback.

- Design: duck music, inject PCM or a secondary stream, or an MPD mixer path

---

## Continuous sample stream

**Goal:** Playback with no joins between buffers.

The page already copies about one second of chunks into one buffer at the audio context's sample rate. The later step is an AudioWorklet that reads one queue of samples, so nothing is scheduled as a separate clip.

**Touch:** `frontend/static/app.js`.

---

## Nice-to-have

- Album art in now-playing / Speaker view (tags or folder art)

---

## Suggested order

1. Headless Pi (or generic) speaker client  
2. Remote delay/volume registry  
3. Guest/host split  
4. Announcements  
5. Album art (and codec only if bandwidth demands it)
