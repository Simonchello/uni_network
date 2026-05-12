"""Main bot entry point."""
import asyncio
import logging
import re
from urllib.parse import quote

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    ContextTypes,
    filters,
)

from config import (
    BOT_TOKEN, ADMIN_IDS, SERVER_IP, VISION_PORT, XHTTP_PORT,
    SNI, FINGERPRINT, PUBLIC_KEY, SHORT_ID, XHTTP_PATH,
)
import db
import xray_manager
import stats


logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)


def is_admin(telegram_id: int) -> bool:
    return telegram_id in ADMIN_IDS


def make_vision_link(user_uuid: str, label: str) -> str:
    return (
        f"vless://{user_uuid}@{SERVER_IP}:{VISION_PORT}"
        f"?encryption=none&flow=xtls-rprx-vision"
        f"&security=reality&sni={SNI}&fp={FINGERPRINT}"
        f"&pbk={PUBLIC_KEY}&sid={SHORT_ID}&type=tcp"
        f"#{quote(label)}-Vision"
    )


def make_xhttp_link(user_uuid: str, label: str) -> str:
    return (
        f"vless://{user_uuid}@{SERVER_IP}:{XHTTP_PORT}"
        f"?encryption=none"
        f"&security=reality&sni={SNI}&fp={FINGERPRINT}"
        f"&pbk={PUBLIC_KEY}&sid={SHORT_ID}"
        f"&type=xhttp&path={quote(XHTTP_PATH)}"
        f"#{quote(label)}-XHTTP"
    )


async def migrate_legacy_keys():
    """Import existing Xray users (created manually) into DB as legacy."""
    try:
        clients = xray_manager.list_clients()
    except Exception as e:
        logger.error(f"Failed to read xray config during migration: {e}")
        return

    imported = 0
    for email, uuid_str in clients:
        if not await db.email_exists(email):
            await db.create_key_record(
                telegram_id=None,
                email=email,
                uuid_str=uuid_str,
                is_legacy=True,
            )
            imported += 1
    if imported:
        logger.info(f"Imported {imported} legacy keys into DB")


# ========== user commands ==========

async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    logger.info(f"/start from telegram_id={user.id} username=@{user.username} name={user.first_name}")
    await db.upsert_user(user.id, user.username)

    # Apply any pending admin-granted allowance
    applied = 0
    if user.username:
        applied = await db.consume_pending_allowance(user.username)
        if applied:
            await db.increase_keys_limit(user.id, applied)

    u = await db.get_user(user.id)
    remaining = u["keys_limit"] - u["keys_created"]

    # Admin branch
    if is_admin(user.id):
        msg = f"👋 Привет, {user.first_name}!\n\n🛡️ Ты админ.\n"
        msg += "Команды: /stats, /users, /pending, /allow, /revoke\n"
        msg += "Или просто отправь @username чтобы +1 ключ."
        await update.message.reply_text(msg)
        return

    # User has keys or allowance — normal welcome
    if remaining > 0 or u["keys_created"] > 0:
        msg = f"👋 Привет, {user.first_name}!\n\n"
        if applied:
            msg += f"✅ Админ выдал тебе {applied} разрешение(й) на ключ.\n"
        if remaining > 0:
            msg += f"Доступно ключей: {remaining}\nИспользуй /getkey чтобы создать."
        else:
            msg += "Ты исчерпал лимит ключей. Обратись к админу @account_for_programming."
        await update.message.reply_text(msg)
        return

    # User already has a pending request
    existing_req = await db.get_pending_request(user.id)
    if existing_req:
        await update.message.reply_text(
            f"⏳ Привет, {user.first_name}!\n\n"
            f"Твой запрос уже на рассмотрении у админа. Жди ответа."
        )
        return

    # New user, no allowance — ask for intro
    await db.set_awaiting_intro(user.id, True)
    await update.message.reply_text(
        f"👋 Привет, {user.first_name}!\n\n"
        "🤔 Думаем, выдавать ли тебе ключ...\n\n"
        "Напиши одним сообщением: *кто ты* и *зачем тебе ключ*.\n"
        "Это отправится на рассмотрение админу.",
        parse_mode="Markdown",
    )


