import asyncio
import json
import logging
import os
import tempfile
from datetime import datetime, timezone, timedelta
from functools import partial
from pathlib import Path
from typing import Dict, List, Optional, Tuple
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import (
    ApplicationBuilder,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

from smart_assistant import (
    CalendarAutomationAssistant,
    EmailEventIngestor,
    GoogleCalendarClient,
    GoogleTaskClient,
    OpenAIEventParser,
)
from smart_assistant.calendar_client import SCOPES
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
PARSER: Optional[OpenAIEventParser] = None
GOOGLE_SETTINGS: Dict[str, object] = {}
EMAIL_SETTINGS: Dict[str, object] = {}
PENDING_OAUTH_FLOWS: Dict[int, Dict[str, object]] = {}
OOB_REDIRECT_URI = "urn:ietf:wg:oauth:2.0:oob"
ALLOWED_MODELS: List[str] = []
CURRENT_MODEL: str = ""
BASE_VISION_MODEL: Optional[str] = None
CURRENT_VISION_MODEL: str = ""
MODEL_STATE_PATH: str = "model_state.json"
PERSONA_FILE_PATH: Optional[str] = None
EDIT_PERSONA_CHATS: set[int] = set()


def bootstrap() -> None:
    global ASSISTANT, EMAIL_INGESTOR, TELEGRAM_TOKEN, CONFIG, DEFAULT_TIMEZONE, PARSER, GOOGLE_SETTINGS, EMAIL_SETTINGS

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
    task_list_id = get_config_value(CONFIG, "google.task_list_id", "GOOGLE_TASK_LIST_ID", "@default")
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
    allowed_models_raw = get_config_value(
        CONFIG,
        "openai.allowed_models",
        "OPENAI_ALLOWED_MODELS",
        default=None,
        cast=lambda value: value,
    )
    model_state_path = get_config_value(
        CONFIG,
        "openai.model_state_path",
        "OPENAI_MODEL_STATE_PATH",
        "model_state.json",
    )

    required = {
        "TELEGRAM_BOT_TOKEN/telegram.bot_token": TELEGRAM_TOKEN,
        "OPENAI_API_KEY/openai.api_key": openai_key,
    }
    required["GOOGLE_CLIENT_SECRETS_PATH/google.client_secrets_path"] = google_client_secrets_path
    missing = [key for key, value in required.items() if not value]
    if missing:
        raise RuntimeError(f"缺少必要配置: {', '.join(missing)}")

    # Load persona: prefer external file if provided; fallback to inline notes
    assistant_cfg = (CONFIG.get("assistant", {}) or {})
    persona_file = assistant_cfg.get("persona_file")
    persona_text = None
    global PERSONA_FILE_PATH
    PERSONA_FILE_PATH = persona_file
    usage_path = assistant_cfg.get("usage_path")
    if persona_file:
        try:
            with open(persona_file, "r", encoding="utf-8") as f:
                file_notes = f.read().strip()
                if file_notes:
                    persona_text = file_notes
        except Exception:
            # ignore file load errors; fallback to inline notes
            pass

    parser = OpenAIEventParser(
        api_key=openai_key,
        default_timezone=default_timezone,
        base_url=openai_base_url,
        text_model=openai_text_model,
        vision_model=openai_vision_model,
        allowed_task_lists=(GOOGLE_SETTINGS.get("task_preset_lists") or []),
        allowed_event_categories=list((GOOGLE_SETTINGS.get("category_colors") or {}).keys()),
        persona_text=persona_text,
        usage_path=usage_path,
    )
    PARSER = parser
    global ALLOWED_MODELS, CURRENT_MODEL, BASE_VISION_MODEL, CURRENT_VISION_MODEL, MODEL_STATE_PATH
    ALLOWED_MODELS = _normalize_allowed_models(
        allowed_models_raw,
        default_text=openai_text_model,
        default_vision=openai_vision_model or openai_text_model,
    )
    CURRENT_MODEL = openai_text_model
    BASE_VISION_MODEL = openai_vision_model
    CURRENT_VISION_MODEL = openai_vision_model or openai_text_model
    MODEL_STATE_PATH = model_state_path
    _load_model_state()
    GOOGLE_SETTINGS = {
        "client_secrets_path": google_client_secrets_path,
        "token_path": google_token_path,
        "calendar_id": calendar_id,
        "task_list_id": task_list_id,
        "category_colors": category_colors,
        "default_color_id": default_color_id,
    }

    imap_host = get_config_value(CONFIG, "email.imap_host", "ASSISTANT_IMAP_HOST")
    imap_user = get_config_value(CONFIG, "email.username", "ASSISTANT_EMAIL")
    imap_password = get_config_value(CONFIG, "email.password", "ASSISTANT_EMAIL_PASSWORD")
    poll_interval_raw = get_config_value(CONFIG, "email.poll_interval", "ASSISTANT_EMAIL_POLL_INTERVAL", 60)
    folder = get_config_value(CONFIG, "email.folder", "ASSISTANT_IMAP_FOLDER", "INBOX")
    use_ssl_raw = get_config_value(CONFIG, "email.use_ssl", "ASSISTANT_IMAP_SSL", True)
    EMAIL_SETTINGS = {
        "host": imap_host or "",
        "username": imap_user or "",
        "password": imap_password or "",
        "folder": folder,
        "use_ssl": str(use_ssl_raw).lower() != "false",
        "poll_interval": int(poll_interval_raw),
    }
    if not imap_host or not imap_user or not imap_password:
        logger.info("Email ingestion disabled. 在 config.yaml 中填写 email.* 或 ASSISTANT_IMAP_* 以启用。")

    calendar_client = None
    try:
        calendar_client = GoogleCalendarClient(
            calendar_id=calendar_id,
            client_secrets_path=google_client_secrets_path,
            token_path=google_token_path,
            allow_interactive=False,
        )
    except Exception as exc:
        logger.warning(
            "Google OAuth token 未就绪：%s。请在 Telegram 中发送 /google_auth 完成授权。", exc
        )

    if calendar_client:
        _initialize_assistant(calendar_client)


async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "你好，我是你的日历助手 📅\n"
        "支持三种方式添加日程：\n"
        "1. 把邮件转发到助手邮箱，我会自动解析并写入日历。\n"
        "2. 在 Telegram 发文字或语音转文字描述日程。\n"
        "3. 上传会议/活动海报照片，我能读图识别时间地点。\n"
        "请告诉我你想安排的事情吧！"
    )

