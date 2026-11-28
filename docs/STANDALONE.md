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
