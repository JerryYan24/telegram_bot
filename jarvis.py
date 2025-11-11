import asyncio
import logging
import os
import tempfile
from datetime import datetime, timezone
from functools import partial
from typing import Dict, Optional, Tuple
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from telegram import Update
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

from smart_assistant import (
    CalendarAutomationAssistant,
    EmailEventIngestor,
    GoogleCalendarClient,
    OpenAIEventParser,
)
from smart_assistant.config import get_config_value, load_config
from smart_assistant.models import AssistantResult


logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger("SmartAssistantBot")

ASSISTANT: Optional[CalendarAutomationAssistant] = None
EMAIL_INGESTOR: Optional[EmailEventIngestor] = None
TELEGRAM_TOKEN: Optional[str] = None
CONFIG: Dict[str, object] = {}
DEFAULT_TIMEZONE: str = "UTC"


def bootstrap() -> None:
    global ASSISTANT, EMAIL_INGESTOR, TELEGRAM_TOKEN, CONFIG, DEFAULT_TIMEZONE

    config_path = os.getenv("ASSISTANT_CONFIG_PATH")
    CONFIG = load_config(config_path)
    if CONFIG:
        logger.info("Loaded config from %s", config_path or "config.yaml")

    TELEGRAM_TOKEN = get_config_value(CONFIG, "telegram.bot_token", "TELEGRAM_BOT_TOKEN")
    openai_key = get_config_value(CONFIG, "openai.api_key", "OPENAI_API_KEY")
    openai_base_url = get_config_value(CONFIG, "openai.base_url", "OPENAI_BASE_URL")
    openai_text_model = get_config_value(CONFIG, "openai.text_model", "OPENAI_TEXT_MODEL", "gpt-4o-mini")
    openai_vision_model = get_config_value(CONFIG, "openai.vision_model", "OPENAI_VISION_MODEL")
    google_client_secrets_path = get_config_value(
        CONFIG, "google.client_secrets_path", "GOOGLE_CLIENT_SECRETS_PATH"
    )
    google_token_path = get_config_value(
        CONFIG, "google.token_path", "GOOGLE_TOKEN_PATH", "google_token.json"
    )
    calendar_id = get_config_value(CONFIG, "google.calendar_id", "GOOGLE_CALENDAR_ID", "primary")
    default_timezone = get_config_value(
        CONFIG, "assistant.default_tz", "ASSISTANT_DEFAULT_TZ", "UTC"
    )
    DEFAULT_TIMEZONE = default_timezone
    category_colors = get_config_value(
        CONFIG,
        "google.category_colors",
        "",
        default=None,
        cast=lambda value: value,
    )
    if not isinstance(category_colors, dict):
        category_colors = None
    default_color_id = get_config_value(CONFIG, "google.default_color_id", "GOOGLE_DEFAULT_COLOR_ID")

    required = {
        "TELEGRAM_BOT_TOKEN/telegram.bot_token": TELEGRAM_TOKEN,
        "OPENAI_API_KEY/openai.api_key": openai_key,
    }
    required["GOOGLE_CLIENT_SECRETS_PATH/google.client_secrets_path"] = google_client_secrets_path
    missing = [key for key, value in required.items() if not value]
    if missing:
        raise RuntimeError(f"缺少必要配置: {', '.join(missing)}")

    parser = OpenAIEventParser(
        api_key=openai_key,
        default_timezone=default_timezone,
        base_url=openai_base_url,
        text_model=openai_text_model,
        vision_model=openai_vision_model,
    )
    calendar_client = GoogleCalendarClient(
        calendar_id=calendar_id,
        client_secrets_path=google_client_secrets_path,
        token_path=google_token_path,
    )
    ASSISTANT = CalendarAutomationAssistant(
        parser,
        calendar_client,
        category_colors=category_colors,
        default_color_id=default_color_id,
    )

    imap_host = get_config_value(CONFIG, "email.imap_host", "ASSISTANT_IMAP_HOST")
    imap_user = get_config_value(CONFIG, "email.username", "ASSISTANT_EMAIL")
    imap_password = get_config_value(CONFIG, "email.password", "ASSISTANT_EMAIL_PASSWORD")

    if imap_host and imap_user and imap_password:
        poll_interval_raw = get_config_value(
            CONFIG, "email.poll_interval", "ASSISTANT_EMAIL_POLL_INTERVAL", 60
        )
        folder = get_config_value(CONFIG, "email.folder", "ASSISTANT_IMAP_FOLDER", "INBOX")
        use_ssl_raw = get_config_value(CONFIG, "email.use_ssl", "ASSISTANT_IMAP_SSL", True)
        poll_interval = int(poll_interval_raw)
        use_ssl = str(use_ssl_raw).lower() != "false"
        EMAIL_INGESTOR = EmailEventIngestor(
            host=imap_host,
            username=imap_user,
            password=imap_password,
            assistant=ASSISTANT,
            folder=folder,
            use_ssl=use_ssl,
            poll_interval=poll_interval,
        )
        EMAIL_INGESTOR.start()
        logger.info("Email ingestion enabled for %s", imap_user)
    else:
        logger.warning("Email ingestion disabled. 在 config.yaml 或环境变量中设置 ASSISTANT_IMAP_* 以启用。")


