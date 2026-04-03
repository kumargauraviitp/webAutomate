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
import json
import io
from typing import Dict, Optional
from flask import Flask
from datetime import datetime, timezone, timedelta
from dotenv import load_dotenv

from telegram import Update, ReplyKeyboardMarkup, KeyboardButton, InlineKeyboardMarkup, InlineKeyboardButton
from telegram.ext import ApplicationBuilder, MessageHandler, CommandHandler, ContextTypes, filters, CallbackQueryHandler
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
LOG_CHANNEL_ID = os.getenv("LOG_CHANNEL_ID", "")

db_admins_raw = os.getenv("DATABASE_ADMIN_USERNAMES", "")
DATABASE_ADMIN_USERNAMES = [u.strip().lower().lstrip("@") for u in db_admins_raw.split(",") if u.strip()]

# Indian Standard Time (IST)
IST = timezone(timedelta(hours=5, minutes=30))

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

# ================= ACTIVITY LOG =================
def log_activity(action, username, details):
    entry = {
        "timestamp": datetime.now(IST).isoformat(),
        "action": action,
        "user": username,
        "details": details
    }
    try:
        logs = []
        if os.path.exists("activity_log.json"):
            with open("activity_log.json", "r", encoding="utf-8") as f:
                logs = json.load(f)
        logs.append(entry)
        with open("activity_log.json", "w", encoding="utf-8") as f:
            json.dump(logs, f, indent=2)
    except Exception as e:
        logger.error(f"Failed to write log: {e}")

def get_recent_logs(n=50):
    try:
        if os.path.exists("activity_log.json"):
            with open("activity_log.json", "r", encoding="utf-8") as f:
                logs = json.load(f)
                return logs[-n:]
    except Exception as e:
        logger.error(f"Failed to read logs: {e}")
    return []

async def send_success_and_json(context, message_or_update, text, action, username, details):
    if hasattr(message_or_update, 'reply_text'):
        msg = message_or_update
    else:
        msg = message_or_update.message
    await msg.reply_text(text, parse_mode="HTML")
    log_activity(action, username, details)
    json_bytes = json.dumps({"action": action, "user": username, "timestamp": datetime.now(IST).isoformat(), "details": details}, indent=2).encode('utf-8')
    f = io.BytesIO(json_bytes)
    f.name = f"{action.lower().replace(' ', '_')}_{int(time.time())}.json"
    
    if LOG_CHANNEL_ID:
        try:
            await context.bot.send_document(chat_id=LOG_CHANNEL_ID, document=f, caption=f"Silently storing log for {action}")
        except Exception as e:
            logger.error(f"Failed to silently send log: {e}")

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
        [KeyboardButton("📊 Activity Log"), KeyboardButton("📦 Export Data")],
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
        "• <code>/delete [ID]</code>: Remove one notice.\n"
        "• <code>/reset</code>: Delete ALL notices at once.\n"
        "• <code>BANNER: [text]</code>: Update marquee.\n"
        "• <code>/log</code>: Show recent bot activity.\n"
        "• <code>/log full</code>: Get all activity logs as .txt file.\n"
        "• <code>/export</code>: Export all notices as .json file.\n"
        "• <b>IMPORT</b>: Send a .json file with caption IMPORT to restore notices.\n\n"
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
        await send_success_and_json(
            context,
            update, 
            f"🗑 Notice <code>{post_id}</code> deleted successfully.", 
            "Delete Notice", 
            update.effective_user.username, 
            {"post_id": post_id, "response": r.json()}
        )
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
        
        await send_success_and_json(
            context,
            update,
            f"🗑 <b>Successfully deleted {count} notices!</b>\nNotice board is now clean.",
            "Reset All Notices",
            update.effective_user.username,
            {"deleted_count": count, "notices_deleted": [n['id'] for n in notices]}
        )
    else:
        await update.message.reply_text("❌ <b>Failed to connect to WordPress for reset.</b>", parse_mode="HTML")

