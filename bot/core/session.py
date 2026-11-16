"""وحدة session — جزء من حزمة core (مقسومة من core.py)."""
from playwright.sync_api import Error as PWError
from urllib.parse import urlsplit
import re
import time

from .answers import (
    normalize_text,
)
from .config import (
    LoginFailed,
    PageNotReady,
    SITE_URL,
    Stopped,
    TARGET_URL,
    WAIT_MEDIUM,
    WAIT_SHORT,
    _VISIBLE_JS,
    _job,
)
from .curriculum import (
    _is_test_item,
    _read_units,
    _unit_items,
    fetch_curriculum,
    summarize_curriculum,
)
from .page import (
    _wait_for_url,
    _wait_until,
)
from .runtime import (
    _timed,
    check_cancel,
    say,
    say_debug,
)


INTRO_BUTTON = re.compile(
    r"^(ابدأ|ابدأ الآن|يلا نبدأ|فهمت|تمام!?|حسنًا|حسنا|Got it|OK|Okay|Start|Let.?s go|Skip|تخطي|تخطّي|إغلاق|Close)$",
    re.IGNORECASE,
)

OVERLAY_CLOSE_BUTTON = re.compile(
    r"^(?:إغلاق|غلق|close|skip|تخطي|تخطّى|حسنًا|حسنا|تمام|ok|okay|got it|×|x)$",
    re.IGNORECASE,
)

def _try_dismiss_active_dialog(page):
    """Close a visible dialog using a known close action or Escape."""
    try:
        dialogs = page.get_by_role("dialog")
        for index in range(dialogs.count()):
            dialog = dialogs.nth(index)
            if not dialog.is_visible():
                continue
            buttons = dialog.get_by_role("button", name=OVERLAY_CLOSE_BUTTON)
            for button_index in range(buttons.count()):
                button = buttons.nth(button_index)
                if button.is_visible() and button.is_enabled():
                    button.click(force=True, no_wait_after=True)
                    page.wait_for_timeout(WAIT_SHORT)
                    return True
            page.keyboard.press("Escape")
            page.wait_for_timeout(WAIT_SHORT)
            return True
        return False
    except Stopped:
        raise
    except PWError as exc:
        say_debug(f"تعذر إغلاق النافذة الظاهرة ({type(exc).__name__}).")
        return False

def _try_dismiss_navigation_overlay(page):
    """Close a modal or navigation drawer before retrying an unresponsive activity."""
    if _try_dismiss_active_dialog(page):
        return True
    try:
        sidebar = page.get_by_role("complementary")
        if not sidebar.count() or not sidebar.is_visible():
            return False
        page.keyboard.press("Escape")
        page.wait_for_timeout(WAIT_SHORT)
        return True
    except Stopped:
        raise
    except PWError as exc:
        say_debug(f"تعذر إغلاق لوحة المنهج ({type(exc).__name__}).")
        return False

@_timed
def dismiss_intro(page):
    """يغلق نافذة ترحيب/تعليمات ظاهرة (شائعة في أول دخول لحساب جديد)."""
    try:
        dialogs = page.get_by_role("dialog")
        if not dialogs.count():
            return False
        button = dialogs.last.get_by_role("button", name=INTRO_BUTTON)
        if button.count() and button.first.is_visible():
            button.first.click(force=True, no_wait_after=True)
            page.wait_for_timeout(WAIT_MEDIUM)
            return True
    except Stopped:  # الإيقاف لا يُبلَع داخل محاولات الاستعادة
        raise
    except Exception:  # noqa: BLE001
        pass
    return False

def describe_page(page):
    """وصف مختصر للصفحة الحالية (للتشخيص لما صفحة الدخول متظهرش زي المتوقع)."""
    try:
        title = page.title()
    except Stopped:  # الإيقاف لا يُبلَع داخل محاولات الاستعادة
        raise
    except Exception:  # noqa: BLE001
        title = "?"
    try:
        inputs = [
            (el.get_attribute("type") or "text") + ":" + (el.get_attribute("placeholder") or "-")
            for el in page.locator("input").all()[:5]
        ]
    except Stopped:  # الإيقاف لا يُبلَع داخل محاولات الاستعادة
        raise
    except Exception:  # noqa: BLE001
        inputs = []
    try:
        body = " ".join(page.locator("body").inner_text(timeout=3000).split())[:160]
    except Stopped:  # الإيقاف لا يُبلَع داخل محاولات الاستعادة
        raise
    except Exception:  # noqa: BLE001
        body = "?"
    return f"url={page.url} | title={title} | inputs={inputs} | text={body}"

