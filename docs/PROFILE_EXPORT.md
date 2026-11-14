# Private music profile: full library and recent changes

This is the owner-controlled bridge between the Telegram bot and occasional
human/AI recommendation audits. **No new ratings, quizzes, scrobblers, or
continuous monitoring are required.**

## One-command export in private Telegram chat

Send `/tasteexport` to your own bot. It replies with a private JSON document
(`music-profile-YYYYMMDD.json`) of the entire library, not just the last 25
tracks. It contains:

- all saved tracks: local numeric ID, title, artist, and original `added_at_utc`
  **if known**;
- liked/disliked status, ratings 1–10, recent explicit manual skip/finish counts;
- recent *Telegram message deliveries* (not verified listens), with timestamp;
- all custom and liked playlists, including their ordered list of track IDs;
- owner-confirmed/imported MusicBrainz recording IDs when available;
- `recently_added_track_ids`, and the count of legacy tracks with unknown dates.

The file **never includes** `file_id`, `file_unique_id`, Telegram account
or chat IDs, bot tokens, URLs, cookies, full audio/video data, fingerprints, or
download credentials. The bot stores no second copy on its disk for this command.

If a very large export exceeds 20 MiB, use the CLI below. Sending the JSON
to the owner is explicitly requested by `/tasteexport` and inherits the same
private chat and numeric owner guards as the other bot commands.

## Local CLI (no BotFather token needed)

```bash
python -m music_bot.profile_export \
  --database data/music.db \
  --output profile-exports/music-profile-20261114.json \
  --recent-days 30

# Optionally compare the previous JSON with the new snapshot:
python -m music_bot.profile_export \
  --database data/music.db \
  --output profile-exports/music-profile-20261214.json \
  --compare profile-exports/music-profile-20261114.json
```

The export is written only when the destination does not exist, with mode
`0600` on Unix. No automatic GitHub push, Telegram scheduled send, or network
call is performed. The CLI emits only summary counts, not raw titles.

## How to use in ChatGPT or GitHub

**Easiest:** attach the exported JSON to your conversation and ask:

> Изучи всю мою медиатеку, сравни с прошлым экспортом, предложи 30 треков
> и улучши алгоритм рекомендаций в pacman-codes/music на основе наблюдений.

ChatGPT can inspect the existing tracks, playlist structure, liked/rated
preferences, recent additions, and manually reported feedback; it can also
give a ready-to-use list of suggested tracks, clearly distinguishing songs
already playable in the current Telegram bot from metadata-only discoveries.

**GitHub:** store those snapshots in a **separate private repository**
(e.g. `music-taste-private`) and allow the connected GitHub app to read it.
On explicit request the assistant can compare old/new snapshots and submit
code/test changes to `pacman-codes/music`. Do **not** commit your personal
snapshot to the current **public** `pacman-codes/music` repository: removed
files remain visible in its git history.

## Why a snapshot instead of automatic model training?

The changes are evidence, not proof of preference. Sending audio in Telegram
is not an actual listen; saving it is a weak positive signal, not a like.
Algorithm modifications should be compared against an unchanged baseline on
withheld positive ratings/manual feedback before being merged. A user can
request such a review occasionally without constant interaction.

`added_at_utc` is recorded for **new** imports going forward. Existing
entries from before the migration have null timestamps, rather than invented
import dates. Comparing two full exports can still identify songs that appear
for the first time between those snapshots.
