from __future__ import annotations

import logging
from typing import Dict, Optional, Tuple, List, Set
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

from .models import TaskItem, TaskSyncError
from dateutil import parser as date_parser


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

        # If presets are defined, restrict to presets only
        if self._preset_names and normalized not in self._preset_names:
            # Ensure presets exist in mapping (may create up to limit)
            try:
                self._ensure_preset_lists()
                mapping = self._list_cache_by_name or mapping
            except Exception:
                pass
            # Pick the closest preset name, not arbitrary existing lists
            preset_choice = self._pick_closest_name(normalized, self._preset_names)
            if preset_choice:
                # Return its id (ensure cache updated)
                if preset_choice in mapping:
                    return mapping[preset_choice]
                # Create it if not present and under cap
                if len(mapping) < self._max_lists:
                    try:
                        created = self.service.tasklists().insert(body={"title": preset_choice}).execute()
                        created_id = created.get("id")
                        if created_id:
                            self._list_cache_by_name[preset_choice] = created_id
                            return created_id
                    except HttpError:
                        pass
                # As last resort, fall back to configured default
                return self._get_fallback_list_id()
            # No reasonable preset match; fall back to default
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

    def _pick_closest_name(self, candidate: str, options: Set[str]) -> Optional[str]:
        """Pick the closest name from options using simple heuristics."""
        if not options:
            return None
        # Exact
        if candidate in options:
            return candidate
        # Prefix/suffix containment
        for opt in options:
            if candidate in opt or opt in candidate:
                return opt
        # Small Levenshtein-like heuristic (length diff and common prefix)
        best = None
        best_score = -1
        for opt in options:
            common_prefix = 0
            for a, b in zip(candidate, opt):
                if a == b:
                    common_prefix += 1
                else:
                    break
            score = common_prefix - abs(len(candidate) - len(opt))
            if score > best_score:
                best_score = score
                best = opt
        return best

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
        # Exact match already handled; try simple heuristics among existing lists
        for existing_name, list_id in mapping.items():
            if existing_name == normalized:
                return list_id
        # Prefix/suffix containment
        for existing_name, list_id in mapping.items():
            if normalized in existing_name or existing_name in normalized:
                return list_id
        return None

    def _build_task_link(self, created: dict) -> str:
        task_id = created.get("id", "")
        list_id = self._extract_list_id(created)
        # Public deep links are not officially supported; fall back to main Tasks site.
        return "https://tasks.google.com/"

    def list_tasks_for_date(self, local_date_str: str, timezone_name: str = "UTC") -> list:
        """List tasks due on the given local date using server-side filtering (dueMin/dueMax).
        This avoids client-side timezone edge cases. Aggregates preset lists + default."""
        tasks: list = []
        try:
            target_tz = ZoneInfo(timezone_name)
        except ZoneInfoNotFoundError:
            target_tz = ZoneInfo("UTC")
        try:
            # Compute [start,end) window in UTC for the given local date
            year, month, day = [int(x) for x in local_date_str.split("-")]
            start_local = datetime(year, month, day, 0, 0, 0, tzinfo=target_tz)
            end_local = start_local.replace(hour=0, minute=0, second=0, microsecond=0) + timedelta(days=1)
            due_min = start_local.astimezone(timezone.utc).isoformat()
            due_max = end_local.astimezone(timezone.utc).isoformat()

            mapping = self._refresh_list_cache()
            # Scan ALL lists to avoid missing tasks created into non-preset lists
            target_list_ids: List[str] = list(mapping.values())
            for list_id in target_list_ids:
                page_token = None
                while True:
                    resp = (
                        self.service.tasks()
                        .list(
                            tasklist=list_id,
                            showCompleted=False,
                            showDeleted=False,
                            maxResults=100,
                            dueMin=due_min,
                            dueMax=due_max,
                            pageToken=page_token,
                        )
                        .execute()
                    )
                    for item in resp.get("items", []) or []:
                        # Status double-check and annotate list id
                        if item.get("status") == "completed":
                            continue
                        item["_list_id"] = list_id
                        tasks.append(item)
                    page_token = resp.get("nextPageToken")
                    if not page_token:
                        break
        except HttpError as exc:
            self.logger.exception("Google Tasks API list error: %s", exc)
            # return what we have
        return tasks

    def _extract_list_id(self, created: dict) -> str:
        self_link = created.get("selfLink") or ""
        if "/lists/" in self_link and "/tasks/" in self_link:
            return self_link.split("/lists/")[1].split("/tasks/", 1)[0]
        parent = created.get("parent")
        if parent:
            return parent
        return self.task_list_id if self.task_list_id not in ("", "@default") else ""