async def add_info_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id if update.effective_chat else None
    if chat_id is None:
        return
    EDIT_PERSONA_CHATS.add(chat_id)
    keyboard = InlineKeyboardMarkup(
        [[InlineKeyboardButton("退出编辑模式", callback_data="exit_persona_mode")]]
    )
    await update.message.reply_text(
        "已进入偏好编辑模式。发送消息来完善你的偏好；完成后点击下方按钮退出。",
        reply_markup=keyboard,
    )


async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "随时发送活动描述、转发邮件或分享图片，我会把其中的事件同步到你的 Google Calendar。"
    )

async def usage_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not PARSER:
        await update.message.reply_text("助手尚未初始化，无法查询用量。")
        return
    lines = PARSER.get_usage_summary_lines()
    await update.message.reply_text("模型用量（tokens）:\n" + "\n".join(lines))


async def model_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not PARSER:
        await update.message.reply_text("助手尚未初始化，无法切换模型。")
        return
    if not ALLOWED_MODELS:
        await update.message.reply_text("当前没有配置可切换的模型。")
        return
    if context.args:
        target = context.args[0].strip()
        message = _handle_model_switch(target)
        await update.message.reply_text(message)
        return

    keyboard = _build_model_keyboard()
    allowed_str = ", ".join(ALLOWED_MODELS)
    message = (
        f"当前文本模型: {CURRENT_MODEL}\n"
        f"当前视觉模型: {CURRENT_VISION_MODEL}\n"
        f"可选模型: {allowed_str}\n\n"
        "直接点击下方按钮或输入 `/model 模型名` 即可切换。"
    )
    await update.message.reply_text(message, reply_markup=keyboard)


