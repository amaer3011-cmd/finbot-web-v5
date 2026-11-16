"""وحدة navigation — جزء من حزمة core (مقسومة من core.py)."""
from playwright.sync_api import Error as PWError
from urllib.parse import urlsplit, urlunsplit
import re
import time

from .answers import (
    normalize_text,
)
from .config import (
    Stopped,
    TARGET_URL,
    WAIT_MEDIUM,
    WAIT_SHORT,
    _BUTTONS_JS,
    _LEAVES_JS,
    _job,
)
from .curriculum import (
    _read_units,
    _side_size,
    expand_next_unit,
    fetch_curriculum,
    next_pending_item,
)
from .page import (
    _activity_view_snapshot,
    _wait_until,
    main_content,
)
from .quiz import (
    is_graded_test,
    is_quiz_result_page,
    visible_page_text,
)
from .runtime import (
    _timed,
    check_cancel,
    say,
    say_debug,
)
from .session import (
    _open_finance_card,
    _open_subject_continue_button,
)


@_timed
def return_to_curriculum(page):
    """Return from a quiz result through its back control, with a curriculum URL fallback."""
    back_name = re.compile(r"^\s*رجوع\s*$", re.IGNORECASE)
    controls = (
        lambda: page.get_by_role("button", name=back_name),
        lambda: page.get_by_role("link", name=back_name),
        lambda: page.get_by_text(back_name),
        lambda: page.locator(
            "button, a, [role='button'], [role='link'], [onclick]"
        ),
    )

    expected_path = urlsplit(TARGET_URL).path.rstrip("/")

    def curriculum_is_ready(timeout=30000):
        deadline = time.monotonic() + timeout / 1000
        while True:
            check_cancel()
            try:
                current_path = urlsplit(page.url).path.rstrip("/")
                if current_path == expected_path:
                    text = visible_page_text(page)
                    units, _locked = _read_units(page)
                    if text.strip() and not is_quiz_result_page(text) and units:
                        return True
            except Stopped:
                raise
            except Stopped:  # الإيقاف لا يُبلَع داخل محاولات الاستعادة
                raise
            except Exception:  # noqa: BLE001
                pass
            if time.monotonic() >= deadline:
                return False
            page.wait_for_timeout(250)

    overview_path = urlsplit(TARGET_URL).path.rstrip("/").rsplit("/", 1)[0]

    def continue_from_subject_overview(timeout=25000):
        try:
            if urlsplit(page.url).path.rstrip("/") != overview_path:
                return False
        except Stopped:  # الإيقاف لا يُبلَع داخل محاولات الاستعادة
            raise
        except Exception:  # noqa: BLE001
            return False
        if not _open_subject_continue_button(page, timeout=15000):
            say_debug("وصلت لملخص الثقافة المالية لكن زر «كمل التعلم» لم يظهر.")
            return False
        return curriculum_is_ready(timeout=timeout)

    def curriculum_or_overview_ready(timeout=30000):
        try:
            if urlsplit(page.url).path.rstrip("/") == overview_path:
                return continue_from_subject_overview(timeout=timeout)
        except Stopped:
            raise
        except Stopped:  # الإيقاف لا يُبلَع داخل محاولات الاستعادة
            raise
        except Exception:  # noqa: BLE001
            return False
        return curriculum_is_ready(timeout=timeout)

    back_clicked = False
    for control_index, locate in enumerate(controls):
        check_cancel()
        try:
            candidates = locate()
            for index in range(candidates.count()):
                control = candidates.nth(index)
                if not control.is_visible():
                    continue
                if control_index == len(controls) - 1:
                    try:
                        labels = (
                            control.inner_text(timeout=500),
                            control.get_attribute("aria-label"),
                            control.get_attribute("title"),
                        )
                    except Stopped:  # الإيقاف لا يُبلَع داخل محاولات الاستعادة
                        raise
                    except Exception:  # noqa: BLE001
                        continue
                    if not any(
                        normalize_text(label or "") == normalize_text("رجوع")
                        for label in labels
                    ):
                        continue
                try:
                    control.scroll_into_view_if_needed(timeout=1500)
                except Stopped:  # الإيقاف لا يُبلَع داخل محاولات الاستعادة
                    raise
                except Exception:  # noqa: BLE001
                    pass
                control.click(force=True, no_wait_after=True)
                back_clicked = True
                if curriculum_or_overview_ready(timeout=12000):
                    return True
                break
        except Stopped:
            raise
        except Stopped:  # الإيقاف لا يُبلَع داخل محاولات الاستعادة
            raise
        except Exception as exc:  # noqa: BLE001
            say_debug(f"تعذر استخدام زر الرجوع الظاهر ({type(exc).__name__}).")
        if back_clicked:
            break

    if back_clicked:
        say_debug("زر الرجوع لم يُظهر قائمة الوحدات؛ أتابع بخطة الاستعادة.")

        check_cancel()
    try:
        page.go_back(wait_until="domcontentloaded", timeout=15000)
        if curriculum_or_overview_ready(timeout=10000):
            return True
    except Stopped:
        raise
    except Stopped:  # الإيقاف لا يُبلَع داخل محاولات الاستعادة
        raise
    except Exception as exc:  # noqa: BLE001
        say_debug(f"تعذر الرجوع بسجل المتصفح ({type(exc).__name__}).")

    say_debug("لم يفتح زر الرجوع قائمة المنهج؛ سأفتح رابط المنهج مباشرة.")
    check_cancel()
    try:
        page.goto(TARGET_URL, wait_until="domcontentloaded", timeout=45000)
    except Stopped:  # الإيقاف لا يُبلَع داخل محاولات الاستعادة
        raise
    except Exception as exc:  # noqa: BLE001
        say_debug(f"تعذر فتح رابط المنهج ({type(exc).__name__}).")
        return False

    if curriculum_or_overview_ready(timeout=35000):
        return True

    say_debug("رابط المنهج لم يحمّل محتوى القائمة؛ سأعيد تحميل الصفحة مرة واحدة.")
    check_cancel()
    try:
        page.reload(wait_until="domcontentloaded", timeout=45000)
    except Stopped:  # الإيقاف لا يُبلَع داخل محاولات الاستعادة
        raise
    except Exception as exc:  # noqa: BLE001
        say_debug(f"تعذرت إعادة تحميل المنهج ({type(exc).__name__}).")
        return False
    if curriculum_or_overview_ready(timeout=35000):
        return True

    say_debug("تعذر عرض المنهج بعد إعادة التحميل؛ سأرجع للرئيسية وأفتح بطاقة الثقافة المالية.")
    target = urlsplit(TARGET_URL)
    dashboard_url = urlunsplit(
        (target.scheme, target.netloc, "/dashboard", "", "")
    )
    try:
        page.goto(dashboard_url, wait_until="domcontentloaded", timeout=45000)
        if not _open_finance_card(page):
            say_debug("لم أجد بطاقة الثقافة المالية في الصفحة الرئيسية.")
            return False
        page.wait_for_url(
            re.compile(r".*/subjects/7(?:/curriculum)?/?$"),
            timeout=25000,
        )
    except Stopped:
        raise
    except Stopped:  # الإيقاف لا يُبلَع داخل محاولات الاستعادة
        raise
    except Exception as exc:  # noqa: BLE001
        say_debug(f"تعذر فتح المنهج من الصفحة الرئيسية ({type(exc).__name__}).")
        return False
    if curriculum_or_overview_ready(timeout=35000):
        return True

    say_debug("بقيت نتيجة الاختبار ظاهرة بعد فتح المنهج؛ لن أكرر فحص الصفحة.")
    return False

