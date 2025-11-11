import logging
from pathlib import Path
from urllib.parse import urlparse, parse_qs
from typing import Optional

from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from google.auth.transport.requests import Request
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

from .models import CalendarEvent, CalendarSyncError

SCOPES = [
    "https://www.googleapis.com/auth/calendar",
    "https://www.googleapis.com/auth/tasks",
]


class GoogleCalendarClient:
    def __init__(
        self,
        calendar_id: str = "primary",
        client_secrets_path: Optional[str] = None,
        token_path: str = "google_token.json",
        *,
        credentials: Optional[Credentials] = None,
        allow_interactive: bool = True,
    ):
        self.logger = logging.getLogger(self.__class__.__name__)
        if credentials is None:
            if not client_secrets_path:
                raise ValueError("OAuth 模式需要提供 google.client_secrets_path")
            credentials = self._load_user_credentials(
                client_secrets_path, token_path, allow_interactive=allow_interactive
            )
        self.credentials = credentials
        self.service = build("calendar", "v3", credentials=self.credentials, cache_discovery=False)
        self.calendar_id = calendar_id

    def create_event(self, event: CalendarEvent) -> str:
        body = event.to_google_body()
        try:
            created = (
                self.service.events()
                .insert(calendarId=self.calendar_id, body=body, sendUpdates="all")
                .execute()
            )
            link = created.get("htmlLink", "")
            self.logger.info("Created calendar event '%s'", event.title)
            return link
        except HttpError as exc:
            self.logger.exception("Google Calendar API error: %s", exc)
            raise CalendarSyncError(exc) from exc

    def _load_user_credentials(
        self, client_secrets_path: str, token_path: str, allow_interactive: bool = True
    ) -> Credentials:
        token_file = Path(token_path).expanduser()
        creds: Optional[Credentials] = None

        if token_file.exists():
            creds = Credentials.from_authorized_user_file(token_file, SCOPES)

        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())

        if not creds or not creds.valid:
            if not allow_interactive:
                raise RuntimeError(
                    "未找到可用的 Google OAuth token。请先运行 /google_auth，在 Telegram 中完成授权后再重试。"
                )
            flow = InstalledAppFlow.from_client_secrets_file(client_secrets_path, SCOPES)
            try:
                creds = flow.run_local_server(port=0, prompt="consent")
            except Exception as exc:  # Fallback for headless environments
                self.logger.warning("Local OAuth flow failed (%s), falling back to console mode.", exc)
                creds = self._run_console_authorization(flow)
            token_file.parent.mkdir(parents=True, exist_ok=True)
            token_file.write_text(creds.to_json())
            self.logger.info("Saved Google OAuth token to %s", token_file)

        return creds

    def _run_console_authorization(self, flow: InstalledAppFlow) -> Credentials:
        auth_url, _ = flow.authorization_url(
            access_type="offline",
            prompt="consent",
            include_granted_scopes="true",
        )
        print("\n请在浏览器打开以下链接完成 Google 授权:\n")
        print(auth_url)
        raw_code = input("\n授权完成后，将地址栏中的完整链接或 code=... 值粘贴到此处并按 Enter: ").strip()
        code = self._extract_code(raw_code)
        if not code:
            raise ValueError("未检测到有效的授权 code，请重新复制 Google 返回的完整链接。")
        flow.fetch_token(code=code)
        return flow.credentials

    @staticmethod
    def _extract_code(raw: str) -> Optional[str]:
        """Support pasting whole redirect URL or plain code string."""
        raw = raw.strip()
        if not raw:
            return None
        if raw.startswith("http://") or raw.startswith("https://"):
            parsed = urlparse(raw)
            params = parse_qs(parsed.query)
            codes = params.get("code")
            if codes:
                return codes[0]
            return None
        if raw.startswith("code="):
            return raw.split("code=", 1)[1].split("&", 1)[0]
        if "&scope=" in raw:
            return raw.split("&scope=", 1)[0]
        return raw
