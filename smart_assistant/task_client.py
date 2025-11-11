from __future__ import annotations

import logging
from typing import Dict, Optional, Tuple, List, Set

from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

from .models import TaskItem, TaskSyncError


class GoogleTaskClient:
    def __init__(self, credentials: Credentials, task_list_id: str = "@default", preset_list_names: Optional[List[str]] = None, max_lists: int = 5):
        self.logger = logging.getLogger(self.__class__.__name__)
        self.service = build("tasks", "v1", credentials=credentials, cache_discovery=False)
        self.task_list_id = task_list_id or "@default"
        self._list_cache_by_name: Dict[str, str] = {}  # lowercase title -> id
        self._default_list_id: Optional[str] = None
        self._max_lists = max(1, int(max_lists))
        self._preset_names: Set[str] = set(n.strip().lower() for n in (preset_list_names or []) if str(n).strip())
        if self._preset_names:
            try:
                self._ensure_preset_lists()
            except Exception as exc:
                self.logger.warning("Failed to ensure preset task lists: %s", exc)

    def create_task(self, task: TaskItem) -> str:
        body = task.to_google_body()
        try:
            # Determine target list: prefer explicit list_name, then category, else configured/default
            target_name = (task.list_name or task.category or "").strip()
            if target_name:
                list_id = self._resolve_or_create_list(target_name)
            else:
                list_id = self._get_fallback_list_id()

            created = (
                self.service.tasks()
                .insert(tasklist=list_id, body=body)
                .execute()
            )
            self.logger.info("Created Google Task '%s'", task.title)
            return self._build_task_link(created)
        except HttpError as exc:
            self.logger.exception("Google Tasks API error: %s", exc)
            raise TaskSyncError(exc) from exc

    def _get_fallback_list_id(self) -> str:
        # Use configured list if not @default, otherwise discover default list id
        if self.task_list_id and self.task_list_id != "@default":
            return self.task_list_id
        if not self._default_list_id:
            self._default_list_id = self._discover_default_list_id()
        return self._default_list_id or "@default"

    def _discover_default_list_id(self) -> Optional[str]:
        # The API uses '@default' alias; we still attempt to find the first list as default id for linking consistency.
        try:
            resp = self.service.tasklists().list(maxResults=100).execute()
            items = resp.get("items", []) or []
            if not items:
                return "@default"
            # Heuristic: the first list is typically the default
            return items[0].get("id") or "@default"
        except Exception:
            return "@default"

    def _refresh_list_cache(self) -> Dict[str, str]:
        resp = self.service.tasklists().list(maxResults=100).execute()
        mapping: Dict[str, str] = {}
        for item in resp.get("items", []) or []:
            name = (item.get("title") or "").strip().lower()
            list_id = item.get("id") or ""
            if name and list_id:
                mapping[name] = list_id
        self._list_cache_by_name = mapping
        return mapping

    def _resolve_or_create_list(self, name: str) -> str:
        normalized = name.strip().lower()
        if not normalized:
            return self._get_fallback_list_id()

        # Lookup cache, then refresh if miss
        list_id = self._list_cache_by_name.get(normalized)
        if list_id:
            return list_id
        mapping = self._refresh_list_cache()
        if normalized in mapping:
            return mapping[normalized]

        # If presets are defined, only allow creating names in presets
        if self._preset_names and normalized not in self._preset_names:
            similar = self._pick_similar_list(normalized, mapping)
            if similar:
                return similar
            return self._get_fallback_list_id()

        # Enforce max list count: if already at cap, fallback to best existing
        total_lists = len(mapping)
        if total_lists >= self._max_lists:
            # Prefer an existing semantically similar bucket if available
            similar = self._pick_similar_list(normalized, mapping)
            if similar:
                return similar
            return self._get_fallback_list_id()

        # Create new list
        try:
            created = self.service.tasklists().insert(body={"title": name}).execute()
            created_id = created.get("id")
            if created_id:
                # Update cache
                self._list_cache_by_name[normalized] = created_id
                return created_id
        except HttpError as exc:
            self.logger.exception("Failed to create task list '%s': %s", name, exc)
            # fall through to fallback
        return self._get_fallback_list_id()

    def _ensure_preset_lists(self) -> None:
        if not self._preset_names:
            return
        mapping = self._refresh_list_cache()
        for preset in self._preset_names:
            if preset in mapping:
                continue
            # If we already reached limit, stop creating new ones
            if len(mapping) >= self._max_lists:
                break
            try:
                created = self.service.tasklists().insert(body={"title": preset}).execute()
                created_id = created.get("id")
                if created_id:
                    mapping[preset] = created_id
                    self._list_cache_by_name[preset] = created_id
            except HttpError as exc:
                self.logger.warning("Unable to create preset task list '%s': %s", preset, exc)

    def _pick_similar_list(self, normalized: str, mapping: Dict[str, str]) -> Optional[str]:
        # Exact match already handled; try simple heuristics
        for existing_name, list_id in mapping.items():
            if existing_name == normalized:
                return list_id
        # Prefix/suffix containment
        for existing_name, list_id in mapping.items():
            if normalized in existing_name or existing_name in normalized:
                return list_id
        # Common buckets
        for bucket in ("work", "personal", "meeting", "travel", "study", "finance", "family", "health", "other"):
            if bucket in mapping and (normalized.startswith(bucket) or bucket in normalized):
                return mapping[bucket]
        return None

    def _build_task_link(self, created: dict) -> str:
        task_id = created.get("id", "")
        list_id = self._extract_list_id(created)
        # Public deep links are not officially supported; fall back to main Tasks site.
        return "https://tasks.google.com/"

    def _extract_list_id(self, created: dict) -> str:
        self_link = created.get("selfLink") or ""
        if "/lists/" in self_link and "/tasks/" in self_link:
            return self_link.split("/lists/")[1].split("/tasks/", 1)[0]
        parent = created.get("parent")
        if parent:
            return parent
        return self.task_list_id if self.task_list_id not in ("", "@default") else ""
