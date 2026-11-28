"""Owner-only controls for the shared Wave preferences."""
from aiogram import F, Router
from aiogram.filters import Command
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message


def wave_settings_page(db):
    values = db.get_wave_settings()
    text = ("🎵 LAIN · Настройки Волны\n"
            f"Разнообразие: {values['diversity']}%\n"
            f"Защита от повторов: {values['repeat_window']} последних отправок\n\n"
            "Больше разнообразия — меньше песен одного артиста подряд. "
            "Пропорции выбранного режима сохраняются.\n"
            "Окно повторов — предпочтение, не строгий запрет. При нехватке песен "
            "Волна может повторять их. Последние 3 отправки всегда получают базовую защиту.")
    rows = []
    for key, options in (("diversity", (0,25,50,75,100)), ("repeat_window", (0,10,30,60,100))):
        rows.append([InlineKeyboardButton(
            text=("✓ " if values[key] == n else "") + str(n) + ("%" if key=="diversity" else " ♫"),
            callback_data=f"waveset:{key}:{n}") for n in options])
    rows.append([InlineKeyboardButton(text="Сбросить",callback_data="waveset:reset"),
                 InlineKeyboardButton(text="🏠 Меню",callback_data="nav:home")])
    return text, InlineKeyboardMarkup(inline_keyboard=rows)


def install_wave_settings(router: Router, db, render_panel):
    @router.message(Command("wavesettings"))
    async def command(message: Message):
        text, markup = wave_settings_page(db)
        await render_panel(message,text,markup)

    @router.callback_query(F.data.startswith("waveset:"))
    async def callback_handler(callback: CallbackQuery):
        parts = (callback.data or "").split(":")
        if not isinstance(callback.message, Message):
            await callback.answer()
            return
        if parts == ["waveset","reset"]:
            db.set_wave_settings(diversity=50,repeat_window=10)
        elif parts != ["waveset","open"]:
            if (len(parts)!=3 or parts[1] not in {"diversity","repeat_window"}
                    or not parts[2].isascii() or not parts[2].isdigit()
                    or len(parts[2])>3 or int(parts[2])>100):
                await callback.answer("Неверная настройка",show_alert=True)
                return
            db.set_wave_settings(**{parts[1]:int(parts[2])})
        await callback.answer()
        text, markup = wave_settings_page(db)
        await render_panel(callback.message,text,markup,edit=True)
