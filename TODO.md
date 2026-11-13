# TODO

**Product:** One song, same moment, in every room on the **private LAN**. Each device adjusts its own playout delay and volume. One global stream only — not separate zones or playlists per room.

**Done when:** Someone can open the page on each speaker, tap once, leave the phone on a counter, and music stays in sync without babysitting tabs.

---

## Non-goals (do not drift)

| Topic | Decision |
|-------|----------|
| **Multiple zones / streams** | Out of scope. One timeline, one fanout. Do not add per-room queues or simultaneous different content. |
| **Reachability beyond the home LAN** | No WAN exposure, tunneling, or “open to the internet” features. Optional LAN tokens are for **houseparty roles**, not remote access. Same-origin / bind-on-LAN assumptions stay. |
| **Streaming services** | Not planned unless product scope changes deliberately (Spotify, AirPlay, etc.). |

---

## Client roles: Main, Sub, Guest

**Goal:** One recurring model for safe houseparty, live input, announcements, and remote calibration — without turning Roomz into multi-zone.

| Role | Typical device | Capabilities (target) |
|------|----------------|------------------------|
| **Main** | Couch phone / kitchen tablet used to run the house | Full transport, library upload, queue edits, saved playlists, live external input, **live announcements**, view/edit **all** device delays/volumes |
| **Sub** | Counter speaker / bedroom phone in Speaker mode | Playout + local delay/volume; optional “add to queue”; no destructive actions |
| **Guest** | Friend on Wi‑Fi | Listen; queue a track (optional); no clear/upload/delete stored playlist |

**Mechanism (direction):** On WebSocket `hello`, claim role via env **house PIN / token** (Main) or default Sub; Guest is token-less or guest PIN. Server enforces destructive commands in `_run_command` and sensitive REST routes. UI hides or confirms dangerous controls for non-Main.

**Touch:** `app/main.py`, `app/ws_manager.py`, `frontend/static/app.js`, `.env.example`.

---

## Set and forget speakers

**Goal:** Fixed outputs that stay in sync without an attended browser tab.

| Gap | Direction |
|-----|-----------|
| Browser tab + ~176 KB/s PCM | **Headless client** (Pi / small PC): WebSocket `/ws`, same binary frame format + clock sync, ALSA/Pulse output, delay/volume in a local config |
| Phone Speaker mode | Stronger **kiosk story**: optional `?speaker=1`, PWA install hints, wake lock / visibility documented limits |
| Ops | **Connected clients** list: labels from `hello`, role, optional registered delay/volume, connection health |
| Reliability | **AudioWorklet** continuous queue (see below) to reduce chunk joins |

**Touch:** new client package or binary; `app/main.py`, `frontend/static/app.js`; reuse `sync` / `sync_response` and `chunk_resend`.

---

## Onboarding audit and friction removal

**Goal:** First LAN setup succeeds without reading the README.

**Shipped (phase 1):** health problem list, audio unlock overlay, `?speaker=1` and `?label=`, setup hint, upload progress, scan/upload errors on main view.

**Still open:**

- **Delay calibration helper**: test tone or align-to-reference-client flow
- PWA install hint in Speaker mode
- Full setup checklist (MPD / FIFO / WS / audio) beyond health banner
- Poll library after scan while MPD `updating` (optional)

**Touch:** `frontend/static/app.js`, `frontend/index.html`, copy in status banner.

---

## Rich now playing and album art

**Goal:** Now playing and Speaker view feel like a real player, not a placeholder.

**Shipped:** Status carries album, genre, track, year; `GET /api/art` (MPD embed + folder cover); art + metadata + up-next in main and Speaker views; lock-screen metadata artwork when available.

**Still open:** server-side art cache; playlist rows with art thumbnails.

**Touch:** `app/mpd_client.py`, `app/art.py`, `app/routers/api.py`, `frontend/static/app.js`, CSS.

---

## Remote delay and volume

**Goal:** Fix the kitchen from the couch (Main client).

- Server registry: device id, label, `delay_ms`, optional volume, role
- Main UI: this device vs others; push delay/volume to Sub/headless targets
- Play, pause, seek, and queue stay global via MPD

**Touch:** `app/main.py` WebSocket, `frontend/static/app.js`, small persistence (not playlist data).

---

## Continuous sample stream (AudioWorklet)

**Goal:** Playback without audible joins between stitched buffers.

The page stitches ~1 s of chunks today. Next step: AudioWorklet reading one sample queue so playback is one continuous stream.

**Touch:** `frontend/static/app.js` (and a small worklet file).

---

## External input (live sources)

**Goal:** Occasionally play **live** audio (line-in, mic, BT capture on a laptop, etc.) through the **same** synchronized fanout as library playback.

**Research summary**

| Approach | Fits Roomz? | Notes |
|----------|-------------|-------|
| **Main client → server PCM ingest** | **Preferred for ad‑hoc LAN sources** | Designated Main captures (e.g. `getUserMedia`), sends 44.1 kHz / 16-bit / stereo chunks on WebSocket using the **same header layout** as `app/stream.py`. Server **selects active source**: live ingest while active, else MPD FIFO. Snapcast-style **priority/meta** pattern without adding Snapcast. |
| **MPD `alsa` / `pipe` / HTTP input** | Supplement on the **server host** | Anything MPD plays still exits the **FIFO** Roomz already reads. Good for a sound card on the Roomz machine; awkward for a phone across the LAN. MPD FIFO is **output only** — you cannot push arbitrary PCM into the FIFO bypassing MPD. |
| **Every client streams to server** | No | Wastes bandwidth and breaks “one timeline”. |

**Design notes**

