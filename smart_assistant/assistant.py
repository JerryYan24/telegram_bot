from __future__ import annotations

import logging
from typing import Dict, List, Optional, Tuple

from .models import (
    AssistantResult,
    CalendarEvent,
    CalendarSyncError,
    EventExtractionError,
    ParsedItems,
    TaskItem,
    TaskSyncError,
)
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
        calendar_client: "AppleCalendarClient",
        *,
        task_client=None,
        category_colors: Optional[Dict[str, str]] = None,
        default_color_id: Optional[str] = None,
    ):
        self.parser = parser
        self.calendar_client = calendar_client
        self.task_client = task_client
        self.logger = logging.getLogger(self.__class__.__name__)
        base_mapping = category_colors if category_colors is not None else DEFAULT_CATEGORY_COLORS
        self.category_colors = self._normalize_category_colors(base_mapping)
        self.default_color_id = self._normalize_color(default_color_id)

    def process_text_payload(self, text: str, context: Optional[Dict[str, str]] = None) -> AssistantResult:
        try:
            parsed = self.parser.parse_text(text, context=context)
        except EventExtractionError as exc:
            self.logger.exception("Text parsing failed")
            return AssistantResult(False, f"解析失败: {exc}")
        return self._persist_items(parsed)

    def process_image_payload(
        self,
        image_path: str,
        hint: str = "",
        context: Optional[Dict[str, str]] = None,
    ) -> AssistantResult:
        try:
            parsed = self.parser.parse_image(image_path, hint=hint, context=context)
        except EventExtractionError as exc:
            self.logger.exception("Image parsing failed")
            return AssistantResult(False, f"看图失败: {exc}")
        return self._persist_items(parsed)

    def process_email_payload(self, subject: str, body: str, context: Optional[Dict[str, str]] = None) -> AssistantResult:
        combined = f"Subject: {subject}\n\n{body}"
        result = self.process_text_payload(combined, context=context)
        if result.success:
            parts = []
            if result.events:
                parts.append(f"{len(result.events)} 个日历事件")
            if result.tasks:
                parts.append(f"{len(result.tasks)} 条待办")
            summary = "、".join(parts) if parts else "0 条记录"
            result.message = f"已从邮件添加：{summary}。"
        return result

    def _persist_items(self, parsed: ParsedItems) -> AssistantResult:
        events = parsed.events
        tasks = parsed.tasks
        if not events and not tasks:
            return AssistantResult(False, "没有找到可以添加到日历或任务列表的条目。")

        created_events: List[Tuple[CalendarEvent, str]] = []
        event_failures = 0
        if events:
            for event in events:
                self._apply_category_color(event)
                try:
                    link = self.calendar_client.create_event(event)
                    created_events.append((event, link))
                except CalendarSyncError:
                    event_failures += 1
                    self.logger.exception("Calendar sync failed for '%s'", event.title)

        created_tasks: List[Tuple[TaskItem, str]] = []
        task_failures = 0
        if tasks:
            if not self.task_client:
                self.logger.warning("Task client not configured; %d tasks ignored.", len(tasks))
            else:
                for task in tasks:
                    # Enforce task category/list to be within allowed presets if configured
                    try:
                        presets = [s.strip().lower() for s in (self.parser.allowed_task_lists or []) if str(s).strip()]
                    except Exception:
                        presets = []
                    if presets:
                        cat = (task.category or "").strip().lower()
                        lst = (task.list_name or "").strip().lower()
                        if (cat and cat not in presets) or (lst and lst not in presets) or (not cat and not lst):
                            try:
                                mapped_cat, mapped_list = self.parser.map_task_to_allowed(task.title, task.notes)
                                if mapped_cat:
                                    task.category = mapped_cat
                                if mapped_list:
                                    task.list_name = mapped_list
                                # Final normalization
                                if not task.list_name and task.category:
                                    task.list_name = task.category
                            except Exception:
                                # If mapping fails, keep original and let client fallback
                                pass
                    try:
                        link = self.task_client.create_task(task)
                        created_tasks.append((task, link))
                    except TaskSyncError:
                        task_failures += 1
                        self.logger.exception("Task sync failed for '%s'", task.title)

        if not created_events and not created_tasks:
            return AssistantResult(False, "同步失败：所有事件/任务均创建失败。")

        parts = []
        if created_events:
            parts.append(f"日历事件 {len(created_events)} 个")
        if created_tasks:
            parts.append(f"待办 {len(created_tasks)} 条")
        message = "创建成功：" + "、".join(parts)

        if event_failures:
            message += f"（另有 {event_failures} 个事件失败）"
        if task_failures:
            message += f"（另有 {task_failures} 条待办失败）"

        events_list = [event for event, _ in created_events]
        links_list = [link for _, link in created_events]
        tasks_list = [task for task, _ in created_tasks]
        task_links = [link for _, link in created_tasks]
        return AssistantResult(
            True,
            message,
            events=events_list,
            calendar_links=links_list,
            tasks=tasks_list,
            task_links=task_links,
        )

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
            self.logger.debug("Applied color '%s' to category '%s'", event.color_id, category)
        elif self.default_color_id:
            event.color_id = self.default_color_id
            self.logger.debug("Applied default color '%s' to category '%s'", event.color_id, category)
        else:
            self.logger.warning("No color applied for category '%s' (not in category_colors and no default_color_id)", category)