async def google_auth_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_key = _flow_owner_id(update)
    if user_key is None:
        await update.message.reply_text("无法识别用户，请在私聊或群组中直接使用 /google_auth。")
        return

    existing_entry = PENDING_OAUTH_FLOWS.pop(user_key, None)
    if existing_entry:
        await _delete_auth_prompt(context, existing_entry)

    client_secrets_path = GOOGLE_SETTINGS.get("client_secrets_path")
    if not client_secrets_path:
        await update.message.reply_text("缺少 google.client_secrets_path，请先在 config.yaml 中配置。")
        return
    try:
        flow = InstalledAppFlow.from_client_secrets_file(client_secrets_path, SCOPES)
        flow.redirect_uri = OOB_REDIRECT_URI
        auth_url, _ = flow.authorization_url(
            access_type="offline",
            prompt="consent",
            include_granted_scopes="true",
        )
    except Exception as exc:
        logger.exception("Failed to create OAuth flow")
        await update.message.reply_text(f"生成授权链接失败：{exc}")
        return

    status_line = "当前状态：已授权 ✅（可重新授权）" if ASSISTANT else "当前状态：尚未授权 ❌"
    keyboard = InlineKeyboardMarkup(
        [[InlineKeyboardButton("取消本次授权", callback_data="cancel_oauth")]]
    )
    sent_message = await update.message.reply_text(
        f"{status_line}\n\n"
        "请打开以下链接完成 Google 授权：\n\n"
        f"{auth_url}\n\n"
        "授权完成后，Google 页面会显示一段 code。复制该 code 后发送命令：\n"
        "/google_auth_code <code>\n\n"
        "如果需要重新开始，可再次发送 /google_auth。",
        reply_markup=keyboard,
    )
    PENDING_OAUTH_FLOWS[user_key] = {
        "flow": flow,
        "message_id": sent_message.message_id,
        "chat_id": sent_message.chat_id,
        "expires_at": datetime.now(timezone.utc) + timedelta(minutes=2),
    }


async def google_auth_code_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_key = _flow_owner_id(update)
    if user_key is None:
        await update.message.reply_text("无法识别用户，请在私聊或群组中直接使用 /google_auth_code。")
        return
    if not context.args:
        await update.message.reply_text("请在命令后附上 Google 页面显示的 code。")
        return

    raw_code = " ".join(context.args).strip()
    await _process_oauth_code(user_key, raw_code, update, context, invoked_from_command=True)


async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # Persona edit mode first
    chat_id = update.effective_chat.id if update.effective_chat else None
    if chat_id in EDIT_PERSONA_CHATS and PARSER and PERSONA_FILE_PATH:
        raw_text = (update.message.text or "").strip()
        if raw_text:
            try:
                try:
                    with open(PERSONA_FILE_PATH, "r", encoding="utf-8") as f:
                        current_md = f.read()
                except Exception:
                    current_md = ""
                new_md = await run_in_executor(PARSER.refine_persona_markdown, current_md, raw_text)
                if new_md and new_md != current_md:
                    with open(PERSONA_FILE_PATH, "w", encoding="utf-8") as f:
                        f.write(new_md)
                    PARSER.persona_text = new_md
                    await update.message.reply_text("已更新你的偏好信息到 persona 文件。")
                else:
                    await update.message.reply_text("没有需要更新的偏好信息。")
            except Exception as exc:
                logger.exception("Persona update failed")
                await update.message.reply_text(f"更新偏好失败：{exc}")
        return
    user_key = _flow_owner_id(update)
    if user_key is not None:
        raw_text = (update.message.text or "").strip()
        if raw_text and user_key in PENDING_OAUTH_FLOWS:
            handled = await _process_oauth_code(
                user_key, raw_text, update, context, invoked_from_command=False
            )
            if handled:
                return
    if not ASSISTANT:
        await update.message.reply_text("助手尚未初始化，请先发送 /google_auth 完成授权。")
        return
    text = update.message.text or ""
    metadata = build_metadata(update, source="telegram-text")
    result = await run_in_executor(ASSISTANT.process_text_payload, text, metadata)
    await reply_with_result(update, result)


