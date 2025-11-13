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
    emoji: str = ""  # Emoji for Telegram display

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

        # Build description for Google Calendar (without emoji)
        description_parts = []
        if self.description:
            description_parts.append(self.description)
        if self.attendees:
            description_parts.append(f"Attendees: {', '.join(self.attendees)}")
        if self.category:
            description_parts.append(f"Category: {self.category}")
        google_description = "\n".join(description_parts) if description_parts else ""
        
        body = {
            "summary": self.title,  # Google Calendar title (no emoji)
            "start": start_payload,
            "end": end_payload,
            "description": google_description,
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
        
        # Format date and time
        # Use English month abbreviations and day names
        month_names = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 
                      'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec']
        day_names = ['Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun']
        
        if self.all_day:
            start_date = f"{start_dt.day} {month_names[start_dt.month-1]} ({day_names[start_dt.weekday()]})"
            end_date = f"{end_dt.day} {month_names[end_dt.month-1]} ({day_names[end_dt.weekday()]})"
            if start_date == end_date:
                date_str = start_date
            else:
                date_str = f"{start_date} - {end_date}"
            time_str = "All day"
        else:
            start_date = f"{start_dt.day} {month_names[start_dt.month-1]} ({day_names[start_dt.weekday()]})"
            end_date = f"{end_dt.day} {month_names[end_dt.month-1]} ({day_names[end_dt.weekday()]})"
            start_time = start_dt.strftime("%H:%M")
            end_time = end_dt.strftime("%H:%M")
            
            if start_date == end_date:
                date_str = start_date
                time_str = f"{start_time} - {end_time}"
            else:
                date_str = f"{start_date} - {end_date}"
                time_str = f"{start_time} - {end_time}"
        
        # Build formatted output
        emoji_prefix = f"{self.emoji} " if self.emoji else ""
        title_line = f"{emoji_prefix}[{self.title}] Added"
        
        parts = [title_line]
        
        if self.location:
            parts.append(f"@ {self.location}")
        
        parts.append(f" · Date: {date_str}")
        parts.append(f" · Time: {time_str}")
        
        if self.description:
            parts.append(f" · Notes: {self.description}")
        
        if self.attendees:
            parts.append(f" · Attendees: {', '.join(self.attendees)}")
        
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
