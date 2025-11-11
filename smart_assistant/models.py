from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import List, Optional
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError


class EventExtractionError(Exception):
    """Raised when GPT event extraction fails."""


class CalendarSyncError(Exception):
    """Raised when the calendar API rejects an event."""


class TaskSyncError(Exception):
    """Raised when the Google Tasks API rejects a task."""


@dataclass
class CalendarEvent:
    title: str
    start: datetime
    end: datetime
    timezone: str
    description: str = ""
    location: str = ""
    attendees: List[str] = field(default_factory=list)
    all_day: bool = False
    category: str = ""
    color_id: Optional[str] = None

    def _resolve_timezone(self) -> ZoneInfo:
        """Resolve configured timezone with UTC fallback."""
        try:
            return ZoneInfo(self.timezone)
        except ZoneInfoNotFoundError:
            return ZoneInfo("UTC")

    def _normalize_datetime(self, dt: datetime) -> datetime:
        tz = self._resolve_timezone()
        if dt.tzinfo is None:
            return dt.replace(tzinfo=tz)
        return dt.astimezone(tz)

    def to_google_body(self) -> dict:
        """Convert into Google Calendar event payload."""
        start_dt = self._normalize_datetime(self.start)
        end_dt = self._normalize_datetime(self.end)

        if self.all_day:
            start_payload = {"date": start_dt.date().isoformat()}
            end_payload = {"date": end_dt.date().isoformat()}
        else:
            start_payload = {
                "dateTime": start_dt.isoformat(),
                "timeZone": self.timezone,
            }
            end_payload = {
                "dateTime": end_dt.isoformat(),
                "timeZone": self.timezone,
            }

        body = {
            "summary": self.title,
            "start": start_payload,
            "end": end_payload,
            "description": self.description,
        }

        if self.location:
            body["location"] = self.location

        if self.attendees:
            body["attendees"] = [{"email": attendee} for attendee in self.attendees]

        if self.color_id:
            body["colorId"] = str(self.color_id)

        return body

    def to_human_readable(self) -> str:
        """Return a friendly string for Telegram responses."""
        start_dt = self._normalize_datetime(self.start)
        end_dt = self._normalize_datetime(self.end)
        date_fmt = "%Y-%m-%d" if self.all_day else "%Y-%m-%d %H:%M"
        start_str = start_dt.strftime(date_fmt)
        end_str = end_dt.strftime(date_fmt)
        parts = [
            f"标题: {self.title}",
            f"开始: {start_str}",
            f"结束: {end_str}",
            f"时区: {self.timezone}",
        ]
        if self.location:
            parts.append(f"地点: {self.location}")
        if self.description:
            parts.append(f"说明: {self.description}")
        if self.attendees:
            parts.append(f"参与者: {', '.join(self.attendees)}")
        if self.category:
            parts.append(f"分类: {self.category}")
        if self.color_id:
            parts.append(f"颜色ID: {self.color_id}")
        return "\n".join(parts)


@dataclass
class TaskItem:
    title: str
    due: Optional[datetime] = None
    timezone: str = "UTC"
    notes: str = ""
    category: str = ""
    list_name: str = ""

    def _resolve_timezone(self) -> ZoneInfo:
        try:
            return ZoneInfo(self.timezone)
        except ZoneInfoNotFoundError:
            return ZoneInfo("UTC")

    def to_google_body(self) -> dict:
        body = {"title": self.title}
        if self.notes:
            body["notes"] = self.notes
        if self.due:
            due_dt = self._normalize_due(self.due)
            body["due"] = due_dt.isoformat()
        return body

    def _normalize_due(self, dt: datetime) -> datetime:
        tz = self._resolve_timezone()
        if dt.tzinfo is None:
            return dt.replace(tzinfo=tz)
        return dt.astimezone(tz)

    def to_human_readable(self) -> str:
        parts = [f"标题: {self.title}"]
        if self.due:
            due_dt = self._normalize_due(self.due)
            parts.append(f"截止: {due_dt.strftime('%Y-%m-%d %H:%M')}")
        if self.notes:
            parts.append(f"备注: {self.notes}")
        if self.category:
            parts.append(f"分类: {self.category}")
        return "\n".join(parts)


@dataclass
class ParsedItems:
    events: List[CalendarEvent] = field(default_factory=list)
    tasks: List[TaskItem] = field(default_factory=list)


@dataclass
class AssistantResult:
    success: bool
    message: str
    events: List[CalendarEvent] = field(default_factory=list)
    calendar_links: List[str] = field(default_factory=list)
    tasks: List[TaskItem] = field(default_factory=list)
    task_links: List[str] = field(default_factory=list)