async def handle_photo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not ASSISTANT:
        await update.message.reply_text("助手尚未初始化，请先发送 /google_auth 完成授权。")
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
    blocks = []
    if result.success and result.events:
        event_blocks = []
        for idx, event in enumerate(result.events, start=1):
            block_lines = [f"{idx}. {event.to_human_readable()}"]
            if idx - 1 < len(result.calendar_links):
                link = result.calendar_links[idx - 1]
                if link:
                    block_lines.append(f"链接: {link}")
            event_blocks.append("\n".join(block_lines))
        if event_blocks:
            blocks.append("🗓 日历事件:\n" + "\n\n".join(event_blocks))

    if result.success and result.tasks:
        task_blocks = []
        for idx, task in enumerate(result.tasks, start=1):
            block_lines = [f"{idx}. {task.to_human_readable()}"]
            if idx - 1 < len(result.task_links):
                link = result.task_links[idx - 1]
                if link:
                    block_lines.append(f"链接: {link}")
            task_blocks.append("\n".join(block_lines))
        if task_blocks:
            blocks.append("✅ 待办事项:\n" + "\n\n".join(task_blocks))

    if blocks:
        message = f"{result.message}\n\n" + "\n\n".join(blocks)
    else:
        message = result.message
    await update.message.reply_text(message)


