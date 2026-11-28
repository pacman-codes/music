# LAIN Standalone — foundation

This is the first migration stage, **not an Android/iOS release**. The native API
runs without BOT_TOKEN, Telegram initData or calls to Telegram. Existing bot and
Mini App remain unchanged. The native service uses a separate database copy and
audio directory, not the live bot volume. There is no two-way sync between these
two databases. Do not start the old bot against the standalone database.

## Safe deployment (only after CI and backup)

Keep the existing reverse proxy and services untouched. The new Compose profile
binds to 127.0.0.1:8086, not a public interface. If migrating an existing library,
copy it before starting the standalone service (the clone refuses overwrite).
Stop the legacy writers briefly for the final copy, or take an independently
verified SQLite backup if read-only WAL access is unavailable:

```sh
docker compose stop music web
docker compose --profile migration run --rm --build standalone-migrate
docker compose up -d music web
docker compose --profile standalone up -d --build standalone
curl --fail http://127.0.0.1:8086/healthz
```

If the clone fails, restart the old services and investigate before continuing.
Do not delete the old volume. For a fresh, empty standalone library, omit the
migration. Configure a separate HTTPS reverse-proxy hostname for the native API,
with a trusted certificate, request/body limits, and no logging of authorization
headers or pairing bodies. Do not expose port 8086 on the internet. Back up
standalone_data including both music.db (SQLite-consistent backup) and audio/.

## Pair a device

Run on the trusted VPS terminal:

```sh
docker compose exec standalone python -m music_bot.native_cli pair
```

This prints a random one-use pairing secret, valid for 10 minutes. A native
client posts it as JSON to POST /v1/pair with a device label. The response
contains a 90-day bearer token and a device ID. Keep the token in OS secure
storage (future Flutter client); send it only in the Authorization header over
HTTPS, never in URLs, application logs, or analytics. Do not share the pairing
secret. An expired session must be paired again. For revocation:

```sh
docker compose exec standalone python -m music_bot.native_cli devices
docker compose exec standalone python -m music_bot.native_cli revoke DEVICE_ID
```

Device list never prints secrets. SQLite stores only token digests. There is
no HTTP admin endpoint. DELETE /v1/session revokes the caller's session.

## Audio

The clone copies track IDs, metadata, playlists, ratings, catalog matches and
preference records, not Telegram audio bytes. Unmigrated tracks remain visible
but unplayable. Native Wave only selects available local assets.

Import authorized local files as a trusted server administrator. Accepted file
extensions: MP3, M4A, FLAC, OGG, up to 200 MiB. The operator must supply genuine
audio (this foundation does not probe codecs or transcode). Mount a directory
read-only for import, ensuring UID 10001 can read the file:

```sh
docker compose --profile standalone run --rm --no-deps \
  -v /absolute/path/to/authorized-music:/import:ro standalone \
  python -m music_bot.native_cli import /import/song.mp3 --title 'Song' --artist 'Artist'
```

Add --track-id EXISTING_ID only after checking the exact recording to attach
audio to the existing track without losing ratings and playlist entries.
Files are content-addressed, copied atomically and never served from a
client-supplied path. Exact duplicate bytes reuse the existing track on new
imports; explicitly attaching to an existing track preserves its ID. No HTTP
upload or download-from-URL functionality is included. Audio files are only
served through authenticated GET /v1/tracks/{id}/audio, with HTTP range support
for seeking. A missing file is not considered playable. File integrity is
checked by size on serving; full hash verification and media probing are
future operational work.

## API v1

* POST /v1/pair — one-time pairing; all other private routes require bearer auth.
* GET /v1/tracks?q=&before= — keyset pagination, metadata and playable flag.
* GET /v1/tracks/{id}/audio — protected file response.
* GET /v1/wave?mode=balanced — up to ten local audio tracks; no Telegram send.
* GET /v1/playlists and GET /v1/playlists/{id}?page= — existing collection.
* PUT /v1/tracks/{id}/rating, PUT /v1/tracks/{id}/reaction — explicit feedback.
* DELETE /v1/session — logout.

No mobile app, automatic source ingestion, native playback event reporting,
native queue persistence or signing workflow yet. A Wave request does not
fabricate listening events or Telegram sends. Until native playback telemetry
is implemented, rotation relies on migrated send history and explicit taste.


## Optional server-side prefetch

The standalone worker is off by default. Configure LAIN_PREFETCH_HOSTS in
the Compose environment to exact trusted public HTTPS hosts controlled by
the operator. Ensure these hosts cannot resolve to private/internal addresses
and enforce network egress restrictions at the deployment boundary. The
worker accepts direct MP3/M4A URLs, not search results, streaming-service
links, redirects or signed URLs. The operator must have permission to store
the audio and verify the exact recording before approval.

```sh
docker compose --profile standalone --profile prefetch up -d --build standalone standalone-prefetch
docker compose exec standalone python -m music_bot.native_cli approve-source TRACK_ID https://audio.example.com/song.mp3
```

