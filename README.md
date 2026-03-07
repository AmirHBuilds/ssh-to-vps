# 🚀 Telegram SSH Bot

A powerful, production-ready Telegram bot that provides a full SSH terminal interface directly in Telegram. Connect to any VPS/server, run commands, interact with scripts, and manage multiple servers — all from Telegram.

---

## ✨ Features

- 🔌 **Full Interactive SSH Sessions** — Real PTY shell, works with any command
- 🤖 **Interactive Script Support** — Scripts that ask questions (like `bash <(curl ...)` install scripts) work perfectly
- 💾 **Saved Servers** — Save credentials for quick reconnection (AES encrypted)
- 🔐 **Multiple Auth Methods** — Password, SSH Key, SSH Key + Passphrase
- 💓 **Keep-Alive** — Prevents server from disconnecting due to inactivity
- 🔒 **Encrypted Storage** — All credentials encrypted with Fernet (AES-128)
- 📊 **Admin Reports** — Every connection/command/error reported to your admin group with topics
- ⚡ **High Concurrency** — Celery task queue handles many users simultaneously
- 🐳 **One-Click Docker Deploy** — Full Docker Compose setup

---

## 🚀 Quick Start

### 1. Clone & Configure

```bash
# Copy the env file
cp .env.example .env

# Edit with your values
nano .env
```

### 2. Fill in `.env`

```env
# Get from @BotFather on Telegram
BOT_TOKEN=1234567890:ABCdef...

# Your admin group ID (negative number for groups)
ADMIN_GROUP_ID=-1001234567890

# Topic Thread IDs in your admin group
TOPIC_NEW_CONNECTION=1
TOPIC_COMMANDS=2
TOPIC_ERRORS=3
TOPIC_AUTH=4
TOPIC_DISCONNECTIONS=5

# Strong random secret for encryption
SECRET_KEY=your_very_strong_random_secret_key_here
```

### 3. Launch

```bash
docker compose up -d --build
```

That's it! 🎉

---

## 📋 Admin Group Setup

1. Create a Telegram group
2. Enable **Topics** in group settings
3. Create topics: `New Connections`, `Commands`, `Errors`, `Auth`, `Disconnections`
4. Get the **Thread ID** of each topic (forward a message from the topic to @getidsbot)
5. Add your bot to the group as an admin
6. Fill in the Topic IDs in `.env`

---

## 🤖 Bot Commands

| Command | Description |
|---------|-------------|
| `/start` | Open main menu |
| `/disconnect` | End current SSH session |
| `/status` | Show current session info |
| `/info` | Alias for `/status` |
| `/help` | Show help |

---

## 🔌 Connecting to a Server

1. Send `/start`
2. Tap **New Connection**
3. Enter hostname/IP
4. Enter port (default: 22)
5. Enter SSH username
6. Choose auth method (Password / SSH Key / SSH Key + Passphrase)
7. Enter credentials
8. Choose keep-alive setting
9. Optionally save the server
10. **You're connected!**

---

## ⌨️ Using Interactive Scripts

The bot supports fully interactive scripts. For example:

```bash
bash <(curl -Ls https://raw.githubusercontent.com/mhsanaei/3x-ui/master/install.sh)
```

1. Connect to your server
2. Type the command and send
3. When the script asks a question, type your answer and send
4. The bot relays everything back and forth in real-time

---

## 🗂️ Services

| Service | Description | Port |
|---------|-------------|------|
| `bot` | Main Telegram bot | — |
| `worker` | Celery task workers | — |
| `beat` | Celery periodic tasks | — |
| `redis` | Message broker | — |
| `flower` | Celery monitoring UI | 5555 |

Access Flower at `http://your-server:5555` (admin/admin — change in docker-compose.yml)

---

## 🔒 Security

- All passwords and private keys are encrypted at rest using Fernet (AES-128-CBC)
- Password messages are auto-deleted from Telegram chat after receipt
- Private key messages are auto-deleted from Telegram chat after receipt
- SSH host key verification is set to AutoAdd (suitable for personal use; for enterprise, configure known_hosts)

---

## 📁 Project Structure

```
telegram-ssh-bot/
├── bot/
│   └── main.py          # Main bot logic & conversation handlers
├── models/
│   └── database.py      # SQLAlchemy models, encryption
├── utils/
│   ├── ssh_manager.py   # SSH connection handler with PTY
│   ├── keyboards.py     # Inline keyboards & UI templates
│   └── reporter.py      # Admin group reporter
├── workers/
│   ├── celery_app.py    # Celery configuration
│   └── tasks.py         # Background tasks
├── .env.example         # Environment template
├── requirements.txt
├── Dockerfile
└── docker-compose.yml
```

---

## 🛠️ Troubleshooting

**Bot doesn't respond:**
```bash
docker compose logs bot
```

**Connection issues:**
- Check your server's SSH port is open
- Verify credentials
- Check firewall rules

**Admin reports not sending:**
- Ensure bot is admin in the group
- Topics must be enabled in group settings
- Thread IDs must be correct

**View all logs:**
```bash
docker compose logs -f
```

**Restart everything:**
```bash
docker compose restart
```
