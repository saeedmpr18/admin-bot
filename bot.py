# -*- coding: utf-8 -*-
"""
ربات مدیریت گروه تلگرام
قابلیت‌ها:
  - حذف خودکار لینک
  - فیلتر کلمات ناشایست (قابل تنظیم توسط ادمین)
  - سیستم اخطار خودکار (بعد از رسیدن به سقف -> میوت یا بن)
  - خوش‌آمدگویی به اعضای جدید
  - دستورات کامل مدیریتی: warn, unwarn, ban, unban, mute, unmute, ...

نحوه اجرا: python bot.py
توکن ربات باید در متغیر محیطی TELEGRAM_BOT_TOKEN یا در فایل .env قرار بگیره.
"""

import logging
import os
import re
from datetime import timedelta

from telegram import Update, ChatPermissions, ChatMemberUpdated
from telegram.constants import ChatMemberStatus, ParseMode
from telegram.ext import (
    Application,
    ApplicationBuilder,
    ChatMemberHandler,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

import database as db

# ---------------------------------------------------------------------------
# تنظیمات اولیه
# ---------------------------------------------------------------------------

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)

TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")
if not TOKEN:
    # تلاش برای خوندن از فایل .env در صورت وجود
    env_path = os.path.join(os.path.dirname(__file__), ".env")
    if os.path.exists(env_path):
        with open(env_path, encoding="utf-8") as f:
            for line in f:
                if line.strip().startswith("TELEGRAM_BOT_TOKEN"):
                    TOKEN = line.strip().split("=", 1)[1].strip().strip('"').strip("'")

LINK_REGEX = re.compile(
    r"(https?://\S+|t\.me/\S+|www\.\S+|@[\w_]{4,32}|\S+\.(com|ir|net|org|xyz|info|biz|co|shop|online|site)\b)",
    re.IGNORECASE,
)

FULL_PERMISSIONS = ChatPermissions(
    can_send_messages=True,
    can_send_audios=True,
    can_send_documents=True,
    can_send_photos=True,
    can_send_videos=True,
    can_send_video_notes=True,
    can_send_voice_notes=True,
    can_send_polls=True,
    can_send_other_messages=True,
    can_add_web_page_previews=True,
    can_invite_users=True,
)

MUTED_PERMISSIONS = ChatPermissions(can_send_messages=False)


# ---------------------------------------------------------------------------
# توابع کمکی
# ---------------------------------------------------------------------------