Source registration is local administrator-only. Approval is stored in the
private database; do not approve URLs containing secrets. No URL is returned
to clients. This does not search for or download arbitrary songs.

Authenticated POST /v1/wave/prepare returns ready audio immediately and a
separate preparing array of up to five approved missing tracks selected by
the existing Wave engine. POST /v1/prefetch with {"track_ids":[1,2]} queues
the next tracks in a client playlist. POST /v1/prefetch/status with the same
body returns ready, queued, running, failed, unavailable or not_queued.
The queue may respond busy when full. Clients should poll modestly (e.g.
every 5 seconds while needed) and never block current playback on a download.
Only ready audio belongs in the playable queue.

One download globally, at most 30 pending jobs, maximum 45 MiB per download,
90-second network deadline, three attempts with backoff and expiring leases.
The worker checks for approximately 1 GiB of free space plus working room
before starting. This is a guardrail, not a filesystem quota. Native assets
are persistent; no automatic deletion of music. Failed jobs need operator
reapproval. Restart recovery does not require the phone to remain online.
Worker activation and live source transfers still require testing on the VPS.
Phone-side buffering, automatic source discovery and native playback telemetry
remain separate client/provider work.


## Mobile client and native playback

See [mobile README](../mobile/README.md) for Flutter setup and CI.
Native playback reports (POST /v1/playback) use a random 32-hex play_id scoped
to a device, track_id, event (start/skip/complete/stop), listened_ms and optional
duration_ms. Reports below two seconds are ignored; completion requires >=80%
played time and early explicit skip requires <25% and <30 seconds. Reports
are client telemetry, not independently verified acoustics. No duplicate
terminal feedback for the same play ID. GET /v1/history exposes this history.
GET/PUT /v1/wave/settings share the existing settings. Native Wave uses
native playback history, rather than treating migrated Telegram sends as plays.
The original legacy history is retained unchanged.


### Optional one-time transfer of legacy audio bytes

After copying the metadata database, you can import existing bot-accessible
audio using the same legacy BOT_TOKEN. Only do this for audio you are
authorized to store. The one-off container never polls or sends messages;
the standalone API never receives the Telegram token.

```sh
docker compose --profile migration run --rm --build standalone-audio-migrate
# Continue with the next_after value returned by the prior batch:
docker compose --profile migration run --rm standalone-audio-migrate \
  python -m music_bot.native_cli migrate-telegram-audio --confirm-authorized --after 50
```

The cursor 50 is an example, not a fixed page number. Default batch 50;
maximum 100. Existing local files are skipped, old track IDs and ratings
are retained. The official hosted Bot API download limit is approximately
20 MB; the migration conservatively accepts <=19 MiB, checks file size and
basic audio signature, and reports unavailable items rather than inventing
audio. Large/unavailable files need separate authorized local import.
Rate limiting or low disk space stops the batch and leaves a resumable cursor.
The explicit migration container is the only new component with BOT_TOKEN.


## Native discovery and source index

The mobile discovery tab queries MusicBrainz/ListenBrainz, with the existing
iTunes album fallback. Search results are metadata, not audio. The private
/v1/discovery, /v1/albums and /v1/artists endpoints support search, saved
records, reactions, discographies and explicit audio associations. Album
position matching requires the current recording ID and explicit confirmation.
Playlist CRUD, reordering and full-queue retrieval (maximum 1000 tracks) use
the same SQLite collection. Native smart playlists use native playback
events, not Telegram delivery history.

An operator can register a JSON manifest of up to 10000 approved exact
MusicBrainz recording IDs and stable direct MP3/M4A URLs, for example:

```json
[
  {"mbid":"00000000-0000-0000-0000-000000000001","url":"https://audio.example.com/song.mp3"}
]
```

The example is illustrative, not an actual music source. Use real authorized
recordings hosted on trusted domains in LAIN_PREFETCH_HOSTS. No signed URLs,
query parameters or client-supplied download URLs. The file is limited to
8 MiB. Mount it read-only when running the CLI:

```sh
docker compose --profile standalone run --rm --no-deps \
  -v /absolute/path/to/manifest.json:/import/manifest.json:ro standalone \
  python -m music_bot.native_cli register-source-manifest /import/manifest.json
```

Registration does not create playable tracks or perform downloads. When a
matching known catalog record is prepared, LAIN creates or reuses its local
track ID, keeps explicit reactions, and queues the approved URL through the
bounded prefetch worker. Wave may prepare up to five approved missing tracks
per request, including positively marked or saved indexed recordings; rejected,
parked and unmarked unsaved discoveries are not automatically materialized.
Favorites mode only uses positive add decisions for this extra indexed pool.
A preparation request does not override an existing source or reset failed
jobs. Use the existing administrator approval command to update that source.
The index adds/replaces entries; it is not a full synchronization/deletion
protocol.

