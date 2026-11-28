"""Owner-only current taste and track explanation panels."""
from aiogram import F, Router
from aiogram.filters import Command
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message

from music_bot.catalog import CatalogError
from music_bot.taste_profile import music_profile, explain_wave_track, profile_genres


def button(text, callback_data):
    return InlineKeyboardButton(text=text, callback_data=callback_data)


def profile_page(db):
    data = music_profile(db)
    lines = ["🎵 LAIN · Музыкальный профиль",
             f"В медиатеке: {data['library_count']} · для Волны: {data['eligible_count']}",
             f"Избранное: {data['liked_count']} · оценено: {data['rated_count']}",
             "", "Главные исполнители в текущем профиле:"]
    for item in data["artists"][:8]:
        lines.append(f"• {item['name'][:80]} · вес {item['affinity']:.2f}")
    if not data["artists"]:
        lines.append("Пока мало положительных сигналов.")
    lines.append("\nНедавние положительные оценки и реакции (30 дней):")
    for item in data["recent_positive_artists"][:5]:
        lines.append(f"• {item['name'][:80]} · {item['tracks']} треков")
    if not data["recent_positive_artists"]:
        lines.append("Нет недавних положительных отметок.")
    lines.append("\nВес — внутренний коэффициент, не процент совпадения. "
                 "Это текущие предпочтения, а не история изменения оценок.")
    return "\n".join(lines), InlineKeyboardMarkup(inline_keyboard=[
        [button("🎧 Жанры избранных артистов", "tasteprofile:genres")],
        [button("⚙ Волна","waveset:open"),button("↻ Обновить","tasteprofile:open")],
        [button("🏠 Меню","nav:home")]])


def install_taste_profile(router: Router, db, catalog, render_panel):
    async def show(message, edit=False):
        text, markup = profile_page(db)
        await render_panel(message,text,markup,edit=edit)

    @router.message(Command("tasteprofile"))
    async def command(message: Message):
        await show(message)

    @router.callback_query(F.data == "tasteprofile:open")
    async def open_profile(callback: CallbackQuery):
        await callback.answer()
        if isinstance(callback.message,Message):
            await show(callback.message,True)

    @router.callback_query(F.data == "tasteprofile:genres")
    async def genres(callback: CallbackQuery):
        await callback.answer()
        if not isinstance(callback.message,Message):
            return
        if catalog is None:
            await callback.message.answer("Каталог недоступен.")
            return
        try:
            data = await profile_genres(db,catalog)
        except CatalogError as exc:
            await callback.message.answer(str(exc))
            return
        lines = ["🎵 LAIN · Жанры избранных артистов",
                 "MusicBrainz · до 3 сохранённых артистов. Не анализ аудио."]
        for item in data["artists"]:
            lines.append(f"• {item['name'][:100]}: " + (", ".join(item["genres"]) or "нет жанров"))
        if not data["requested"]:
            lines.append("Сначала сохрани исполнителей.")
        if data["unavailable"]:
            lines.append(f"Недоступно: {data['unavailable']}")
        await render_panel(callback.message,"\n".join(lines)[:3800],
            InlineKeyboardMarkup(inline_keyboard=[[button("← Профиль","tasteprofile:open")]]),edit=True)

    @router.callback_query(F.data.startswith("wavewhy:"))
    async def why(callback: CallbackQuery):
        raw = (callback.data or "").partition(":")[2]
        if not raw.isascii() or not raw.isdigit() or len(raw)>18:
            await callback.answer("Неверный трек",show_alert=True)
            return
        try:
            data = explain_wave_track(db,int(raw))
        except ValueError:
            await callback.answer("Трек не найден",show_alert=True)
            return
        await callback.answer()
        if not isinstance(callback.message,Message):
            return
        categories = {"familiar":"Любимое","similar":"Похожее","new":"Исследование"}
        lines = ["🎵 LAIN · Факторы Волны",
                 f"{data['artist'][:80]} — {data['title'][:100]}",
                 categories.get(data["category"],"Не входит в текущий пул")]
        lines.extend("＋ "+reason for reason in data["reasons"])
        lines.extend("• "+reason for reason in data["cautions"])
        lines.append(data["note"])
        await render_panel(callback.message,"\n".join(lines),
            InlineKeyboardMarkup(inline_keyboard=[[button("← Трек",f"vopen:l:{raw}"),
                button("Профиль","tasteprofile:open")]]),edit=True)
