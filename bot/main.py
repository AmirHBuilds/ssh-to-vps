"""
Main Telegram Bot — SSH Bot Handler
Full interactive SSH over Telegram with inline keyboards
"""
import asyncio
import logging
import os
import threading
from datetime import datetime
from typing import Optional

from dotenv import load_dotenv
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.constants import ParseMode
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    ConversationHandler,
    ContextTypes,
    filters,
)

load_dotenv()

from models.database import init_db, get_session, User, SavedServer, SSHSession
from utils.ssh_manager import (
    SSHConnection, SSHConnectionError,
    store_connection, get_connection, remove_connection, get_active_count
)
from utils.keyboards import (
    main_menu_keyboard, auth_type_keyboard, keep_alive_keyboard,
    save_server_keyboard, session_keyboard, saved_servers_keyboard,
    delete_servers_keyboard, confirm_delete_keyboard, cancel_keyboard,
    WELCOME_MESSAGE, HELP_MESSAGE
)
from utils.reporter import (
    report_new_connection, report_command, report_disconnect,
    report_auth_attempt, report_error
)

logger = logging.getLogger(__name__)

# ── Conversation States ───────────────────────────────────────────────────────
(
    MAIN_MENU,
    ENTER_HOST,
    ENTER_PORT,
    ENTER_SSH_USER,
    CHOOSE_AUTH,
    ENTER_PASSWORD,
    ENTER_PRIVATE_KEY,
    ENTER_PASSPHRASE,
    CHOOSE_KEEPALIVE,
    CHOOSE_SAVE,
    ENTER_SERVER_LABEL,
    CONNECTED,
    MANAGE_SERVERS,
) = range(13)

# Per-user pending connection data
pending: dict[int, dict] = {}
# Per-user active session metadata
session_meta: dict[int, dict] = {}


# ── Helpers ───────────────────────────────────────────────────────────────────

def get_or_create_user(db, telegram_user) -> User:
    user = db.query(User).filter(User.telegram_id == telegram_user.id).first()
    if not user:
        user = User(
            telegram_id=telegram_user.id,
            username=telegram_user.username,
            first_name=telegram_user.first_name,
        )
        db.add(user)
        db.commit()
        db.refresh(user)
    else:
        user.username = telegram_user.username
        user.first_name = telegram_user.first_name
        db.commit()
    return user


async def send_output_to_user(bot, chat_id: int, text: str, is_final: bool = False):
    """Send SSH output back to user, formatted nicely."""
    text = text.strip()
    if not text:
        return
    try:
        # Wrap in code block for terminal-like look
        max_len = int(os.getenv("MAX_OUTPUT_LENGTH", "3500"))
        chunks = [text[i:i+max_len] for i in range(0, len(text), max_len)]
        for chunk in chunks:
            await bot.send_message(
                chat_id=chat_id,
                text=f"<pre>{chunk}</pre>",
                parse_mode=ParseMode.HTML,
                reply_markup=session_keyboard() if is_final else None,
            )
    except Exception as e:
        logger.error(f"Failed to send output to {chat_id}: {e}")


def make_output_callback(bot, chat_id: int, loop: asyncio.AbstractEventLoop):
    def callback(text: str, is_final: bool = False):
        asyncio.run_coroutine_threadsafe(
            send_output_to_user(bot, chat_id, text, is_final), loop
        )
    return callback


def make_disconnect_callback(bot, chat_id: int, loop: asyncio.AbstractEventLoop, user_id: int):
    def callback(reason: str):
        async def _handle():
            remove_connection(user_id)
            meta = session_meta.pop(user_id, {})
            host = meta.get("host", "unknown")
            connected_at = meta.get("connected_at")
            duration = ""
            if connected_at:
                secs = int((datetime.utcnow() - connected_at).total_seconds())
                duration = f"{secs//3600}h {(secs%3600)//60}m {secs%60}s"

            await bot.send_message(
                chat_id=chat_id,
                text=(
                    f"🔴 <b>Connection Closed</b>\n\n"
                    f"📋 <b>Reason:</b> {reason}\n"
                    f"🖥️ <b>Server:</b> <code>{host}</code>\n"
                    + (f"⏱️ <b>Duration:</b> {duration}\n" if duration else "")
                    + f"\nUse /start to connect again."
                ),
                parse_mode=ParseMode.HTML,
                reply_markup=main_menu_keyboard(),
            )

            # Admin report
            db = get_session()
            try:
                tg_user_obj = meta.get("tg_user")
                if tg_user_obj:
                    db_user = get_or_create_user(db, tg_user_obj)
                    await report_disconnect(bot, db_user, host, reason, duration)

                # Record session end in DB
                session_id = meta.get("session_id")
                if session_id:
                    from workers.tasks import record_session_end
                    record_session_end.delay(session_id, reason)
            finally:
                db.close()

        asyncio.run_coroutine_threadsafe(_handle(), loop)
    return callback


