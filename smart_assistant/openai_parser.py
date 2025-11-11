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

from .models import CalendarEvent, EventExtractionError
from .colors import normalize_color_hint

PROMPT_TEMPLATE = """
You are a meticulous executive assistant. Extract calendar-ready events from the user's input.
Always respond with valid JSON. Use this schema:
{{
  "has_event": bool,
  "title": string,
  "start": ISO 8601 datetime (YYYY-MM-DDTHH:MM, include timezone if known),
  "end": ISO 8601 datetime,
  "timezone": IANA timezone string,
  "location": string,
  "description": string,
  "attendees": list of email strings,
  "all_day": bool,
  "category": string (lowercase classification such as work, meeting, personal, travel, study, finance, family, health, reminder, other),
  "color": string (optional color hint when the user explicitly specifies one, e.g. \"red\", \"blue\", \"green\")
}}
If no schedulable event exists, set has_event to false.
Infer missing timezone from context; default to {default_timezone}.
Always try to set category; use "other" only when unsure. Keep titles short but specific.
Never fabricate URLs, meeting links, QR codes, or map locations—only include them when the user explicitly shares them.
"""



class OpenAIEventParser:
    def __init__(
        self,
        api_key: str,
        default_timezone: str = "UTC",
        text_model: str = "gpt-4o-mini",
        vision_model: Optional[str] = None,
        base_url: Optional[str] = None,
    ):
        self.default_timezone = default_timezone
        self.text_model = text_model
        self.vision_model = vision_model or text_model
        client_kwargs = {"api_key": api_key}
        if base_url:
            client_kwargs["base_url"] = base_url
        self.client = OpenAI(**client_kwargs)
        self.logger = logging.getLogger(self.__class__.__name__)

    def parse_text(self, text: str, context: Optional[Dict[str, str]] = None) -> List[CalendarEvent]:
        payload = self._run_completion(
            model=self.text_model,
            user_content=[{"type": "input_text", "text": self._build_user_prompt(text, context)}],
        )
        return self._payload_to_events(payload)

    def parse_image(
        self,
        image_path: str,
        hint: str = "",
        context: Optional[Dict[str, str]] = None,
    ) -> List[CalendarEvent]:
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
        return self._payload_to_events(payload)

    def _run_completion(self, model: str, user_content):
        response = self.client.responses.create(
            model=model,
            input=[
                {
                    "role": "system",
                    "content": PROMPT_TEMPLATE.format(default_timezone=self.default_timezone),
                },
                {"role": "user", "content": user_content},
            ],
        )
        text = self._response_to_text(response)
        return self._extract_json(text)

    def _response_to_text(self, response) -> str:
        chunks = []
        for item in getattr(response, "output", []):
            for content in getattr(item, "content", []):
                if getattr(content, "type", "") == "output_text":
                    chunks.append(content.text)
        if not chunks and hasattr(response, "choices"):
            # Fallback for compatibility with older SDKs
            for choice in response.choices:
                chunks.append(choice.message.content)
        return "\n".join(chunks).strip()

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

    def _payload_to_events(self, payload) -> List[CalendarEvent]:
        if not payload:
            return []

        # Support {"events": [...]} or pure list
        if isinstance(payload, dict) and isinstance(payload.get("events"), list):
            candidates = payload["events"]
        elif isinstance(payload, list):
            candidates = payload
        elif isinstance(payload, dict):
            candidates = [payload]
        else:
            raise EventExtractionError("模型返回了无法识别的结构。")

        events: List[CalendarEvent] = []
        for item in candidates:
            if not isinstance(item, dict):
                continue
            if item.get("has_event") is False:
                continue
            events.append(self._dict_to_event(item))

        return events

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