async def cancel_google_auth(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if not query:
        return
    await query.answer()
    user = query.from_user
    user_key = user.id if user else None
    if user_key is None:
        await query.edit_message_text("无法识别用户，取消操作失败。")
        return
    entry = PENDING_OAUTH_FLOWS.pop(user_key, None)
    if not entry:
        try:
            await query.edit_message_text("当前没有待取消的授权请求。")
        except Exception:
            pass
        return
    chat_id = entry.get("chat_id")
    await _delete_auth_prompt(context, entry)
    if chat_id:
        await context.bot.send_message(chat_id, "已取消本次 Google 授权请求。")


async def model_selection_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if not query:
        return
    await query.answer()
    data = query.data or ""
    if not data.startswith("model_select:"):
        return
    target = data.split("model_select:", 1)[1]
    message = _handle_model_switch(target)
    try:
        await query.edit_message_text(message, reply_markup=_build_model_keyboard())
    except Exception:
        await query.message.reply_text(message)

async def exit_persona_mode_cb(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if not query:
        return
    await query.answer()
    chat_id = query.message.chat_id if query.message else None
    if chat_id in EDIT_PERSONA_CHATS:
        EDIT_PERSONA_CHATS.discard(chat_id)
    await query.edit_message_text("已退出偏好编辑模式。")


def _flow_owner_id(update: Update) -> Optional[int]:
    user = update.effective_user
    if user and user.id:
        return user.id
    chat = update.effective_chat
    if chat and chat.id:
        return chat.id
    return None


def _persist_credentials(creds: Credentials) -> None:
    token_path = GOOGLE_SETTINGS.get("token_path") or "google_token.json"
    token_file = Path(token_path).expanduser()
    token_file.parent.mkdir(parents=True, exist_ok=True)
    token_file.write_text(creds.to_json())
    logger.info("Saved Google OAuth token to %s", token_file)


def _handle_model_switch(target: str) -> str:
    global CURRENT_MODEL, CURRENT_VISION_MODEL
    normalized = _match_allowed_model(target)
    if not normalized:
        allowed_str = ", ".join(ALLOWED_MODELS)
        return f"未知模型: {target}。可选项：{allowed_str}"
    if normalized == CURRENT_MODEL:
        return f"当前已使用模型 {normalized}。"
    vision_model = BASE_VISION_MODEL or normalized
    CURRENT_MODEL = normalized
    CURRENT_VISION_MODEL = vision_model
    _apply_current_model_to_parser()
    _persist_model_state()
    return f"解析模型已切换为 {normalized}。"


def _build_model_keyboard() -> Optional[InlineKeyboardMarkup]:
    if not ALLOWED_MODELS:
        return None
    buttons = []
    for model in ALLOWED_MODELS:
        label = f"✅ {model}" if model == CURRENT_MODEL else model
        buttons.append(InlineKeyboardButton(label, callback_data=f"model_select:{model}"))
    rows = [buttons[i : i + 2] for i in range(0, len(buttons), 2)]
    return InlineKeyboardMarkup(rows)


def _match_allowed_model(target: str) -> Optional[str]:
    if not target:
        return None
    lowered = target.lower()
    for candidate in ALLOWED_MODELS:
        if candidate.lower() == lowered:
            return candidate
    return None


def _persist_model_state() -> None:
    if not MODEL_STATE_PATH:
        return
    path = Path(MODEL_STATE_PATH).expanduser()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
    except FileExistsError:
        pass
    data = {
        "text_model": CURRENT_MODEL,
        "vision_model": CURRENT_VISION_MODEL,
    }
    try:
        path.write_text(json.dumps(data))
    except Exception as exc:
        logger.warning("Failed to persist model state: %s", exc)


def _load_model_state() -> None:
    if not MODEL_STATE_PATH:
        return
    path = Path(MODEL_STATE_PATH).expanduser()
    if not path.exists():
        _apply_current_model_to_parser()
        return
    try:
        data = json.loads(path.read_text() or "{}")
    except Exception as exc:
        logger.warning("Failed to read model state file %s: %s", path, exc)
        _apply_current_model_to_parser()
        return
    text_model = data.get("text_model")
    vision_model = data.get("vision_model")
    matched_model = _match_allowed_model(text_model) if text_model else None
    if matched_model:
        global CURRENT_MODEL, CURRENT_VISION_MODEL
        CURRENT_MODEL = matched_model
        if BASE_VISION_MODEL:
            CURRENT_VISION_MODEL = BASE_VISION_MODEL
        else:
            CURRENT_VISION_MODEL = vision_model or matched_model
    _apply_current_model_to_parser()


def _apply_current_model_to_parser() -> None:
    if PARSER:
        vision_model = BASE_VISION_MODEL or CURRENT_VISION_MODEL or CURRENT_MODEL
        PARSER.update_models(text_model=CURRENT_MODEL, vision_model=vision_model)


async def _process_oauth_code(
    user_key: int,
    raw_code: str,
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    *,
    invoked_from_command: bool,
) -> bool:
    entry = PENDING_OAUTH_FLOWS.get(user_key)
    if not entry:
        if invoked_from_command:
            await update.message.reply_text("没有待处理的授权请求，请先发送 /google_auth 获取链接。")
        return False

    expires_at = entry.get("expires_at")
    if expires_at and datetime.now(timezone.utc) > expires_at:
        await _delete_auth_prompt(context, entry)
        PENDING_OAUTH_FLOWS.pop(user_key, None)
        await update.message.reply_text("授权请求已过期，请重新发送 /google_auth。")
        return True

    code = GoogleCalendarClient._extract_code(raw_code)
    if not code:
        await update.message.reply_text("未检测到有效的 code，请直接粘贴 Google 页面显示的字符串。")
        return True

    flow: InstalledAppFlow = entry["flow"]
    try:
        flow.fetch_token(code=code)
    except Exception as exc:
        logger.exception("Failed to exchange OAuth code")
        PENDING_OAUTH_FLOWS.pop(user_key, None)
        await _delete_auth_prompt(context, entry)
        await update.message.reply_text(f"换取 token 失败：{exc}\n请重新发送 /google_auth 再试。")
        return True

    creds = flow.credentials
    PENDING_OAUTH_FLOWS.pop(user_key, None)
    await _delete_auth_prompt(context, entry)
    try:
        _persist_credentials(creds)
    except Exception as exc:
        logger.exception("Failed to persist OAuth token")
        await update.message.reply_text(f"保存 token 失败：{exc}")
        return True

    try:
        calendar_client = GoogleCalendarClient(
            calendar_id=GOOGLE_SETTINGS.get("calendar_id", "primary"),
            client_secrets_path=GOOGLE_SETTINGS.get("client_secrets_path"),
            token_path=GOOGLE_SETTINGS.get("token_path", "google_token.json"),
            credentials=creds,
        )
    except Exception as exc:
        logger.exception("Failed to build Google Calendar client after OAuth")
        await update.message.reply_text(f"初始化 Google Calendar 失败：{exc}")
        return True

    _initialize_assistant(calendar_client)
    await update.message.reply_text("Google 授权成功，现在可以开始创建日程了！")
    return True


async def _delete_auth_prompt(context: ContextTypes.DEFAULT_TYPE, entry: Dict[str, object]) -> None:
    chat_id = entry.get("chat_id")
    message_id = entry.get("message_id")
    if not chat_id or not message_id:
        return
    try:
        await context.bot.delete_message(chat_id=chat_id, message_id=message_id)
    except Exception:
        logger.debug("Failed to delete auth prompt message for chat %s", chat_id)


def _initialize_assistant(calendar_client: GoogleCalendarClient) -> None:
    global ASSISTANT
    if not PARSER:
        raise RuntimeError("OpenAI 事件解析器尚未初始化。")

    task_client = None
    try:
        task_list_id = GOOGLE_SETTINGS.get("task_list_id", "@default")
        preset_lists = GOOGLE_SETTINGS.get("task_preset_lists") or []
        task_client = GoogleTaskClient(
            calendar_client.credentials,
            task_list_id=task_list_id,
            preset_list_names=preset_lists,
            max_lists=max(6, len(preset_lists)) or 6,
        )
    except Exception as exc:
        logger.warning("Google Tasks 客户端初始化失败，将仅同步日历：%s", exc)

    ASSISTANT = CalendarAutomationAssistant(
        PARSER,
        calendar_client,
        task_client=task_client,
        category_colors=GOOGLE_SETTINGS.get("category_colors"),
        default_color_id=GOOGLE_SETTINGS.get("default_color_id"),
    )
    _ensure_email_ingestor()
    logger.info("Google Calendar/Tasks 凭证已就绪，助手完成初始化。")


def _ensure_email_ingestor() -> None:
    global EMAIL_INGESTOR
    host = EMAIL_SETTINGS.get("host")
    username = EMAIL_SETTINGS.get("username")
    password = EMAIL_SETTINGS.get("password")
    if not host or not username or not password:
        _stop_email_ingestor()
        return
    if not ASSISTANT:
        _stop_email_ingestor()
        return
    if EMAIL_INGESTOR:
        EMAIL_INGESTOR.assistant = ASSISTANT
        return
    EMAIL_INGESTOR = EmailEventIngestor(
        host=host,
        username=username,
        password=password,
        assistant=ASSISTANT,
        folder=EMAIL_SETTINGS.get("folder", "INBOX"),
        use_ssl=bool(EMAIL_SETTINGS.get("use_ssl", True)),
        poll_interval=int(EMAIL_SETTINGS.get("poll_interval", 60)),
    )
    EMAIL_INGESTOR.start()
    logger.info("Email ingestion enabled for %s", username)


def _stop_email_ingestor() -> None:
    global EMAIL_INGESTOR
    if EMAIL_INGESTOR:
        EMAIL_INGESTOR.stop()
        EMAIL_INGESTOR = None


async def run_in_executor(func, *args):
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(None, partial(func, *args))


def main():
    bootstrap()
    if not TELEGRAM_TOKEN:
        raise RuntimeError("缺少 TELEGRAM_BOT_TOKEN。")
    if not ASSISTANT:
        logger.warning("助手尚未完成 Google 授权，发送 /google_auth 以继续。")

    application = ApplicationBuilder().token(TELEGRAM_TOKEN).build()
    application.add_handler(CommandHandler("start", start_command))
    application.add_handler(CommandHandler("help", help_command))
    application.add_handler(CommandHandler("usage", usage_command))
    application.add_handler(CommandHandler("model", model_command))
    application.add_handler(CommandHandler("add_info", add_info_command))
    application.add_handler(CommandHandler("google_auth", google_auth_command))
    application.add_handler(CommandHandler("google_auth_code", google_auth_code_command))
    application.add_handler(CallbackQueryHandler(cancel_google_auth, pattern="^cancel_oauth$"))
    application.add_handler(CallbackQueryHandler(model_selection_callback, pattern="^model_select:"))
    application.add_handler(CallbackQueryHandler(exit_persona_mode_cb, pattern="^exit_persona_mode$"))
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


def _normalize_allowed_models(value, default_text: Optional[str], default_vision: Optional[str]) -> List[str]:
    models: List[str] = []
    if isinstance(value, str):
        raw_items = value.replace(";", ",").split(",")
        models = [item.strip() for item in raw_items if item.strip()]
    elif isinstance(value, (list, tuple, set)):
        for item in value:
            if not item:
                continue
            models.append(str(item).strip())
    else:
        models = []
    defaults = [default_text, default_vision]
    for item in defaults:
        if item:
            models.append(item)
    seen = set()
    unique_models = []
    for model in models:
        if not model or model in seen:
            continue
        seen.add(model)
        unique_models.append(model)
    return unique_models


if __name__ == "__main__":
    main()