def _visible_matches(side, text):
    """Prefer exact visible sidebar labels before considering partial matches."""
    if not text:
        return []
    normalized_text = normalize_text(text)
    try:
        exact = side.get_by_text(text, exact=True)
    except Stopped:  # الإيقاف لا يُبلَع داخل محاولات الاستعادة
        raise
    except Exception:  # noqa: BLE001
        exact = None

    def visible_matches(locator):
        found = []
        if locator is None:
            return found
        try:
            for index in range(min(locator.count(), 12)):
                match = locator.nth(index)
                if match.is_visible():
                    found.append(match)
        except Stopped:  # الإيقاف لا يُبلَع داخل محاولات الاستعادة
            raise
        except Exception:  # noqa: BLE001
            return []
        return found

    exact_matches = visible_matches(exact)
    if exact_matches:
        return exact_matches

    try:
        partial = side.get_by_text(text[: min(len(text), 40)])
    except Stopped:  # الإيقاف لا يُبلَع داخل محاولات الاستعادة
        raise
    except Exception:  # noqa: BLE001
        return []
    matches = visible_matches(partial)
    ranked = []
    for match in matches:
        try:
            label = normalize_text(match.inner_text(timeout=750))
        except Stopped:  # الإيقاف لا يُبلَع داخل محاولات الاستعادة
            raise
        except Exception:  # noqa: BLE001
            continue
        if normalized_text in label:
            ranked.append((len(label), match))
    ranked.sort(key=lambda result: result[0])
    return [match for _length, match in ranked]

