# music project rules

Only personal media bot. No VOID/Soodoo subscription, payment, VPN, production-management logic or credentials.

- Never deploy or alter Soodoo production as part of music.
- Require private chat and exact numeric owner Telegram user ID.
- Never commit .env, passwords, tokens, private source URLs, SQLite or media.
- Telegram native player needs actually uploaded media; file_id is reusable only with the corresponding bot.
- No fake play/skip analytics: Bot API does not give these events.
- Update docs and tests with all structural changes.
- Test with python -m pytest.
- External providers must respect content rights, API limits, URL validation, rate limits.
