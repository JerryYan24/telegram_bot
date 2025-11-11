from __future__ import annotations

import logging
from typing import Optional

from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

from .models import TaskItem, TaskSyncError


class GoogleTaskClient:
    def __init__(self, credentials: Credentials, task_list_id: str = "@default"):
        self.logger = logging.getLogger(self.__class__.__name__)
        self.service = build("tasks", "v1", credentials=credentials, cache_discovery=False)
        self.task_list_id = task_list_id or "@default"

    def create_task(self, task: TaskItem) -> str:
        body = task.to_google_body()
        try:
            created = (
                self.service.tasks()
                .insert(tasklist=self.task_list_id, body=body)
                .execute()
            )
            self.logger.info("Created Google Task '%s'", task.title)
            return self._build_task_link(created)
        except HttpError as exc:
            self.logger.exception("Google Tasks API error: %s", exc)
            raise TaskSyncError(exc) from exc

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