# ── /start ────────────────────────────────────────────────────────────────────
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    db = get_session()
    try:
        get_or_create_user(db, update.effective_user)
    finally:
        db.close()

    conn = get_connection(update.effective_user.id)
    if conn and conn.is_connected:
        await update.message.reply_text(
            "⚡ <b>You have an active SSH session.</b>\n\nJust type your commands!",
            parse_mode=ParseMode.HTML,
            reply_markup=session_keyboard(),
        )
        return CONNECTED

    await update.message.reply_text(
        WELCOME_MESSAGE,
        parse_mode=ParseMode.HTML,
        reply_markup=main_menu_keyboard(),
    )
    return MAIN_MENU


# ── /help ─────────────────────────────────────────────────────────────────────
async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(HELP_MESSAGE, parse_mode=ParseMode.HTML,
                                    reply_markup=cancel_keyboard())


# ── /disconnect ───────────────────────────────────────────────────────────────
async def disconnect_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    conn = get_connection(user_id)
    if conn:
        conn.disconnect("👤 Disconnected by user")
        remove_connection(user_id)
    await update.message.reply_text(
        "🔌 <b>Disconnected.</b>",
        parse_mode=ParseMode.HTML,
        reply_markup=main_menu_keyboard(),
    )
    return MAIN_MENU


async def _send_ctrl_shortcut(update: Update, ctrl_key: str, label: str):
    user_id = update.effective_user.id
    conn = get_connection(user_id)

    if not conn or not conn.is_connected:
        await update.message.reply_text(
            "⚠️ <b>No active session.</b> Use /start to connect.",
            parse_mode=ParseMode.HTML,
            reply_markup=main_menu_keyboard(),
        )
        return MAIN_MENU

    try:
        conn.send_control(ctrl_key)
        await update.message.reply_text(
            f"⌨️ Sent <b>{label}</b> to remote shell.",
            parse_mode=ParseMode.HTML,
            reply_markup=session_keyboard(),
        )
    except SSHConnectionError as e:
        await update.message.reply_text(
            f"❌ <b>Failed to send {label}:</b> {e}",
            parse_mode=ParseMode.HTML,
            reply_markup=session_keyboard(),
        )
    return CONNECTED


