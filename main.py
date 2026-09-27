from fastapi import FastAPI, Query, HTTPException
from datetime import datetime
import json
import re
import html as _html
import time
import requests
from typing import Dict, List, Any, Optional

app = FastAPI(title="Sirius UniHelper API", version="11.0")

BASE_URL = "https://schedule.siriusuniversity.ru"
PAGE_URL = f"{BASE_URL}/"
LIVEWIRE_ENDPOINT_TEMPLATE = f"{BASE_URL}/livewire/message/{{component}}"

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/125.0 Safari/537.36"
)

HEADERS_BASE = {
    "User-Agent": USER_AGENT,
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
}

CACHE = {}
CACHE_TTL = 3600

DAYS_NAME = {
    0: "ПОНЕДЕЛЬНИК",
    1: "ВТОРНИК",
    2: "СРЕДА",
    3: "ЧЕТВЕРГ",
    4: "ПЯТНИЦА",
    5: "СУББОТА",
    6: "ВОСКРЕСЕНЬЕ",
}


def get_from_cache(key: str):
    if key in CACHE:
        timestamp, data = CACHE[key]
        if time.time() - timestamp < CACHE_TTL:
            return data
        else:
            del CACHE[key]
    return None


def set_to_cache(key: str, data: dict):
    CACHE[key] = (time.time(), data)


class SiriusParser:
    def __init__(self):
        self.session = requests.Session()
        self.session.headers.update(HEADERS_BASE)
        
        self.token: str = ""
        self.fingerprint: Dict[str, Any] = {}
        self.server_memo: Dict[str, Any] = {}
        self.component_name: str = "main-grid"

    def init_session(self) -> bool:
        try:
            resp = self.session.get(PAGE_URL, timeout=15)
            resp.raise_for_status()
            
            html_content = resp.text
            
            token_match = re.search(r"livewire_token\s*=\s*['\"]([^'\"]+)['\"]", html_content)
            if not token_match:
                return False
            self.token = token_match.group(1)
            
            data_match = re.search(r'wire:initial-data="([^"]+)"', html_content)
            if not data_match:
                return False
                
            raw_json = _html.unescape(data_match.group(1))
            initial_data = json.loads(raw_json)
            
            self.fingerprint = initial_data["fingerprint"]
            self.server_memo = initial_data["serverMemo"]
            self.component_name = self.fingerprint.get("name", "main-grid")
            return True
        except Exception:
            return False

    def set_group(self, group_code: str) -> bool:
        updates = [
            {
                "type": "syncInput",
                "payload": {
                    "id": "group",
                    "name": "group",
                    "value": group_code
                }
            }
        ]
        return self._post_livewire(updates)

    def navigate_to_offset(self, offset: int) -> bool:
        """
        Перелистывает недели на offset шагов.
        Положительное число (напр. 2) -> перелистывает на 2 недели вперед.
        Отрицательное число (напр. -1) -> перелистывает на 1 неделю назад.
        """
        if offset == 0:
            return True

        method_name = "addWeek" if offset > 0 else "minusWeek"
        steps = abs(offset)

        for i in range(steps):
            updates = [
                {
                    "type": "callMethod",
                    "payload": {
                        "id": f"nav_{i}",
                        "method": method_name,
                        "params": []
                    }
                }
            ]
            if not self._post_livewire(updates):
                return False
            time.sleep(0.1)  # Небольшая задержка между шагами

        return True

    def parse_events(self) -> List[Dict[str, Any]]:
        data = self.server_memo.get("data", {})
        events_raw = data.get("events", {})
        parsed_events = []
        
        if isinstance(events_raw, dict):
            for day_key, day_events in events_raw.items():
                if isinstance(day_events, list):
                    for ev in day_events:
                        parsed_event = self._normalize_event(ev, day_key)
                        if parsed_event:
                            parsed_events.append(parsed_event)
        elif isinstance(events_raw, list):
             for ev in events_raw:
                 parsed_event = self._normalize_event(ev, "Unknown")
                 if parsed_event:
                     parsed_events.append(parsed_event)
                
        return parsed_events

    def _normalize_event(self, ev: Dict, day_label: str) -> Optional[Dict]:
        if not isinstance(ev, dict):
            return None

        subject = ev.get("subject") or ev.get("discipline") or ev.get("name") or "Без названия"
        start_time = ev.get("startTime") or ev.get("start_time") or ""
        end_time = ev.get("endTime") or ev.get("end_time") or ""
        room = ev.get("room") or ev.get("auditorium") or ev.get("classroom") or "-"
        teachers_list = self._extract_teachers(ev.get("teachers") or ev.get("teacher"))
        event_type = ev.get("eventType") or ev.get("groupType") or ""
        date_str = ev.get("date") or ""

        return {
            "date": date_str,
            "day_label": str(day_label),
            "time_start": start_time,
            "time_end": end_time,
            "title": subject,
            "room": room,
            "teacher": ", ".join(teachers_list) if teachers_list else "",
            "type": event_type
        }

    def _extract_teachers(self, teachers_data: Any) -> List[str]:
        if not teachers_data:
            return []
        names = []
        if isinstance(teachers_data, dict):
            for key, value in teachers_data.items():
                if isinstance(value, dict):
                    name = value.get("fio") or value.get("fullName") or value.get("name")
                    if name:
                        names.append(str(name))
                elif isinstance(value, str):
                    if len(value) > 2 and ' ' in value:
                         names.append(value)
        elif isinstance(teachers_data, list):
            for item in teachers_data:
                if isinstance(item, dict):
                    name = item.get("fio") or item.get("fullName") or item.get("name")
                    if name:
                        names.append(str(name))
                elif isinstance(item, str):
                    names.append(item)
        elif isinstance(teachers_data, str):
            if teachers_data.strip():
                names.append(teachers_data)
        return names

    def _post_livewire(self, updates: List[Dict]) -> bool:
        url = LIVEWIRE_ENDPOINT_TEMPLATE.format(component=self.component_name)
        payload = {
            "fingerprint": self.fingerprint,
            "serverMemo": self.server_memo,
            "updates": updates
        }
        headers = {
            "Content-Type": "application/json",
            "X-Livewire": "true",
            "Referer": PAGE_URL,
            "X-CSRF-TOKEN": self.token,
            "Accept": "text/html, application/xhtml+xml",
        }
        try:
            resp = self.session.post(url, json=payload, headers=headers, timeout=20)
            if resp.status_code != 200:
                return False
            response_json = resp.json()
            incoming_memo = response_json.get("serverMemo")
            if not incoming_memo:
                return False
            self.server_memo = self._merge_server_memo(self.server_memo, incoming_memo)
            return True
        except Exception:
            return False

    def _merge_server_memo(self, old: Dict, new: Dict) -> Dict:
        merged = old.copy()
        for key, value in new.items():
            if key == "data":
                continue
            merged[key] = value
            
        old_data = old.get("data", {})
        new_data = new.get("data", {})
        merged_data = old_data.copy()
        critical_keys = ["group", "date", "numWeek", "count", "events"]
        
        for key, value in new_data.items():
            if value is None and key in critical_keys and key in old_data and old_data[key] is not None:
                continue
            merged_data[key] = value
            
        merged["data"] = merged_data
        return merged


