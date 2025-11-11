from __future__ import annotations

import base64
import json
import logging
import os
from datetime import datetime, timedelta
from typing import Dict, List, Optional
from zoneinfo import ZoneInfo

from dateutil import parser as date_parser
from openai import OpenAI

from .models import CalendarEvent, EventExtractionError, ParsedItems, TaskItem
from .colors import normalize_color_hint

PROMPT_TEMPLATE = """
You are a meticulous executive assistant. Extract calendar-ready entries from the user's input.
Always respond with valid JSON. Use this schema:
{{
  "has_entry": bool,
  "entry_type": "event" | "task",
  "title": string,
  "start": ISO 8601 datetime (YYYY-MM-DDTHH:MM, include timezone if known),
  "end": ISO 8601 datetime,
  "timezone": IANA timezone string,
  "location": string,
  "description": string,
  "attendees": list of email strings,
  "all_day": bool,
  "category": string (lowercase classification such as work, meeting, personal, travel, study, finance, family, health, shopping, reminder, other),
  "color": string (optional color hint when the user explicitly specifies one, e.g. "red", "blue", "green"),
  "task_due": ISO 8601 datetime or date string (only for tasks; leave empty if not provided),
  "task_notes": string (task-specific notes or action items),
  "task_list": string (optional name when the user specifies a particular task list)
}}
If no schedulable entry exists, set has_entry to false.
Infer missing timezone from context; default to {default_timezone}.
Use entry_type="task" for to-dos/reminders without fixed meeting slots; otherwise use "event".
Always try to set category; use "other" only when unsure. Keep titles short but specific.
Never fabricate URLs, meeting links, QR codes, or map locations—only include them when the user explicitly shares them.
"""

PERSONA_EDIT_PROMPT = """
You are helping maintain a concise persona/preferences document for a single user.
Given the current markdown and a new user message, update the markdown to reflect stable, reusable preferences.
Rules:
- Keep it short, structured, and deduplicated.
- Prefer adding or refining existing bullets over long prose.
- Only include enduring preferences (not one-off requests).
- Output FINAL markdown only, no explanations, no code fences.

CURRENT MARKDOWN:
{current_md}

NEW MESSAGE:
{new_message}
"""


