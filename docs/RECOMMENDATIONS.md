# Ratings and Wave — specification

## Semantics

- 1–10 numeric rating is an explicit preference signal, independent of the liked playlist.
- A track can be in "Мне нравится" and have a low score, or retain 10/10 while temporarily absent from Wave.
- "Dislike / never recommend" is a separate explicit action; it removes from liked as before.
- Repeatedly skipped favorites stay in their original playlists.

## What the bot really observes

Telegram native audio messages offer NO bot-side playback events: the bot cannot read Play, Next, Skip, or completion. The current buttons and commands /skip ID and /finished ID record *manual reports*. Each event has a source field. Sent-history is not treated as played-history. Automatic analytics requires a separately instrumented player, at odds with the native-player preference, so it is not enabled.

## Ranking and recovery

For each song, take at most the last 20 reported events in the preceding 30 days.
- If skips >= 3 and skips >= 2*completions+3, the song is temporarily suppressed from generated Wave, even when liked; it is not deleted from playlists or ratings.
- A completion can lift suppression. Old events expire after 30 days.
- A single skip does not penalize a song. Further skips lower the probability; completions raise the weight up to a bounded bonus.
- Ratings 1–10 multiply ranking weight: 1 => 0.25x, 5 ~ 0.94x, 10 => 1.80x.
- Favorites and artists similar to favorites get positive weights, diverse artists are preferred.
- Recently *sent* tracks are downweighted to reduce repeats, with no claim that they were listened to.

The Wave uses weighted sampling without replacement **inside each batch**. The next 10 are selected only when the owner requests "Ещё 10", using updated ratings and manually reported behavior. Recently sent songs are penalized; the last three are avoided when enough alternatives exist. Repeats across batches are intentional: reliable favorites may return later. Previously sent songs are never removed from Telegram by a new rating. Explicit playlists keep their fixed snapshot order. Thresholds are starting heuristics to tune with actual explicit feedback.

## Controls

Telegram: /rate ID SCORE, /skip ID, /finished ID or rating inline keyboard.
Mini App: 1–10 selector, manual feedback buttons.
An HTML widget renderer exists but remains disabled pending Telegram widget runtime safety and aiogram widget_query support.

## Personalized catalogue discovery (not native playback)

This is separate from the local playable /wave.

- 1–10 score is a positive seed only above 4; liked and high-rated songs
  contribute to artist preferences. A manual skip streak removes an artist
  seed derived solely from a frequently skipped song, without unliking it.
- At most three favorite artists are selected; similar recording metadata
  comes from ListenBrainz and is resolved by MusicBrainz.
- Records are interleaved across seed artists, duplicate recordings and
  previously owned titles filtered out. Partial provider failures degrade
  gracefully.
- Only a confirmed MusicBrainz MBID is used as a definite identity. A textual
  match is a suggestion, not proof.
- No track is auto-sent in Telegram until a real Telegram audio file exists.
  Do not count a catalog recommendation as a native playback event.