@_timed
def login(page, username, password):
    check_cancel()
    page.goto(SITE_URL, wait_until="domcontentloaded", timeout=45000)
    check_cancel()
    say_debug("تم فتح صفحة تسجيل الدخول.")

    password_field = page.locator("input[type='password']").first
    if not _wait_until(page, password_field.is_visible, 40000):
        raise PageNotReady("صفحة الدخول مظهرتش زي المتوقع. " + describe_page(page))

    check_cancel()
    user_field = page.locator(
        "input:not([type='password']):not([type='hidden']):not([type='checkbox'])"
        ":not([type='radio']):not([type='submit']):not([type='button'])"
    ).first
    user_field.fill(username, timeout=15000)
    check_cancel()
    say_debug("تم إدخال كود الدخول.")
    password_field.fill(password, timeout=15000)
    check_cancel()
    say_debug("تم إدخال كلمة المرور.")

    check_cancel()
    submit = page.get_by_role("button", name=re.compile(r"Let.?s Go|تسجيل الدخول|دخول|Log ?in|Sign ?in", re.IGNORECASE))
    if submit.count():
        submit.first.click(no_wait_after=True, timeout=10000)
    else:
        password_field.press("Enter")
    say_debug("تم إرسال طلب تسجيل الدخول.")
    dashboard_pattern = re.compile(r".*/dashboard$")
    site_host = urlsplit(SITE_URL).netloc.casefold()

    def login_finished():
        current_url = page.url
        return bool(dashboard_pattern.search(current_url)) or (
            urlsplit(current_url).netloc.casefold() == site_host
            and not is_login_page(page)
        )

    if not _wait_until(page, login_finished, 20000, poll_interval=50):
        _wait_until(page, login_finished, 1000, poll_interval=50)
    check_cancel()
    if is_login_page(page):
        message = normalize_text(page.locator("body").inner_text())[-300:]
        raise LoginFailed(
            "لم تؤكد المنصة تسجيل الدخول. "
            f"تحقق من البيانات أو حالة الحساب. رسالة الصفحة: {message}"
        )
    say_debug("تم تسجيل الدخول.")

def is_login_page(page):
    """Recognize the platform login route without mistaking another host's page."""
    current = urlsplit(page.url)
    if current.netloc.casefold() != urlsplit(SITE_URL).netloc.casefold():
        return False
    login_path = urlsplit(SITE_URL).path.rstrip("/").casefold()
    current_path = current.path.rstrip("/").casefold()
    return current_path == login_path or bool(
        re.search(r"/(?:login|log-in|signin|sign-in)(?:/|$)", current.path, re.IGNORECASE)
    )

def _finance_card_action_index(candidates):
    continue_labels = {"كمل", "استكمل", "استكمال", "continue", "continue learning"}
    start_labels = {"ابدأ", "ابدا", "start"}
    for candidate in candidates:
        if not candidate["visible"]:
            continue
        label = normalize_text(candidate["label"])
        if label not in continue_labels | start_labels:
            continue
        ancestor_texts = [normalize_text(text) for text in candidate["ancestors"]]
        finance_card = next(
            (
                text
                for text in ancestor_texts
                if "الثقافة المالية" in text
                or "financial literacy" in text
                or ("ثقافة" in text and "مالية" in text)
            ),
            "",
        )
        if not finance_card or "global finance quest" in finance_card:
            continue
        return candidate["i"]
    return None

def _open_finance_card(page):
    """ينتظر زر بطاقة الثقافة المالية نفسها ثم يضغطه دون لمس بطاقة أخرى."""
    actions = page.locator("button, a, [role='button'], [role='link']")
    candidate_script = (
        "(els) => els.map((el, i) => { "
        "const ancestors = []; let node = el; "
        "for (let depth = 0; node && depth < 8; depth++, node = node.parentElement) "
        "{ ancestors.push((node.innerText || '').trim()); } "
        "return { i, label: (el.innerText || el.getAttribute('aria-label') || '').trim(), "
        "ancestors, visible: (" + _VISIBLE_JS + ")(el) }; })"
    )
    deadline = time.monotonic() + 15
    while True:
        check_cancel()
        try:
            candidates = actions.evaluate_all(candidate_script)
        except PWError as exc:
            say_debug(f"تعذر فحص أزرار بطاقات الرئيسية ({type(exc).__name__}).")
            return False
        action_index = _finance_card_action_index(candidates)
        if action_index is not None:
            actions.nth(action_index).click(force=True, no_wait_after=True)
            return True
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            say_debug("لم يظهر زر «كمل» أو «ابدأ» داخل بطاقة الثقافة المالية.")
            return False
        page.wait_for_timeout(min(250, max(1, int(remaining * 1000))))