async def cmd_getkey(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    u = await db.get_user(user.id)
    if not u:
        await update.message.reply_text("Сначала сделай /start")
        return

    if u["keys_created"] >= u["keys_limit"]:
        await update.message.reply_text(
            "❌ У тебя нет разрешений на создание новых ключей. Обратись к админу @account_for_programming."
        )
        return

    username = u["username"] or "noname"
    # Уникальный email: {id}_{username}_{номер_ключа}
    # keys_created инкрементируется в create_key_record, поэтому +1 для нового
    key_num = u["keys_created"] + 1
    email = f"{user.id}_{username}_{key_num}"

    await update.message.reply_text("⏳ Создаю ключ (перезапускаю Xray)...")

    try:
        new_uuid = await asyncio.to_thread(xray_manager.add_client, email)
    except Exception as e:
        logger.error(f"Failed to add client {email}: {e}")
        await update.message.reply_text(f"❌ Ошибка при создании ключа: {e}")
        return

    await db.create_key_record(user.id, email, new_uuid)

    vision = make_vision_link(new_uuid, email)
    xhttp = make_xhttp_link(new_uuid, email)

    msg = (
        "✅ Ключ создан!\n\n"
        "*Vision (скорость, основной):*\n"
        f"`{vision}`\n\n"
        "*XHTTP (устойчивость, мобильный):*\n"
        f"`{xhttp}`\n\n"
        "Импортируй один из них в Hiddify Next / v2rayNG."
    )
    await update.message.reply_text(msg, parse_mode="Markdown")


async def cmd_mykeys(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    keys = await db.get_keys_by_user(user.id)
    if not keys:
        await update.message.reply_text("У тебя пока нет ключей.")
        return

    user_stats = await asyncio.to_thread(stats.query_user_stats)

    lines = [f"🔑 Твои ключи ({len(keys)}):\n"]
    for k in keys:
        s = user_stats.get(k["email"], {"uplink": 0, "downlink": 0})
        total = s["uplink"] + s["downlink"]
        lines.append(f"• {k['email']}: {stats.format_bytes(total)}")

    await update.message.reply_text("\n".join(lines))


# ========== admin commands ==========

async def cmd_stats(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    if not is_admin(user.id):
        await update.message.reply_text("❌ Доступ только для админов.")
        return

    all_keys = await db.get_all_keys()
    user_stats = await asyncio.to_thread(stats.query_user_stats)

    combined = []
    for k in all_keys:
        s = user_stats.get(k["email"], {"uplink": 0, "downlink": 0})
        combined.append({
            "email": k["email"],
            "username": k.get("owner_username"),
            "telegram_id": k.get("telegram_id"),
            "is_legacy": bool(k["is_legacy"]),
            "uplink": s["uplink"],
            "downlink": s["downlink"],
            "total": s["uplink"] + s["downlink"],
        })

    combined.sort(key=lambda x: x["total"], reverse=True)

    total_up = sum(c["uplink"] for c in combined)
    total_down = sum(c["downlink"] for c in combined)
    total = total_up + total_down

    lines = [
        f"📊 *Total: {stats.format_bytes(total)}*",
        f"↑ {stats.format_bytes(total_up)}  ↓ {stats.format_bytes(total_down)}",
        f"Active keys: {len(combined)}",
        "",
    ]

    for i, c in enumerate(combined, 1):
        if c["is_legacy"]:
            label = f"{c['email']} [legacy]"
        else:
            uname = f"@{c['username']}" if c.get("username") else "—"
            label = f"{c['email']} ({uname})"
        lines.append(f"{i}. {label}: {stats.format_bytes(c['total'])}")

    msg = "\n".join(lines)
    if len(msg) > 4000:
        msg = msg[:4000] + "\n...(обрезано)"

    await update.message.reply_text(msg, parse_mode="Markdown")


async def cmd_users(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    if not is_admin(user.id):
        await update.message.reply_text("❌ Доступ только для админов.")
        return

    users = await db.list_all_users()
    if not users:
        await update.message.reply_text("Зарегистрированных пользователей нет.")
        return

    lines = [f"👥 Пользователи ({len(users)}):\n"]
    for u in users:
        uname = f"@{u['username']}" if u.get("username") else "—"
        lines.append(
            f"• {u['telegram_id']} {uname}: "
            f"{u['keys_created']}/{u['keys_limit']} ключей"
        )
    await update.message.reply_text("\n".join(lines))


async def cmd_pending(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    if not is_admin(user.id):
        await update.message.reply_text("❌ Доступ только для админов.")
        return

    requests = await db.list_pending_requests()
    allowances = await db.list_pending_allowances()

    if not requests and not allowances:
        await update.message.reply_text("⏳ Pending пусто.")
        return

    lines = []

    if requests:
        lines.append("🔔 *Заявки на ключ (ждут твоего решения):*\n")
        for r in requests:
            uname = f"@{r['username']}" if r.get("username") else "—"
            intro = (r.get("intro_text") or "").strip()
            if len(intro) > 200:
                intro = intro[:200] + "..."
            lines.append(
                f"• {r['first_name']} ({uname}, id {r['telegram_id']}):\n"
                f"  «{intro}»"
            )
        lines.append("")

    if allowances:
        lines.append("📝 *Выданные разрешения (юзер ещё не писал боту):*\n")
        for p in allowances:
            lines.append(f"• @{p['username']}: +{p['amount']} ключ(ей)")

    await update.message.reply_text("\n".join(lines), parse_mode="Markdown")


async def cmd_allow(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    if not is_admin(user.id):
        await update.message.reply_text("❌ Доступ только для админов.")
        return

    args = context.args
    if not args:
        await update.message.reply_text("Использование: /allow @username [N]")
        return

    username = args[0].lstrip("@").lower()
    amount = 1
    if len(args) > 1:
        try:
            amount = int(args[1])
        except ValueError:
            await update.message.reply_text("N должно быть числом")
            return

    await _handle_allowance(update, username, amount, user.id)


async def cmd_revoke(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    if not is_admin(user.id):
        await update.message.reply_text("❌ Доступ только для админов.")
        return

    args = context.args
    if not args:
        await update.message.reply_text("Использование: /revoke @username")
        return

    username = args[0].lstrip("@").lower()
    target = await db.get_user_by_username(username)
    if not target:
        await update.message.reply_text(f"@{username} не найден в базе.")
        return

    emails = await db.delete_user_keys(target["telegram_id"])
    for email in emails:
        try:
            await asyncio.to_thread(xray_manager.remove_client, email)
        except Exception as e:
            logger.error(f"Failed to remove xray client {email}: {e}")

    await update.message.reply_text(
        f"✅ @{username}: удалено {len(emails)} ключ(ей), доступ отозван."
    )


async def handle_username_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Admin shorthand: bare '@username' message = +1 allowance."""
    user = update.effective_user
    if not is_admin(user.id):
        return

    text = update.message.text.strip()
    m = re.fullmatch(r"@(\w+)", text)
    if not m:
        return

    username = m.group(1).lower()
    await _handle_allowance(update, username, 1, user.id)


async def handle_intro_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Any non-command text from user awaiting intro → save + notify admins."""
    user = update.effective_user
    if is_admin(user.id):
        return  # admins use @username shortcut

    u = await db.get_user(user.id)
    if not u or not u.get("awaiting_intro"):
        return  # not expecting intro from this user

    intro_text = update.message.text.strip()
    if not intro_text:
        return

    # Save request, clear awaiting flag
    await db.create_pending_request(user.id, user.username, user.first_name, intro_text)
    await db.set_awaiting_intro(user.id, False)

    # Notify admins with Accept/Decline buttons
    admin_msg = (
        f"🔔 *Новый запрос на ключ*\n\n"
        f"От: {user.first_name} "
        f"({'@' + user.username if user.username else 'без username'}, id `{user.id}`)\n\n"
        f"Сообщение:\n«{intro_text}»"
    )
    keyboard = InlineKeyboardMarkup([[
        InlineKeyboardButton("✅ Accept", callback_data=f"acc:{user.id}"),
        InlineKeyboardButton("❌ Decline", callback_data=f"dec:{user.id}"),
    ]])
    for admin_id in ADMIN_IDS:
        try:
            await context.bot.send_message(
                chat_id=admin_id,
                text=admin_msg,
                reply_markup=keyboard,
                parse_mode="Markdown",
            )
        except Exception as e:
            logger.error(f"Failed to notify admin {admin_id}: {e}")

    await update.message.reply_text(
        "✅ Запрос отправлен админу. Жди ответа."
    )


async def handle_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Accept/Decline button callback from admin."""
    query = update.callback_query
    clicker = query.from_user

    if not is_admin(clicker.id):
        await query.answer("❌ Доступ только для админов.", show_alert=True)
        return

    await query.answer()

    try:
        action, user_id_str = query.data.split(":", 1)
        user_id = int(user_id_str)
    except (ValueError, AttributeError):
        await query.edit_message_text(
            (query.message.text or "") + "\n\n⚠ Невалидный callback"
        )
        return

    req = await db.get_pending_request(user_id)
    if not req:
        await query.edit_message_text(
            (query.message.text or "") + "\n\n⚠ Запрос уже обработан другим админом."
        )
        return

    original = query.message.text or ""

    if action == "acc":
        await db.increase_keys_limit(user_id, 1)
        await db.delete_pending_request(user_id)
        await query.edit_message_text(
            original + f"\n\n✅ Одобрено ({clicker.first_name})"
        )
        try:
            await context.bot.send_message(
                chat_id=user_id,
                text=(
                    "✅ *Твой запрос одобрен!*\n\n"
                    "Теперь ты можешь создать VPN-ключ: /getkey"
                ),
                parse_mode="Markdown",
            )
        except Exception as e:
            logger.error(f"Failed to notify user {user_id} of accept: {e}")

    elif action == "dec":
        await db.delete_pending_request(user_id)
        await query.edit_message_text(
            original + f"\n\n❌ Отклонено ({clicker.first_name})"
        )
        try:
            await context.bot.send_message(
                chat_id=user_id,
                text="❌ Твой запрос на доступ отклонён.",
            )
        except Exception as e:
            logger.error(f"Failed to notify user {user_id} of decline: {e}")


async def _handle_allowance(update: Update, username: str, amount: int, admin_id: int):
    existing = await db.get_user_by_username(username)
    if existing:
        await db.increase_keys_limit(existing["telegram_id"], amount)
        u = await db.get_user(existing["telegram_id"])
        remaining = u["keys_limit"] - u["keys_created"]
        await update.message.reply_text(
            f"✅ @{username} (id {existing['telegram_id']}): "
            f"+{amount} ключ(ей). Доступно: {remaining}"
        )
    else:
        await db.add_pending_allowance(username, amount, admin_id)
        await update.message.reply_text(
            f"⏳ @{username} ещё не писал боту. Разрешение в очереди (+{amount}).\n"
            f"Попроси его сделать /start — разрешение применится автоматически."
        )


async def post_init(application: Application):
    await db.init_db()
    await migrate_legacy_keys()
    logger.info(f"Bot initialized. Admins: {ADMIN_IDS}")


def main():
    if not BOT_TOKEN or BOT_TOKEN == "YOUR_BOT_TOKEN_FROM_BOTFATHER":
        raise RuntimeError("BOT_TOKEN not configured in config.py")
    if not ADMIN_IDS:
        logger.warning("ADMIN_IDS is empty — no admin commands will work")

    app = Application.builder().token(BOT_TOKEN).post_init(post_init).build()

    # User commands
    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("getkey", cmd_getkey))
    app.add_handler(CommandHandler("mykeys", cmd_mykeys))

    # Admin commands
    app.add_handler(CommandHandler("stats", cmd_stats))
    app.add_handler(CommandHandler("users", cmd_users))
    app.add_handler(CommandHandler("pending", cmd_pending))
    app.add_handler(CommandHandler("allow", cmd_allow))
    app.add_handler(CommandHandler("revoke", cmd_revoke))

    # Admin shorthand: plain '@username' (goes first; matches only bare @username)
    app.add_handler(MessageHandler(
        filters.TEXT & ~filters.COMMAND & filters.Regex(r"^@\w+$"),
        handle_username_message,
    ))

    # Intro text from new users (non-@username, non-command)
    app.add_handler(MessageHandler(
        filters.TEXT & ~filters.COMMAND,
        handle_intro_text,
    ))

    # Inline keyboard callbacks (Accept/Decline)
    app.add_handler(CallbackQueryHandler(handle_callback, pattern=r"^(acc|dec):\d+$"))

    logger.info("Starting bot polling...")
    app.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    main()
