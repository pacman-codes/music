# LAIN mobile (Android and iOS)

First standalone native client. Uses the independent /v1 API, not Telegram.
This is development software; device playback and signing must be verified.

## Run locally

Install current stable Flutter, Android SDK (Android) and Xcode (iOS).
From mobile/:

```sh
python3 tool/bootstrap.py
flutter pub get
flutter analyze
flutter test
flutter run
```

The bootstrap generates the standard Android/iOS shell from Flutter and adds
audio foreground service, loopback-only Android network security config, iOS
background audio and keychain settings. Generated platform directories are
not committed. Remote server URLs must use HTTPS and a trusted certificate.
No token or key is embedded in the binary.

The native API must be reachable over HTTPS (separate reverse proxy to
standalone's port 8086); the existing Telegram-only hostname won't work unless
the proxy routes /v1/ to the native API. Generate a pairing code via the VPS CLI
from docs/STANDALONE.md. The app remembers the device token in secure storage.
A session expires after 90 days; re-pair if expired. On logout the client
attempts revocation and clears its local credentials and audio cache; if the
server is unreachable, use the administrator CLI to revoke the session.

## What works in the code

- Native background audio handler with media controls and seeking. Current
  track streams over authenticated HTTPS or plays from a cached local file.
- Wave obtains available music immediately and queues approved sources on
  the server. It polls preparation statuses and refills before the local queue
  runs out. Ready tracks may be appended, never unready placeholders.
- Configurable temporary LRU cache: 256 MiB to 4 GiB; prepare 0, 1, 3 or
  5 upcoming tracks. Explicit permanent downloads are separate from cache,
  have a 1/5/10/20 GiB admission limit and are not LRU victims. Permanent downloads use original audio; current file is protected from cleanup. OS cache eviction is possible.
- Search, library pages, editable playlists, five reactions, ratings,
  Wave settings and native history. Full playlists (up to 1000 tracks) play in order or shuffle; unavailable audio is skipped explicitly. No automatic import from unapproved URLs.
- Reports playback start only after approximately two seconds of position
  progression, natural completion and explicit next; seeks, stopping and
  interruptions do not count as completion. Server applies conservative
  thresholds and idempotent play IDs. Reporting is best effort, not offline
  durable. No artificial Telegram sent-history events.

## CI artifacts

Workflow lain-mobile builds a debug APK on Linux and an unsigned iPhone
application bundle packaged into an IPA on macOS. Find artifacts under the
GitHub Actions run. Debug APK signatures can differ across CI runners; do not
assume upgrades will install without uninstalling. A permanent signing key,
release versioning and signed iOS distribution are later steps. The unsigned
IPA requires valid signing/provisioning before it can launch on an iPhone.
Neither AltStore signing nor playback on real devices has been tested.

Flutter stable and dependency constraints are used; full SDK/dependency
pinning and committed platform shells are future reproducibility work.


## Discovery and collection

The fifth tab, Открытия, searches MusicBrainz recordings, MusicBrainz/iTunes
albums, and artists, and shows personal track/album recommendations. It
distinguishes metadata from locally playable audio, exposes five catalog
reactions and a wishlist, and supports explicit identity linking (including
iTunes album track positions), saved artists, discographies and saved albums.
Cover Art Archive thumbnails are public and sent without credentials.
The source index can prepare exact MBIDs from an operator-approved manifest;
see the server documentation. It cannot retrieve arbitrary streaming-service
audio. Native smart playlists use native playback history rather than legacy
Telegram delivery counts. Full playlists can be created, renamed, deleted,
reordered, and played in order or shuffled. Queue and profile are shared with
the native server. These changes still require real-device verification.


## Offline playback and full player

Select Скачать офлайн from a track, album, artist or playlist. A download
screen processes ready tracks sequentially, with per-track success/failure
counts and cancellation between files. Explicit downloads are retained in
the application's private support directory across restarts. They are
not removed by automatic prefetch eviction or clearing temporary cache.
Only completed audio files are published. Downloads are limited to
200 MiB/file and 2000 indexed tracks; the explicit download quota controls
new admissions, not deletion of already downloaded files. No background
download service for a killed application and no automatic transcoding.
Original source format is used for permanent downloads.

The Скачанная музыка screen uses local files only, with ordered or shuffled
playback. An unavailable local file cannot silently turn into a network
stream in offline mode. Ordinary online queues may also use a local copy.
Cache and download size, prefetch depth, and clear-cache controls are under
Хранилище и предзагрузка. Sign-out explicitly warns that device-local
downloads, queue snapshots and unsynchronized events are deleted.

Tap the mini-player to open full player controls, seek on release, volume,
repeat-one and upcoming-track reorder/removal. Album art in catalog views
is unchanged; the player currently uses a generic illustration.
A device-local snapshot retains up to 1000 tracks, active index and position
every five seconds and at control transitions. Relaunch restores the queue
paused; pressing play resumes the saved position, with a new play ID.
It does not invent a completion for the previous process.

Playback reports now use an atomic local outbox (up to 2000 reports);
server retries remain idempotent. Explicit offline playback doesn't
automatically send its events; switch to online playback or use the sync
button to flush. Failed network/auth requests retain reports, while invalid
reports are dropped. Older entries can be dropped when the cap is reached;
abrupt process termination between periodic snapshots may lose recent
position or a not-yet-written event. No cross-device queue synchronization
yet. Physical-device verification is still required.


## Audio quality, similarity and equalizer

Under storage settings select original or economy streaming. The server
needs its optional media worker for economy. Next tracks are prioritized
for processing, temporary compressed files remain separate from original
downloads, and a pending representation does not silently stream a larger
file. Local original copies can play without network transfer.

Track actions open a Звучание page with measured approximate frequency
balance, dynamics, optional tempo estimate, and similar playable local
tracks. The server uses a small capped sound-based ranking bonus. These
are classical signal descriptors, not AI embeddings or verified genres.

On Android only, the profile contains an experimental equalizer. Opt in,
restart the application, start a track and refresh the equalizer screen.
It uses the actual frequency bands and gain limits exposed by just_audio
AndroidEqualizer, with a conservative ±6 dB additional bound and flat,
bass, voice, and treble presets. The audio effect is disabled by default;
turn it on explicitly. The boot opt-in persists but gains are session-local.
If the device effect fails, turn off the opt-in and restart. No iOS EQ is
claimed. Android effects, Bluetooth output and background transitions still
need testing on physical phones.