async def is_group_admin(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    """چک می‌کنه که فرستنده پیام، ادمین یا سازنده گروه هست یا نه"""
    chat = update.effective_chat
    user = update.effective_user
    if chat.type == "private":
        return False
    member = await context.bot.get_chat_member(chat.id, user.id)
    return member.status in (ChatMemberStatus.ADMINISTRATOR, ChatMemberStatus.OWNER)


async def require_admin(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    """اگه ادمین نبود پیام خطا میده و False برمی‌گردونه"""
    if not await is_group_admin(update, context):
        await update.message.reply_text("⛔️ این دستور فقط برای ادمین‌های گروه در دسترسه.")
        return False
    return True


def get_target_user(update: Update):
    """کاربر هدف رو از روی ریپلای پیدا می‌کنه"""
    if update.message.reply_to_message:
        return update.message.reply_to_message.from_user
    return None


def mention(user) -> str:
    name = user.full_name or user.username or str(user.id)
    return f'<a href="tg://user?id={user.id}">{name}</a>'


# ---------------------------------------------------------------------------
# خوش‌آمدگویی به اعضای جدید
# ---------------------------------------------------------------------------

async def greet_new_member(update: Update, context: ContextTypes.DEFAULT_TYPE):
    result = update.chat_member
    if result is None:
        return
    old_status = result.old_chat_member.status
    new_status = result.new_chat_member.status
    # فقط وقتی که کاربر تازه عضو شده (نه ادمین شده یا آنبن شده و ...)
    joined = old_status in (ChatMemberStatus.LEFT, ChatMemberStatus.BANNED) and new_status == ChatMemberStatus.MEMBER
    if not joined:
        return

    settings = db.get_settings(result.chat.id)
    if not settings["welcome_enabled"]:
        return

    user = result.new_chat_member.user
    text = settings["welcome_message"].replace("{name}", mention(user))
    await context.bot.send_message(chat_id=result.chat.id, text=text, parse_mode=ParseMode.HTML)


# ---------------------------------------------------------------------------
# فیلتر پیام‌ها (لینک و کلمات ناشایست)
# ---------------------------------------------------------------------------

async def filter_messages(update: Update, context: ContextTypes.DEFAULT_TYPE):
    message = update.effective_message
    chat = update.effective_chat
    user = update.effective_user

    if chat.type == "private" or message is None or message.text is None:
        return

    # ادمین‌ها از فیلتر معاف هستن
    try:
        if await is_group_admin(update, context):
            return
    except Exception:
        pass

    settings = db.get_settings(chat.id)
    text_lower = message.text.lower()
    violation = None

    if settings["link_filter"] and LINK_REGEX.search(message.text):
        violation = "لینک"

    if violation is None and settings["word_filter"]:
        banned_words = db.get_banned_words(chat.id)
        for word in banned_words:
            if word in text_lower:
                violation = "کلمه‌ی نامناسب"
                break

    if violation is None:
        return

    # حذف پیام
    try:
        await message.delete()
    except Exception as e:
        logger.warning(f"نتونستم پیام رو حذف کنم: {e}")

    # اضافه کردن اخطار
    count = db.add_warning(chat.id, user.id)
    max_warn = settings["max_warnings"]

    warn_text = (
        f"⚠️ {mention(user)} پیامت به دلیل داشتن {violation} حذف شد.\n"
        f"تعداد اخطار: {count}/{max_warn}"
    )
    sent = await context.bot.send_message(chat_id=chat.id, text=warn_text, parse_mode=ParseMode.HTML)

    if count >= max_warn:
        db.reset_warnings(chat.id, user.id)
        punishment = settings["punishment"]
        try:
            if punishment == "ban":
                await context.bot.ban_chat_member(chat.id, user.id)
                result_text = f"🚫 {mention(user)} به دلیل رسیدن به سقف اخطار از گروه اخراج شد."
            else:
                await context.bot.restrict_chat_member(chat.id, user.id, permissions=MUTED_PERMISSIONS)
                result_text = f"🔇 {mention(user)} به دلیل رسیدن به سقف اخطار سکوت شد."
            await context.bot.send_message(chat_id=chat.id, text=result_text, parse_mode=ParseMode.HTML)
        except Exception as e:
            logger.warning(f"خطا در اعمال مجازات: {e}")


# ---------------------------------------------------------------------------
# دستورات پایه
# ---------------------------------------------------------------------------

async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "سلام! 👋\n"
        "من ربات مدیریت گروه هستم.\n"
        "من رو به گروهت اضافه کن و ادمینم کن تا شروع به کار کنم.\n"
        "برای دیدن دستورات از /help استفاده کن."
    )


async def cmd_help(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = (
        "📋 <b>دستورات ربات مدیریت گروه</b>\n\n"
        "<b>دستورات ادمین (روی پیام کاربر ریپلای کن):</b>\n"
        "/warn - یک اخطار به کاربر بده\n"
        "/unwarn - یک اخطار از کاربر کم کن\n"
        "/warns - تعداد اخطارهای کاربر رو نشون بده\n"
        "/resetwarns - همه اخطارهای کاربر رو پاک کن\n"
        "/ban - کاربر رو از گروه اخراج کن\n"
        "/mute [دقیقه] - کاربر رو ساکت کن (اختیاری: مدت به دقیقه)\n"
        "/unmute - سکوت کاربر رو بردار\n\n"
        "<b>تنظیمات گروه:</b>\n"
        "/linkfilter on|off - فیلتر لینک رو روشن/خاموش کن\n"
        "/wordfilter on|off - فیلتر کلمات رو روشن/خاموش کن\n"
        "/addword کلمه - اضافه کردن کلمه ممنوعه\n"
        "/removeword کلمه - حذف کلمه ممنوعه\n"
        "/listwords - نمایش لیست کلمات ممنوعه\n"
        "/setwelcome متن - تنظیم پیام خوش‌آمدگویی (از {name} برای اسم استفاده کن)\n"
        "/welcome on|off - روشن/خاموش کردن خوش‌آمدگویی\n"
        "/setmaxwarn عدد - تنظیم سقف اخطار\n"
        "/setpunishment mute|ban - تنظیم نوع مجازات بعد از سقف اخطار\n"
        "/settings - نمایش تنظیمات فعلی گروه\n"
    )
    await update.message.reply_text(text, parse_mode=ParseMode.HTML)


async def cmd_settings(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await require_admin(update, context):
        return
    s = db.get_settings(update.effective_chat.id)
    words = db.get_banned_words(update.effective_chat.id)
    text = (
        f"⚙️ <b>تنظیمات فعلی گروه</b>\n\n"
        f"فیلتر لینک: {'روشن ✅' if s['link_filter'] else 'خاموش ❌'}\n"
        f"فیلتر کلمات: {'روشن ✅' if s['word_filter'] else 'خاموش ❌'}\n"
        f"خوش‌آمدگویی: {'روشن ✅' if s['welcome_enabled'] else 'خاموش ❌'}\n"
        f"سقف اخطار: {s['max_warnings']}\n"
        f"نوع مجازات: {s['punishment']}\n"
        f"تعداد کلمات ممنوعه: {len(words)}\n"
    )
    await update.message.reply_text(text, parse_mode=ParseMode.HTML)


# ---------------------------------------------------------------------------
# دستورات اخطار
# ---------------------------------------------------------------------------

async def cmd_warn(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await require_admin(update, context):
        return
    target = get_target_user(update)
    if not target:
        await update.message.reply_text("روی پیام کاربر موردنظر ریپلای کن.")
        return
    chat_id = update.effective_chat.id
    count = db.add_warning(chat_id, target.id)
    settings = db.get_settings(chat_id)
    max_warn = settings["max_warnings"]

    if count >= max_warn:
        db.reset_warnings(chat_id, target.id)
        punishment = settings["punishment"]
        if punishment == "ban":
            await context.bot.ban_chat_member(chat_id, target.id)
            await update.message.reply_text(
                f"🚫 {mention(target)} به دلیل رسیدن به سقف اخطار اخراج شد.", parse_mode=ParseMode.HTML
            )
        else:
            await context.bot.restrict_chat_member(chat_id, target.id, permissions=MUTED_PERMISSIONS)
            await update.message.reply_text(
                f"🔇 {mention(target)} به دلیل رسیدن به سقف اخطار سکوت شد.", parse_mode=ParseMode.HTML
            )
    else:
        await update.message.reply_text(
            f"⚠️ {mention(target)} یک اخطار گرفت. ({count}/{max_warn})", parse_mode=ParseMode.HTML
        )


async def cmd_unwarn(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await require_admin(update, context):
        return
    target = get_target_user(update)
    if not target:
        await update.message.reply_text("روی پیام کاربر موردنظر ریپلای کن.")
        return
    count = db.remove_warning(update.effective_chat.id, target.id)
    await update.message.reply_text(f"✅ یک اخطار کم شد. تعداد فعلی: {count}", parse_mode=ParseMode.HTML)


async def cmd_warns(update: Update, context: ContextTypes.DEFAULT_TYPE):
    target = get_target_user(update) or update.effective_user
    count = db.get_warnings(update.effective_chat.id, target.id)
    settings = db.get_settings(update.effective_chat.id)
    await update.message.reply_text(
        f"{mention(target)} تعداد اخطار: {count}/{settings['max_warnings']}", parse_mode=ParseMode.HTML
    )


async def cmd_resetwarns(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await require_admin(update, context):
        return
    target = get_target_user(update)
    if not target:
        await update.message.reply_text("روی پیام کاربر موردنظر ریپلای کن.")
        return
    db.reset_warnings(update.effective_chat.id, target.id)
    await update.message.reply_text(f"✅ اخطارهای {mention(target)} پاک شد.", parse_mode=ParseMode.HTML)


# ---------------------------------------------------------------------------
# دستورات بن / میوت
# ---------------------------------------------------------------------------

async def cmd_ban(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await require_admin(update, context):
        return
    target = get_target_user(update)
    if not target:
        await update.message.reply_text("روی پیام کاربر موردنظر ریپلای کن.")
        return
    await context.bot.ban_chat_member(update.effective_chat.id, target.id)
    await update.message.reply_text(f"🚫 {mention(target)} از گروه اخراج شد.", parse_mode=ParseMode.HTML)


async def cmd_unban(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await require_admin(update, context):
        return
    if not context.args:
        await update.message.reply_text("آیدی عددی کاربر رو وارد کن. مثال: /unban 123456789")
        return
    try:
        user_id = int(context.args[0])
    except ValueError:
        await update.message.reply_text("آیدی عددی معتبر نیست.")
        return
    await context.bot.unban_chat_member(update.effective_chat.id, user_id, only_if_banned=True)
    await update.message.reply_text("✅ کاربر آنبن شد.")


async def cmd_mute(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await require_admin(update, context):
        return
    target = get_target_user(update)
    if not target:
        await update.message.reply_text("روی پیام کاربر موردنظر ریپلای کن.")
        return

    until_date = None
    if context.args:
        try:
            minutes = int(context.args[0])
            until_date = update.message.date + timedelta(minutes=minutes)
        except ValueError:
            pass

    kwargs = {"permissions": MUTED_PERMISSIONS}
    if until_date:
        kwargs["until_date"] = until_date

    await context.bot.restrict_chat_member(update.effective_chat.id, target.id, **kwargs)
    duration_text = f" برای {context.args[0]} دقیقه" if until_date else ""
    await update.message.reply_text(f"🔇 {mention(target)} سکوت شد{duration_text}.", parse_mode=ParseMode.HTML)


async def cmd_unmute(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await require_admin(update, context):
        return
    target = get_target_user(update)
    if not target:
        await update.message.reply_text("روی پیام کاربر موردنظر ریپلای کن.")
        return
    await context.bot.restrict_chat_member(update.effective_chat.id, target.id, permissions=FULL_PERMISSIONS)
    await update.message.reply_text(f"🔊 سکوت {mention(target)} برداشته شد.", parse_mode=ParseMode.HTML)


# ---------------------------------------------------------------------------
# دستورات تنظیمات
# ---------------------------------------------------------------------------

async def cmd_linkfilter(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await require_admin(update, context):
        return
    if not context.args or context.args[0].lower() not in ("on", "off"):
        await update.message.reply_text("استفاده: /linkfilter on یا /linkfilter off")
        return
    value = 1 if context.args[0].lower() == "on" else 0
    db.update_setting(update.effective_chat.id, "link_filter", value)
    await update.message.reply_text(f"✅ فیلتر لینک {'روشن' if value else 'خاموش'} شد.")


async def cmd_wordfilter(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await require_admin(update, context):
        return
    if not context.args or context.args[0].lower() not in ("on", "off"):
        await update.message.reply_text("استفاده: /wordfilter on یا /wordfilter off")
        return
    value = 1 if context.args[0].lower() == "on" else 0
    db.update_setting(update.effective_chat.id, "word_filter", value)
    await update.message.reply_text(f"✅ فیلتر کلمات {'روشن' if value else 'خاموش'} شد.")


async def cmd_addword(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await require_admin(update, context):
        return
    if not context.args:
        await update.message.reply_text("استفاده: /addword کلمه")
        return
    word = " ".join(context.args)
    db.add_banned_word(update.effective_chat.id, word)
    await update.message.reply_text(f"✅ کلمه‌ی «{word}» به لیست ممنوعه اضافه شد.")


async def cmd_removeword(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await require_admin(update, context):
        return
    if not context.args:
        await update.message.reply_text("استفاده: /removeword کلمه")
        return
    word = " ".join(context.args)
    removed = db.remove_banned_word(update.effective_chat.id, word)
    if removed:
        await update.message.reply_text(f"✅ کلمه‌ی «{word}» حذف شد.")
    else:
        await update.message.reply_text("این کلمه در لیست نبود.")


async def cmd_listwords(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await require_admin(update, context):
        return
    words = db.get_banned_words(update.effective_chat.id)
    if not words:
        await update.message.reply_text("لیست کلمات ممنوعه خالیه.")
        return
    await update.message.reply_text("🚫 کلمات ممنوعه:\n" + "\n".join(f"- {w}" for w in words))


async def cmd_setwelcome(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await require_admin(update, context):
        return
    if not context.args:
        await update.message.reply_text("استفاده: /setwelcome متن پیام (می‌تونی از {name} استفاده کنی)")
        return
    text = update.message.text.split(" ", 1)[1]
    db.update_setting(update.effective_chat.id, "welcome_message", text)
    await update.message.reply_text("✅ پیام خوش‌آمدگویی تنظیم شد.")


async def cmd_welcome_toggle(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await require_admin(update, context):
        return
    if not context.args or context.args[0].lower() not in ("on", "off"):
        await update.message.reply_text("استفاده: /welcome on یا /welcome off")
        return
    value = 1 if context.args[0].lower() == "on" else 0
    db.update_setting(update.effective_chat.id, "welcome_enabled", value)
    await update.message.reply_text(f"✅ خوش‌آمدگویی {'روشن' if value else 'خاموش'} شد.")


async def cmd_setmaxwarn(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await require_admin(update, context):
        return
    if not context.args or not context.args[0].isdigit():
        await update.message.reply_text("استفاده: /setmaxwarn عدد (مثال: /setmaxwarn 3)")
        return
    db.update_setting(update.effective_chat.id, "max_warnings", int(context.args[0]))
    await update.message.reply_text(f"✅ سقف اخطار روی {context.args[0]} تنظیم شد.")


async def cmd_setpunishment(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await require_admin(update, context):
        return
    if not context.args or context.args[0].lower() not in ("mute", "ban"):
        await update.message.reply_text("استفاده: /setpunishment mute یا /setpunishment ban")
        return
    db.update_setting(update.effective_chat.id, "punishment", context.args[0].lower())
    await update.message.reply_text(f"✅ نوع مجازات روی «{context.args[0].lower()}» تنظیم شد.")


# ---------------------------------------------------------------------------
# راه‌اندازی ربات
# ---------------------------------------------------------------------------

def main():
    if not TOKEN:
        raise RuntimeError(
            "توکن ربات پیدا نشد! لطفاً متغیر محیطی TELEGRAM_BOT_TOKEN رو ست کن "
            "یا اون رو در فایل .env قرار بده."
        )

    db.init_db()

    app: Application = ApplicationBuilder().token(TOKEN).build()

    # دستورات پایه
    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("help", cmd_help))
    app.add_handler(CommandHandler("settings", cmd_settings))

    # اخطار
    app.add_handler(CommandHandler("warn", cmd_warn))
    app.add_handler(CommandHandler("unwarn", cmd_unwarn))
    app.add_handler(CommandHandler("warns", cmd_warns))
    app.add_handler(CommandHandler("resetwarns", cmd_resetwarns))

    # بن / میوت
    app.add_handler(CommandHandler("ban", cmd_ban))
    app.add_handler(CommandHandler("unban", cmd_unban))
    app.add_handler(CommandHandler("mute", cmd_mute))
    app.add_handler(CommandHandler("unmute", cmd_unmute))

    # تنظیمات
    app.add_handler(CommandHandler("linkfilter", cmd_linkfilter))
    app.add_handler(CommandHandler("wordfilter", cmd_wordfilter))
    app.add_handler(CommandHandler("addword", cmd_addword))
    app.add_handler(CommandHandler("removeword", cmd_removeword))
    app.add_handler(CommandHandler("listwords", cmd_listwords))
    app.add_handler(CommandHandler("setwelcome", cmd_setwelcome))
    app.add_handler(CommandHandler("welcome", cmd_welcome_toggle))
    app.add_handler(CommandHandler("setmaxwarn", cmd_setmaxwarn))
    app.add_handler(CommandHandler("setpunishment", cmd_setpunishment))

    # خوش‌آمدگویی (نیازمند فعال بودن chat_member updates)
    app.add_handler(ChatMemberHandler(greet_new_member, ChatMemberHandler.CHAT_MEMBER))

    # فیلتر پیام‌ها
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND & filters.ChatType.GROUPS, filter_messages))

    logger.info("ربات در حال اجراست...")
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
