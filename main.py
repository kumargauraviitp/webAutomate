import os
import base64
import logging
import requests
import markdown
import re
import html
import threading
import time
import socket
from typing import Dict, Optional
from flask import Flask
from datetime import datetime
from dotenv import load_dotenv

from telegram import Update, ReplyKeyboardMarkup, KeyboardButton
from telegram.ext import ApplicationBuilder, MessageHandler, CommandHandler, ContextTypes, filters
from requests.adapters import HTTPAdapter
from urllib3.util import Retry

# ================= LOAD ENV =================
load_dotenv()

WP_URL = os.getenv("WP_URL", "https://your-school-site.com")
WP_USERNAME = os.getenv("WP_USERNAME", "admin")
WP_APP_PASSWORD = os.getenv("WP_APP_PASSWORD", "xxxx xxxx xxxx xxxx")
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "your_bot_token")
# Comma-separated admin usernames for startup notifications, e.g. "alice,bob"
admin_usernames_raw = os.getenv("ADMIN_USERNAME", "")
ADMIN_USERNAMES = [u.strip().lower().lstrip("@") for u in admin_usernames_raw.split(",") if u.strip()]

# Runtime cache: username -> chat_id, filled when each admin first messages the bot
_admin_chat_ids: Dict[str, Optional[int]] = {u: None for u in ADMIN_USERNAMES}

auth_users_raw = os.getenv("AUTHORIZED_USERNAMES", "")
AUTHORIZED_USERNAMES = [u.strip().lower() for u in auth_users_raw.split(",") if u.strip()]

CONTACT_USERNAME = os.getenv("CONTACT_USERNAME", "admin")

# ================= SESSION WITH RETRIES =================
def get_session():
    """Create a session with automatic retries for WordPress API calls."""
    session = requests.Session()
    retries = Retry(
        total=3,
        backoff_factor=1,
        status_forcelist=[502, 503, 504],
        allowed_methods=["POST", "GET", "DELETE"]
    )
    session.mount("https://", HTTPAdapter(max_retries=retries))
    session.headers.update(get_auth())
    return session