def _visible_activity_matches(side, title):
    """Find an activity using the same leaf rows as curriculum scanning."""
    normalized_title = normalize_text(title)
    if not normalized_title:
        return []
    leaves = side.locator("div.cursor-pointer")
    try:
        rows = leaves.evaluate_all(_LEAVES_JS)
    except PWError:
        return []
    ranked = []
    for row in rows:
        if not row["visible"]:
            continue
        label = normalize_text(row["text"])
        if not label:
            continue
        if label == normalized_title:
            rank = 0
        elif normalized_title in label or label in normalized_title:
            rank = 1
        else:
            continue
        ranked.append((rank, len(label), leaves.nth(row["i"])))
    ranked.sort(key=lambda match: (match[0], match[1]))
    return [match for _rank, _length, match in ranked]

def _activity_is_current(page, title):
    normalized_title = normalize_text(title)
    if not normalized_title:
        return False
    try:
        content = main_content(page)
        content_text = content.inner_text(timeout=1500)
        normalized_content = normalize_text(content_text)
        if not normalized_content or "اختر درسا من القائمة" in normalized_content:
            return False
        headings = content.locator("h1, h2")
        for index in range(min(headings.count(), 3)):
            heading = normalize_text(headings.nth(index).inner_text(timeout=750))
            if heading and (heading == normalized_title or normalized_title in heading):
                return True
        lines = content_text.splitlines()
    except PWError:
        return False
    for line in lines[:3]:
        normalized_line = normalize_text(line)
        if not normalized_line:
            continue
        if normalized_line == normalized_title or normalized_title in normalized_line:
            return True
    return False

def _wait_for_activity_view_change(page, before):
    def view_changed():
        current = _activity_view_snapshot(page)
        return current["url"] != before["url"] or current["text"] != before["text"]

    return _wait_until(page, view_changed, 2000)

def _expand_unit_named(page, side, unit_name):
    """Open a specific unlocked unit from the curriculum API pick."""
    normalized_name = normalize_text(unit_name)
    if not normalized_name:
        return False
    units, _locked = _read_units(page)
    matches = [
        unit for unit in units
        if normalize_text(unit["name"]) == normalized_name
    ]
    if not matches:
        matches = [
            unit for unit in units
            if normalized_name in normalize_text(unit["name"])
            or normalize_text(unit["name"]) in normalized_name
        ]
    if len(matches) != 1:
        return False

    unit = matches[0]
    if unit["disabled"] or (unit["total"] and unit["done"] >= unit["total"]):
        return False
    button = side.get_by_role("button").nth(unit["index"])
    try:
        expanded = button.get_attribute("aria-expanded")
        if expanded == "true":
            return True
        before = _side_size(side)
        button.click(force=True, no_wait_after=True)
        page.wait_for_timeout(WAIT_SHORT)
        after = _side_size(side)
        expanded = button.get_attribute("aria-expanded")
        if expanded == "true" or after > before:
            return True
        if after < before:
            button.click(force=True, no_wait_after=True)
            page.wait_for_timeout(WAIT_SHORT)
            return _side_size(side) >= before
        return expanded is None
    except Stopped:  # الإيقاف لا يُبلَع داخل محاولات الاستعادة
        raise
    except Exception as exc:  # noqa: BLE001
        say_debug(f"تعذر فتح الوحدة «{unit_name}» ({type(exc).__name__}).")
        return False