async def ctrl_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Send a generic Ctrl+<key> to remote shell. Usage: /ctrl s"""
    if not context.args:
        await update.message.reply_text(
            "ℹ️ Usage: <code>/ctrl &lt;letter&gt;</code>\n"
            "Examples: <code>/ctrl s</code>, <code>/ctrl x</code>, <code>/ctrl c</code>",
            parse_mode=ParseMode.HTML,
            reply_markup=session_keyboard(),
        )
        return CONNECTED

    raw = context.args[0].strip().lower()
    # Accept: s, ^s, ctrl+s, ctrl-s, ctrl_s
    if raw.startswith("^") and len(raw) >= 2:
        key = raw[1]
    elif raw.startswith("ctrl"):
        tail = raw[4:].lstrip("+-_")
        key = tail[:1] if tail else ""
    else:
        key = raw[:1]

    if not key.isalpha():
        await update.message.reply_text(
            "❌ Invalid key. Use a single letter, e.g. <code>/ctrl s</code>.",
            parse_mode=ParseMode.HTML,
            reply_markup=session_keyboard(),
        )
        return CONNECTED

    return await _send_ctrl_shortcut(update, key, f"Ctrl+{key.upper()}")



# ── /status ───────────────────────────────────────────────────────────────────
async def status_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    conn = get_connection(user_id)
    meta = session_meta.get(user_id, {})

    if conn and conn.is_connected:
        host = meta.get("host", "?")
        connected_at = meta.get("connected_at")
        duration = ""
        if connected_at:
            secs = int((datetime.utcnow() - connected_at).total_seconds())
            duration = f"{secs//3600}h {(secs%3600)//60}m {secs%60}s"

        keep_alive = meta.get("keep_alive", False)
        auth_type = meta.get("auth_type", "?")

        text = (
            f"✅ <b>Active SSH Session</b>\n\n"
            f"🖥️ <b>Server:</b> <code>{host}</code>\n"
            f"👨‍💻 <b>User:</b> <code>{meta.get('ssh_user', '?')}</code>\n"
            f"🔑 <b>Auth:</b> <code>{auth_type}</code>\n"
            f"💓 <b>Keep-Alive:</b> {'✅' if keep_alive else '❌'}\n"
            f"⏱️ <b>Duration:</b> {duration}\n"
            f"🌐 <b>Total Active:</b> {get_active_count()} sessions"
        )
        await update.message.reply_text(text, parse_mode=ParseMode.HTML,
                                        reply_markup=session_keyboard())
    else:
        await update.message.reply_text(
            "💤 <b>No active SSH session.</b>",
            parse_mode=ParseMode.HTML,
            reply_markup=main_menu_keyboard(),
        )


# ── /info ─────────────────────────────────────────────────────────────────────
async def info_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await status_command(update, context)


# ── Callback: Main Menu Buttons ───────────────────────────────────────────────
async def handle_main_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    data = query.data

    if data == "main_menu":
        await query.edit_message_text(
            WELCOME_MESSAGE, parse_mode=ParseMode.HTML,
            reply_markup=main_menu_keyboard()
        )
        return MAIN_MENU

    if data == "help":
        await query.edit_message_text(HELP_MESSAGE, parse_mode=ParseMode.HTML,
                                      reply_markup=InlineKeyboardMarkup([
                                          [InlineKeyboardButton("⬅️ Back", callback_data="main_menu")]
                                      ]))
        return MAIN_MENU

    if data == "status":
        user_id = update.effective_user.id
        conn = get_connection(user_id)
        meta = session_meta.get(user_id, {})

        if conn and conn.is_connected:
            host = meta.get("host", "?")
            connected_at = meta.get("connected_at")
            duration = ""
            if connected_at:
                secs = int((datetime.utcnow() - connected_at).total_seconds())
                duration = f"{secs//3600}h {(secs%3600)//60}m {secs%60}s"
            text = (
                f"✅ <b>Active SSH Session</b>\n\n"
                f"🖥️ <b>Server:</b> <code>{host}</code>\n"
                f"⏱️ <b>Duration:</b> {duration}\n"
                f"🌐 <b>Global Active:</b> {get_active_count()}"
            )
            await query.edit_message_text(text, parse_mode=ParseMode.HTML,
                                          reply_markup=InlineKeyboardMarkup([[
                                              InlineKeyboardButton("⬅️ Back", callback_data="main_menu")
                                          ]]))
        else:
            await query.edit_message_text(
                "💤 <b>No active session.</b>",
                parse_mode=ParseMode.HTML,
                reply_markup=InlineKeyboardMarkup([[
                    InlineKeyboardButton("⬅️ Back", callback_data="main_menu")
                ]])
            )
        return MAIN_MENU

    if data == "new_connection":
        pending[update.effective_user.id] = {}
        await query.edit_message_text(
            "🖥️ <b>New SSH Connection</b>\n\n"
            "Please enter the <b>hostname or IP address</b> of your server:\n\n"
            "<i>Example: 192.168.1.100 or myserver.com</i>",
            parse_mode=ParseMode.HTML,
            reply_markup=cancel_keyboard(),
        )
        return ENTER_HOST

    if data == "saved_servers":
        db = get_session()
        try:
            user = db.query(User).filter(User.telegram_id == update.effective_user.id).first()
            servers = db.query(SavedServer).filter(SavedServer.user_id == user.id).all() if user else []
            if not servers:
                await query.edit_message_text(
                    "📋 <b>No saved servers yet.</b>\n\nConnect to a server and choose to save it!",
                    parse_mode=ParseMode.HTML,
                    reply_markup=InlineKeyboardMarkup([
                        [InlineKeyboardButton("🔌 New Connection", callback_data="new_connection")],
                        [InlineKeyboardButton("⬅️ Back", callback_data="main_menu")],
                    ])
                )
            else:
                await query.edit_message_text(
                    f"💾 <b>Your Saved Servers</b> ({len(servers)} total)\n\nSelect a server to connect:",
                    parse_mode=ParseMode.HTML,
                    reply_markup=saved_servers_keyboard(servers)
                )
        finally:
            db.close()
        return MAIN_MENU

    if data == "manage_servers":
        db = get_session()
        try:
            user = db.query(User).filter(User.telegram_id == update.effective_user.id).first()
            servers = db.query(SavedServer).filter(SavedServer.user_id == user.id).all() if user else []
            if not servers:
                await query.edit_message_text("No saved servers.", reply_markup=InlineKeyboardMarkup([[
                    InlineKeyboardButton("⬅️ Back", callback_data="main_menu")
                ]]))
            else:
                await query.edit_message_text(
                    "🗑️ <b>Select a server to delete:</b>",
                    parse_mode=ParseMode.HTML,
                    reply_markup=delete_servers_keyboard(servers)
                )
        finally:
            db.close()
        return MANAGE_SERVERS

    if data.startswith("connect_saved_"):
        server_id = int(data.split("_")[-1])
        db = get_session()
        try:
            server = db.query(SavedServer).filter(SavedServer.id == server_id).first()
            if not server:
                await query.edit_message_text("❌ Server not found.", reply_markup=main_menu_keyboard())
                return MAIN_MENU

            pd = {
                "host": server.host,
                "port": server.port,
                "ssh_user": server.ssh_username,
                "auth_type": server.auth_type,
                "password": server.password,
                "private_key": server.private_key,
                "key_passphrase": server.key_passphrase,
                "keep_alive": server.keep_alive,
                "server_id": server.id,
            }
        finally:
            db.close()

        await query.edit_message_text(
            f"🔄 <b>Connecting to</b> <code>{pd['host']}:{pd['port']}</code>...\n\n"
            f"👨‍💻 <b>User:</b> <code>{pd['ssh_user']}</code>\n"
            f"🔑 <b>Auth:</b> <code>{pd['auth_type']}</code>",
            parse_mode=ParseMode.HTML,
        )
        return await do_connect(update, context, pd)

    if data == "disconnect":
        user_id = update.effective_user.id
        conn = get_connection(user_id)
        if conn:
            conn.disconnect("👤 Disconnected by user")
        await query.edit_message_text(
            "🔌 <b>Disconnected.</b>",
            parse_mode=ParseMode.HTML,
            reply_markup=main_menu_keyboard(),
        )
        return MAIN_MENU

    if data == "session_info":
        await status_command(update, context)
        return CONNECTED

    if data == "cancel":
        pending.pop(update.effective_user.id, None)
        await query.edit_message_text(
            "❌ <b>Cancelled.</b>",
            parse_mode=ParseMode.HTML,
            reply_markup=main_menu_keyboard(),
        )
        return MAIN_MENU

    return MAIN_MENU


# ── Connection Flow ───────────────────────────────────────────────────────────

async def enter_host(update: Update, context: ContextTypes.DEFAULT_TYPE):
    host = update.message.text.strip()
    if not host:
        await update.message.reply_text("❌ Please enter a valid hostname.", reply_markup=cancel_keyboard())
        return ENTER_HOST

    user_id = update.effective_user.id
    pending[user_id] = {"host": host}

    await update.message.reply_text(
        f"✅ <b>Host:</b> <code>{host}</code>\n\n"
        "🔢 Enter the <b>SSH port</b>:\n\n<i>Default is 22, just type 22 if unsure</i>",
        parse_mode=ParseMode.HTML,
        reply_markup=cancel_keyboard(),
    )
    return ENTER_PORT


async def enter_port(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        port = int(update.message.text.strip())
        if not (1 <= port <= 65535):
            raise ValueError
    except ValueError:
        await update.message.reply_text("❌ Invalid port. Enter a number between 1–65535:",
                                        reply_markup=cancel_keyboard())
        return ENTER_PORT

    user_id = update.effective_user.id
    pending[user_id]["port"] = port

    await update.message.reply_text(
        f"✅ <b>Port:</b> <code>{port}</code>\n\n"
        "👤 Enter the <b>SSH username</b>:\n\n<i>Example: root, ubuntu, admin</i>",
        parse_mode=ParseMode.HTML,
        reply_markup=cancel_keyboard(),
    )
    return ENTER_SSH_USER


async def enter_ssh_user(update: Update, context: ContextTypes.DEFAULT_TYPE):
    ssh_user = update.message.text.strip()
    user_id = update.effective_user.id
    pending[user_id]["ssh_user"] = ssh_user

    await update.message.reply_text(
        f"✅ <b>SSH User:</b> <code>{ssh_user}</code>\n\n"
        "🔑 <b>Choose authentication method:</b>",
        parse_mode=ParseMode.HTML,
        reply_markup=auth_type_keyboard(),
    )
    return CHOOSE_AUTH


async def choose_auth(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user_id = update.effective_user.id
    data = query.data

    auth_map = {
        "auth_password": "password",
        "auth_key": "key",
        "auth_key_passphrase": "key_passphrase",
    }

    if data not in auth_map:
        return CHOOSE_AUTH

    pending[user_id]["auth_type"] = auth_map[data]

    if data == "auth_password":
        await query.edit_message_text(
            "🔒 <b>Enter your SSH password:</b>\n\n"
            "<i>⚠️ Your password will be encrypted and stored securely.</i>",
            parse_mode=ParseMode.HTML,
            reply_markup=cancel_keyboard(),
        )
        return ENTER_PASSWORD

    elif data in ("auth_key", "auth_key_passphrase"):
        await query.edit_message_text(
            "🗝️ <b>Paste your private SSH key:</b>\n\n"
            "<i>Include the full key including BEGIN/END lines.\n"
            "Supports RSA, Ed25519, ECDSA, DSA formats.</i>",
            parse_mode=ParseMode.HTML,
            reply_markup=cancel_keyboard(),
        )
        return ENTER_PRIVATE_KEY


async def enter_password(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    pending[user_id]["password"] = update.message.text

    # Delete the password message for security
    try:
        await update.message.delete()
    except Exception:
        pass

    await update.message.reply_text(
        "✅ <b>Password received</b> (hidden for security)\n\n"
        "💓 <b>Keep-Alive option:</b>\n"
        "Should the bot send periodic packets to prevent the server from disconnecting due to inactivity?",
        parse_mode=ParseMode.HTML,
        reply_markup=keep_alive_keyboard(),
    )
    return CHOOSE_KEEPALIVE


async def enter_private_key(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    pending[user_id]["private_key"] = update.message.text.strip()

    try:
        await update.message.delete()
    except Exception:
        pass

    if pending[user_id]["auth_type"] == "key_passphrase":
        await update.message.reply_text(
            "✅ <b>Private key received</b> (hidden)\n\n"
            "🔐 <b>Enter the passphrase for your key:</b>",
            parse_mode=ParseMode.HTML,
            reply_markup=cancel_keyboard(),
        )
        return ENTER_PASSPHRASE
    else:
        await update.message.reply_text(
            "✅ <b>Private key received</b> (hidden)\n\n"
            "💓 <b>Keep-Alive option:</b>\n"
            "Enable to prevent connection timeout?",
            parse_mode=ParseMode.HTML,
            reply_markup=keep_alive_keyboard(),
        )
        return CHOOSE_KEEPALIVE


async def enter_passphrase(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    pending[user_id]["key_passphrase"] = update.message.text

    try:
        await update.message.delete()
    except Exception:
        pass

    await update.message.reply_text(
        "✅ <b>Passphrase received</b> (hidden)\n\n"
        "💓 <b>Keep-Alive option:</b>\n"
        "Enable to prevent connection timeout?",
        parse_mode=ParseMode.HTML,
        reply_markup=keep_alive_keyboard(),
    )
    return CHOOSE_KEEPALIVE


async def choose_keepalive(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user_id = update.effective_user.id

    pending[user_id]["keep_alive"] = query.data == "keepalive_yes"

    pd = pending[user_id]
    host = pd.get("host")
    port = pd.get("port", 22)
    ssh_user = pd.get("ssh_user")
    auth_type = pd.get("auth_type")
    keep_alive = pd.get("keep_alive")

    await query.edit_message_text(
        f"🔍 <b>Connection Summary</b>\n\n"
        f"🖥️ <b>Host:</b> <code>{host}:{port}</code>\n"
        f"👤 <b>User:</b> <code>{ssh_user}</code>\n"
        f"🔑 <b>Auth:</b> <code>{auth_type}</code>\n"
        f"💓 <b>Keep-Alive:</b> {'✅ Enabled' if keep_alive else '❌ Disabled'}\n\n"
        "💾 <b>Would you like to save this server for quick access later?</b>",
        parse_mode=ParseMode.HTML,
        reply_markup=save_server_keyboard(),
    )
    return CHOOSE_SAVE


async def choose_save(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user_id = update.effective_user.id

    if query.data == "save_server_yes":
        await query.edit_message_text(
            "💾 <b>Give this server a label:</b>\n\n"
            "<i>Example: My VPS, Production Server, Home Server</i>",
            parse_mode=ParseMode.HTML,
            reply_markup=cancel_keyboard(),
        )
        return ENTER_SERVER_LABEL
    else:
        # Proceed to connect without saving
        pd = pending.get(user_id, {})
        await query.edit_message_text(
            f"🔄 <b>Connecting to</b> <code>{pd.get('host')}:{pd.get('port', 22)}</code>...",
            parse_mode=ParseMode.HTML,
        )
        return await do_connect(update, context, pd)


async def enter_server_label(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    label = update.message.text.strip()
    pd = pending.get(user_id, {})

    # Save server to DB
    db = get_session()
    try:
        tg_user = update.effective_user
        user = get_or_create_user(db, tg_user)
        server = SavedServer(
            user_id=user.id,
            label=label,
            host=pd["host"],
            port=pd.get("port", 22),
            ssh_username=pd["ssh_user"],
            auth_type=pd.get("auth_type", "password"),
            keep_alive=pd.get("keep_alive", True),
        )
        if pd.get("password"):
            server.password = pd["password"]
        if pd.get("private_key"):
            server.private_key = pd["private_key"]
        if pd.get("key_passphrase"):
            server.key_passphrase = pd["key_passphrase"]

        db.add(server)
        db.commit()
        pd["server_id"] = server.id
    finally:
        db.close()

    await update.message.reply_text(
        f"✅ <b>Server saved as '{label}'</b>\n\n"
        f"🔄 <b>Connecting to</b> <code>{pd.get('host')}:{pd.get('port', 22)}</code>...",
        parse_mode=ParseMode.HTML,
    )
    return await do_connect(update, context, pd)


# ── Do Connect ────────────────────────────────────────────────────────────────
async def do_connect(update: Update, context: ContextTypes.DEFAULT_TYPE, pd: dict):
    user_id = update.effective_user.id
    tg_user = update.effective_user
    bot = context.bot

    chat_id = update.effective_chat.id
    loop = asyncio.get_event_loop()

    host = pd.get("host")
    port = pd.get("port", 22)
    ssh_user = pd.get("ssh_user")
    auth_type = pd.get("auth_type", "password")
    keep_alive = pd.get("keep_alive", True)
    server_id = pd.get("server_id")

    # Disconnect existing session if any
    existing = get_connection(user_id)
    if existing and existing.is_connected:
        existing.disconnect("Replaced by new connection")
        remove_connection(user_id)

    try:
        conn = SSHConnection(
            host=host,
            port=port,
            username=ssh_user,
            password=pd.get("password"),
            private_key=pd.get("private_key"),
            key_passphrase=pd.get("key_passphrase"),
            auth_type=auth_type,
            keep_alive=keep_alive,
            on_output=make_output_callback(bot, chat_id, loop),
            on_disconnect=make_disconnect_callback(bot, chat_id, loop, user_id),
        )
        conn.connect()

        store_connection(user_id, conn)

        # Store session metadata
        session_meta[user_id] = {
            "host": f"{host}:{port}",
            "ssh_user": ssh_user,
            "auth_type": auth_type,
            "keep_alive": keep_alive,
            "connected_at": datetime.utcnow(),
            "tg_user": tg_user,
        }

        # Record in DB
        from workers.tasks import record_session_start
        task = record_session_start.delay(user_id, host, port, ssh_user, server_id)
        session_meta[user_id]["session_id"] = None  # Will be updated below

        # Admin report
        db = get_session()
        try:
            db_user = get_or_create_user(db, tg_user)
            await report_new_connection(bot, db_user, host, port, ssh_user, auth_type, keep_alive)
            await report_auth_attempt(bot, db_user, host, True, auth_type)
        finally:
            db.close()

        # Success message
        success_msg = (
            f"✅ <b>Connected Successfully!</b>\n\n"
            f"🖥️ <b>Server:</b> <code>{host}:{port}</code>\n"
            f"👤 <b>User:</b> <code>{ssh_user}</code>\n"
            f"🔑 <b>Auth:</b> <code>{auth_type}</code>\n"
            f"💓 <b>Keep-Alive:</b> {'✅ Enabled' if keep_alive else '❌ Disabled'}\n\n"
            f"⌨️ <b>You're now in an interactive SSH session.</b>\n"
            f"Just type and send your commands!\n\n"
            f"<i>Commands: /ctrl &lt;letter&gt; /disconnect /status /help</i>"
        )

        if update.callback_query:
            await update.callback_query.edit_message_text(
                success_msg, parse_mode=ParseMode.HTML,
                reply_markup=session_keyboard()
            )
        else:
            await context.bot.send_message(
                chat_id=chat_id, text=success_msg,
                parse_mode=ParseMode.HTML,
                reply_markup=session_keyboard()
            )

        pending.pop(user_id, None)
        return CONNECTED

    except SSHConnectionError as e:
        error_msg = str(e)

        # Admin report
        db = get_session()
        try:
            db_user = get_or_create_user(db, tg_user)
            await report_auth_attempt(bot, db_user, host, False, auth_type, error_msg)
        finally:
            db.close()

        fail_msg = (
            f"❌ <b>Connection Failed</b>\n\n"
            f"🖥️ <b>Server:</b> <code>{host}:{port}</code>\n"
            f"⚠️ <b>Error:</b> {error_msg}"
        )

        if update.callback_query:
            await update.callback_query.edit_message_text(
                fail_msg, parse_mode=ParseMode.HTML,
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("🔄 Retry", callback_data="new_connection")],
                    [InlineKeyboardButton("⬅️ Main Menu", callback_data="main_menu")],
                ])
            )
        else:
            await context.bot.send_message(
                chat_id=chat_id, text=fail_msg,
                parse_mode=ParseMode.HTML,
                reply_markup=main_menu_keyboard()
            )

        return MAIN_MENU


# ── Handle Commands While Connected ──────────────────────────────────────────
async def handle_connected_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    conn = get_connection(user_id)

    if not conn or not conn.is_connected:
        await update.message.reply_text(
            "⚠️ <b>No active session.</b> Use /start to connect.",
            parse_mode=ParseMode.HTML,
            reply_markup=main_menu_keyboard(),
        )
        return MAIN_MENU

    command = update.message.text

    try:
        conn.send_command(command)

        # Log command to admin
        meta = session_meta.get(user_id, {})
        tg_user = meta.get("tg_user", update.effective_user)
        db = get_session()
        try:
            db_user = get_or_create_user(db, tg_user)
            await report_command(context.bot, db_user, command, meta.get("host", "?"))
        finally:
            db.close()

        # Increment command counter
        session_id = meta.get("session_id")
        if session_id:
            from workers.tasks import increment_command_count
            increment_command_count.delay(session_id)

    except SSHConnectionError as e:
        await update.message.reply_text(
            f"❌ <b>Failed to send command:</b> {e}",
            parse_mode=ParseMode.HTML,
            reply_markup=main_menu_keyboard(),
        )
        return MAIN_MENU

    return CONNECTED


# ── Manage Servers ────────────────────────────────────────────────────────────
async def handle_manage_servers(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    data = query.data

    if data.startswith("delete_server_"):
        server_id = int(data.split("_")[-1])
        db = get_session()
        try:
            server = db.query(SavedServer).filter(SavedServer.id == server_id).first()
            if server:
                await query.edit_message_text(
                    f"🗑️ <b>Delete server?</b>\n\n"
                    f"<b>{server.label}</b> ({server.host}:{server.port})",
                    parse_mode=ParseMode.HTML,
                    reply_markup=confirm_delete_keyboard(server_id),
                )
        finally:
            db.close()
        return MANAGE_SERVERS

    if data.startswith("confirm_delete_"):
        server_id = int(data.split("_")[-1])
        db = get_session()
        try:
            server = db.query(SavedServer).filter(SavedServer.id == server_id).first()
            if server:
                db.delete(server)
                db.commit()
            await query.edit_message_text(
                "✅ <b>Server deleted.</b>",
                parse_mode=ParseMode.HTML,
                reply_markup=main_menu_keyboard(),
            )
        finally:
            db.close()
        return MAIN_MENU

    if data == "saved_servers":
        return await handle_main_callback(update, context)

    return MANAGE_SERVERS


# ── Fallback ──────────────────────────────────────────────────────────────────
async def fallback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handles messages sent outside of any state."""
    user_id = update.effective_user.id

    # If user has active connection, handle as command
    conn = get_connection(user_id)
    if conn and conn.is_connected:
        return await handle_connected_message(update, context)

    await update.message.reply_text(
        "👋 Use /start to get started!",
        reply_markup=main_menu_keyboard(),
    )
    return MAIN_MENU


