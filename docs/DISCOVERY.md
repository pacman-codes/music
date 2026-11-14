# Track identity & personalized discoveries

## Exact identity, never inferred from text

Existing Telegram audio has a Telegram file_id and numeric track_id but not
necessarily a MusicBrainz recording MBID. Search suggestions use artist and
title only, and are never automatically promoted to a confirmed match.

In Telegram use `/identify 12` to search MusicBrainz, tap the exact match or
`/unmatch 12` to remove an incorrect manual link. In Mini App tap
«🔍 Найти запись» and explicitly confirm. The database stores the link and
its provenance in `track_recording_links`:
- `confirmed`: user explicitly chose a recording.
- `import`: real Telegram audio upload was linked to this MBID; this
  provenance cannot be removed by unmatch.

Old imported assets are backfilled automatically on database initialization.

## Radio by track

`/radiotrack 12` or «🎸 Радио» uses exact MusicBrainz Artist MBID if present;
otherwise falls back to fuzzy artist name search. Only metadata candidates
not already found in the local catalog/audio library are returned.

## For You

`/recommend` or «✨ Для меня» selects at most three top artists based on
positive ratings (5–10), explicit liked favorites, and manually reported
completion vs skip events. A favorite rated <=4 or subject to repeated skips
does not become a positive seed. User preferences do NOT remove songs from
the liked playlist.

ListenBrainz artist radio returns candidate recording MBIDs. MusicBrainz
provides their titles and artist metadata. Results are interleaved between
seed artists, deduplicated, and filtered against owned tracks. External API
failures can leave fewer choices without interrupting native playback.

**No automatic playback telemetry.** Telegram does not provide exact play/
skip/finish events to bots. Only manual reports feed behavioral learning.

## Current limits

- External candidate metadata is NOT directly playable. The native Telegram
  audio player requires an actual Telegram file_id after legal media ingestion.
- No automatic Youtube resolution/yt-dlp and no DRM bypass.
- Offline tests cover owner authentication, identity confirmation, migration,
  feed diversity and incomplete provider results. Real Telegram mobile UX and
  public network behavior remain untested pending a server.
