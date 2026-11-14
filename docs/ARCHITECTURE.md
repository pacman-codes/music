# Архитектура music

## Разделение по процессам

- music_bot.main: aiogram polling, личный доступ по Telegram from_user.id и приватному chat.type.
- music_bot.api: FastAPI, HTML/CSS/JS Mini App, SQLite и отправка порций в Telegram.
- music_bot.webapp_auth: HMAC-SHA-256 подпись Telegram initData, ограничение времени жизни 1 час и OWNER_TELEGRAM_ID.
- music_bot.storage: единая база SQLite (file_id, треки, лайки, плейлисты, сохранённые очереди, история выданных треков).
- music_bot.recommend: локальное взвешенное ранжирование по оценкам, ручным событиям, избранному.
- music_bot.selection: пересчёт следующей порции радио по запросу «Ещё 10» с учётом актуальных оценок и недавних отправок.
- music_bot.widget_bridge: подготовка валидатора будущих widget_query (неактивен, Telegram пока блокирует обычные production-боты).
- web/: адаптивный интерфейс. Не загружает аудио напрямую и не использует собственный player.

## Как связаны плеер и интерфейс

1. Владелец открывает HTTPS Mini App через кнопку бота.
2. Клиент передаёт Telegram.WebApp.initData в заголовке X-Telegram-Init-Data.
3. Backend проверяет криптографическую подпись, auth_date, ID владельца.
4. API выбирает плейлист или первые 10 песен «Волны». Плейлист хранит snapshot, радио — только текущую порцию.
5. Backend отправляет первую порцию по 1–10 Telegram file_id в личный чат ботом.
6. Следующие порции запускаются кнопкой «Ещё 10» в Telegram: радио пересчитывается по свежим оценкам, а пользовательский плейлист идёт в прежнем порядке.
7. Нативный плеер Telegram воспроизводит присланные аудиофайлы; Mini App не перехватывает Play/Next и не считывает фактические прослушивания.

## Структура данных

tracks: id, file_id, file_unique_id, title, artist.
playlists: id, name, kind (liked/custom).
playlist_tracks: playlist_id, track_id, position.
dislikes: track_id.
queues: id, label, source_kind, cursor, created_at.
queue_items: queue_id, position, track_id.
sent_history: id, queue_id, track_id, sent_at.
track_ratings: track_id, score (1..10).
playback_events: track_id, event (manual skip/complete), source, dedupe_key, created_at.
widget_bindings: chat_id + message_id → track_id (только подготовка).
widget_receipts: query_id → результат (идемпотентность при будущем включении).

SQLite CREATE IF NOT EXISTS расширяет v0.1 без сброса медиа и плейлистов. Пользовательские плейлисты используют snapshots; у радио следующие порции добавляются к очереди при запросе. У всех очередей SQLite хранит уже выданные позиции, предотвращая случайное повторение того же блока.

## Приватность и инфраструктура

- Нет открытых публичных API с данными пользователя. Все /api/* endpoints требуют валидную подпись Telegram.
- Статические веб-ресурсы и /healthz публичны, не содержат приватных медиа.
- В Compose веб-порт выставляется только на 127.0.0.1:8085 для последующего HTTPS reverse proxy.
- Без публичного HTTPS WEBAPP_URL в конфигурации бота остаётся пустым и кнопка Mini App не показывается.
- Токен и ID в переменных окружения, не в Git; бот и API используют один bot token, но отдельные процессы.
- Ни один контейнер не использует ресурсы Soodoo или чужой DNS. Реальный деплой не проводился.
- У Mini App нет CORS для посторонних origin; поля HTML выводятся через textContent, а не вставку неэкранированного HTML.

## Ограничения и roadmap

- Telegram file_id — не HTTP URL и привязан к загрузившему файлы боту.
- Прямого воспроизведения произвольной ссылки в нативном Telegram player нет.
- Внешние медиа/рекомендательные источники пока не подключены: сначала проектируем adapters, нормализацию записей, проверку прав и квоты.
- Нет автозагрузки музыки с YouTube, торрент-клиента и браузерного видеоплеера.
- В будущем: MusicBrainz/ListenBrainz, новые кандидаты, «Радио по треку», поиск TMDB/AniList и внешних допустимых источников.