# ================= LOGGING =================
# Must be set up BEFORE Flask/pinger threads start so logger is ready
logging.basicConfig(
    filename="bot.log",
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger(__name__)

# ================= RENDER KEEP-ALIVE =================
app_flask = Flask(__name__)

@app_flask.route("/")
def health_check():
    return "Bot is alive!", 200

def find_free_port(preferred: int = 8080) -> int:
    """Return preferred port if free, otherwise let the OS assign a free one."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        try:
            s.bind(("", preferred))
            return preferred
        except OSError:
            # preferred port is in use — ask OS for any free port
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s2:
                s2.bind(("", 0))
                free = s2.getsockname()[1]
                logger.warning(f"⚠️  Port {preferred} in use. Using port {free} instead.")
                return free

def run_flask():
    preferred = int(os.getenv("PORT", 8080))
    port = find_free_port(preferred)
    logger.info(f"🌐 Flask health server starting on port {port}")
    # use_reloader=False is critical — reloader spawns a child process that breaks threads
    app_flask.run(host="0.0.0.0", port=port, use_reloader=False, threaded=True)

def pinger():
    """Background thread that pings the bot's URL every 8 minutes to prevent Render sleep."""
    url = os.getenv("RENDER_EXTERNAL_URL")
    if not url:
        logger.warning("RENDER_EXTERNAL_URL not set! Self-pinger disabled.")
        return

    logger.info(f"🚀 Self-pinger started targeting: {url}")
    while True:
        try:
            r = requests.get(url, timeout=10)
            logger.info(f"📡 Keep-alive ping to {url}: Status {r.status_code}")
        except Exception as e:
            logger.error(f"❌ Keep-alive ping failed: {str(e)}")
        time.sleep(480)  # wait 8 minutes AFTER each ping

# ================= AUTH =================
def get_auth():
    token = base64.b64encode(f"{WP_USERNAME}:{WP_APP_PASSWORD}".encode()).decode()
    return {
        "Authorization": f"Basic {token}",
        "Content-Type": "application/json"
    }

def get_main_menu():
    keyboard = [
        [KeyboardButton("📝 List Notices"), KeyboardButton("🚩 Update Banner")],
        [KeyboardButton("🗑 Reset All Notices"), KeyboardButton("❓ Help Guide")]
    ]
    return ReplyKeyboardMarkup(keyboard, resize_keyboard=True, is_persistent=True)

def is_auth(username):
    if not username: return False
    return username.lower() in AUTHORIZED_USERNAMES

def cache_admin_chat_id(user):
    """Cache chat_id for any ADMIN_USERNAME user the first time they message the bot."""
    uname = (user.username or "").lower()
    if uname in _admin_chat_ids and _admin_chat_ids[uname] is None:
        _admin_chat_ids[uname] = user.id
        logger.info(f"✅ Admin chat_id cached: {user.id} for @{uname}")

# ================= HELPERS =================

def markdown_to_html(text):
    return markdown.markdown(text)

def generate_seo(title, content):
    desc = content[:150].replace("\n", " ")
    return {
        "yoast_head_json": {
            "title": title,
            "description": desc
        }
    }

def create_post(title, content, media_id=None, date=None):
    url = f"{WP_URL}/wp-json/wp/v2/notice"
    payload = {
        "title": title,
        "content": markdown_to_html(content),
        "status": "future" if date else "publish"
    }
    if date: payload["date"] = date
    if media_id: payload["featured_media"] = media_id
    payload.update(generate_seo(title, content))

    r = get_session().post(url, json=payload)
    if r.status_code not in [200, 201]:
        raise Exception(f"WP Error {r.status_code}: {r.text}")
    return r.json()

def update_banner(text):
    url = f"{WP_URL}/wp-json/wp/v2/settings"
    r = get_session().post(url, json={"rps_marquee_text": text})
    return r.status_code in [200, 201]

def upload_media(file_bytes, filename="telegram_upload.jpg", mime_type="image/jpeg"):
    url = f"{WP_URL}/wp-json/wp/v2/media"
    headers = get_auth()
    headers.update({
        "Content-Disposition": f'attachment; filename="{filename}"',
        "Content-Type": mime_type
    })
    r = get_session().post(url, data=file_bytes, headers=headers)
    if r.status_code not in [200, 201]:
        raise Exception(f"Upload Error {r.status_code}: {r.text}")
    res = r.json()
    return res["id"], res["source_url"]

# ================= COMMANDS =================

async def start_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    username = user.username
    cache_admin_chat_id(user)
    if not is_auth(username):
        await update.message.reply_text(f"🛑 Access Denied.\nUser: @{html.escape(username if username else 'Unknown')}\n\nPlease contact @{html.escape(CONTACT_USERNAME)} for access!")
        return

    welcome_text = (
        f"👋 <b>Welcome, Admin {html.escape(user.first_name)}!</b>\n\n"
        "I am your <b>RPS Website Command Center</b>. Use the buttons below or send me media to update the site instantly.\n\n"
        "✨ <i>Tip: Send an image with a caption to create a visual notice.</i>"
    )
    await update.message.reply_text(welcome_text, parse_mode="HTML", reply_markup=get_main_menu())

async def help_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_auth(update.effective_user.username):
        await update.message.reply_text(f"🛑 Access Denied! Contact @{html.escape(CONTACT_USERNAME)} for access.")
        return
    help_text = (
        "📖 <b>RPS Admin Bot Guide (V6.0)</b>\n\n"
        "✨ <b>Automatic Actions:</b>\n"
        "• <b>Text</b>: Creates a Notice card. First line is Title.\n"
        "• <b>Photo + Caption</b>: Notice with an image.\n"
        "• <b>PDF + Caption</b>: Notice with a document.\n\n"
        "🔧 <b>Available Commands:</b>\n"
        "• <code>/list</code>: See last 5 notices.\n"
        "• <code>/reset</code>: Delete ALL notices at once.\n"
        "• <code>/delete [ID]</code>: Remove one notice.\n"
        "• <code>BANNER: [text]</code>: Update marquee.\n\n"
        "<i>All buttons below are shortcuts for these commands!</i>"
    )
    await update.message.reply_text(help_text, parse_mode="HTML", reply_markup=get_main_menu())

async def list_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_auth(update.effective_user.username):
        await update.message.reply_text(f"🛑 Access Denied! Contact @{html.escape(CONTACT_USERNAME)} for access.")
        return
    url = f"{WP_URL}/wp-json/wp/v2/notice?per_page=5"
    r = get_session().get(url)
    if r.status_code == 200:
        notices = r.json()
        if not notices:
            await update.message.reply_text("📭 No notices found on the site.")
            return
        text = "📝 <b>Recent Notices:</b>\n\n"
        for n in notices:
            safe_title = html.escape(n['title']['rendered'])
            text += f"🆔 <code>{n['id']}</code> - {safe_title}\n"
        await update.message.reply_text(text, parse_mode="HTML")
    else:
        await update.message.reply_text("❌ Failed to fetch list.")

async def delete_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_auth(update.effective_user.username):
        await update.message.reply_text(f"🛑 Access Denied! Contact @{html.escape(CONTACT_USERNAME)} for access.")
        return
    if not context.args:
        await update.message.reply_text("❓ Please provide an ID. Usage: <code>/delete 123</code>", parse_mode="HTML")
        return
    post_id = context.args[0]
    url = f"{WP_URL}/wp-json/wp/v2/notice/{post_id}?force=true"
    r = get_session().delete(url)
    if r.status_code == 200:
        await update.message.reply_text(f"🗑 Notice <code>{post_id}</code> deleted successfully.", parse_mode="HTML")
    else:
        await update.message.reply_text(f"❌ Deletion failed. Check if ID <code>{post_id}</code> is correct.", parse_mode="HTML")

async def reset_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_auth(update.effective_user.username):
        await update.message.reply_text(f"🛑 Access Denied! Contact @{html.escape(CONTACT_USERNAME)} for access.")
        return
    
    await update.message.reply_text("⏳ <b>Fetching notices for bulk deletion...</b>", parse_mode="HTML")
    
    # Fetch all notices (last 100)
    url = f"{WP_URL}/wp-json/wp/v2/notice?per_page=100&status=publish,future,draft"
    r = get_session().get(url)
    
    if r.status_code == 200:
        notices = r.json()
        if not notices:
            await update.message.reply_text("📭 <b>Notice board is already empty!</b>", parse_mode="HTML")
            return
        
        count = 0
        for n in notices:
            del_url = f"{WP_URL}/wp-json/wp/v2/notice/{n['id']}?force=true"
            dr = get_session().delete(del_url)
            if dr.status_code == 200:
                count += 1
        
        await update.message.reply_text(f"🗑 <b>Successfully deleted {count} notices!</b>\nNotice board is now clean.", parse_mode="HTML")
    else:
        await update.message.reply_text("❌ <b>Failed to connect to WordPress for reset.</b>", parse_mode="HTML")

# ================= MAIN HANDLERS =================

async def handle_all(update: Update, context: ContextTypes.DEFAULT_TYPE):
    cache_admin_chat_id(update.effective_user)
    if not is_auth(update.effective_user.username):
        await update.message.reply_text(f"🛑 Access Denied! Contact @{html.escape(CONTACT_USERNAME)} for access.")
        return
    msg = update.message
    
    # Dashboard Button Mapping
    if msg.text == "📝 List Notices":
        return await list_cmd(update, context)
    if msg.text == "🗑 Reset All Notices":
        return await reset_cmd(update, context)
    if msg.text == "❓ Help Guide":
        return await help_cmd(update, context)
    if msg.text == "🚩 Update Banner":
        return await update.message.reply_text("🚩 <b>To update the banner:</b>\nSend a message starting with <code>BANNER:</code> followed by your text.\n\nExample:\n<code>BANNER: Welcome to RPS Kochas!</code>", parse_mode="HTML")

    try:
        # TEXT (Normal or Banner)
        if msg.text and not msg.photo and not msg.document:
            text = msg.text.strip()
            if text.upper().startswith("BANNER:"):
                banner_text = text[7:].strip()
                if update_banner(banner_text):
                    # Strip HTML for a clean preview in Telegram
                    clean_preview = re.sub(r'<[^>]*>', '', banner_text)
                    success_msg = (
                        "🌟 <b>MARQUEE UPDATED SUCCESSFULLY!</b>\n\n"
                        f"📝 <b>Preview:</b> {html.escape(clean_preview)}\n"
                        f"🔗 <b>Website:</b> {html.escape(WP_URL)}\n\n"
                        "<i>The styled banner is now live on the scroll bar.</i>"
                    )
                    await msg.reply_text(success_msg, parse_mode="HTML")
                else:
                    await msg.reply_text("❌ <b>Failed to update Marquee setting.</b>")
                return

            h_match = re.search(r'<(h[1-3])[^>]*>(.*?)</\1>', text, re.IGNORECASE | re.DOTALL)
            if h_match:
                title = re.sub(r'<[^>]*>', '', h_match.group(2)).strip()[:50]
                body_content = text.replace(h_match.group(0), "", 1).strip()
            else:
                lines = text.split('\n')
                first_line = lines[0].strip()
                body_content = "\n".join(lines[1:]).strip() if len(lines) > 1 else text
                title = re.sub(r'<[^>]*>', '', first_line).strip()[:50]

            if not title: title = "School Update"
            create_post(title, body_content)
            safe_title = html.escape(title)
            success_msg = (
                "✅ <b>POSTED TO WEBSITE!</b>\n\n"
                f"📌 <b>Title:</b> {safe_title}\n"
                f"🔗 <b>Visit:</b> {html.escape(WP_URL)}\n\n"
                "<i>Your update is now visible in the notice board.</i>"
            )
            await msg.reply_text(success_msg, parse_mode="HTML")
            return

        # PHOTO
        if msg.photo:
            caption = msg.caption or "School Update"
            h_match = re.search(r'<(h[1-3])[^>]*>(.*?)</\1>', caption, re.IGNORECASE | re.DOTALL)
            if h_match:
                title = re.sub(r'<[^>]*>', '', h_match.group(2)).strip()[:50]
                body_content = caption.replace(h_match.group(0), "", 1).strip()
            else:
                lines = caption.split('\n')
                title = re.sub(r'<[^>]*>', '', lines[0]).strip()[:50]
                body_content = caption

            await msg.reply_text("⏳ Processing image...")
            file = await context.bot.get_file(msg.photo[-1].file_id)
            file_bytes = await file.download_as_bytearray()
            media_id, _ = upload_media(file_bytes, filename="update_image.jpg")
            create_post(title, body_content, media_id=media_id)
            
            safe_title = html.escape(title)
            await msg.reply_text(f"🖼 <b>Posted with Image!</b>\nTitle: {safe_title}\n🔗 {html.escape(WP_URL)}", parse_mode="HTML")
            return

        # DOCUMENT (PDF)
        if msg.document:
            filename = msg.document.file_name
            caption = msg.caption or f"Notice: {filename}"
            h_match = re.search(r'<(h[1-3])[^>]*>(.*?)</\1>', caption, re.IGNORECASE | re.DOTALL)
            if h_match:
                title = re.sub(r'<[^>]*>', '', h_match.group(2)).strip()[:50]
                body_base = caption.replace(h_match.group(0), "", 1).strip()
            else:
                lines = caption.split('\n')
                title = re.sub(r'<[^>]*>', '', lines[0]).strip()[:50]
                body_base = caption

            safe_filename = html.escape(filename)
            await msg.reply_text(f"⏳ Uploading Document: <b>{safe_filename}</b>...", parse_mode="HTML")
            
            file = await context.bot.get_file(msg.document.file_id)
            file_bytes = await file.download_as_bytearray()
            mime = msg.document.mime_type or "application/pdf"
            media_id, doc_url = upload_media(file_bytes, filename=filename, mime_type=mime)
            
            doc_link = f'\n\n<div class="notice-attachment"><a href="{doc_url}" class="notice-doc-btn" target="_blank">📄 View Document: {filename}</a></div>'
            body_with_doc = body_base + doc_link
            create_post(title, body_with_doc)
            
            safe_title = html.escape(title)
            await msg.reply_text(f"📄 <b>Notice Posted with Document!</b>\nFile: {safe_filename}\n🔗 {html.escape(WP_URL)}", parse_mode="HTML")
            return

    except Exception as e:
        logger.error(f"Error in handle: {str(e)}")
        safe_err = html.escape(str(e))
        await msg.reply_text(f"❌ <b>Error Occurred:</b>\n<code>{safe_err}</code>", parse_mode="HTML")

# ================= MAIN =================

def main():
    if not TELEGRAM_BOT_TOKEN or TELEGRAM_BOT_TOKEN == "your_bot_token":
        print("⚠️ Error: Please provide a valid TELEGRAM_BOT_TOKEN in .env")
        return

    # Start Flask + pinger ONCE — outside the retry loop so they are never
    # double-started when the bot crashes and auto-restarts.
    threading.Thread(target=run_flask, daemon=True).start()
    threading.Thread(target=pinger, daemon=True).start()

    # Use a list so the async post_init closure can mutate it (nonlocal workaround)
    _first_run = [True]

    while True:
        try:
            # post_init runs inside the bot's event loop right after it starts —
            # no job-queue package required, works with base python-telegram-bot.
            async def post_init(application):
                if ADMIN_USERNAMES and _first_run[0]:
                    notified = 0
                    for uname, cid in _admin_chat_ids.items():
                        if cid:
                            try:
                                await application.bot.send_message(
                                    chat_id=cid,
                                    text="🚀 <b>Bot restarted:</b> Service is now online!",
                                    parse_mode="HTML"
                                )
                                notified += 1
                            except Exception as err:
                                logger.error(f"Failed to notify @{uname}: {err}")
                    if notified == 0:
                        logger.info("No admin chat_ids cached yet — send /start to register.")
                _first_run[0] = False

            app = (
                ApplicationBuilder()
                .token(TELEGRAM_BOT_TOKEN)
                .post_init(post_init)
                .build()
            )
            app.add_handler(CommandHandler("start", start_cmd))
            app.add_handler(CommandHandler("help", help_cmd))
            app.add_handler(CommandHandler("list", list_cmd))
            app.add_handler(CommandHandler("delete", delete_cmd))
            app.add_handler(CommandHandler("reset", reset_cmd))
            app.add_handler(MessageHandler(filters.ALL & (~filters.COMMAND), handle_all))

            print(f"🚀 Bot V5.1 is running for {len(AUTHORIZED_USERNAMES)} authorized usernames...")

            # drop_pending_updates=True clears old queued messages from a crashed/
            # redeployed instance — also fixes Telegram 'Conflict' errors on Render.
            app.run_polling(drop_pending_updates=True)

        except Exception as e:
            err_str = str(e)
            if "Conflict" in err_str:
                logger.warning("⚠️  Telegram Conflict: another instance is still running. Waiting 30s...")
                time.sleep(30)
            else:
                logger.error(f"⚠️  GLOBAL CRASH: {err_str}. Restarting in 10 seconds...")
                time.sleep(10)

if __name__ == "__main__":
    main()
