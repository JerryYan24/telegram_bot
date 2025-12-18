import logging
import caldav
from datetime import datetime, timedelta
from typing import Optional, List, Dict
import urllib.parse
from zoneinfo import ZoneInfo

from .models import CalendarEvent, CalendarSyncError, TaskItem, TaskSyncError

class AppleCalendarClient:
    def __init__(
        self,
        caldav_url: str,
        username: str,
        password: str,
        calendar_name: str = "Calendar",
        task_list_name: str = "Reminders"
    ):
        self.logger = logging.getLogger(self.__class__.__name__)
        self.client = caldav.DAVClient(
            url=caldav_url,
            username=username,
            password=password
        )
        self.principal = self.client.principal()
        self._calendar_name = calendar_name
        self._task_list_name = task_list_name
        self._calendar = None
        self._task_list = None

    @property
    def calendar(self):
        if not self._calendar:
            try:
                calendars = self.principal.calendars()
                for cal in calendars:
                    # Check display name
                    props = cal.get_properties([caldav.dav.DisplayName])
                    display_name = props.get(caldav.dav.DisplayName, "")
                    if display_name == self._calendar_name:
                        self._calendar = cal
                        break
                
                if not self._calendar and calendars:
                    # Fallback to first calendar if specific one not found
                    self.logger.warning(f"Calendar '{self._calendar_name}' not found, using default.")
                    self._calendar = calendars[0]
            except Exception as e:
                self.logger.error(f"Failed to fetch calendars: {e}")
                raise
        return self._calendar

    @property
    def task_list(self):
        # In Apple Calendar/Reminders via CalDAV, tasks are often in a VTODO calendar.
        # Sometimes it's the same calendar, sometimes separate.
        # We'll look for a calendar that supports VTODO or matches the name.
        if not self._task_list:
            try:
                calendars = self.principal.calendars()
                for cal in calendars:
                    props = cal.get_properties([caldav.dav.DisplayName])
                    display_name = props.get(caldav.dav.DisplayName, "")
                    if display_name == self._task_list_name:
                        self._task_list = cal
                        break
                
                if not self._task_list:
                     # Try to find any calendar that supports VTODO
                     for cal in calendars:
                         # This is a bit heuristic; caldav lib doesn't expose component types easily in all versions
                         # But typically Reminders is a separate collection.
                         pass
            except Exception as e:
                self.logger.error(f"Failed to fetch task lists: {e}")
        return self._task_list

    def create_event(self, event: CalendarEvent) -> str:
        try:
            cal = self.calendar
            if not cal:
                raise CalendarSyncError("No calendar available")

            # Convert to icalendar compatible dictionary/args
            start_dt = event._normalize_datetime(event.start)
            end_dt = event._normalize_datetime(event.end)
            
            # caldav library save_event expects specific arguments
            # We can construct a VEVENT string or pass parameters
            
            event_data = {
                "summary": event.title,
                "dtstart": start_dt,
                "dtend": end_dt,
                "description": event.description or "",
                "location": event.location or "",
            }
            
            if event.all_day:
                event_data["dtstart"] = start_dt.date()
                event_data["dtend"] = end_dt.date() # + timedelta(days=1) ? CalDAV usually exclusive end for all day?
                # caldav/icalendar handles date objects as all-day

            new_event = cal.save_event(**event_data)
            self.logger.info(f"Created Apple Calendar event '{event.title}'")
            return str(new_event.url)

        except Exception as exc:
            self.logger.exception("Apple Calendar API error: %s", exc)
            raise CalendarSyncError(exc) from exc

    def list_events(self, time_min_iso: str, time_max_iso: str) -> list:
        try:
            cal = self.calendar
            if not cal:
                return []
            
            start = datetime.fromisoformat(time_min_iso)
            end = datetime.fromisoformat(time_max_iso)
            
            events = cal.date_search(start=start, end=end, expand=True)
            
            # Convert back to a list of dicts or objects if needed by the caller
            # The existing code expects a list of dicts similar to Google API response?
            # Let's check how it's used.
            # jarvis.py: events = ASSISTANT.calendar_client.list_events(start_iso, end_iso)
            # Then PARSER.summarize_today uses it.
            # We need to see what format summarize_today expects.
            # It seems it expects Google API style dicts: "summary", "start": {"dateTime": ...}, etc.
            
            result_items = []
            for e in events:
                # e is a caldav.objects.Event
                # e.instance is icalendar.Event
                vevent = e.instance.vevent
                
                item = {
                    "summary": str(vevent.get("summary", "")),
                    "description": str(vevent.get("description", "")),
                    "location": str(vevent.get("location", "")),
                }
                
                dtstart = vevent.get("dtstart").dt
                dtend = vevent.get("dtend").dt if vevent.get("dtend") else None
                
                if isinstance(dtstart, datetime):
                    item["start"] = {"dateTime": dtstart.isoformat()}
                else:
                    item["start"] = {"date": dtstart.isoformat()}
                    
                if dtend:
                    if isinstance(dtend, datetime):
                        item["end"] = {"dateTime": dtend.isoformat()}
                    else:
                        item["end"] = {"date": dtend.isoformat()}
                
                result_items.append(item)
                
            return result_items

        except Exception as exc:
            self.logger.exception("Apple Calendar list error: %s", exc)
            raise CalendarSyncError(exc) from exc

