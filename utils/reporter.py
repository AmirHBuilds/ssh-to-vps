"""
Admin Reporter - Sends beautiful reports to admin group with topics
"""
import os
import logging
from datetime import datetime
from telegram import Bot
from telegram.constants import ParseMode

logger = logging.getLogger(__name__)

ADMIN_GROUP_ID = int(os.getenv("ADMIN_GROUP_ID", "0"))

TOPICS = {
    "new_connection": int(os.getenv("TOPIC_NEW_CONNECTION", "1")),
    "commands": int(os.getenv("TOPIC_COMMANDS", "2")),
    "errors": int(os.getenv("TOPIC_ERRORS", "3")),
    "auth": int(os.getenv("TOPIC_AUTH", "4")),
    "disconnections": int(os.getenv("TOPIC_DISCONNECTIONS", "5")),
}


async def send_admin_report(bot: Bot, topic: str, message: str, parse_mode=ParseMode.HTML):
    """Send a report to the admin group in the appropriate topic."""
    if not ADMIN_GROUP_ID:
        return
    thread_id = TOPICS.get(topic)
    try:
        kwargs = {
            "chat_id": ADMIN_GROUP_ID,
            "text": message,
            "parse_mode": parse_mode,
        }
        if thread_id:
            kwargs["message_thread_id"] = thread_id
        await bot.send_message(**kwargs)
    except Exception as e:
        logger.error(f"Failed to send admin report [{topic}]: {e}")


def fmt_user(telegram_id: int, username: str = None, first_name: str = None) -> str:
    name = first_name or "Unknown"
    uname = f"@{username}" if username else "no username"
    return f"<a href='tg://user?id={telegram_id}'>{name}</a> ({uname}, <code>{telegram_id}</code>)"


def now_str():
    return datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S UTC")


async def report_new_connection(bot, user, host, port, username, auth_type, keep_alive):
    msg = (
        f"🟢 <b>New SSH Connection</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"👤 <b>User:</b> {fmt_user(user.telegram_id, user.username, user.first_name)}\n"
        f"🖥️ <b>Server:</b> <code>{host}:{port}</code>\n"
        f"👨‍💻 <b>SSH User:</b> <code>{username}</code>\n"
        f"🔑 <b>Auth:</b> <code>{auth_type}</code>\n"
        f"💓 <b>Keep-Alive:</b> {'✅ Enabled' if keep_alive else '❌ Disabled'}\n"
        f"⏰ <b>Time:</b> <code>{now_str()}</code>"
    )
    await send_admin_report(bot, "new_connection", msg)


async def report_command(bot, user, command, host):
    cmd_preview = command[:80] + "..." if len(command) > 80 else command
    msg = (
        f"⌨️ <b>Command Executed</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"👤 <b>User:</b> {fmt_user(user.telegram_id, user.username, user.first_name)}\n"
        f"🖥️ <b>Server:</b> <code>{host}</code>\n"
        f"💬 <b>Command:</b>\n<pre>{cmd_preview}</pre>\n"
        f"⏰ <b>Time:</b> <code>{now_str()}</code>"
    )
    await send_admin_report(bot, "commands", msg)


async def report_disconnect(bot, user, host, reason, duration_str=""):
    msg = (
        f"🔴 <b>SSH Disconnected</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"👤 <b>User:</b> {fmt_user(user.telegram_id, user.username, user.first_name)}\n"
        f"🖥️ <b>Server:</b> <code>{host}</code>\n"
        f"📋 <b>Reason:</b> {reason}\n"
        f"⏱️ <b>Duration:</b> {duration_str}\n"
        f"⏰ <b>Time:</b> <code>{now_str()}</code>"
    )
    await send_admin_report(bot, "disconnections", msg)


async def report_auth_attempt(bot, user, host, success, auth_type, error_msg=""):
    status = "✅ Success" if success else "❌ Failed"
    msg = (
        f"🔐 <b>Auth Attempt</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"👤 <b>User:</b> {fmt_user(user.telegram_id, user.username, user.first_name)}\n"
        f"🖥️ <b>Server:</b> <code>{host}</code>\n"
        f"🔑 <b>Auth Type:</b> <code>{auth_type}</code>\n"
        f"📊 <b>Status:</b> {status}\n"
        + (f"⚠️ <b>Error:</b> <code>{error_msg}</code>\n" if error_msg else "")
        + f"⏰ <b>Time:</b> <code>{now_str()}</code>"
    )
    await send_admin_report(bot, "auth", msg)


async def report_error(bot, user, error_msg, context=""):
    msg = (
        f"⚠️ <b>Error Report</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"👤 <b>User:</b> {fmt_user(user.telegram_id, user.username, user.first_name)}\n"
        + (f"📍 <b>Context:</b> <code>{context}</code>\n" if context else "")
        + f"❌ <b>Error:</b> <pre>{error_msg}</pre>\n"
        f"⏰ <b>Time:</b> <code>{now_str()}</code>"
    )
    await send_admin_report(bot, "errors", msg)