async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "你好，我是你的日历助手 📅\n"
        "支持三种方式添加日程：\n"
        "1. 把邮件转发到助手邮箱，我会自动解析并写入日历。\n"
        "2. 在 Telegram 发文字或语音转文字描述日程。\n"
        "3. 上传会议/活动海报照片，我能读图识别时间地点。\n"
        "请告诉我你想安排的事情吧！"
    )


async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "随时发送活动描述、转发邮件或分享图片，我会把其中的事件同步到你的 Google Calendar。"
    )


async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not ASSISTANT:
        await update.message.reply_text("助手尚未初始化。")
        return
    text = update.message.text or ""
    metadata = build_metadata(update, source="telegram-text")
    result = await run_in_executor(ASSISTANT.process_text_payload, text, metadata)
    await reply_with_result(update, result)


async def handle_photo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not ASSISTANT:
        await update.message.reply_text("助手尚未初始化。")
        return
    if not update.message.photo:
        await update.message.reply_text("没有检测到可用的图片。")
        return
    photo = update.message.photo[-1]
    telegram_file = await photo.get_file()
    with tempfile.NamedTemporaryFile(delete=False, suffix=".jpg") as tmp:
        await telegram_file.download_to_drive(tmp.name)
        temp_path = tmp.name
    try:
        hint = update.message.caption or ""
        metadata = build_metadata(update, source="telegram-photo")
        result = await run_in_executor(ASSISTANT.process_image_payload, temp_path, hint, metadata)
    finally:
        try:
            os.unlink(temp_path)
        except FileNotFoundError:
            pass
    await reply_with_result(update, result)


def build_metadata(update: Update, source: str) -> Dict[str, str]:
    user = update.effective_user
    chat = update.effective_chat
    local_time, utc_time = _current_time_strings()
    return {
        "source": source,
        "telegram_user_id": str(user.id) if user else "",
        "telegram_username": user.username if user else "",
        "chat_id": str(chat.id) if chat else "",
        "current_time_local": local_time,
        "current_time_utc": utc_time,
    }


async def reply_with_result(update: Update, result: AssistantResult):
    if result.success and result.events:
        blocks = []
        for idx, event in enumerate(result.events, start=1):
            block_lines = [f"{idx}. {event.to_human_readable()}"]
            if idx - 1 < len(result.calendar_links):
                link = result.calendar_links[idx - 1]
                if link:
                    block_lines.append(f"链接: {link}")
            blocks.append("\n".join(block_lines))
        message = f"{result.message}\n\n" + "\n\n".join(blocks)
    else:
        message = result.message
    await update.message.reply_text(message)


async def run_in_executor(func, *args):
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(None, partial(func, *args))


def main():
    bootstrap()
    if not TELEGRAM_TOKEN or not ASSISTANT:
        raise RuntimeError("助手初始化失败。")

    application = ApplicationBuilder().token(TELEGRAM_TOKEN).build()
    application.add_handler(CommandHandler("start", start_command))
    application.add_handler(CommandHandler("help", help_command))
    application.add_handler(MessageHandler(filters.PHOTO, handle_photo))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))

    logger.info("Smart assistant is up and running.")
    application.run_polling(drop_pending_updates=True, close_loop=False)


def _current_time_strings() -> Tuple[str, str]:
    now_utc = datetime.now(timezone.utc)
    try:
        tz = ZoneInfo(DEFAULT_TIMEZONE)
    except ZoneInfoNotFoundError:
        tz = ZoneInfo("UTC")
    local_now = now_utc.astimezone(tz)
    local_str = local_now.strftime("%Y-%m-%d %H:%M (%Z)")
    return local_str, now_utc.isoformat()


if __name__ == "__main__":
    main()