@app.get("/")
def read_root():
    return {"status": "online", "message": "Sirius Schedule API is running"}


@app.get("/api/schedule")
def get_schedule(
    group: str = Query(..., description="Название группы, например ИОП-ИТ-26/1"),
    week_offset: int = Query(0, description="Смещение недели: 0 (текущая), 1 (следующая), 2 (через неделю), -1 (предыдущая)")
):
    cache_key = f"{group.strip().upper()}_offset_{week_offset}"
    cached = get_from_cache(cache_key)
    if cached:
        return cached

    parser = SiriusParser()
    if not parser.init_session():
        raise HTTPException(status_code=502, detail="Не удалось инициализировать сессию с сайтом Сириуса")

    if not parser.set_group(group.strip()):
        raise HTTPException(status_code=500, detail=f"Не удалось выбрать группу {group}")

    if week_offset != 0:
        if not parser.navigate_to_offset(week_offset):
            raise HTTPException(status_code=500, detail=f"Не удалось переключить календарь на офсет {week_offset}")

    raw_lessons = parser.parse_events()

    days_map = {}
    for item in raw_lessons:
        date_str = item.get("date", "")
        day_name = ""
        
        if date_str:
            try:
                dt = datetime.strptime(date_str, "%d.%m.%Y")
                day_name = DAYS_NAME.get(dt.weekday(), "")
            except ValueError:
                pass

        lesson_data = {
            "time_start": item.get("time_start", ""),
            "time_end": item.get("time_end", ""),
            "title": item.get("title", ""),
            "type": item.get("type", ""),
            "room": item.get("room", ""),
            "teacher": item.get("teacher", ""),
        }

        if date_str not in days_map:
            days_map[date_str] = {
                "date": date_str,
                "day_name": day_name,
                "lessons": []
            }

        days_map[date_str]["lessons"].append(lesson_data)

    sorted_days = sorted(
        days_map.values(),
        key=lambda x: datetime.strptime(x["date"], "%d.%m.%Y") if x["date"] else datetime.min
    )

    response = {
        "success": True,
        "group": group,
        "week_offset": week_offset,
        "total_lessons": len(raw_lessons),
        "days": sorted_days
    }

    set_to_cache(cache_key, response)
    return response


@app.get("/api/clear-cache")
def clear_cache():
    global CACHE
    CACHE.clear()
    return {"status": "ok", "message": "Кэш очищен"}