async def send_log_page(target, page: int):
    logs = get_recent_logs(50)
    if not logs:
        if hasattr(target, 'edit_text'):
            await target.edit_text("📭 No recent activity found.")
        else:
            await target.reply_text("📭 No recent activity found.")
        return
        
    # Reverse so newest are at the top
    logs.reverse()
    
    total_pages = (len(logs) - 1) // 10 + 1
    if page < 1: page = 1
    if page > total_pages: page = total_pages
    
    start_idx = (page - 1) * 10
    end_idx = start_idx + 10
    page_logs = logs[start_idx:end_idx]
    
    text = f"📊 <b>Recent Activity (Page {page}/{total_pages})</b>\n\n"
    for log in page_logs:
        action = html.escape(log.get('action', 'Unknown Action'))
        user = html.escape(log.get('user', 'unknown'))
        ts = log.get('timestamp', '')[:19].replace('T', ' ')
        
        icon = "🔹"
        if "Delete" in action or "Reset" in action: icon = "🗑"
        elif "Create" in action: icon = "✅"
        elif "Banner" in action: icon = "🚩"
        
        text += f"{icon} <b>{action}</b> by @{user}\n"
        text += f"   <i>{ts}</i>\n"

    buttons = []
    if page > 1:
        buttons.append(InlineKeyboardButton("⬅️ Prev", callback_data=f"log_page_{page-1}"))
    if page < total_pages:
        buttons.append(InlineKeyboardButton("Next ➡️", callback_data=f"log_page_{page+1}"))
        
    reply_markup = InlineKeyboardMarkup([buttons]) if buttons else None
    
    if hasattr(target, 'edit_text'):
        await target.edit_text(text, parse_mode="HTML", reply_markup=reply_markup)
    else:
        await target.reply_text(text, parse_mode="HTML", reply_markup=reply_markup)

async def log_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if not is_auth(query.from_user.username):
        await query.answer("🛑 Access Denied!", show_alert=True)
        return
        
    await query.answer()
    if query.data.startswith("log_page_"):
        page = int(query.data.split("_")[2])
        await send_log_page(query.message, page)

async def log_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_auth(update.effective_user.username):
        await update.message.reply_text(f"🛑 Access Denied! Contact @{html.escape(CONTACT_USERNAME)} for access.")
        return
    
    parts = update.message.text.split()
    if len(parts) > 1 and parts[1].lower() == "full" or update.message.text.lower() == "log full":
        if not os.path.exists("activity_log.json"):
            await update.message.reply_text("📭 No activity logs found.")
            return
        
        with open("activity_log.json", "r", encoding="utf-8") as f:
            logs = json.load(f)
        
        # Newest at the top for better readability
        logs.reverse()
        
        msg_chunk = "📊 <b>RPS Admin Bot - Full Activity Log</b>\n=====================================\n\n"
        for log in logs:
            action = html.escape(log.get('action', 'Unknown Action'))
            user = html.escape(log.get('user', 'unknown'))
            ts = log.get('timestamp', '')[:19].replace('T', ' ')
            
            icon = "🔹"
            if "Delete" in action or "Reset" in action: icon = "🗑"
            elif "Create" in action: icon = "✅"
            elif "Banner" in action: icon = "🚩"
            
            # Show a smaller, readable snippet of raw details
            detail_val = json.dumps(log.get('details', {}))
            if len(detail_val) > 200: detail_val = detail_val[:197] + "..."
            detail_str = html.escape(detail_val)
            
            entry_text = f"{icon} <b>{action}</b> by @{user}\n   <i>{ts}</i>\n   <pre>{detail_str}</pre>\n\n"
            
            if len(msg_chunk) + len(entry_text) > 3800:
                await update.message.reply_text(msg_chunk, parse_mode="HTML")
                msg_chunk = ""
                
            msg_chunk += entry_text
            
        if msg_chunk:
            await update.message.reply_text(msg_chunk, parse_mode="HTML")
        return

    # Send first page
    await send_log_page(update.message, 1)