class OpenAIEventParser:
    def __init__(
        self,
        api_key: str,
        default_timezone: str = "UTC",
        text_model: str = "gpt-4o-mini",
        vision_model: Optional[str] = None,
        base_url: Optional[str] = None,
        allowed_task_lists: Optional[List[str]] = None,
        allowed_event_categories: Optional[List[str]] = None,
        persona_text: Optional[str] = None,
    ):
        self.default_timezone = default_timezone
        self.text_model = text_model
        self.vision_model = vision_model or text_model
        self.allowed_task_lists = [s.strip() for s in (allowed_task_lists or []) if str(s).strip()]
        self.allowed_event_categories = [s.strip() for s in (allowed_event_categories or []) if str(s).strip()]
        self.persona_text = (persona_text or "").strip()
        client_kwargs = {"api_key": api_key}
        if base_url:
            client_kwargs["base_url"] = base_url
        self.client = OpenAI(**client_kwargs)
        self.logger = logging.getLogger(self.__class__.__name__)

    def update_models(self, text_model: Optional[str] = None, vision_model: Optional[str] = None) -> None:
        if text_model:
            self.text_model = text_model
        if vision_model:
            self.vision_model = vision_model

    def parse_text(self, text: str, context: Optional[Dict[str, str]] = None) -> ParsedItems:
        payload = self._run_completion(
            model=self.text_model,
            user_content=[{"type": "input_text", "text": self._build_user_prompt(text, context)}],
        )
        return self._payload_to_items(payload)

    def parse_image(
        self,
        image_path: str,
        hint: str = "",
        context: Optional[Dict[str, str]] = None,
    ) -> ParsedItems:
        encoded_image = self._encode_image(image_path)
        content = [
            {"type": "input_text", "text": self._build_user_prompt(hint or "从图片中寻找行程信息。", context)},
            {
                "type": "input_image",
                "image_url": {
                    "url": f"data:image/{self._guess_mime_suffix(image_path)};base64,{encoded_image}"
                },
            },
        ]
        payload = self._run_completion(model=self.vision_model, user_content=content)
        return self._payload_to_items(payload)

    def _run_completion(self, model: str, user_content):
        system_prompt = self._build_system_prompt()
        response = self.client.responses.create(
            model=model,
            input=[
                {
                    "role": "system",
                    "content": system_prompt,
                },
                {"role": "user", "content": user_content},
            ],
        )
        text = self._response_to_text(response)
        return self._extract_json(text)

    def _build_system_prompt(self) -> str:
        prompt = PROMPT_TEMPLATE.format(default_timezone=self.default_timezone)
        guidance_parts: List[str] = []
        if self.persona_text:
            guidance_parts.append(f"User preferences and persona: {self.persona_text}")
        if self.allowed_event_categories:
            cats_str = ", ".join(f'"{name}"' for name in self.allowed_event_categories)
            guidance_parts.append(
                f"When entry_type is \"event\", choose category only from: [{cats_str}]."
            )
        if self.allowed_task_lists:
            lists_str = ", ".join(f'"{name}"' for name in self.allowed_task_lists)
            guidance_parts.append(
                f"When entry_type is \"task\", choose category/task_list only from: [{lists_str}]. Avoid inventing new names; if unsure, pick the closest."
            )
        if guidance_parts:
            prompt = prompt + "\n" + " ".join(guidance_parts)
        return prompt

    def refine_persona_markdown(self, current_markdown: str, user_message: str) -> str:
        """Return updated persona markdown based on user_message."""
        system_prompt = "You edit the user's persistent persona/preferences document."
        user_content = PERSONA_EDIT_PROMPT.format(
            current_md=current_markdown or "(empty)",
            new_message=user_message.strip(),
        )
        response = self.client.responses.create(
            model=self.text_model,
            input=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": [{"type": "input_text", "text": user_content}]},
            ],
        )
        text = self._response_to_text(response)
        return (text or "").strip()

    def _response_to_text(self, response) -> str:
        # 1) Prefer unified field if present (some providers expose output_text)
        unified_text = getattr(response, "output_text", None)
        if isinstance(unified_text, str) and unified_text.strip():
            return unified_text.strip()

        # 2) Responses API: response.output -> list of items, each with .content (list) or .content == None
        chunks: List[str] = []
        output_items = getattr(response, "output", None)
        if output_items:
            try:
                for item in output_items:
                    contents = getattr(item, "content", None)
                    if isinstance(contents, list):
                        for content in contents:
                            if getattr(content, "type", "") == "output_text":
                                text_val = getattr(content, "text", "")
                                if isinstance(text_val, str) and text_val:
                                    chunks.append(text_val)
                    elif isinstance(contents, str):
                        chunks.append(contents)
                    # ignore None or unexpected shapes
            except TypeError:
                # In case output_items is not iterable or malformed, ignore and fall back
                pass
        if chunks:
            return "\n".join(chunks).strip()

        # 3) Chat Completions-style fallback
        if hasattr(response, "choices"):
            try:
                for choice in response.choices:
                    message = getattr(choice, "message", None)
                    if message:
                        content = getattr(message, "content", None)
                        if isinstance(content, str) and content.strip():
                            chunks.append(content)
                    # some SDKs use 'text'
                    text_val = getattr(choice, "text", None)
                    if isinstance(text_val, str) and text_val.strip():
                        chunks.append(text_val)
                if chunks:
                    return "\n".join(chunks).strip()
            except Exception:
                pass

        # 4) Dict-based fallbacks (if a raw dict leaked through)
        if isinstance(response, dict):
            # OpenAI chat completions
            if isinstance(response.get("choices"), list):
                for choice in response["choices"]:
                    msg = choice.get("message") or {}
                    content = msg.get("content") or choice.get("text")
                    if isinstance(content, str) and content.strip():
                        chunks.append(content)
                if chunks:
                    return "\n".join(chunks).strip()
            # Responses API-like
            if isinstance(response.get("output"), list):
                for item in response["output"]:
                    contents = item.get("content")
                    if isinstance(contents, list):
                        for content in contents:
                            if content.get("type") == "output_text":
                                t = content.get("text", "")
                                if isinstance(t, str) and t:
                                    chunks.append(t)
                    elif isinstance(contents, str):
                        chunks.append(contents)
                if chunks:
                    return "\n".join(chunks).strip()

        # Nothing found; return empty string to let caller handle
        return ""

    def _extract_json(self, raw_text: str):
        normalized = self._strip_code_fences(raw_text)
        candidates = [normalized, raw_text.strip()]

        for candidate in candidates:
            if not candidate:
                continue
            try:
                return json.loads(candidate)
            except json.JSONDecodeError:
                continue

        # Fallback: slice between braces/brackets
        for opener, closer in [("{", "}"), ("[", "]")]:
            start = normalized.find(opener)
            end = normalized.rfind(closer)
            if start == -1 or end == -1 or end <= start:
                continue
            snippet = normalized[start : end + 1]
            try:
                return json.loads(snippet)
            except json.JSONDecodeError:
                continue

        raise EventExtractionError(f"模型输出中没有找到有效 JSON: {raw_text}")

    def _strip_code_fences(self, text: str) -> str:
        stripped = text.strip()
        if stripped.startswith("```"):
            parts = stripped.split("```")
            if len(parts) >= 3:
                candidate = parts[1]
                candidate = candidate.lstrip()
                if candidate.lower().startswith("json"):
                    candidate = candidate.split("\n", 1)[1] if "\n" in candidate else ""
                stripped = candidate.strip()
        return stripped

    def _payload_to_items(self, payload) -> ParsedItems:
        parsed = ParsedItems()
        if not payload:
            return parsed

        if isinstance(payload, dict) and isinstance(payload.get("events"), list):
            candidates = payload["events"]
        elif isinstance(payload, list):
            candidates = payload
        elif isinstance(payload, dict):
            candidates = [payload]
        else:
            raise EventExtractionError("模型返回了无法识别的结构。")

        for item in candidates:
            if not isinstance(item, dict):
                continue
            if item.get("has_entry") is False and item.get("has_event") is False:
                continue
            entry_type = (item.get("entry_type") or "").lower()
            if entry_type == "task":
                parsed.tasks.append(self._dict_to_task(item))
            else:
                parsed.events.append(self._dict_to_event(item))

        return parsed

    def _dict_to_event(self, payload: Dict) -> CalendarEvent:
        title = payload.get("title") or "Untitled Event"
        timezone = payload.get("timezone") or self.default_timezone
        all_day = bool(payload.get("all_day"))
        category = (payload.get("category") or payload.get("classification") or payload.get("type") or "").strip()

        start_str = payload.get("start") or payload.get("start_time")
        end_str = payload.get("end") or payload.get("end_time")

        if not start_str:
            raise EventExtractionError("模型没有返回开始时间。")

        start_dt = self._parse_datetime(start_str, timezone)
        if end_str:
            end_dt = self._parse_datetime(end_str, timezone)
        else:
            delta = timedelta(days=1) if all_day else timedelta(hours=1)
            end_dt = start_dt + delta

        attendees = payload.get("attendees") or []
        if isinstance(attendees, str):
            attendees = [att.strip() for att in attendees.split(",") if att.strip()]

        description = payload.get("description") or ""
        location = payload.get("location") or ""
        color_hint = payload.get("color_id") or payload.get("colorId") or payload.get("color")
        color_id = normalize_color_hint(color_hint)

        return CalendarEvent(
            title=title.strip(),
            start=start_dt,
            end=end_dt,
            timezone=timezone,
            description=description.strip(),
            location=location.strip(),
            attendees=attendees,
            all_day=all_day,
            category=category,
            color_id=color_id,
        )

    def _dict_to_task(self, payload: Dict) -> TaskItem:
        title = payload.get("title") or "Untitled Task"
        timezone = payload.get("timezone") or self.default_timezone
        due_str = payload.get("task_due") or payload.get("due") or ""
        notes = payload.get("task_notes") or payload.get("description") or ""
        category = (payload.get("category") or payload.get("classification") or payload.get("type") or "").strip()
        task_list_name = (payload.get("task_list") or "").strip()
        # If user didn't specify a list but we have a category, use category as list hint
        if not task_list_name and category:
            task_list_name = category
        # Heuristic: shopping-related titles/notes should go to 'shopping'
        shopping_keywords = ["buy", "purchase", "grocery", "购物", "购买", "采购", "清单", "买", "囤货"]
        text_blob = f"{title} {notes}".lower()
        if any(kw in text_blob for kw in shopping_keywords):
            category = "shopping"
            if not task_list_name:
                task_list_name = "shopping"
        due_dt: Optional[datetime] = None
        if due_str:
            try:
                due_dt = self._parse_datetime(due_str, timezone)
            except Exception:
                due_dt = None

        return TaskItem(
            title=title.strip(),
            due=due_dt,
            timezone=timezone,
            notes=notes.strip(),
            category=category.lower(),
            list_name=task_list_name,
        )

    def _parse_datetime(self, value: str, fallback_tz: str) -> datetime:
        parsed = date_parser.isoparse(value)
        if parsed.tzinfo is None:
            try:
                tz = ZoneInfo(fallback_tz)
            except Exception:
                tz = ZoneInfo("UTC")
            parsed = parsed.replace(tzinfo=tz)
        return parsed

    def _build_user_prompt(self, text: str, context: Optional[Dict[str, str]]) -> str:
        parts = [text.strip()]
        if context:
            context_str = "\n".join(f"{key}: {value}" for key, value in context.items() if value)
            parts.append("上下文:\n" + context_str)
        return "\n\n".join(parts)

    def _encode_image(self, path: str) -> str:
        if not os.path.exists(path):
            raise EventExtractionError(f"图片 {path} 不存在。")
        with open(path, "rb") as img_file:
            return base64.b64encode(img_file.read()).decode("utf-8")

    def _guess_mime_suffix(self, path: str) -> str:
        lower = path.lower()
        if lower.endswith(".png"):
            return "png"
        if lower.endswith(".jpg") or lower.endswith(".jpeg"):
            return "jpeg"
        if lower.endswith(".webp"):
            return "webp"
        return "png"
