"""
UI Keyboards and message templates for the bot
"""
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, ReplyKeyboardMarkup, ReplyKeyboardRemove


# ── Main Menu ────────────────────────────────────────────────────────────────
def main_menu_keyboard():
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("🔌 New Connection", callback_data="new_connection"),
            InlineKeyboardButton("📋 Saved Servers", callback_data="saved_servers"),
        ],
        [
            InlineKeyboardButton("ℹ️ Status", callback_data="status"),
            InlineKeyboardButton("❓ Help", callback_data="help"),
        ],
    ])


# ── Auth Type Selection ───────────────────────────────────────────────────────
def auth_type_keyboard():
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("🔑 Password", callback_data="auth_password"),
            InlineKeyboardButton("🗝️ SSH Key", callback_data="auth_key"),
        ],
        [
            InlineKeyboardButton("🗝️🔐 SSH Key + Passphrase", callback_data="auth_key_passphrase"),
        ],
        [InlineKeyboardButton("❌ Cancel", callback_data="cancel")],
    ])


# ── Keep-Alive Selection ──────────────────────────────────────────────────────
def keep_alive_keyboard():
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("✅ Enable Keep-Alive", callback_data="keepalive_yes"),
            InlineKeyboardButton("❌ Disable", callback_data="keepalive_no"),
        ],
        [InlineKeyboardButton("⬅️ Back", callback_data="cancel")],
    ])


# ── Save Server Prompt ────────────────────────────────────────────────────────
def save_server_keyboard():
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("💾 Save Server", callback_data="save_server_yes"),
            InlineKeyboardButton("🚫 Don't Save", callback_data="save_server_no"),
        ],
    ])


# ── Active Session ────────────────────────────────────────────────────────────
def session_keyboard():
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("⬛ Disconnect", callback_data="disconnect"),
            InlineKeyboardButton("📊 Info", callback_data="session_info"),
        ],
    ])


# ── Server List ───────────────────────────────────────────────────────────────
def saved_servers_keyboard(servers):
    buttons = []
    for server in servers:
        label = f"🖥️ {server.label} ({server.host}:{server.port})"
        buttons.append([InlineKeyboardButton(label, callback_data=f"connect_saved_{server.id}")])
    buttons.append([
        InlineKeyboardButton("➕ New Connection", callback_data="new_connection"),
        InlineKeyboardButton("🗑️ Manage", callback_data="manage_servers"),
    ])
    buttons.append([InlineKeyboardButton("⬅️ Back", callback_data="main_menu")])
    return InlineKeyboardMarkup(buttons)


# ── Delete Server ─────────────────────────────────────────────────────────────
def delete_servers_keyboard(servers):
    buttons = []
    for server in servers:
        label = f"🗑️ {server.label} ({server.host})"
        buttons.append([InlineKeyboardButton(label, callback_data=f"delete_server_{server.id}")])
    buttons.append([InlineKeyboardButton("⬅️ Back", callback_data="saved_servers")])
    return InlineKeyboardMarkup(buttons)


# ── Confirm Delete ────────────────────────────────────────────────────────────
def confirm_delete_keyboard(server_id):
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("✅ Yes, Delete", callback_data=f"confirm_delete_{server_id}"),
            InlineKeyboardButton("❌ Cancel", callback_data="manage_servers"),
        ],
    ])


# ── Cancel Only ───────────────────────────────────────────────────────────────
def cancel_keyboard():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("❌ Cancel", callback_data="cancel")],
    ])


# ── Messages ──────────────────────────────────────────────────────────────────
WELCOME_MESSAGE = """
🚀 <b>SSH Telegram Bot</b>

Welcome! I'll help you connect to your VPS servers directly from Telegram.

<b>Features:</b>
• 🔌 Full interactive SSH sessions
• 💾 Save & manage multiple servers
• 🔐 Password, SSH Key, Key+Passphrase auth
• 💓 Keep-alive to prevent timeouts
• ⌨️ Interactive script support
• 🔒 Encrypted credential storage

Use the menu below to get started 👇
"""

HELP_MESSAGE = """
❓ <b>How to Use SSH Bot</b>

<b>🔌 Connecting:</b>
1. Tap <b>New Connection</b>
2. Enter host, port, username
3. Choose auth method & credentials
4. Toggle keep-alive option
5. Optionally save the server

<b>⌨️ While Connected:</b>
• Just type any command and send
• For interactive scripts, type your response and send
• Use /ctrl <letter> to send Ctrl+<letter> (e.g. /ctrl s, /ctrl x)
• Use /disconnect to end the session
• Use /info for session info

<b>💾 Saved Servers:</b>
• Access via <b>Saved Servers</b> menu
• Credentials are encrypted securely

<b>🔑 Auth Methods:</b>
• <b>Password</b> — standard SSH password
• <b>SSH Key</b> — paste your private key
• <b>SSH Key + Passphrase</b> — key with passphrase

<b>💓 Keep-Alive:</b>
Sends packets to prevent server timeout/disconnect.
"""