def _open_subject_continue_button(page, timeout=15000):
    """Open the course overview's continue-learning button or link."""
    actions = page.locator("button, a, [role='button'], [role='link']")
    continue_labels = {
        "كمل التعلم",
        "كمّل التعلم",
        "استكمل التعلم",
        "ابدأ التعلم",
        "continue learning",
        "resume learning",
    }
    deadline = time.monotonic() + timeout / 1000
    while True:
        check_cancel()
        try:
            candidates = actions.evaluate_all(
                "(els) => els.map((el, i) => ({ "
                "i, label: (el.innerText || el.getAttribute('aria-label') || '').trim(), "
                "visible: (" + _VISIBLE_JS + ")(el) }))"
            )
        except PWError as exc:
            say_debug(f"تعذر فحص زر متابعة الثقافة المالية ({type(exc).__name__}).")
            return False
        for candidate in candidates:
            if (
                candidate["visible"]
                and normalize_text(candidate["label"]) in continue_labels
            ):
                actions.nth(candidate["i"]).click(force=True, no_wait_after=True)
                return True
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            return False
        page.wait_for_timeout(min(250, max(1, int(remaining * 1000))))

@_timed
def open_curriculum(page):
    check_cancel()
    page.goto(TARGET_URL, wait_until="domcontentloaded", timeout=45000)
    check_cancel()

    # قد يظهر زر «ابدأ» للحساب الجديد أو «كمل» للحساب الذي لديه تقدم.
    if "/dashboard" in page.url:
        if not _open_finance_card(page):
            raise PageNotReady("لم أجد زر بطاقة «الثقافة المالية» في الصفحة الرئيسية.")
        subject_pattern = re.compile(r".*/subjects/7(?:/curriculum)?/?$")
        if not _wait_for_url(page, subject_pattern, 25000, poll_interval=50):
            raise PageNotReady(
                "تم الضغط على بطاقة «الثقافة المالية» لكن المنهج لم يفتح."
            )

    if "/curriculum" not in urlsplit(page.url).path:
        if not _open_subject_continue_button(page):
            raise PageNotReady("لم يظهر زر «كمل التعلم» في صفحة الثقافة المالية.")
        expected_path = urlsplit(TARGET_URL).path.rstrip("/")
        expected_pattern = re.compile(r".*" + re.escape(expected_path) + r"/?$")
        if not _wait_for_url(page, expected_pattern, 25000, poll_interval=50):
            raise PageNotReady("تم الضغط على «كمل التعلم» لكن قائمة الوحدات لم تفتح.")

    check_cancel()
    sidebar = page.get_by_role("complementary")
    if not _wait_until(page, sidebar.is_visible, 30000):
        raise PageNotReady("فتح المنهج لكن الشريط الجانبي لم يظهر.")
    check_cancel()
    first_unit = page.get_by_role(
        "button", name=re.compile(r"أساسيات المال|أساسيات الأعمال|التمويل الشخصي|ريادة")
    ).first
    if not _wait_until(page, first_unit.is_visible, 25000):
        raise PageNotReady("ظهر الشريط الجانبي لكن لم تظهر وحدات المنهج.")

@_timed
def announce_plan(page):
    """يبلّغ الطالب: المخلّص قبل كده، اللي هيتكمّل، والمقفول (من الـ API، وإلا من الشريط الجانبي)."""
    job = _job.get()
    curriculum = fetch_curriculum(page)
    if curriculum:
        rows = summarize_curriculum(curriculum)
        locked = []
        for name, done, total, is_locked in rows:
            if is_locked:
                locked.append(name)
            elif total and done >= total:
                say(f"{name}|{done}|{total}", "plan_done")
            else:
                say(f"{name}|{done}|{total}", "plan_todo")
        if locked:
            say("، ".join(locked), "plan_locked")
        tests = [
            it.get("title", "")
            for unit in curriculum
            for _sec, it in _unit_items(unit)
            if _is_test_item(it) and not it.get("is_completed")
        ]
        if tests:
            say(f"{len(tests)}|" + "، ".join(" ".join(t.split())[:50] for t in tests[:5]), "plan_tests")
        return
    units, locked = _read_units(page)
    for unit in units:
        if unit["total"] and unit["done"] >= unit["total"]:
            job["announced"].add(unit["label"])
            say(f"{unit['name']}|{unit['done']}|{unit['total']}", "plan_done")
        else:
            say(f"{unit['name']}|{unit['done']}|{unit['total']}", "plan_todo")
    if locked:
        say("، ".join(locked), "plan_locked")