async def verifychannel_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat = update.effective_chat
    if chat.type not in ['channel', 'group', 'supergroup']:
        await context.bot.send_message(chat_id=chat.id, text="❌ This command must be used inside a Channel or Group.")
        return
        
    try:
        admins = await context.bot.get_chat_administrators(chat_id=chat.id)
        admin_usernames_in_chat = [a.user.username.lower() for a in admins if a.user.username]
        
        missing_admins = []
        for required_admin in DATABASE_ADMIN_USERNAMES:
            if required_admin.lower() not in admin_usernames_in_chat:
                missing_admins.append(required_admin)
                
        if missing_admins:
            await context.bot.send_message(
                chat_id=chat.id, 
                text=f"⚠️ <b>WARNING: Cannot use this channel as a Database!</b>\n\nThe following required admins are NOT administrators in this channel:\n{', '.join(missing_admins)}\n\nPlease make them admins first.", 
                parse_mode="HTML"
            )
            return
            
        await context.bot.send_message(
            chat_id=chat.id,
            text=f"✅ <b>CHANNEL VERIFIED!</b>\n\nThis channel is safe to use as your Database.\nCopy this ID and put it in your `.env` file under `LOG_CHANNEL_ID`:\n\n<code>{chat.id}</code>",
            parse_mode="HTML"
        )
    except Exception as e:
        await context.bot.send_message(chat_id=chat.id, text=f"❌ Error verifying channel: Ensure the bot is an admin first! ({html.escape(str(e))})")

