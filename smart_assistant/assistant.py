from __future__ import annotations

import logging
from typing import Dict, List, Optional, Tuple

from .models import AssistantResult, CalendarEvent, CalendarSyncError, EventExtractionError
from .openai_parser import OpenAIEventParser
from .colors import normalize_color_hint

DEFAULT_CATEGORY_COLORS: Dict[str, str] = {
    "work": "7",
    "meeting": "7",
    "call": "7",
    "personal": "5",
    "family": "2",
    "travel": "9",
    "trip": "9",
    "study": "3",
    "education": "3",
    "finance": "8",
    "payment": "8",
    "health": "10",
    "medical": "10",
    "deadline": "11",
    "reminder": "1",
}


class CalendarAutomationAssistant:
    def __init__(
        self,
        parser: OpenAIEventParser,
        calendar_client,
        *,
        category_colors: Optional[Dict[str, str]] = None,
        default_color_id: Optional[str] = None,
    ):
        self.parser = parser
        self.calendar_client = calendar_client
        self.logger = logging.getLogger(self.__class__.__name__)
        base_mapping = category_colors if category_colors is not None else DEFAULT_CATEGORY_COLORS
        self.category_colors = self._normalize_category_colors(base_mapping)
        self.default_color_id = self._normalize_color(default_color_id)

    def process_text_payload(self, text: str, context: Optional[Dict[str, str]] = None) -> AssistantResult:
        try:
            events = self.parser.parse_text(text, context=context)
        except EventExtractionError as exc:
            self.logger.exception("Text parsing failed")
            return AssistantResult(False, f"解析失败: {exc}")
        return self._persist_events(events)

    def process_image_payload(
        self,
        image_path: str,
        hint: str = "",
        context: Optional[Dict[str, str]] = None,
    ) -> AssistantResult:
        try:
            events = self.parser.parse_image(image_path, hint=hint, context=context)
        except EventExtractionError as exc:
            self.logger.exception("Image parsing failed")
            return AssistantResult(False, f"看图失败: {exc}")
        return self._persist_events(events)

    def process_email_payload(self, subject: str, body: str, context: Optional[Dict[str, str]] = None) -> AssistantResult:
        combined = f"Subject: {subject}\n\n{body}"
        result = self.process_text_payload(combined, context=context)
        if result.success:
            result.message = f"已从邮件添加到日历（共 {len(result.events)} 个）。"
        return result

    def _persist_events(self, events: Optional[List[CalendarEvent]]) -> AssistantResult:
        if not events:
            return AssistantResult(False, "没有找到可以添加到日历的事件。")

        created: List[Tuple[CalendarEvent, str]] = []
        failures = 0

        for event in events:
            self._apply_category_color(event)
            try:
                link = self.calendar_client.create_event(event)
                created.append((event, link))
            except CalendarSyncError as exc:
                failures += 1
                self.logger.exception("Calendar sync failed for '%s'", event.title)

        if not created:
            return AssistantResult(False, "同步日历失败：所有事件均创建失败。")

        message = f"日历事件创建成功，共 {len(created)} 个。"
        if failures:
            message += f" （另有 {failures} 个创建失败，详见日志）"

        events_list = [event for event, _ in created]
        links_list = [link for _, link in created]
        return AssistantResult(True, message, events=events_list, calendar_links=links_list)

    def _normalize_category_colors(self, mapping: Dict[str, str]) -> Dict[str, str]:
        normalized: Dict[str, str] = {}
        for key, value in mapping.items():
            category = (str(key).strip().lower() if key else "")
            color = self._normalize_color(value)
            if category and color:
                normalized[category] = color
        return normalized

    def _normalize_color(self, value: Optional[str]) -> Optional[str]:
        color_id = normalize_color_hint(value)
        return color_id

    def _apply_category_color(self, event: CalendarEvent) -> None:
        if event.color_id:
            return
        category = (event.category or "").strip().lower()
        if category and category in self.category_colors:
            event.color_id = self.category_colors[category]
        elif self.default_color_id:
            event.color_id = self.default_color_id