# ── Error Handler ─────────────────────────────────────────────────────────────
async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE):
    logger.error("Exception:", exc_info=context.error)
    try:
        if update and hasattr(update, "effective_user") and update.effective_user:
            bot = context.bot
            tg_user = update.effective_user
            db = get_session()
            try:
                db_user = get_or_create_user(db, tg_user)
                await report_error(bot, db_user, str(context.error))
            finally:
                db.close()
    except Exception:
        pass


# ── App Builder ───────────────────────────────────────────────────────────────
def build_app():
    token = os.getenv("BOT_TOKEN")
    if not token:
        raise RuntimeError("BOT_TOKEN is not set in .env")

    init_db()

    app = Application.builder().token(token).build()

    conv_handler = ConversationHandler(
        entry_points=[
            CommandHandler("start", start),
            # Keep entry-point callbacks limited to main-menu actions so they don't
            # preempt state-specific callbacks (e.g. auth selection) when
            # allow_reentry=True.
            CallbackQueryHandler(
                handle_main_callback,
                pattern=r"^(new_connection|saved_servers|manage_servers|status|help|main_menu)$",
            ),
        ],
        states={
            MAIN_MENU: [
                CallbackQueryHandler(handle_main_callback),
                CommandHandler("status", status_command),
                CommandHandler("help", help_command),
                CommandHandler("ctrl", ctrl_command),
            ],
            ENTER_HOST: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, enter_host),
                CallbackQueryHandler(handle_main_callback, pattern="^cancel$"),
            ],
            ENTER_PORT: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, enter_port),
                CallbackQueryHandler(handle_main_callback, pattern="^cancel$"),
            ],
            ENTER_SSH_USER: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, enter_ssh_user),
                CallbackQueryHandler(handle_main_callback, pattern="^cancel$"),
            ],
            CHOOSE_AUTH: [
                CallbackQueryHandler(choose_auth, pattern="^auth_"),
                CallbackQueryHandler(handle_main_callback, pattern="^cancel$"),
            ],
            ENTER_PASSWORD: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, enter_password),
                CallbackQueryHandler(handle_main_callback, pattern="^cancel$"),
            ],
            ENTER_PRIVATE_KEY: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, enter_private_key),
                CallbackQueryHandler(handle_main_callback, pattern="^cancel$"),
            ],
            ENTER_PASSPHRASE: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, enter_passphrase),
                CallbackQueryHandler(handle_main_callback, pattern="^cancel$"),
            ],
            CHOOSE_KEEPALIVE: [
                CallbackQueryHandler(choose_keepalive, pattern="^keepalive_"),
                CallbackQueryHandler(handle_main_callback, pattern="^cancel$"),
            ],
            CHOOSE_SAVE: [
                CallbackQueryHandler(choose_save, pattern="^save_server_"),
                CallbackQueryHandler(handle_main_callback, pattern="^cancel$"),
            ],
            ENTER_SERVER_LABEL: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, enter_server_label),
                CallbackQueryHandler(handle_main_callback, pattern="^cancel$"),
            ],
            CONNECTED: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, handle_connected_message),
                CommandHandler("disconnect", disconnect_command),
                CommandHandler("ctrl", ctrl_command),
                CommandHandler("status", status_command),
                CommandHandler("info", info_command),
                CommandHandler("help", help_command),
                CallbackQueryHandler(handle_main_callback, pattern="^(disconnect|session_info|main_menu)$"),
            ],
            MANAGE_SERVERS: [
                CallbackQueryHandler(handle_manage_servers),
            ],
        },
        fallbacks=[
            CommandHandler("start", start),
            CommandHandler("disconnect", disconnect_command),
            CommandHandler("ctrl", ctrl_command),
            MessageHandler(filters.TEXT & ~filters.COMMAND, fallback),
        ],
        allow_reentry=True,
    )

    app.add_handler(conv_handler)
    app.add_error_handler(error_handler)

    return app


if __name__ == "__main__":
    logging.basicConfig(
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
        level=logging.INFO
    )
    app = build_app()
    logger.info("🚀 SSH Bot starting...")
    app.run_polling(drop_pending_updates=True)
