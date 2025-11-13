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
  "category": string (lowercase classification; see constraints below),
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

TODAY_SUMMARY_PROMPT = """
You are an executive assistant. Summarize today's plan based on calendar events and tasks.
Constraints:
- Output a concise, well-structured Chinese summary (bullet list or short paragraphs).
- Start with a one-line headline. Then list time-ordered events, then prioritized tasks.
- Highlight deadlines/time-sensitive items first.
- Keep under 200-250 Chinese characters if possible.

DATE: {date_str} ({tz})
CALENDAR:
{calendar_block}

TASKS:
{tasks_block}
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
        usage_path: Optional[str] = None,
    ):
        self.default_timezone = default_timezone
        self.text_model = text_model
        self.vision_model = vision_model or text_model
        self.allowed_task_lists = [s.strip() for s in (allowed_task_lists or []) if str(s).strip()]
        self.allowed_event_categories = [s.strip() for s in (allowed_event_categories or []) if str(s).strip()]
        self.persona_text = (persona_text or "").strip()
        # Usage tracking per model
        self.usage_by_model: Dict[str, Dict[str, int]] = {}
        self.usage_path = usage_path or ""
        # Cache whether the upstream supports Responses API; fallback to chat.completions if not.
        self._responses_supported: bool = True
        if self.usage_path and os.path.exists(self.usage_path):
            try:
                with open(self.usage_path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    if isinstance(data, dict):
                        # Normalize ints
                        norm: Dict[str, Dict[str, int]] = {}
                        for model_name, stats in data.items():
                            if not isinstance(stats, dict):
                                continue
                            norm[model_name] = {
                                "prompt": int(stats.get("prompt", 0)),
                                "completion": int(stats.get("completion", 0)),
                                "total": int(stats.get("total", 0)),
                            }
                        self.usage_by_model = norm
            except Exception:
                self.usage_by_model = {}
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
        # If we already detected Responses unsupported or model is Gemini-family, go straight to fallback
        if (not self._responses_supported) or ("gemini" in (model or "").lower()):
            text = self._fallback_chat_completion(model, system_prompt, user_content)
            return self._extract_json(text)
        # Try Responses API first
        try:
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
            # Update token usage counters if available
            try:
                prompt_toks, completion_toks, total_toks = self._extract_usage(response)
                if prompt_toks or completion_toks or total_toks:
                    bucket = self.usage_by_model.setdefault(model, {"prompt": 0, "completion": 0, "total": 0})
                    bucket["prompt"] += prompt_toks
                    bucket["completion"] += completion_toks
                    bucket["total"] += (total_toks or (prompt_toks + completion_toks))
                    self._persist_usage()
            except Exception:
                pass
            text = self._response_to_text(response)
            return self._extract_json(text)
        except Exception as exc:
            # Mark Responses unsupported on classic gateway errors to avoid repeated failures
            msg = str(exc).lower()
            if "not implemented" in msg or "501" in msg or "convert_request_failed" in msg or "500" in msg:
                self._responses_supported = False
            # Fallback to Chat Completions for gateways (e.g., Gemini proxy) that don't implement Responses API
            text = self._fallback_chat_completion(model, system_prompt, user_content)
            return self._extract_json(text)

    def _build_system_prompt(self) -> str:
        prompt = PROMPT_TEMPLATE.format(default_timezone=self.default_timezone)
        guidance_parts: List[str] = []
        if self.persona_text:
            guidance_parts.append(f"User preferences and persona: {self.persona_text}")
        if self.allowed_event_categories:
            cats_str = ", ".join(f'"{name}"' for name in self.allowed_event_categories)
            guidance_parts.append(
                f"CRITICAL: When entry_type is \"event\", the category field MUST be exactly one of: [{cats_str}]. "
                f"Do not use any other category names. If the event doesn't clearly match any category, choose the closest one from this list."
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

    def map_task_to_allowed(self, title: str, notes: str = "") -> tuple[str, str]:
        """
        Ask model to map a free-form task into one of the allowed task lists.
        Returns (category, task_list) both guaranteed to be within allowed_task_lists if possible; else empty strings.
        """
        presets = [s.strip() for s in (self.allowed_task_lists or []) if str(s).strip()]
        if not presets:
            return "", ""
        system_prompt = "You assign tasks to one of the provided allowed lists."
        allowed_str = ", ".join(f'"{p}"' for p in presets)
        user_text_parts = [
            "Given a task title and optional notes, choose the BEST matching category/list strictly from this set: ",
            f"[{allowed_str}]. Respond with compact JSON containing keys: category, task_list (both strings). ",
            "Do not invent names outside the set. If multiple fit, pick the most specific.",
            f"\n\nTitle: {title}\nNotes: {notes or ''}",
        ]
        user_text = "".join(user_text_parts)
        # Prefer Responses API
        try:
            response = self.client.responses.create(
                model=self.text_model,
                input=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": [{"type": "input_text", "text": user_text}]},
                ],
            )
            text = self._response_to_text(response)
        except Exception:
            text = self._fallback_chat_completion(
                self.text_model,
                system_prompt,
                [{"type": "input_text", "text": user_text}],
            )
        try:
            data = self._extract_json(text)
            cat = (data.get("category") or "").strip().lower()
            lst = (data.get("task_list") or "").strip().lower()
            if cat in [p.lower() for p in presets]:
                return cat, cat if not lst or lst not in [p.lower() for p in presets] else lst
            if lst in [p.lower() for p in presets]:
                return lst, lst
        except Exception:
            pass
        return "", ""

    def _fallback_chat_completion(self, model: str, system_prompt: str, user_content) -> str:
        """Fallback path using chat.completions when Responses API is not available."""
        # Convert user_content into Chat Completions format
        # user_content can be:
        # - [{"type":"input_text","text": "..."}]
        # - [{"type":"input_text",...}, {"type":"input_image","image_url": {...}}]
        user_message_content = []
        if isinstance(user_content, list):
            for item in user_content:
                item_type = item.get("type")
                if item_type in ("input_text", "text"):
                    text_val = item.get("text") or item.get("content") or ""
                    if text_val:
                        user_message_content.append({"type": "text", "text": text_val})
                elif item_type in ("input_image", "image_url", "image"):
                    image_url = item.get("image_url") or item.get("url") or {}
                    if isinstance(image_url, dict) and image_url.get("url"):
                        user_message_content.append({"type": "image_url", "image_url": {"url": image_url["url"]}})
        else:
            # Plain string as content
            if isinstance(user_content, str) and user_content.strip():
                user_message_content.append({"type": "text", "text": user_content})

        # Some gateways require content to be a string; fallback to joining texts
        messages = [{"role": "system", "content": system_prompt}]
        if user_message_content:
            messages.append({"role": "user", "content": user_message_content})
        else:
            messages.append({"role": "user", "content": system_prompt})

        try:
            chat = self.client.chat.completions.create(model=model, messages=messages)
            # Update usage if available
            try:
                prompt_toks, completion_toks, total_toks = self._extract_usage(chat)
                if prompt_toks or completion_toks or total_toks:
                    bucket = self.usage_by_model.setdefault(model, {"prompt": 0, "completion": 0, "total": 0})
                    bucket["prompt"] += prompt_toks
                    bucket["completion"] += completion_toks
                    bucket["total"] += (total_toks or (prompt_toks + completion_toks))
                    self._persist_usage()
            except Exception:
                pass
            # Extract text
            if hasattr(chat, "choices") and chat.choices:
                choice = chat.choices[0]
                msg = getattr(choice, "message", None)
                if msg and getattr(msg, "content", None):
                    return msg.content
                if getattr(choice, "text", None):
                    return choice.text
            # Dict fallback
            if isinstance(chat, dict):
                ch = chat.get("choices") or []
                if ch:
                    msg = (ch[0].get("message") or {})
                    content = msg.get("content") or ch[0].get("text") or ""
                    return content or ""
        except Exception:
            pass
        return ""

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

    def _extract_usage(self, response) -> tuple[int, int, int]:
        """Return (prompt_tokens, completion_tokens, total_tokens) if present; else zeros."""
        # SDK attribute style
        usage = getattr(response, "usage", None)
        if usage:
            prompt = getattr(usage, "input_tokens", None) or getattr(usage, "prompt_tokens", None) or 0
            completion = getattr(usage, "output_tokens", None) or getattr(usage, "completion_tokens", None) or 0
            total = getattr(usage, "total_tokens", None) or (prompt + completion)
            return int(prompt or 0), int(completion or 0), int(total or 0)
        # Chat-style choices usage
        choices = getattr(response, "choices", None)
        if choices:
            try:
                first = choices[0]
                ch_usage = getattr(first, "usage", None)
                if ch_usage:
                    prompt = getattr(ch_usage, "prompt_tokens", 0) or 0
                    completion = getattr(ch_usage, "completion_tokens", 0) or 0
                    total = getattr(ch_usage, "total_tokens", 0) or (prompt + completion)
                    return int(prompt), int(completion), int(total)
            except Exception:
                pass
        # Dict fallback
        if isinstance(response, dict):
            u = response.get("usage") or {}
            prompt = u.get("input_tokens") or u.get("prompt_tokens") or 0
            completion = u.get("output_tokens") or u.get("completion_tokens") or 0
            total = u.get("total_tokens") or (prompt + completion)
            return int(prompt or 0), int(completion or 0), int(total or 0)
        return 0, 0, 0

    def get_usage_summary_lines(self) -> List[str]:
        if not self.usage_by_model:
            return ["暂无用量数据。"]
        lines: List[str] = []
        for model_name in sorted(self.usage_by_model.keys()):
            stats = self.usage_by_model.get(model_name, {})
            lines.append(
                f"{model_name}: prompt={int(stats.get('prompt', 0))}, completion={int(stats.get('completion', 0))}, total={int(stats.get('total', 0))}"
            )
        return lines

    def _persist_usage(self) -> None:
        if not self.usage_path:
            return
        try:
            payload = self.usage_by_model
            with open(self.usage_path, "w", encoding="utf-8") as f:
                json.dump(payload, f, ensure_ascii=False)
        except Exception:
            pass

    def summarize_today(self, date_str: str, tz: str, calendar_items: List[dict], task_items: List[dict]) -> str:
        """Generate a concise Chinese summary for today's agenda."""
        def format_event(e: dict) -> str:
            title = e.get("summary") or e.get("title") or ""
            start = e.get("start", {})
            end = e.get("end", {})
            def pick_time(x):
                return x.get("dateTime") or x.get("date") or ""
            return f"- {pick_time(start)} ~ {pick_time(end)} {title}".strip()

        def format_task(t: dict) -> str:
            title = t.get("title") or ""
            due = t.get("due") or ""
            return f"- {due} {title}".strip()

        calendar_block = "\n".join(format_event(e) for e in calendar_items) or "- (无)"
        tasks_block = "\n".join(format_task(t) for t in task_items) or "- (无)"
        user_content = TODAY_SUMMARY_PROMPT.format(
            date_str=date_str, tz=tz, calendar_block=calendar_block, tasks_block=tasks_block
        )
        # Prefer Responses API unless disabled for this model
        if (not self._responses_supported) or ("gemini" in (self.text_model or "").lower()):
            text = self._fallback_chat_completion(
                self.text_model, "You write concise daily plans.", [{"type": "input_text", "text": user_content}]
            )
            return (text or "").strip()
        try:
            response = self.client.responses.create(
                model=self.text_model,
                input=[
                    {"role": "system", "content": "You write concise daily plans."},
                    {"role": "user", "content": [{"type": "input_text", "text": user_content}]},
                ],
            )
            text = self._response_to_text(response)
            return (text or "").strip()
        except Exception:
            text = self._fallback_chat_completion(
                self.text_model, "You write concise daily plans.", [{"type": "input_text", "text": user_content}]
            )
            return (text or "").strip()

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

    def _normalize_category(self, category: str) -> str:
        """Normalize category to match allowed_event_categories if configured."""
        if not category or not self.allowed_event_categories:
            return category
        
        category_lower = category.strip().lower()
        original_category = category_lower
        
        # Exact match
        if category_lower in self.allowed_event_categories:
            return category_lower
        
        # Common mappings for similar categories
        category_mappings = {
            "health": "medical",
            "family": "personal",
            "study": "work",
            "education": "work",
            "finance": "work",
            "shopping": "personal",
            "trip": "travel",
            "call": "meeting",
        }
        
        # Try mapping
        mapped = category_mappings.get(category_lower)
        if mapped and mapped in self.allowed_event_categories:
            self.logger.info("Mapped category '%s' to '%s'", original_category, mapped)
            return mapped
        
        # Find closest match by substring or similarity
        for allowed in self.allowed_event_categories:
            if allowed in category_lower or category_lower in allowed:
                self.logger.info("Mapped category '%s' to '%s' (substring match)", original_category, allowed)
                return allowed
        
        # If no match found, use the first allowed category as fallback
        fallback = self.allowed_event_categories[0]
        self.logger.warning("Category '%s' not in allowed list, using fallback '%s'", original_category, fallback)
        return fallback

    def _dict_to_event(self, payload: Dict) -> CalendarEvent:
        title = payload.get("title") or "Untitled Event"
        timezone = payload.get("timezone") or self.default_timezone
        all_day = bool(payload.get("all_day"))
        category = (payload.get("category") or payload.get("classification") or payload.get("type") or "").strip()
        category = self._normalize_category(category)

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
