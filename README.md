<div align="center">

<img src="https://capsule-render.vercel.app/api?type=waving&color=0:0f0c29,50:302b63,100:24243e&height=200&section=header&text=RPS%20Admin%20Bot&fontSize=60&fontColor=ffffff&fontAlignY=38&desc=Telegram%20%E2%86%94%20WordPress%20Command%20Center&descAlignY=60&descSize=18&animation=fadeIn" width="100%"/>

<br/>

[![Typing SVG](https://readme-typing-svg.demolab.com?font=JetBrains+Mono&weight=700&size=22&pause=1000&color=A78BFA&center=true&vCenter=true&width=700&lines=🚀+Publish+notices+from+Telegram+instantly;🖼+Upload+images+%26+PDFs+to+WordPress;🔒+Username-based+access+control;📡+Self-healing+keep-alive+on+Render;⚡+Auto-restart+on+crash)](https://git.io/typing-svg)

<br/>

![Python](https://img.shields.io/badge/Python-3.9%2B-3776AB?style=for-the-badge&logo=python&logoColor=white)
![Telegram](https://img.shields.io/badge/Telegram%20Bot-API-26A5E4?style=for-the-badge&logo=telegram&logoColor=white)
![WordPress](https://img.shields.io/badge/WordPress-REST%20API-21759B?style=for-the-badge&logo=wordpress&logoColor=white)
![Flask](https://img.shields.io/badge/Flask-Keep--Alive-000000?style=for-the-badge&logo=flask&logoColor=white)
![Render](https://img.shields.io/badge/Render-Deployed-46E3B7?style=for-the-badge&logo=render&logoColor=white)
![License](https://img.shields.io/badge/License-MIT-yellow?style=for-the-badge)

</div>

---

<div align="center">

## ✨ What is this?

</div>

**RPS Admin Bot** is a production-grade Telegram bot that acts as a **remote control for your WordPress school website**. Send a message from your phone → it appears as a notice on the site in seconds. No logins, no dashboards — just Telegram.

Built for **[RPS Kochas](https://rpskochas.in)** school, but works with any WordPress site that uses a custom post type.

---

<div align="center">

## 🎯 Features

</div>

<table>
<tr>
<td width="50%">

### 📝 Content Management
- **Text → Notice** — First line becomes the title, rest is body
- **Photo + Caption** → Notice with featured image
- **PDF + Caption** → Notice with downloadable document button
- **BANNER: text** → Updates the site marquee/scrollbar live

</td>
<td width="50%">

### 🔒 Security & Access
- Username-based allowlist (zero numeric IDs needed)
- Multi-admin support (comma-separated)
- Unauthorized users get a friendly rejection message
- No secrets ever hardcoded

</td>
</tr>
<tr>
<td width="50%">

### 🛠 Operations
- `/list` — See last 5 notices with IDs
- `/delete [ID]` — Remove a single notice
- `/reset` — Wipe the entire notice board
- Inline keyboard shortcuts for all commands

</td>
<td width="50%">

### ☁️ Deployment
- Self-hosted Flask HTTP server (Render keep-alive)
- Self-pinger thread (every 8 min, never sleeps)
- Auto-restart loop on crash (10s cooldown)
- Startup notification to all admins on Telegram

</td>
</tr>
</table>

---

<div align="center">

## 🏗 Architecture

</div>

```
┌─────────────────────────────────────────────────────┐
│                   Telegram Admin                     │
│           sends text / photo / PDF                   │
└─────────────────────┬───────────────────────────────┘
                      │  Telegram Bot API (polling)
                      ▼
┌─────────────────────────────────────────────────────┐
│                  main.py (Bot Core)                  │
│                                                      │
│  ┌──────────────┐   ┌───────────────────────────┐   │
│  │ Auth Layer   │   │   Handler Router           │   │
│  │ (username    │──▶│ text / photo / doc / cmd   │   │
│  │  allowlist)  │   └──────────────┬────────────┘   │
│  └──────────────┘                  │                 │
│                                    ▼                 │
│  ┌─────────────────────────────────────────────┐    │
│  │         WordPress REST API (v2)              │    │
│  │  POST /notice  │  POST /media  │  /settings  │    │
│  └─────────────────────────────────────────────┘    │
│                                                      │
│  ┌──────────────────┐   ┌────────────────────────┐  │
│  │  Flask Server    │   │  Pinger Thread          │  │
│  │  :8080 /health   │   │  GET / every 8 min      │  │
│  └──────────────────┘   └────────────────────────┘  │
└─────────────────────────────────────────────────────┘
                      │
                      ▼
             📡 Render.com hosting
```

---

<div align="center">

## 🚀 Setup Guide

</div>

### Step 1 — Prerequisites

| Tool | Purpose | Link |
|------|---------|------|
| Python 3.9+ | Runtime | [python.org](https://python.org) |
| pip | Package manager | Bundled with Python |
| Telegram account | Create your bot | [Telegram](https://telegram.org) |
| WordPress site | Must have REST API enabled | — |

---

### Step 2 — Create a Telegram Bot

1. Open Telegram → search **@BotFather**
2. Send `/newbot` → follow prompts
3. Copy the **Bot Token** (looks like `1234567890:AABBcc...`)

---

### Step 3 — Create WordPress Application Password

1. Log into your WordPress dashboard
2. Go to **Users → Your Profile → Application Passwords**
3. Enter name `Telegram Bot` → click **Add New**
4. Copy the generated password (e.g. `xxxx xxxx xxxx xxxx xxxx xxxx`)

---

### Step 4 — Clone & Install

```bash
# Clone the repo
git clone https://github.com/YOUR_USERNAME/rps-admin-bot.git
cd rps-admin-bot

# Create virtual environment
python -m venv .venv
source .venv/bin/activate      # Linux/Mac
# .venv\Scripts\activate       # Windows

# Install dependencies
pip install -r requirements.txt
```

---

### Step 5 — Configure Environment

```bash
# Copy the example file
cp .env.example .env

# Edit with your values
nano .env      # or use VS Code: code .env
```

Fill in your `.env`:

```env
# WordPress
WP_URL=https://your-school-site.com
WP_USERNAME=your_wp_username
WP_APP_PASSWORD=xxxx xxxx xxxx xxxx xxxx xxxx

# Telegram
TELEGRAM_BOT_TOKEN=your_bot_token_here

# Access control (no '@', comma-separated for multiple)
AUTHORIZED_USERNAMES=alice,bob

# Admins who get startup notifications (subset of above is fine)
ADMIN_USERNAME=alice,bob
```

---

### Step 6 — Run Locally

```bash
python main.py
```

You should see:
```
🚀 Bot V5.1 is running for 2 authorized usernames...
```

Open Telegram → send `/start` to your bot. Done! ✅

---

<div align="center">

## ☁️ Deploy to Render (Free / Paid)

</div>

### Step 1 — Push to GitHub

```bash
git init
git add .
git commit -m "🚀 initial commit"
git remote add origin https://github.com/YOUR_USERNAME/rps-admin-bot.git
git push -u origin main
```

> ⚠️ Make sure `.gitignore` excludes `.env` before pushing!

---

### Step 2 — Create Render Web Service

1. Go to [render.com](https://render.com) → **New → Web Service**
2. Connect your GitHub repo
3. Configure:

| Setting | Value |
|---------|-------|
| **Environment** | Python 3 |
| **Build Command** | `pip install -r requirements.txt` |
| **Start Command** | `python main.py` |
| **Instance Type** | Free (or Starter for always-on) |

---

### Step 3 — Add Environment Variables on Render

Go to your service → **Environment** tab → add these:

| Variable | Value |
|----------|-------|
| `WP_URL` | `https://your-site.com` |
| `WP_USERNAME` | your WordPress username |
| `WP_APP_PASSWORD` | your WP application password |
| `TELEGRAM_BOT_TOKEN` | your bot token |
| `AUTHORIZED_USERNAMES` | `alice,bob` |
| `ADMIN_USERNAME` | `alice,bob` |

> ✅ **Do NOT add `RENDER_EXTERNAL_URL`** — Render injects this automatically.

---

### Step 4 — Deploy & Verify

1. Click **Deploy** → wait for build to finish
2. Open the Render URL in browser — you should see: `Bot is alive!`
3. Send `/start` to your bot on Telegram

The bot will now **never sleep** — the self-pinger hits the health endpoint every 8 minutes, keeping Render's 15-minute inactivity timeout at bay.

---

<div align="center">

## 📋 Environment Variables Reference

</div>

| Variable | Required | Description | Example |
|----------|----------|-------------|---------|
| `WP_URL` | ✅ | Your WordPress site URL | `https://school.com` |
| `WP_USERNAME` | ✅ | WordPress login username | `admin` |
| `WP_APP_PASSWORD` | ✅ | WP Application Password | `xxxx xxxx xxxx` |
| `TELEGRAM_BOT_TOKEN` | ✅ | Bot token from @BotFather | `123456:ABCdef` |
| `AUTHORIZED_USERNAMES` | ✅ | Who can use the bot | `alice,bob` |
| `ADMIN_USERNAME` | ⚡ | Who gets startup notifications | `alice` |
| `PORT` | auto | Flask server port (Render sets this) | `8080` |
| `RENDER_EXTERNAL_URL` | auto | Your Render service URL (auto-injected) | auto |

---

<div align="center">

## 💬 Bot Commands

</div>

| Command / Action | Description |
|-----------------|-------------|
| `/start` | Show welcome screen + keyboard |
| `/help` | Full guide |
| `/list` | Last 5 notices with IDs |
| `/delete [ID]` | Delete a specific notice |
| `/reset` | Delete ALL notices |
| `BANNER: your text` | Update the website marquee |
| Send any **text** | Creates a notice (first line = title) |
| Send **photo + caption** | Notice with featured image |
| Send **PDF + caption** | Notice with downloadable doc button |

---

<div align="center">

## 🔐 Security Notes

</div>

- ✅ All secrets loaded from `.env` — never hardcoded
- ✅ `.env` is in `.gitignore` — never committed
- ✅ Username-based allowlist — unauthorized users are blocked at every handler
- ✅ WordPress Application Passwords used (not your main WP password)
- ✅ All user-supplied text is HTML-escaped before rendering
- ✅ HTTP retries with backoff — resilient to transient WP server errors
- ✅ Error messages are sanitized before being shown to users
- ⚠️ Bot runs with polling (not webhooks) — fine for single-instance Render deployment
- ⚠️ Admin chat ID caching is in-memory only — resets on restart (by design)

---

<div align="center">

## 🧰 Tech Stack

</div>

<div align="center">

![Python](https://skillicons.dev/icons?i=python,flask)

</div>

| Library | Purpose |
|---------|---------|
| `python-telegram-bot` | Telegram Bot API wrapper |
| `flask` | HTTP server for Render keep-alive |
| `requests` | WordPress REST API calls with retry |
| `markdown` | Convert Markdown to HTML for WP posts |
| `python-dotenv` | Load `.env` config file |

---

<div align="center">

## 📁 Project Structure

</div>

```
rps-admin-bot/
├── main.py            # 🤖 Core bot logic
├── requirements.txt   # 📦 Python dependencies
├── .env.example       # 📋 Config template (safe to commit)
├── .env               # 🔒 Your real secrets (NEVER commit)
├── .gitignore         # 🚫 Git exclusion rules
└── README.md          # 📖 This file
```

---

<div align="center">

## 🤝 Contributing

Pull requests are welcome! For major changes, please open an issue first to discuss what you'd like to change.

---

<img src="https://capsule-render.vercel.app/api?type=waving&color=0:24243e,50:302b63,100:0f0c29&height=120&section=footer&animation=fadeIn" width="100%"/>

*Made with ❤️ *

</div>
