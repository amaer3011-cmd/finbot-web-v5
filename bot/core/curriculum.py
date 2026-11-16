"""وحدة curriculum — جزء من حزمة core (مقسومة من core.py)."""
import re

from .answers import (
    normalize_text,
)
from .config import (
    Stopped,
    WAIT_SHORT,
    _job,
)
from .quiz import (
    is_graded_test,
)
from .runtime import (
    _timed,
    say,
)


_UNIT_COUNT = re.compile(r"(\d+)\s*/\s*(\d+)\s*عنصر")

def _read_units(page):
    """وحدات الشريط الجانبي (الأزرار اللي عليها «N/M عنصر») + المقفولة."""
    units, locked = [], []
    try:
        side = page.get_by_role("complementary")
        if not side.count():
            return units, locked
        buttons = side.get_by_role("button")
        total = buttons.count()
    except Stopped:  # الإيقاف لا يُبلَع داخل محاولات الاستعادة
        raise
    except Exception:  # noqa: BLE001
        return units, locked
    skip_words = ("رجوع", "توسيع الشريط الجانبي", "طيّ الشريط الجانبي", "طي الشريط")
    for index in range(total):
        button = buttons.nth(index)
        try:
            label = " ".join(button.inner_text(timeout=1000).split())
            disabled = button.is_disabled()
        except Stopped:  # الإيقاف لا يُبلَع داخل محاولات الاستعادة
            raise
        except Exception:  # noqa: BLE001
            continue
        if not label or any(word in label for word in skip_words):
            continue
        name = re.sub(r"\s+", " ", re.sub(r"\d+\s*/\s*\d+\s*(?:عنصر)?|عنصر", "", label)).strip(" -–:")
        count = _UNIT_COUNT.search(label)
        if count:
            units.append({"index": index, "label": label, "name": name, "disabled": disabled,
                          "done": int(count.group(1)), "total": int(count.group(2))})
        elif disabled or "مقفل" in label or "🔒" in label:
            locked.append(name)
    return units, locked

def _side_size(side):
    try:
        return side.get_by_role("button").count() + side.locator("div.cursor-pointer").count()
    except Stopped:  # الإيقاف لا يُبلَع داخل محاولات الاستعادة
        raise
    except Exception:  # noqa: BLE001
        return 0

def expand_next_unit(page, side):
    """يفتح أول وحدة ناقصة ومطوية حاليًا. بيرجّع True لو فتح حاجة."""
    job = _job.get()
    opened_units = job.setdefault("opened_units", set())
    units, _locked = _read_units(page)
    for unit in units:
        if unit["disabled"] or (unit["total"] and unit["done"] >= unit["total"]):
            continue
        key = normalize_text(unit["name"])
        button = side.get_by_role("button").nth(unit["index"])
        try:
            expanded = button.get_attribute("aria-expanded")
            if expanded == "true":
                opened_units.add(key)
                continue  # مفتوحة بالفعل
            if key in opened_units and expanded is None:
                continue
            if expanded not in (None, "false"):
                continue  # مفتوحة أصلًا
            before = _side_size(side)
            button.click(force=True, no_wait_after=True)
            page.wait_for_timeout(WAIT_SHORT)
            if _side_size(side) < before:
                # كانت مفتوحة وقفلناها بالغلط → نفتحها تاني
                side.get_by_role("button").nth(unit["index"]).click(force=True, no_wait_after=True)
                page.wait_for_timeout(WAIT_SHORT)
        except Stopped:  # الإيقاف لا يُبلَع داخل محاولات الاستعادة
            raise
        except Exception:  # noqa: BLE001
            continue
        opened_units.add(key)
        say(unit["name"], "unit_open")
        return True
    return False

CURRICULUM_API = "/api/v1/subjects/7/curriculum"

@_timed
def fetch_curriculum(page):
    """بيانات المنهج من الـ API (نفس اللي الصفحة بتستخدمه) — أدق من قراءة الشريط الجانبي."""
    try:
        data = page.evaluate(
            "async (url) => { const r = await fetch(url, {credentials: 'include'}); return await r.json(); }",
            CURRICULUM_API,
        )
        if isinstance(data, dict) and data.get("success"):
            return data.get("data") or []
    except Stopped:  # الإيقاف لا يُبلَع داخل محاولات الاستعادة
        raise
    except Exception:  # noqa: BLE001
        pass
    return []

def _unit_items(unit):
    for sec in unit.get("sections") or []:
        for item in sec.get("items") or []:
            yield sec.get("name", ""), item
    for item in unit.get("items") or []:
        yield unit.get("title", ""), item

def _is_test_item(item):
    kind = normalize_text(str(item.get("item_type", "")))
    return is_graded_test(item.get("title", "")) or any(
        word in kind for word in ("test", "quiz", "exam", "assessment")
    )

def summarize_curriculum(curriculum):
    """[(اسم الوحدة، المخلّص، الإجمالي، مقفولة؟)] من بيانات الـ API."""
    out = []
    for unit in curriculum:
        items = [it for _sec, it in _unit_items(unit)]
        done = sum(1 for it in items if it.get("is_completed"))
        locked = bool(items) and not done and not any(it.get("is_unlocked") for it in items)
        out.append((unit.get("title", ""), done, len(items), locked))
    return out

def next_pending_item(curriculum, completed_tests=()):
    """أول عنصر مفتوح وغير مكتمل، مع استبعاد اختبارات أُنجزت في المهمة الحالية."""
    completed_test_titles = {
        normalize_text(title) for title in completed_tests if normalize_text(title)
    }
    for unit in curriculum:
        for sec_name, item in _unit_items(unit):
            if item.get("is_completed") or not item.get("is_unlocked"):
                continue
            title = normalize_text(item.get("title", ""))
            if _is_test_item(item) and title in completed_test_titles:
                continue
            return ("item", unit.get("title", ""), sec_name, item)
    return None
