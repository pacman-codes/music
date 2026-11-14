# Passive recommendation ranking (Wave 2.x)

This change improves **ranking of audio already uploaded to the current Telegram bot**. It does not pretend that MusicBrainz suggestions are playable and does not require any new buttons, account, scrobbling integration or permissions.

## Signals and provenance

* **Explicit (strong):** liked/disliked, rating 1–10, manually reported skip/finish. Explicit dislikes and repeated skips remain exclusion signals.
* **Library context (weak):** membership in custom playlists, and co-membership with a positively rated/liked track. We deliberately ignore liked-system playlist for co-occurrence and skip giant catch-all custom playlists with more than 80 tracks.
* **Exposure (weak, reversible):** the latest 240 *delivered Telegram audio messages* are weighted by position in delivery history. The same song's recent deliveries reduce its short-term selection probability; they never imply a play or skip.
* **Diversity:** nearby repeats of one artist get a soft penalty, and quota categories are interleaved when possible. A small library can still repeat artists.
* **Playable-only:** local radio selection never includes metadata-only ListenBrainz or MusicBrainz suggestions.

These weak signals deliberately do not override user dislikes or skip-fatigue suppression. On a tiny or unrated collection the algorithm degrades gracefully to weighted random selection.

## Design limits

1. Telegram's native audio player has no Bot API play/skip/seek/completion telemetry.
2. Co-playlist affinity is **not acoustic similarity**: unrelated songs in a mixed playlist can be grouped. Its weight is capped and custom playlists above 80 items do not create cross-artist links.
3. Existing UI choices are unchanged. Existing queue snapshots remain fixed, and the next radio batch adapts to the latest preferences.
4. The fixed balanced/familiar/explore mix quotas remain unchanged; no new server dependency or background job was added.
5. The 10,000 track in-memory selection limit is a pragmatic cap. For larger libraries, add chunked candidate retrieval and indexing, not unbounded RAM.
6. This is a source-backed engineering improvement, **not a measured claim that recommendations improved**: validation requires real owner feedback and a hold-out evaluation using explicit likes/ratings.

## Best next upgrades

1. Cache safe MusicBrainz artist/recording tags and associate them with *confirmed* MBIDs, never fuzzy names as facts. Use genre and mood similarity in the local item-to-item candidate pool.
2. Use ListenBrainz artist-radio and CF suggestions to discover cross-artist tracks (metadata only until a real audio file is uploaded).
3. Add privacy-preserving offline evaluation: temporal holdout on explicit positive feedback, coverage, diversity, novelty and repetition; choose mixing weights against a baseline, not intuition.
4. Only then consider compact on-device/offline audio embeddings for legal local files; Chromaprint is for identity, not semantic musical similarity.

Sources:
- https://listenbrainz.readthedocs.io/en/latest/users/api/core.html
- https://listenbrainz.readthedocs.io/en/latest/users/api/recommendation.html
- https://musicbrainz.org/doc/MusicBrainz_API
- https://link.springer.com/article/10.1007/s11257-024-09406-0

## Tests

Run `python -m pytest -q`; `tests/test_passive_recommendations.py` checks provenance, playlist co-occurrence, category quotas, deterministic randomness, and that giant catch-all playlists do not make all songs similar.