def _expand_collapsed_sidebar(page, side):
    """Open the curriculum sidebar when it is collapsed after returning from an activity."""
    toggle_name = re.compile(
        r"(توسيع الشريط الجانبي|فتح الشريط الجانبي|expand sidebar|open sidebar)",
        re.IGNORECASE,
    )
    for scope in (side, page):
        try:
            toggles = scope.get_by_role("button", name=toggle_name)
            for index in range(toggles.count()):
                toggle = toggles.nth(index)
                if not toggle.is_visible():
                    continue
                toggle.click(force=True, no_wait_after=True)
                page.wait_for_timeout(WAIT_SHORT)
                if _read_units(page)[0]:
                    return True
        except Stopped:  # الإيقاف لا يُبلَع داخل محاولات الاستعادة
            raise
        except Exception:  # noqa: BLE001
            continue
    return False

def open_next_via_api(page):
    """بديل مستقل عن شكل الـ HTML: يجيب العنصر الجاي من الـ API ويفتحه بالنص."""
    job = _job.get()
    curriculum = fetch_curriculum(page)
    if not curriculum:
        say_debug("API المنهج رجّع فاضي")
        return False
    pick = next_pending_item(
        curriculum, _job.get().get("completed_tests", set())
    )
    if not pick:
        return False
    _kind, unit_title, sec_name, item = pick
    title = item.get("title", "")
    key = str(item.get("id") or title)
    attempts = job.setdefault("api_attempts", {})
    if _activity_is_current(page, title):
        attempts.pop(key, None)
        job["api_current"] = title
        say_debug(f"النشاط «{title}» مفتوح بالفعل؛ لن أكرر النقر عليه.")
        return False

    failed_attempts = attempts.get(key, 0)
    if failed_attempts >= 3:
        job["stuck"] = f"العنصر «{title}» ({unit_title}) مكملش بعد 3 محاولات"
        return False

    side = page.get_by_role("complementary")
    targets = []
    unit_expanded = False
    section_expanded = False
    section_found = False
    normalized_title = normalize_text(title)
    normalized_section = normalize_text(sec_name)
    section_is_parent = bool(
        normalized_section
        and normalized_section != normalize_text(unit_title)
    )

    def exact_activity_matches(matches):
        exact = []
        for match in matches:
            try:
                if normalize_text(match.inner_text(timeout=750)) == normalized_title:
                    exact.append(match)
            except Stopped:  # الإيقاف لا يُبلَع داخل محاولات الاستعادة
                raise
            except Exception:  # noqa: BLE001
                continue
        return exact

    for _ in range(6):
        activity_matches = _visible_activity_matches(side, title)
        targets = exact_activity_matches(activity_matches)
        if targets:
            break
        if activity_matches:
            targets = activity_matches
            break
        if not _read_units(page)[0] and _expand_collapsed_sidebar(page, side):
            continue
        if not unit_expanded:
            unit_expanded = _expand_unit_named(page, side, unit_title)
            if unit_expanded:
                continue
        if not section_expanded and section_is_parent:
            matches = _visible_matches(side, sec_name)
            if matches:
                section_found = True
                section = matches[0]
                try:
                    expanded = section.get_attribute("aria-expanded")
                except Stopped:  # الإيقاف لا يُبلَع داخل محاولات الاستعادة
                    raise
                except Exception:  # noqa: BLE001
                    expanded = None
                if expanded != "true":
                    section.click(force=True, no_wait_after=True)
                    page.wait_for_timeout(WAIT_SHORT)
                section_expanded = True
                continue
            section_expanded = True
        if not section_found:
            targets = _visible_matches(side, title)
            if targets:
                break
        if section_is_parent:
            break
    if not targets:
        say_debug(f"مش لاقي «{title}» ظاهر في الشريط الجانبي (وحدة: {unit_title} / قسم: {sec_name})")
        refreshed = job.setdefault("api_refreshed", set())
        if key not in refreshed:
            refreshed.add(key)
            try:
                page.reload(wait_until="domcontentloaded", timeout=45000)
                page.wait_for_timeout(WAIT_MEDIUM)
            except PWError as exc:
                say_debug(f"تعذر تحديث صفحة المنهج للعنصر «{title}» ({type(exc).__name__}).")
                return False
            updated_curriculum = fetch_curriculum(page)
            if not updated_curriculum:
                say_debug(f"لم تتوفر بيانات المنهج بعد تحديث الصفحة للعنصر «{title}».")
                return False
            return open_next_via_api(page)
        attempts[key] = failed_attempts + 1
        job["stuck"] = f"النشاط «{title}» متاح في بيانات المنهج، لكن لم أستطع فتحه من الشريط الجانبي."
        return False

    index = 1 if (item.get("item_type") == "review" and len(targets) >= 2) else 0
    previous_leaf = job.get("leaf")
    before = _activity_view_snapshot(page)
    targets[index].click(force=True, no_wait_after=True)
    if not _wait_for_activity_view_change(page, before) and not _activity_is_current(page, title):
        say_debug(f"تم الضغط على «{title}» لكن محتوى النشاط لم يتغير؛ سأحدّث المنهج وأعيد المحاولة.")
        refreshed = job.setdefault("api_refreshed", set())
        if key not in refreshed:
            refreshed.add(key)
            try:
                page.reload(wait_until="domcontentloaded", timeout=45000)
                page.wait_for_timeout(WAIT_MEDIUM)
            except PWError as exc:
                say_debug(f"تعذر تحديث المنهج لإعادة فتح «{title}» ({type(exc).__name__}).")
                return False
            updated_curriculum = fetch_curriculum(page)
            if not updated_curriculum:
                say_debug(f"لم تتوفر بيانات المنهج بعد تحديث الصفحة لإعادة فتح «{title}».")
                return False
            return open_next_via_api(page)
        attempts[key] = failed_attempts + 1
        job["stuck"] = f"النشاط «{title}» متاح، لكن صفحة النشاط لم تفتح بعد إعادة المحاولة."
        return False
    attempts.pop(key, None)
    job.get("api_refreshed", set()).discard(key)
    job.pop("api_current", None)
    page.wait_for_timeout(WAIT_SHORT)
    if previous_leaf and not is_graded_test(previous_leaf):
        say(previous_leaf, "done")
    job["leaf"] = " ".join(title.split())[:100]
    job["worked"].add(unit_title)
    say(job["leaf"], "open")
    return True

