from __future__ import annotations
import json
import os
from datetime import datetime, timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError
import sys
from pathlib import Path
from googleapiclient.discovery import build

# Ensure project root is on sys.path to import smart_assistant
PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from smart_assistant.config import load_config, get_config_value
from smart_assistant.calendar_client import GoogleCalendarClient
from smart_assistant.task_client import GoogleTaskClient


def main() -> None:
    # Load config
    config_path = os.getenv("ASSISTANT_CONFIG_PATH")
    CONFIG = load_config(config_path)
    if not CONFIG:
        raise RuntimeError("配置未找到，请检查 config.yaml 或设置 ASSISTANT_CONFIG_PATH")

    # Timezone and date
    default_tz = get_config_value(CONFIG, "assistant.default_tz", "ASSISTANT_DEFAULT_TZ", "UTC")
    now_utc = datetime.now(timezone.utc)
    try:
        tz = ZoneInfo(default_tz)
    except ZoneInfoNotFoundError:
        tz = ZoneInfo("UTC")
    
    local_today = now_utc.astimezone(tz).date()
    local_date_str = local_today.isoformat()

    # Google settings
    client_secrets_path = get_config_value(CONFIG, "google.client_secrets_path", "GOOGLE_CLIENT_SECRETS_PATH")
    token_path = get_config_value(CONFIG, "google.token_path", "GOOGLE_TOKEN_PATH", "google_token.json")
    calendar_id = get_config_value(CONFIG, "google.calendar_id", "GOOGLE_CALENDAR_ID", "primary")
    task_list_id = get_config_value(CONFIG, "google.task_list_id", "GOOGLE_TASK_LIST_ID", "@default")
    preset_lists = (CONFIG.get("google", {}) or {}).get("task_preset_lists") or []

    if not client_secrets_path:
        raise RuntimeError("缺少 google.client_secrets_path 配置。")

    # Build calendar client (to load credentials)
    cal = GoogleCalendarClient(
        calendar_id=calendar_id,
        client_secrets_path=client_secrets_path,
        token_path=token_path,
        allow_interactive=False,
    )

    print("=" * 80)
    print("SCANNING ALL TASK LISTS FOR TODAY'S TASKS")
    print("=" * 80)
    print(f"Local date: {local_date_str}")
    print(f"Timezone: {default_tz}")
    print()

    # Build service
    service = build('tasks', 'v1', credentials=cal.credentials)
    
    # Get all task lists
    tasklists = service.tasklists().list().execute()
    all_lists = tasklists.get('items', [])
    
    print(f"Found {len(all_lists)} task lists:")
    for tl in all_lists:
        print(f"  - {tl['title']:<30} (id: {tl['id']})")
    print()

    # Scan each list
    all_today_tasks = []
    all_no_due_tasks = []
    
    for task_list in all_lists:
        list_id = task_list['id']
        list_title = task_list['title']
        
        print("=" * 80)
        print(f"LIST: {list_title}")
        print("=" * 80)
        
        result = service.tasks().list(
            tasklist=list_id,
            showCompleted=False,  # 只显示未完成的
            showHidden=False
        ).execute()
        
        items = result.get('items', [])
        print(f"Total uncompleted tasks: {len(items)}")
        
        if items:
            for i, item in enumerate(items, start=1):
                title = item.get('title', '(no title)')
                due = item.get('due')
                status = item.get('status', 'unknown')
                
                print(f"  {i}. [{status}] {title}")
                
                if due:
                    # Parse date
                    task_date = due.split('T')[0]
                    print(f"     due: {task_date}", end="")
                    
                    if task_date == local_date_str:
                        print(" ← TODAY! ✓")
                        all_today_tasks.append({
                            'title': title,
                            'due': task_date,
                            'status': status,
                            'list': list_title,
                            'list_id': list_id,
                            'task_id': item.get('id')
                        })
                    else:
                        print()
                else:
                    print(f"     due: NO DUE DATE")
                    all_no_due_tasks.append({
                        'title': title,
                        'status': status,
                        'list': list_title,
                        'list_id': list_id,
                        'task_id': item.get('id')
                    })
        print()

    # Summary
    print("=" * 80)
    print("SUMMARY")
    print("=" * 80)
    print(f"\n📅 Tasks with due date = TODAY ({local_date_str}): {len(all_today_tasks)}")
    for i, task in enumerate(all_today_tasks, start=1):
        print(f"  {i}. [{task['list']}] {task['title']}")
    
    print(f"\n⏰ Tasks with NO due date: {len(all_no_due_tasks)}")
    for i, task in enumerate(all_no_due_tasks, start=1):
        print(f"  {i}. [{task['list']}] {task['title']}")
    
    # JSON output
    print("\n" + "=" * 80)
    print("JSON OUTPUT (tasks for today)")
    print("=" * 80)
    print(json.dumps(all_today_tasks, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()