# Five reaction playlists (owner-only)

A single nonnumeric choice per discovery; no mandatory star ratings or
constant interaction. The five *mutually exclusive* decisions are:

| Decision | Code | Automatic recommendation handling |
| --- | --- | --- |
| 🤔 Может добавлю | `maybe` | Weak candidate for future reassessment; stays eligible for local Wave |
| ❤️ Добавлю | `add` | Strong positive; for a playable track, synchronized with existing ❤️ likes |
| 🚫 Никогда не буду слушать | `never` | Hard negative; excludes the recording from normal recommendations |
| ⏳ Точно не сейчас | `later` | Neutral pause, **not** a dislike or manual skip |
| 🧠 Интересно, но слушать не буду | `interesting` | Keep for review, but don't put it into everyday playback |

Choosing a different decision **moves** the track between lists, rather than
copying it into several. Existing 1–10 scores remain available as optional
extra information. These five choices are distinct from manual skip/finished
events, which Telegram native audio cannot report.

## In Telegram

- `/decisions` or `/verdicts` opens all five lists; each is also on
  `/playlists` and the home keyboard
- Tap a list to see its tracks (8 per page), change decisions, search external
  music in Yandex, or play a playlist of Telegram-available tracks
- Each uploaded song has all five decisions as **one-tap buttons**
- When the bot sends an album-like group of 10 songs, tap the numbered 🧭
  track button, then select one of five reactions
- External `/find`, `/radio`, `/recommend`, and `/weekly` results offer
  `🧭 Оценить` for metadata-only MusicBrainz recordings. This opens the
  same five-choice picker
- Optional direct command for Telegram-uploaded files:
  `/decide 42 maybe` (other codes: `add`, `never`, `later`, `interesting`)

The same external song is not repeatedly presented by personalized discovery
once assessed. After the owner confirms a MusicBrainz↔Telegram identity,
subsequent verdict changes synchronize across the local and catalog views.

## Telegram Mini App

The **🧭 Пять реакций** section appears above ordinary playlists. It allows
opening any of the five lists, browsing pages, changing decisions, sending
available tracks to the native Telegram audio queue, and following Yandex
Music search links for catalog-only discoveries.

The normal library and external-discovery cards also show optional five-way
decision selectors; the old 1–10 score selector remains optional. Endpoints
`GET /api/decisions`, `GET /api/decisions/{status}`,
`POST /api/decisions/local/{id}`, `POST /api/decisions/catalog/{mbid}`,
and `POST /api/decisions/{status}/play` all require the existing Telegram
initData authorization for the exact numeric owner.

Existing explicit local likes are migrated as `add`; old dislikes as
`never`. Preexisting 1–10 ratings are not turned into any decision.

## Listening restrictions

A song uploaded to *this exact* Telegram bot has a reusable Telegram file_id:
the playlist displays ▶ and can enqueue its actual audio. Opening a category's
`▶ Слушать доступные` button creates a standard native audio queue, even
for `never` or `interesting`: **manual review is always allowed**.

A catalog discovery does **not** automatically have playable Telegram audio.
The playlist displays an explicit **external Yandex Music search link**; the
user can select the recording and listen through Yandex subject to their
account and geographic catalog. The button does not pretend that MusicBrainz
metadata or an unrelated YouTube title can be played in Telegram.

## Personalization and privacy

Normal Wave excludes `never`, `later`, and `interesting` statuses from
automatic playback. This is a **per-song** exclusion, not an artist ban.
`maybe` is a weak, reversible positive but never automatically becomes a like.
`add` locally marks the song liked; the category stays separate for review.

The full private taste export includes local and catalog decisions, so future
human/AI analysis can understand the difference between interest, a passing
mood, and strong dislikes. The current public GitHub repo contains code only,
not the owner's decisions or private playlists.