- One **active distributor source** at a time; bump **epoch** on switch (same as seek/pause today).
- While live: decide whether MPD **pauses**, **ducks**, or keeps playing muted under the ingest (announcements = short override; full live = longer override).
- Live path needs Main role; bitrate ~176 KB/s upload from one Main is acceptable on LAN.
- Low-latency TV/line-in multi-room (sub-100 ms) is a **different problem** than file playback; do not promise WiiM-class HDMI sync without a dedicated latency mode.

**Touch:** `app/stream.py` or new `app/sources.py`, `app/main.py` WebSocket binary path from Main, `frontend/static/app.js` capture UI.

---

## Live announcements (from Main)

**Goal:** Short voice or chime over the music **without** a full “external input” session.

- Main client: hold-to-talk or upload short clip; server **priority overlay** on the fanout (duck MPD via volume command or software mix)
- Distinct from long-form external input: time-limited, auto-revert to MPD FIFO
- Requires Main role

**Touch:** overlap with external input mux; may share ingest path with stricter caps (duration, sample count).

---

## Queue, library, and UX polish

| Item | Direction |
|------|-----------|
| Confirmations | Main-only or confirm: `playlist_clear`, delete stored playlist |
| Queue affordances | “Play next” vs “add to end” |
| Library maintenance | Delete/rename via API + MPD rescan (careful with paths) |
| Chat | Optional “request track” with Main approve (low priority) |

---

## Optional: Roomz supervises MPD

**Today:** MPD is a separate process; Roomz reads `mpd/mpd.conf` only (`AGENTS.md`).

**Research:** Starting MPD from Roomz is **plausible** for dev and single-box installs:

```text
mpd --no-daemon /path/to/mpd/mpd.conf
```

- Probe until TCP port accepts (same as `/health`).
- On Roomz shutdown: terminate MPD explicitly (`SIGTERM`, wait, then `SIGKILL` if needed).
- **Caveat:** MPD does not reliably exit when its parent dies (handles `SIGHUP` differently); do **not** rely on orphan cleanup — supervise the child process in `lifespan`.
- **Production alternative:** `systemd --user start mpd` with `mpd.service` and socket activation — document both; default stays “operator starts MPD” until opt-in env e.g. `ROOMZ_START_MPD=1`.

**Touch:** `run.py` / `app/main.py` lifespan, docs, `mpd/mpd.conf.example`, tests with subprocess MPD (already partially patterned in tests).

---

## Home automation (LAN only)

**Goal:** Home Assistant (or scripts on the LAN) can drive transport and read now playing **without** cloud or WAN.

**Research:** Common pattern is a **thin REST control plane** plus push or poll for state:

| Piece | Direction |
|-------|-----------|
| `GET /api/status` | JSON mirror of MPD snapshot (now playing, queue summary, playing state) |
| `POST /api/transport` | `play`, `pause`, `next`, … same semantics as WebSocket commands |
| Auth | Same **Main** token as houseparty, or read-only status without token |
| HA | Custom integration or `rest_command` + `rest` sensor; optional MQTT **on LAN** for people who already run a broker locally |

Not a dependency in core Roomz; ship stable HTTP first, HA card/integration later.

**Touch:** `app/routers/api.py`, `app/schemas.py`, optional `docs/ha.md`.

---

## Hi-res and variable rate (viability)

**Today:** Fixed **44100 Hz, 16-bit, stereo** end-to-end in `app/stream.py`; Opus path assumes 48 kHz encode with resampling; clients negotiate format via status `stream` object.

| Question | Assessment |
|----------|------------|
| **Hi-res file playback** | MPD can decode hi-res files; **wire format** can stay 44.1/16 (MPD/ffmpeg resample in `audio_output`) with minimal change. True **lossless hi-res fanout** means raising FIFO `format`, chunk size math, Opus off or new codec — **medium effort**, all clients must agree. |
| **Variable rate per track** | MPD may change decode rate between tracks if output format not forced. Roomz timestamps assume **constant** `FRAME_SAMPLES` per 100 ms wall of audio. **Viable only if** MPD `audio_output` **forces** one rate (recommended) or server resamples to a **canonical** rate before chunking (**preferred** if hi-res library matters). |
| **Sync impact** | Industry multi-room stacks (e.g. Snapcast) fix one `sampleformat` per source. Variable wire rate is poor fit for sample-accurate group sync. |

**Direction:** Document “force 44100:16:2 in mpd.conf” as the supported setup. Optional later milestone: **48 kHz** canonical wire format as a breaking/configured upgrade — not arbitrary per-track variable rate on the wire.

---

## Mobile native app (maybe later)

**Goal:** iOS/Android background audio where WebView/Web Audio is limiting.

- Likely same protocol as headless client (WebSocket + frame decode + local output)
- **Maybe later**; browser + headless Pi covers most house speakers first

---

## Suggested order

1. **Client roles (Main / Sub / Guest)** — foundation for safe LAN and live features  
2. **Set and forget** — headless client + connected clients + Speaker/PWA hardening  
3. **Remote delay and volume** (Main manages house)  
4. **Rich now playing + album art**  
5. **Onboarding audit**  
6. **AudioWorklet** continuity  
7. **Live announcements** (short, Main)  
8. **External input** (priority ingest from Main)  
9. **Home automation REST** (LAN)  
10. **Optional MPD supervise** (opt-in)  
11. **Queue/library polish**  
12. **Hi-res** only if library quality demands it (fixed canonical rate, not multi-format wire)  
13. **Mobile app** if still needed

---

## Previously tracked (unchanged intent)

- **Guests on the LAN** → superseded by **Client roles: Main, Sub, Guest** above.  
- **Announcements** → split into **Live announcements** and longer **External input**.  
- **Headless speaker** → under **Set and forget speakers**.
