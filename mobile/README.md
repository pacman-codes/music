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
- Prefetch next three ready tracks sequentially into a private, temporary
  per-device cache. Cache target limit 1 GiB, maximum 200 MiB/file, LRU
  eviction, temporary file and atomic rename, bounded network timeouts.
  Playback does not wait for the background cache. Cache may be evicted by OS;
  this is not permanent offline library or a promise of gapless playback.
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
