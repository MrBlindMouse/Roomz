# TODO

**Product:** One song, same moment, in every room on the LAN. Each device only adjusts its own playout delay and volume.

**Done when:** Someone can open the page on each speaker, tap once, leave the phone on a counter, and music stays in sync without babysitting tabs.

---

## 1. First-run setup (operator)

**Goal:** A new host gets sound without editing three configs and guessing why it is silent.

| Gap | Implement |
|-----|-----------|
| MPD paths (`mpd/mpd.conf`, `ROOMZ_LIBRARY_BASE`, `ROOMZ_FIFO`) must match by hand | Document or generate `mpd.conf` from `.env`; fail loudly on startup if paths disagree |
| `/health` reports problems; the UI does not | Banner on the main screen from `/health` (MPD down, FIFO missing, mismatch hint) |
| MPD errors only appear in the closed Library panel | Show transport/MPD errors on the home view, not only `library-error` |

---

## 2. Sync and delay (per speaker)

**Goal:** Rooms sound like one speaker, not a slap-back echo. Set delay once, rarely touch it again.

| Gap | Implement |
|-----|-----------|
| No feedback on early/late/starved playout | Small UI: buffer health, estimated offset, or “catching up” state |
| Clock offset is sampled once; no long-run drift handling | Periodic gentle resync or drift metric; document limits |
| Slider to −250 ms with ~300 ms base buffer; extreme negative delay drops frames | Raise buffer, clamp slider, or reschedule instead of drop when adjusting “earlier” |
| Wi‑Fi glitch longer than buffer skips on that client only | Larger jitter buffer and/or bounded catch-up after underrun |

**Touch:** `frontend/static/app.js` (scheduler, slider), `app/stream.py` (frame timing).

---

## 3. Speaker device that stays on (browser client)

**Goal:** A phone on the counter keeps playing after lock screen and does not need the tab in front.

| Gap | Implement |
|-----|-----------|
| Audio needs a user gesture; background tab suspends `AudioContext` | Wake Lock API where supported; `Media Session` metadata + play state |
| No “always-on speaker” mode | Optional fullscreen / kiosk layout: large now-playing, minimal chrome, dim-friendly |
| ~176 KB/s PCM per tab; screen stays on | Later: lighter codec or native/headless client (Pi); out of scope until browser path works |

**Touch:** `frontend/static/app.js`, `frontend/index.html`, CSS for speaker layout.

---

## 4. Control another room from here

**Goal:** Hear that the kitchen is early; fix it from the couch without walking there.

| Gap | Implement |
|-----|-----------|
| Delay is only `localStorage` on that device | Optional server registry: device name + delay_ms; “this device” vs remote list (read-only or admin) |
| Volume is local only | Same pattern: per-client volume on server, or “louder here” stays local by design (decide) |
| Transport (play/pause/skip/queue) already global via MPD | Keep global; only delay/volume are per-device |

**Touch:** `app/main.py` WebSocket messages, `frontend/static/app.js`, small persistence (memory or file; not a second playlist).

---

## 5. Fanout must not block the house

**Goal:** One slow phone does not stall PCM for every other speaker.

| Gap | Implement |
|-----|-----------|
| `broadcast_bytes` sends to each WebSocket serially | Parallel sends; skip or disconnect client that cannot keep up within one frame time |
| Slow reader blocks FIFO loop | Decouple: queue frames per client or drop slow consumer without blocking broadcast |

**Touch:** `app/ws_manager.py`, `app/stream.py` / `supervise_fifo`.

---

## 6. Library and queue (music picking)

**Goal:** Pick an album or search, not scroll a flat file list.

| Gap | Implement |
|-----|-----------|
| `listallinfo` rendered as one big tree; no search | Search/filter by title, artist, album; group by folder/album |
| Upload only to library root | Optional subfolder in upload API + UI |
| Now-playing is small (title + ♪) | Speaker view: large title/artist, optional art from tags |

**Touch:** `app/routers/api.py`, `app/mpd_client.py`, `frontend/static/app.js`, CSS.

---

## 7. Guests on the LAN (safety without login)

**Goal:** Friends can queue a track; they cannot wipe the house queue by mistake.

| Gap | Implement |
|-----|-----------|
| No auth; every client has full WebSocket power | Soft roles: read-only guest vs host (env token or PIN on destructive actions) |
| Clear playlist, seek, upload unrestricted | Confirm dialogs; hide or gate `playlist_clear`, mass delete, upload for guests |

**Touch:** `app/main.py` `_run_command`, frontend controls.

---

## 8. Announcements (later)

**Goal:** Short voice or chime over the stream without stopping MPD playback.

| Gap | Implement |
|-----|-----------|
| Not built | Design: duck music, inject PCM or secondary stream, or MPD mixer path; see README future plans |

---

## Suggested order

1. Setup banner + path checks (unblocks every tester)  
2. Fanout non-blocking (stability at scale)  
3. Background playback + speaker UI (makes phones usable as speakers)  
4. Sync feedback + buffer/slider fixes (quality)  
5. Library search/grouping  
6. Remote delay/volume registry  
7. Guest/host split  
8. Announcements  
