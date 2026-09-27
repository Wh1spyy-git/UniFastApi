from fastapi import FastAPI, Query, HTTPException
from datetime import datetime
import json
import time
import traceback
import nest_asyncio
from playwright.async_api import async_playwright

nest_asyncio.apply()

app = FastAPI(title="Sirius UniHelper API", version="4.0")

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


async def parse_sirius_schedule(group_name: str, next_week: bool = False) -> list:
    async with async_playwright() as p:
        browser = await p.chromium.launch(
            headless=True,
            args=["--no-sandbox", "--disable-setuid-sandbox"]
        )
        context = await browser.new_context(
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
        )
        page = await context.new_page()

        captured_events = []

        async def handle_response(response):
            nonlocal captured_events
            if "livewire" in response.url and response.status == 200:
                try:
                    res_json = await response.json()
                    server_memo = res_json.get("serverMemo", {})
                    data = server_memo.get("data", {})
                    events = data.get("events", [])

                    if isinstance(events, dict):
                        captured_events = []
                        for k, l in events.items():
                            if isinstance(l, list):
                                captured_events.extend(l)
                    elif isinstance(events, list) and events:
                        captured_events = events
                except Exception:
                    pass

        page.on("response", handle_response)

        try:
            await page.goto("https://schedule.siriusuniversity.ru/", wait_until="networkidle", timeout=30000)

            select_group_trigger = page.locator('text="Выберите группу"', has_text="Выберите группу").first
            if await select_group_trigger.is_visible():
                await select_group_trigger.click()
                await page.wait_for_timeout(500)

            search_input = page.locator("#searchListInput")
            await search_input.fill(group_name, force=True)
            await search_input.dispatch_event("input")
            await search_input.dispatch_event("change")
            await page.wait_for_timeout(1000)

            group_item = page.locator(f'text="{group_name}"').first
            try:
                if await group_item.is_visible(timeout=2000):
                    await group_item.click()
                else:
                    await search_input.press("Enter")
            except Exception:
                await search_input.press("Enter")

            await page.wait_for_timeout(2000)

            if next_week:
                add_week_btn = page.locator('[wire\\:click="addWeek"]').first
                if await add_week_btn.is_visible():
                    await add_week_btn.click()
                else:
                    await page.evaluate("""() => {
                        const el = document.querySelector('[wire\\\\:id]');
                        if (el && window.Livewire) {
                            const wireId = el.getAttribute('wire:id');
                            const comp = window.Livewire.find(wireId);
                            if (comp && typeof comp.addWeek === 'function') {
                                comp.addWeek();
                            } else if (comp && typeof comp.call === 'function') {
                                comp.call('addWeek');
                            }
                        }
                    }""")

                await page.wait_for_timeout(3000)

            if not captured_events:
                div_rasp = await page.query_selector("div[wire\\:initial-data], div[wire\\:id]")
                if div_rasp:
                    raw_data = await div_rasp.get_attribute("wire:initial-data")
                    if raw_data:
                        parsed = json.loads(raw_data)
                        events = parsed.get("serverMemo", {}).get("data", {}).get("events", [])
                        if isinstance(events, dict):
                            captured_events = []
                            for k, l in events.items():
                                if isinstance(l, list):
                                    captured_events.extend(l)
                        elif isinstance(events, list):
                            captured_events = events

        finally:
            await browser.close()

        return captured_events


@app.get("/api/schedule")
async def get_schedule(
    group: str = Query(..., description="Название группы"),
    next_week: bool = Query(False, description="Получить расписание на следующую неделю")
):
    cache_key = f"{group.strip().upper()}_{'next' if next_week else 'current'}"
    cached = get_from_cache(cache_key)
    if cached:
        return cached

    try:
        raw_lessons = await parse_sirius_schedule(group_name=group, next_week=next_week)
    except Exception as e:
        print("\n--- ОШИБКА ПАРСИНГА ---")
        traceback.print_exc()
        raise HTTPException(status_code=500, detail=f"Ошибка парсинга расписания: {repr(e)}")

    days_map = {}

    for item in raw_lessons:
        if not isinstance(item, dict):
            continue

        date_str = item.get("date", "")
        day_name = ""
        if date_str:
            try:
                dt = datetime.strptime(date_str, "%d.%m.%Y")
                day_name = DAYS_NAME.get(dt.weekday(), "")
            except ValueError:
                pass

        lesson_data = {
            "time_start": item.get("startTime", ""),
            "time_end": item.get("endTime", ""),
            "title": item.get("discipline", item.get("name", "")),
            "type": item.get("groupType", ""),
            "room": item.get("auditorium", ""),
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
        "next_week": next_week,
        "total_lessons": len(raw_lessons),
        "days": sorted_days
    }

    set_to_cache(cache_key, response)
    return response


@app.get("/api/clear-cache")
def clear_cache():
    global CACHE
    CACHE.clear()
    return {"status": "ok", "message": "Кеш очищен"}