async def export_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_auth(update.effective_user.username):
        await update.message.reply_text(f"🛑 Access Denied! Contact @{html.escape(CONTACT_USERNAME)} for access.")
        return
    
    await update.message.reply_text("⏳ <b>Fetching all notices for export...</b>", parse_mode="HTML")
    url = f"{WP_URL}/wp-json/wp/v2/notice?per_page=100&status=publish,future,draft"
    r = get_session().get(url)
    
    if r.status_code == 200:
        notices = r.json()
        if not notices:
            await update.message.reply_text("📭 No notices found to export.")
            return
        
        json_bytes = json.dumps(notices, indent=2).encode('utf-8')
        f = io.BytesIO(json_bytes)
        f.name = f"notices_export_{datetime.now().strftime('%Y-%m-%d')}.json"
        
        await update.message.reply_document(
            document=f, 
            caption=f"📦 <b>Export Complete!</b>\n{len(notices)} notices exported.\n\n<i>To restore: send this file back with caption 'IMPORT'</i>", 
            parse_mode="HTML"
        )
    else:
        await update.message.reply_text("❌ Failed to fetch notices for export.")

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
    if msg.text == "📊 Activity Log":
        return await log_cmd(update, context)
    if msg.text == "📦 Export Data":
        return await export_cmd(update, context)
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
                    await send_success_and_json(
                        context,
                        update,
                        success_msg,
                        "Update Banner",
                        update.effective_user.username,
                        {"banner_text": banner_text, "clean_preview": clean_preview}
                    )
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
            resp = create_post(title, body_content)
            safe_title = html.escape(title)
            success_msg = (
                "✅ <b>POSTED TO WEBSITE!</b>\n\n"
                f"📌 <b>Title:</b> {safe_title}\n"
                f"🔗 <b>Visit:</b> {html.escape(WP_URL)}\n\n"
                "<i>Your update is now visible in the notice board.</i>"
            )
            await send_success_and_json(
                context,
                update,
                success_msg,
                "Create Notice (Text)",
                update.effective_user.username,
                {"title": title, "content": body_content, "response": resp}
            )
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
            resp = create_post(title, body_content, media_id=media_id)
            
            safe_title = html.escape(title)
            await send_success_and_json(
                context,
                update,
                f"🖼 <b>Posted with Image!</b>\nTitle: {safe_title}\n🔗 {html.escape(WP_URL)}",
                "Create Notice (Photo)",
                update.effective_user.username,
                {"title": title, "content": body_content, "media_id": media_id, "response": resp}
            )
            return

        # DOCUMENT (PDF or JSON import)
        if msg.document:
            filename = msg.document.file_name
            caption = msg.caption or f"Notice: {filename}"
            
            if caption.strip().upper().startswith("IMPORT") and filename.endswith(".json"):
                await msg.reply_text(f"⏳ Processing IMPORT from <b>{html.escape(filename)}</b>...", parse_mode="HTML")
                file = await context.bot.get_file(msg.document.file_id)
                file_bytes = await file.download_as_bytearray()
                try:
                    import_data = json.loads(file_bytes.decode('utf-8'))
                    if not isinstance(import_data, list):
                        await msg.reply_text("❌ Invalid JSON format (expected a list of notices).")
                        return
                    
                    count = 0
                    for n in import_data:
                        title = n.get('title', {}).get('rendered', 'Imported Notice')
                        content = n.get('content', {}).get('raw', n.get('content', {}).get('rendered', ''))
                        if not content:
                            content = "Imported content missing."
                        
                        title = re.sub(r'<[^>]*>', '', title).strip()[:50]
                        create_post(title, content)
                        count += 1
                        
                    await send_success_and_json(
                        context,
                        update,
                        f"✅ <b>IMPORT COMPLETE</b>\nSuccessfully created {count} notices.",
                        "Import Data",
                        update.effective_user.username,
                        {"imported_count": count, "filename": filename}
                    )
                except Exception as e:
                    await msg.reply_text(f"❌ Failed to parse JSON or import: {html.escape(str(e))}", parse_mode="HTML")
                return

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
            resp = create_post(title, body_with_doc)
            
            safe_title = html.escape(title)
            await send_success_and_json(
                context,
                update,
                f"📄 <b>Notice Posted with Document!</b>\nFile: {safe_filename}\n🔗 {html.escape(WP_URL)}",
                "Create Notice (Document)",
                update.effective_user.username,
                {"title": title, "content": body_with_doc, "filename": filename, "response": resp}
            )
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

                async def daily_summary_job(context: ContextTypes.DEFAULT_TYPE):
                    date_str = datetime.now(IST).strftime('%Y-%m-%d')
                    logs = get_recent_logs(200)
                    from_today = [l for l in logs if l.get("timestamp", "").startswith(date_str)]
                    
                    if not from_today:
                        # Write to last_sync anyway
                        with open("last_sync.json", "w") as sf:
                            json.dump({"last_sync": date_str}, sf)
                        return
                        
                    json_bytes = json.dumps(from_today, indent=2).encode('utf-8')
                    f = io.BytesIO(json_bytes)
                    f.name = f"daily_summary_{date_str}.json"
                    
                    text = f"📊 <b>Daily Activity Digest - {date_str}</b>\n\nThere were {len(from_today)} backup actions recorded today. Master backup JSON attached."
                    
                    for uname, cid in _admin_chat_ids.items():
                        if cid:
                            try:
                                f.seek(0)
                                await context.bot.send_document(chat_id=cid, document=f, caption=text, parse_mode="HTML")
                            except Exception as e:
                                logger.error(f"Failed to send daily digest to {uname}: {e}")
                    
                    try:
                        with open("last_sync.json", "w") as sf:
                            json.dump({"last_sync": date_str}, sf)
                    except Exception as e:
                        logger.error(f"Error saving last_sync: {e}")

                import datetime as dt
                target_time = dt.time(hour=23, minute=0, tzinfo=IST)
                if application.job_queue:
                    application.job_queue.run_daily(daily_summary_job, time=target_time)
                
                date_str = datetime.now(IST).strftime('%Y-%m-%d')
                try:
                    if os.path.exists("last_sync.json"):
                        with open("last_sync.json", "r") as sf:
                            last_sync = json.load(sf).get("last_sync", "")
                    else:
                        last_sync = ""
                except: last_sync = ""
                
                if datetime.now(IST).hour >= 23 and last_sync != date_str:
                    if application.job_queue:
                        application.job_queue.run_once(daily_summary_job, 10)

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
            app.add_handler(CommandHandler("log", log_cmd))
            app.add_handler(CommandHandler("export", export_cmd))
            app.add_handler(CommandHandler("verifychannel", verifychannel_cmd))
            app.add_handler(CallbackQueryHandler(log_callback, pattern="^log_page_"))
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