def _fallback_open(page, side):
    if expand_next_unit(page, side):
        return True
    return open_next_via_api(page)

@_timed
def open_next_available_activity(page, visited, current_group, expanded_course=False):
    side = page.get_by_role("complementary")
    if not side.count():
        return open_next_via_api(page), current_group

    def open_unfinished_leaf(group_name):
        scan = {"done": 0, "locked": 0, "quiz": 0, "pending": 0}
        leaves = side.locator("div.cursor-pointer")
        try:
            infos = leaves.evaluate_all(_LEAVES_JS)
        except Stopped:  # الإيقاف لا يُبلَع داخل محاولات الاستعادة
            raise
        except Exception:  # noqa: BLE001
            infos = []

        for info in infos:
            if not info["visible"]:
                continue

            index = info["i"]
            status = info["title"]
            label = info["text"]
            normalized_label = normalize_text(label)

            completed_test_titles = _job.get().get("completed_tests", set())
            locally_completed_test = is_graded_test(label) and any(
                title and title in normalized_label
                for title in completed_test_titles
            )

            if status == "مكتمل" or "مكتمل" in label:
                scan["done"] += 1
            elif status == "مقفول" or "مقفول" in label or "🔒" in label:
                scan["locked"] += 1
            elif is_graded_test(label):
                scan["quiz"] += 1
                if not locally_completed_test:
                    _job.get()["tests"].append(" ".join(label.split())[:100])

            if locally_completed_test:
                continue

            if (
                not normalized_label
                or status in ("مكتمل", "مقفول")
                or "مكتمل" in label
                or "ممتاز" in label
                or "توسيع الشريط الجانبي" in label
                or "رجوع" in label
                or "مقفول" in label
            ):
                continue

            key = f"{normalize_text(group_name)}|{normalized_label}|{index}"
            if key in visited:
                scan["pending"] += 1  # فتحناه قبل كده وما اكتملش على المنصة
                continue

            job = _job.get()
            if job.get("leaf") and not is_graded_test(job["leaf"]):
                previous_leaf = job["leaf"]
            else:
                previous_leaf = None

            before = _activity_view_snapshot(page)
            leaves.nth(index).click(force=True, no_wait_after=True)
            if not _wait_for_activity_view_change(page, before):
                if _activity_is_current(page, label):
                    visited.add(key)
                    job = _job.get()
                    job["leaf"] = " ".join(label.split())[:100]
                    job["worked"].add(group_name)
                    job.pop("api_current", None)
                    say_debug(f"النشاط «{label}» كان مفتوحًا بالفعل؛ سأتابع التعامل مع محتواه.")
                    say(job["leaf"], "open")
                    return True
                say_debug(f"النقر على «{label}» لم يفتح النشاط؛ سأحاول تحديده من بيانات المنهج.")
                if open_next_via_api(page):
                    return True
                if not _job.get().get("stuck"):
                    _job.get()["stuck"] = (
                        f"ظهر النشاط «{label}» في الشريط الجانبي، لكن لم تتغير صفحة المحتوى بعد إعادة المحاولة."
                    )
                scan["pending"] += 1
                _job.get()["scan"] = scan
                continue
            page.wait_for_timeout(WAIT_SHORT)
            visited.add(key)
            if previous_leaf:
                say(previous_leaf, "done")
            job["leaf"] = " ".join(label.split())[:100]
            job["worked"].add(group_name)
            say(job["leaf"], "open")
            return True

        _job.get()["scan"] = scan
        return False

    # أولوية: أكمل المجموعة الحالية أولاً
    if current_group:
        if open_unfinished_leaf(current_group):
            return True, current_group
        job = _job.get()
        if current_group in job["worked"] and current_group not in job["announced"]:
            job["announced"].add(current_group)
            say(current_group, "group_done")

    buttons = side.get_by_role("button")
    button_data = []
    try:
        button_rows = buttons.evaluate_all(_BUTTONS_JS)
    except Stopped:  # الإيقاف لا يُبلَع داخل محاولات الاستعادة
        raise
    except Exception:  # noqa: BLE001
        button_rows = None

    if button_rows is not None:
        for row in button_rows:
            label = " ".join(row["label"].split())
            if row["disabled"] or not label or is_graded_test(label):
                continue
            if any(
                skip in label
                for skip in ("عنصر", "رجوع", "توسيع الشريط الجانبي", "طيّ الشريط الجانبي", "طي الشريط")
            ):
                continue
            button_data.append((label, buttons.nth(row["i"])))
    else:
        for index in range(buttons.count()):
            button = buttons.nth(index)
            try:
                if button.is_disabled():
                    continue
                label = " ".join(button.inner_text(timeout=1000).split())
            except Stopped:  # الإيقاف لا يُبلَع داخل محاولات الاستعادة
                raise
            except Exception:  # noqa: BLE001
                continue

            if not label or is_graded_test(label):
                continue
            if any(
                skip in label
                for skip in ("عنصر", "رجوع", "توسيع الشريط الجانبي", "طيّ الشريط الجانبي", "طي الشريط")
            ):
                continue
            button_data.append((label, button))

    if not button_data:
        # الشريط مطوي: افتح أول وحدة لسه ناقصة (مش بالضرورة الأولى)
        if _fallback_open(page, side):
            return True, current_group
        return False, current_group

    for label, button in button_data:
        if normalize_text(label) == normalize_text(current_group or ""):
            continue

        job = _job.get()
        count_match = re.search(r"(\d+)\s*/\s*(\d+)", label)
        if (
            (count_match and count_match.group(1) == count_match.group(2) and int(count_match.group(2)) > 0)
            or "100%" in label
        ):
            if label not in job["announced"] and label not in job["worked"]:
                job["announced"].add(label)
                say(label, "group_skipped")
            continue
        if "🔒" in label or "مقفل" in label:
            continue

        try:
            button.click(force=True, no_wait_after=True)
            page.wait_for_timeout(WAIT_SHORT)
        except Stopped:  # الإيقاف لا يُبلَع داخل محاولات الاستعادة
            raise
        except Exception:  # noqa: BLE001
            continue

        if open_unfinished_leaf(label):
            return True, label

        job = _job.get()
        scan = job.get("scan") or {}
        if label not in job["worked"] and label not in job["announced"]:
            if scan.get("done") and not scan.get("locked"):
                job["announced"].add(label)
                say(label, "group_skipped")
        current_group = label

    # مفيش شغل في الأقسام المفتوحة → افتح الوحدة الناقصة التالية (لو فيه)
    if _fallback_open(page, side):
        return True, current_group
    return False, current_group