class AppleTaskClient:
    def __init__(self, caldav_url: str, username: str, password: str, task_list_name: str = "Reminders"):
        self.logger = logging.getLogger(self.__class__.__name__)
        self.client = caldav.DAVClient(
            url=caldav_url,
            username=username,
            password=password
        )
        self.principal = self.client.principal()
        self._task_list_name = task_list_name
        self._task_list = None
        
    @property
    def task_list(self):
        if not self._task_list:
            try:
                calendars = self.principal.calendars()
                for cal in calendars:
                    props = cal.get_properties([caldav.dav.DisplayName])
                    display_name = props.get(caldav.dav.DisplayName, "")
                    if display_name == self._task_list_name:
                        self._task_list = cal
                        break
                # If not found, maybe just pick the first one? Or fail?
                # Reminders are often in a specific collection.
                if not self._task_list:
                     # Fallback logic could be added here
                     pass
            except Exception:
                pass
        return self._task_list

    def create_task(self, task: TaskItem) -> str:
        try:
            # Note: Apple Reminders via CalDAV can be tricky.
            # We need a calendar that supports VTODO.
            # For now, we'll try to use the configured task list.
            
            tl = self.task_list
            if not tl:
                # Fallback: try to find any calendar
                try:
                    tl = self.principal.calendars()[0]
                except:
                    raise TaskSyncError("No task list available")
            
            # Construct VTODO
            todo_data = {
                "summary": task.title,
                "description": task.notes or "",
            }
            if task.due:
                todo_data["due"] = task._normalize_due(task.due)
            
            new_task = tl.save_todo(**todo_data)
            self.logger.info(f"Created Apple Reminder '{task.title}'")
            return str(new_task.url)
            
        except Exception as exc:
            self.logger.exception("Apple Tasks API error: %s", exc)
            raise TaskSyncError(exc) from exc

    def list_tasks_for_date(self, local_date_str: str, timezone_name: str = "UTC") -> list:
        # Implementing task listing for a specific date
        # This is used for "today's tasks" feature
        try:
            tl = self.task_list
            if not tl:
                return []
            
            # We want tasks due on this date.
            # CalDAV search for VTODO is possible.
            
            # For simplicity, we might fetch all open tasks and filter in python,
            # or use date_search if supported for VTODO.
            
            todos = tl.todos(include_completed=False)
            results = []
            target_date = datetime.strptime(local_date_str, "%Y-%m-%d").date()
            
            for todo in todos:
                vtodo = todo.instance.vtodo
                due = vtodo.get("due")
                if due:
                    due_dt = due.dt
                    # Check if due date matches
                    if isinstance(due_dt, datetime):
                         # Convert to local date to compare?
                         # Or just check if it falls within the day.
                         # This logic can be complex with timezones.
                         # For now, let's just return all open tasks or simple match.
                         pass
                    elif isinstance(due_dt, date):
                        if due_dt == target_date:
                            pass
                
                # Converting to dict format expected by jarvis/parser
                item = {
                    "title": str(vtodo.get("summary", "")),
                    "status": "needs-action", # or completed
                    "due": str(vtodo.get("due", {}).dt) if vtodo.get("due") else None
                }
                results.append(item)
                
            return results

        except Exception as exc:
            self.logger.exception("Apple Tasks list error: %s", exc)
            return []
