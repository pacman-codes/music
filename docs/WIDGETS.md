# Telegram HTML Widgets for music

Status (2026-11-14): prototype only, NOT active in the bot. Source:
https://core.telegram.org/bots/widgets

The pure HTML generator music_bot/widgets.py shows 1–10 rating, manually reported
skip and completion actions. It escapes catalog metadata, uses no external scripts
and validates Telegram compressed/UTF-8 size limits. This does NOT mean the
Telegram widget platform itself is proven secure.

## Security

Telegram Bot API 10.4 supports widget_query and answerWidgetQuery, but media
controls inside native Telegram player are NOT exposed as events to bots.
November 2026 reports disclosed QuickJS runtime memory-safety/RCE bugs.
Telegram reported fixes, including server mitigation and newer clients.
Private bots reduce attacker access but do NOT themselves patch Telegram clients.

**Critical rollout blocker as of Nov 14, 2026:** https://github.com/tdlib/telegram-bot-api/issues/947
reports `INPUT_MEDIA_WIDGET_NOT_ALLOWED` from `sendWidget` even in private chats.
A Telegram contributor stated on Nov 13 that *general bots currently are not
allowed to use widgets even in private chats*. This overrides the generic
availability table in https://core.telegram.org/bots/widgets for our bot.
The feature remains OFF until Telegram re-enables ordinary production bots,
aiogram supports 10.4, and official patched mobile/desktop clients are verified. Native inline keyboard and Mini App
must remain functional fallbacks.

## Protocol to implement before enabling

1. Owner-only private Telegram chat and explicit feature flag default false.
2. Bot calls sendWidget with escaped HTML, is_shared=false, allow_queries=true.
   Never put tokens, private URLs or user-provided scripts into widget code.
3. Store message chat_id + message_id -> track_id on server (SQLite).
4. Accept widget_query only from authenticated owner id, matching private chat,
   known message binding, known request "rate:N"/"skip"/"complete".
5. Do not accept arbitrary track_id in request data. It belongs to the message.
6. Deduplicate queries by widget_query.id and acknowledge every query using
   answerWidgetQuery within 15 seconds, including invalid requests.
7. Update rating or record MANUAL skip/complete. Never infer native playback.
8. Limit the rate and avoid wallets, fetching, open links, shared widgets.
9. On broken or unsupported widgets use standard buttons/Mini App.
10. Disable integration on any renewed widget runtime security advisory.

The aiogram v3 PR for Bot API 10.4 support was pending during implementation.
Do not send widgets that request callbacks until update delivery is tested.
