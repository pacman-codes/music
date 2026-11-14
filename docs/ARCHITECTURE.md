# Архитектура music

## Разделение по процессам

- music_bot.main: aiogram polling, личный доступ по Telegram from_user.id и приватному chat.type.
- music_bot.api: FastAPI, HTML/CSS/JS Mini App, SQLite и отправка порций в Telegram.
- music_bot.webapp_auth: HMAC-SHA-256 подпись Telegram initData, ограничение времени жизни 1 час и OWNER_TELEGRAM_ID.
- music_bot.storage: единая база SQLite (file_id, треки, лайки, плейлисты, сохранённые очереди, история выданных треков).
- music_bot.recommend: локальная взвешенная «Волна» (любимое, близкие исполнители, снижение веса недавно отправленных треков).
- web/: адаптивный интерфейс. Не загружает аудио напрямую и не использует собственный player.

## Как связаны плеер и интерфейс

1. Владелец открывает HTTPS Mini App через кнопку бота.
2. Клиент передаёт Telegram.WebApp.initData в заголовке X-Telegram-Init-Data.
3. Backend проверяет криптографическую подпись, auth_date, ID владельца.
4. API выбирает плейлист/Волну, сохраняет snapshot очереди.
5. Backend отправляет первую порцию по 1–10 Telegram file_id в личный чат ботом.
6. Следующие порции запускаются кнопкой «Ещё 10» в Telegram.
7. Нативный плеер Telegram воспроизводит присланные аудиофайлы; Mini App не перехватывает Play/Next и не считывает фактические прослушивания.

## Структура данных

tracks: id, file_id, file_unique_id, title, artist.
playlists: id, name, kind (liked/custom).
playlist_tracks: playlist_id, track_id, position.
dislikes: track_id.
queues: id, label, source_kind, cursor, created_at.
queue_items: queue_id, position, track_id.
sent_history: id, queue_id, track_id, sent_at.

SQLite CREATE IF NOT EXISTS расширяет v0.1 без сброса медиа и плейлистов. Длинные очереди считаются snapshot: перестановка плейлиста не ломает уже выданную очередь.

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