Single-recording preparation is available at POST /v1/discovery/{mbid}/prepare;
album preparation at POST /v1/albums/{id}/prepare. The API reports playable and
preparable separately and does not expose source URLs. This enables automatic
exact matching to approved audio, not unrestricted streaming-service
downloading. Live provider integration and real device testing remain pending.


## Optional offline media processing

The media worker is independent of the source download worker and HTTP
server. Activate only when the VPS has sufficient memory and disk:

```sh
docker compose --profile standalone --profile media up -d --build standalone standalone-media
```

The optional image installs FFmpeg/ffprobe. The worker has no network access,
runs as the unprivileged music user, and has Compose limits (0.75 CPU,
256 MiB RAM, 32 processes). It holds an exclusive volume-local lock,
processes one job at a time, and checks for at least 1 GiB disk headroom
plus working space before encoding. The derivative store has a 2 GiB
admission cap and never deletes original audio. These are guardrails, not a
filesystem quota. A full derived store leaves additional variants pending;
operator disk maintenance is not automated. Temporary partial files are
cleaned on the next worker iteration.

Local MP3/M4A/FLAC/OGG are probed, bounded to two hours. An AAC stereo
representation uses a target bitrate of 96 kb/s and MP4 fast-start metadata
for seeking. It is published atomically only after verifying duration and size.
If it is no smaller than the original, the original is used instead and
identified explicitly. Derivatives and descriptors are keyed by original
content hash and processing version. Missing derivatives can be regenerated
after a new preparation request. Three bounded attempts with backoff; failed
jobs do not loop forever. Requests only adjust priority, not run FFmpeg.

- GET /v1/tracks/{id}/audio?quality=original — unchanged protected original.
- GET /v1/tracks/{id}/audio?quality=economy — prepared smaller representation
  or already smaller original; HTTP 409 if not ready. Supports byte ranges.
- POST /v1/media/prepare — authenticated list of up to five track IDs to
  prioritize; no file paths or URLs from clients.
- GET /v1/tracks/{id}/analysis — measured features and preparation state.
- GET /v1/tracks/{id}/similar — available analyzed tracks ordered by acoustic
  descriptor distance, excluding explicit negative/parked tracks, low ratings
  and duplicate content hashes.

The analysis decodes up to 90 seconds of mono PCM near the middle of a
recording, computes approximate frequency balance, dynamics, crest factor,
zero crossings, silence fraction and a cautious rhythmic periodicity estimate.
This is DSP, **not a neural embedding, genre inference, LUFS measurement or
guaranteed BPM**. Half/double-tempo ambiguity is possible; weak peaks have
no estimate. Similarity scores are distances, not calibrated percentages.
Only analyzed local assets participate. Native Wave may boost candidates by
up to 20% based on acoustic proximity to up to 12 explicit favorite/high-rated
audio seeds. Hard exclusions, explicit feedback, rotation and category quotas
remain unchanged. Legacy Telegram Wave is unaffected.

The app can choose streaming quality. Offline downloads always retain
originals. The economy cache has separate file identities; cached originals
may play without network traffic. It prioritizes next ready tracks for server
processing and does not silently fetch a larger original if the economy file
is not prepared. This is a fixed quality option, not adaptive bitrate HLS.


## Cross-device continuation and listening statistics

All paired devices belong to the same private owner. GET /v1/devices returns
active device labels and checkpoint summaries (no credentials).
PUT /v1/checkpoint stores the calling device's ordered queue of 1–1000 known
track IDs (repeats allowed), current index, position in milliseconds and
optional Wave mode. Each device has its own monotonically increasing
revision. GET /v1/devices/{device_id}/checkpoint retrieves that device's
snapshot, resolving current library metadata and available audio. Missing
non-current tracks are omitted with an explicit count; an unavailable current
track returns 409. DELETE /v1/checkpoint clears only the caller's snapshot.
Session revocation removes the corresponding checkpoint. No remote commands
or automatic replacement of another device's queue. The client confirms
before applying a snapshot and restores it paused.

Automatic publication is opt-in and happens approximately every 15 seconds
of online playing and when paused, with one request in flight. Offline-only
queues are not uploaded. Device-local snapshots remain independent; failed
cloud writes do not stop playback. This is checkpoint transfer rather than
real-time synchronized audio, and remote device playback is not stopped.

Native playback accepts a cumulative progress event alongside start, skip,
complete and stop. A play ID has at most one terminal outcome. Progress
updates cannot decrease the measured listening time, create a second play
or produce preference feedback; terminal reports remain idempotent.
GET /v1/listening-summary?period=7|30|365|all aggregates reported time,
sessions, unique recordings/artists, substantial completions and early
skips, top recordings/artists by time, and daily totals (up to 365 recorded
days). No Telegram send history is counted. Time is client-estimated and
can be incomplete; period and daily grouping use the first report's
server receipt date in UTC, so delayed offline uploads may shift dates.
