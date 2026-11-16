"""وحدة flow — جزء من حزمة core (مقسومة من core.py)."""
from playwright.sync_api import Error as PWError
from playwright.sync_api import TimeoutError as PWTimeout
from playwright.sync_api import sync_playwright
import getpass
import os
import time

from .answers import (
    locate_answer_file,
    normalize_text,
    parse_answer_file,
)
from .config import (
    BLOCK_ASSETS,
    HEADLESS,
    LoginFailed,
    MAX_STEPS,
    PageNotReady,
    RETRIES,
    SESSION_RECOVERY_LIMIT,
    Stopped,
    TEST_PAGE_MARKERS,
    WAIT_MEDIUM,
    _cancel,
    _hook,
    _job,
    _report_consent,
    _timing,
)
from .curriculum import (
    _read_units,
    fetch_curriculum,
    next_pending_item,
)
from .navigation import (
    open_next_available_activity,
    return_to_curriculum,
)
from .page import (
    current_context,
    main_content,
)
from .questions import (
    answer_text_question_if_supported,
    click_next,
    mark_review_points,
    solve_one_visible_question,
)
from .quiz import (
    has_resumable_quiz_attempt,
    is_graded_test,
    is_quiz_failed_result,
    is_quiz_result_page,
    scroll_quiz_page,
    solve_quiz,
    visible_page_text,
)
from .runtime import (
    _diagnostic_report,
    _redacted_screenshot,
    _send_diagnostic,
    check_cancel,
    say,
    say_debug,
)
from .session import (
    _try_dismiss_active_dialog,
    _try_dismiss_navigation_overlay,
    announce_plan,
    dismiss_intro,
    is_login_page,
    login,
    open_curriculum,
)


def run_learning_flow(page, entries, relogin=None):
    completed = set()
    visited = set()
    handled_test_pages = set()
    current_activity_rechecks = set()
    dialog_rechecks = set()
    current_group = None
    last_state = None
    stagnant_rounds = 0
    pending_test_completion = None
    retried_quiz_pages = set()
    returned_from_result_pages = set()
    session_recoveries = 0

    def recover_expired_session():
        nonlocal session_recoveries, current_group, last_state
        nonlocal stagnant_rounds, pending_test_completion
        if relogin is None or session_recoveries >= SESSION_RECOVERY_LIMIT:
            return False
        check_cancel()
        say("انتهت جلسة المنصة؛ أعيد تسجيل الدخول وأستكمل من التقدم المحفوظ…", "phase")
        relogin()
        session_recoveries += 1
        completed.clear()
        visited.clear()
        handled_test_pages.clear()
        current_activity_rechecks.clear()
        dialog_rechecks.clear()
        retried_quiz_pages.clear()
        returned_from_result_pages.clear()
        pending_test_completion = None
        current_group = None
        last_state = None
        stagnant_rounds = 0
        _job.get().pop("api_current", None)
        say_debug(f"تم استرداد الجلسة تلقائيًا ({session_recoveries}/{SESSION_RECOVERY_LIMIT}).")
        return True

    for step in range(MAX_STEPS):
        check_cancel()
        if is_login_page(page):
            if recover_expired_session():
                continue
            message = "انتهت جلسة المنصة ولم أستطع استعادتها تلقائيًا."
            _job.get()["stuck"] = message
            say_debug(message)
            return

        test_hit = False
        result_hit = False
        try:
            ctx = current_context(page)
            full_text = main_content(page).inner_text(timeout=2000)
            page_text = visible_page_text(page, main_text=full_text)
            page_fingerprint = normalize_text(page_text)
            result_hit = is_quiz_result_page(page_text)
            test_hit = not result_hit and any(marker in full_text for marker in TEST_PAGE_MARKERS)
        except Exception:  # noqa: BLE001
            ctx = page.url
            page_fingerprint = ""

        state = (page.url, ctx, page_fingerprint)
        if state == last_state:
            stagnant_rounds += 1
            if stagnant_rounds >= 3:
                say_debug("الصفحة ثابتة لعدة جولات؛ توقفت لتجنب التكرار.")
                _job.get()["stuck"] = "الصفحة لم تتغير بعد عدة محاولات."
                return
        else:
            stagnant_rounds = 0
        last_state = state

        if pending_test_completion is not None:
            completed_key, completed_context = pending_test_completion
            job = _job.get()
            submission_result = job.get("quiz_submission_result")
            api_failed = (
                isinstance(submission_result, dict)
                and submission_result.get("passed") is False
            )
            if api_failed or (
                is_quiz_result_page(page_text) and is_quiz_failed_result(page_text)
            ):
                job["test_reached"] = completed_context
                job["test_failed"] = True
                say_debug(
                    f"المنصة أكدت عدم اجتياز الاختبار «{completed_context}»؛ "
                    "لن أسجله مكتملًا."
                )
                return

            if test_hit and (page.url, ctx) == completed_key:
                api_passed = (
                    isinstance(submission_result, dict)
                    and submission_result.get("passed") is True
                )
                if api_passed:
                    say_debug("رد المنصة أكد نجاح الاختبار؛ أحاول الرجوع للمنهج من الصفحة الحالية.")
                    if return_to_curriculum(page):
                        _job.get().get("opened_units", set()).clear()
                        last_state = None
                        stagnant_rounds = 0
                        continue
                    message = "أكدت المنصة نجاح الاختبار لكن تعذّر الرجوع إلى المنهج."
                    job["stuck"] = message
                    say_debug(message)
                    return
                if (
                    has_resumable_quiz_attempt(page_text)
                    and completed_key not in retried_quiz_pages
                ):
                    retried_quiz_pages.add(completed_key)
                    handled_test_pages.discard(completed_key)
                    pending_test_completion = None
                    last_state = None
                    stagnant_rounds = 0
                    say_debug(f"المحاولة ما زالت قيد التنفيذ؛ سأستأنف الاختبار «{ctx}» مرة واحدة.")
                    continue
                message = f"صفحة الاختبار «{ctx}» بقيت ظاهرة بعد محاولة معالجتها؛ أوقفت التكرار."
                _job.get()["stuck"] = message
                say_debug(message)
                return
            completed_source = (
                job.get("leaf")
                if is_graded_test(job.get("leaf") or "")
                else completed_context
            )
            completed_title = normalize_text(completed_source)
            job.setdefault("completed_tests", set()).add(completed_title)
            job["tests"] = [
                title
                for title in job.get("tests", [])
                if completed_title not in normalize_text(title)
            ]
            say(completed_context, "quiz_done")
            pending_test_completion = None

        if result_hit:
            result_key = (page.url, normalize_text(ctx))
            if result_key in returned_from_result_pages:
                message = "ظلت نتيجة الاختبار ظاهرة بعد محاولة الرجوع؛ أوقفت التكرار."
                _job.get()["stuck"] = message
                say_debug(message)
                return
            returned_from_result_pages.add(result_key)
            say_debug("ظهرت نتيجة الاختبار؛ أرجع للمنهج وأفحص النشاط التالي.")
            if not return_to_curriculum(page):
                _job.get()["stuck"] = "ظهرت نتيجة الاختبار لكن تعذّر الرجوع إلى قائمة المنهج."
                return
            _job.get().get("opened_units", set()).clear()
            last_state = None
            stagnant_rounds = 0
            continue

        # صفحة اختبار؟
        if test_hit:
            job = _job.get()
            test_key = (page.url, ctx)
            if test_key in handled_test_pages:
                message = f"صفحة الاختبار «{ctx}» بقيت ظاهرة بعد محاولة معالجتها؛ أوقفت التكرار."
                job["stuck"] = message
                say_debug(message)
                return
            handled_test_pages.add(test_key)
            say(ctx, "quiz_solving")
            solved = solve_quiz(page, entries)
            if not solved and is_login_page(page) and recover_expired_session():
                continue
            if solved:
                pending_test_completion = (test_key, ctx)
                last_state = None
                stagnant_rounds = 0
                continue
            else:
                if (
                    has_resumable_quiz_attempt(page_text)
                    and test_key not in retried_quiz_pages
                ):
                    retried_quiz_pages.add(test_key)
                    handled_test_pages.discard(test_key)
                    last_state = None
                    stagnant_rounds = 0
                    continue
                # لا نعيد بدء/معالجة نفس صفحة الاختبار تلقائيًا بعد فشل المحاولة.
                job["test_reached"] = ctx
                say(ctx, "quiz_skipped")
                return

        # الأولوية القصوى: حل أي سؤال ظاهر
        if solve_one_visible_question(page, entries, completed):
            last_state = None
            stagnant_rounds = 0
            continue

        if answer_text_question_if_supported(page, entries, completed):
            last_state = None
            stagnant_rounds = 0
            continue

        # نقاط المراجعة (علّم كمراجع)
        if mark_review_points(page):
            last_state = None
            stagnant_rounds = 0
            continue

        # أزرار التالي / إكمال
        if click_next(page):
            last_state = None
            stagnant_rounds = 0
            continue

        # فتح نشاط جديد من الشريط الجانبي
        opened, current_group = open_next_available_activity(
            page, visited, current_group
        )
        if opened:
            last_state = None
            stagnant_rounds = 0
            continue

        if dismiss_intro(page):
            last_state = None
            stagnant_rounds = 0
            continue

        if is_login_page(page):
            if recover_expired_session():
                continue
            message = "انتهت جلسة المنصة ولم أستطع استعادتها تلقائيًا."
            _job.get()["stuck"] = message
            say_debug(message)
            return

        # لو وصلنا هنا ومفيش تقدم
        if _job.get().get("api_current") and not _job.get().get("stuck"):
            current = _job.get()["api_current"]
            recheck_key = (page.url, normalize_text(current))
            if recheck_key not in current_activity_rechecks:
                current_activity_rechecks.add(recheck_key)
                if _try_dismiss_navigation_overlay(page):
                    say_debug("أرسلت Escape لمحاولة إغلاق لوحة المنهج قبل إعادة الفحص.")
                say_debug(f"النشاط «{current}» مفتوح بالفعل؛ أعيد فحصه بعد تمرير المحتوى.")
                scroll_quiz_page(page)
                page.wait_for_timeout(WAIT_MEDIUM)
                _job.get().pop("api_current", None)
                last_state = None
                stagnant_rounds = 0
                continue
            _job.get()["stuck"] = (
                f"النشاط «{current}» مفتوح، لكن لم يظهر سؤال أو زر متابعة مدعوم "
                "بعد إعادة فحص محتواه."
            )
            say_debug(_job.get()["stuck"])
            return

        dialog_key = (page.url, page_fingerprint)
        if dialog_key not in dialog_rechecks:
            dialog_rechecks.add(dialog_key)
            if _try_dismiss_active_dialog(page):
                say_debug("أغلقت نافذة ظاهرة قبل إعادة فحص النشاط.")
                last_state = None
                stagnant_rounds = 0
                continue

        pending = (_job.get().get("scan") or {}).get("pending", 0)
        if pending and not _job.get().get("stuck"):
            _job.get()["stuck"] = f"بقي {pending} نشاط مفتوح لم يكتمل على المنصة."
        say_debug("لا توجد خطوة آمنة مدعومة في الصفحة الحالية؛ توقفت دون تخمين.")
        say_debug("الصفحة الحالية:", page.url)
        say_debug("النشاط الحالي:", ctx)
        try:
            buttons = main_content(page).get_by_role("button")
            visible_btns = []
            for i in range(min(buttons.count(), 12)):
                b = buttons.nth(i)
                if b.is_visible():
                    visible_btns.append(" ".join(b.inner_text().split())[:70])
            say_debug("الأزرار الظاهرة:", visible_btns)
        except Exception:  # noqa: BLE001
            pass
        return

    raise RuntimeError(f"تجاوزت الحد الأقصى للخطوات ({MAX_STEPS}).")

def _attempt(username, password, entries, headless):
    """تشغيل واحد للمتصفح: دخول ثم حل. التقدم محفوظ على المنصة فإعادة التشغيل تكمل من حيث توقف."""
    page = None
    job = _job.get()
    job["diagnostic_screenshot"] = None
    job["diagnostic_page"] = None
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(
            headless=headless,
            args=[
                "--no-sandbox",
                "--disable-dev-shm-usage",
                "--disable-blink-features=AutomationControlled",
                "--disable-extensions",
                "--disable-gpu",
                "--disable-background-networking",
                "--disable-default-apps",
                "--no-first-run",
            ],
        )
        try:
            context = browser.new_context(
                user_agent=(
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
                ),
                locale="en-US",
                viewport={"width": 1366, "height": 768},
            )
            if BLOCK_ASSETS:
                context.route(
                    "**/*",
                    lambda route: route.abort()
                    if route.request.resource_type in ("image", "font", "media")
                    else route.continue_(),
                )
            page = context.new_page()
            login(page, username, password)
            job["diagnostic_page"] = page.url
            say("", "login_ok")
            check_cancel()
            open_curriculum(page)
            announce_plan(page)

            def relogin():
                login(page, username, password)
                job["diagnostic_page"] = page.url
                say("", "login_ok")
                open_curriculum(page)
                announce_plan(page)

            run_learning_flow(page, entries, relogin=relogin)
            job = _job.get()
            if not (job.get("test_reached") or job.get("tests") or job.get("stuck") or job.get("unmatched")):
                curriculum = fetch_curriculum(page)
                if curriculum:
                    pick = next_pending_item(curriculum)
                    left = [pick[3].get("title", "")] if pick and pick[0] == "item" else []
                else:
                    left = [
                        f"{u['name']} ({u['done']}/{u['total']})"
                        for u in _read_units(page)[0]
                        if not u["disabled"] and u["total"] and u["done"] < u["total"]
                    ]
                if left:
                    job["stuck"] = "لسه فيه وحدات ناقصة ومقدرتش أوصل لعناصرها: " + "، ".join(left)
            if (
                _report_consent.get()
                and (job.get("test_reached") or job.get("tests") or job.get("stuck") or job.get("unmatched"))
            ):
                job["diagnostic_page"] = page.url
                job["diagnostic_screenshot"] = _redacted_screenshot(page)
        except Exception:
            if _report_consent.get() and page is not None:
                job["diagnostic_page"] = page.url
                job["diagnostic_screenshot"] = _redacted_screenshot(page)
            raise
        finally:
            browser.close()

def run_job(username, password, entries, hook=None, cancel=None, headless=True, report_consent=False):
    """يشغّل مهمة كاملة لحساب واحد. يرجّع dict فيه status: finished/stopped/login_failed/error."""
    username = str(username or "").strip()
    _hook.set(hook)
    _cancel.set(cancel)
    _report_consent.set(report_consent)
    _job.set({"leaf": None, "worked": set(), "announced": set(), "scan": None,
              "unmatched": [], "stuck": None, "tests": [], "test_reached": None,
              "completed_tests": set()})
    _timing.set({})

    def clean(text):
        text = str(text)
        for secret in (password, username):
            if secret:
                text = text.replace(secret, "***")
        return text

    def report(status, error, last):
        _send_diagnostic(
            _diagnostic_report(
                status,
                clean(error),
                last,
                _job.get().get("diagnostic_screenshot"),
                _job.get().get("diagnostic_page"),
            )
        )

    try:
        for attempt in range(RETRIES + 1):
            job = _job.get()
            job["unmatched"].clear()
            job["stuck"] = None
            try:
                _attempt(username, password, entries, headless)
                break
            except (PWTimeout, PageNotReady, PWError) as exc:
                if attempt >= RETRIES:
                    raise
                delay = min(5, 2 ** attempt)
                say(
                    f"انقطاع مؤقت ({type(exc).__name__}) — "
                    f"محاولة الاستعادة {attempt + 1}/{RETRIES} بعد {delay}ث",
                    "phase",
                )
                check_cancel()
                cancel_event = _cancel.get()
                if cancel_event is not None and cancel_event.wait(delay):
                    raise Stopped("stopped")
                if cancel_event is None:
                    time.sleep(delay)
                check_cancel()
        job = _job.get()
        table = _timing.get() or {}
        if table:
            parts = [f"{n}: {c} مرة / {t:.1f}ث" for n, (c, t) in sorted(table.items(), key=lambda kv: -kv[1][1])]
            say_debug("⏱️ زمن الخطوات → " + " | ".join(parts))
        if job["unmatched"]:
            shown = "\n".join("• " + q for q in job["unmatched"][:5])
            error = f"توقف الحل عند أسئلة لا يوجد لها تطابق مؤكد في بنك الإجابات:\n{shown}"
            report("stuck", error, job.get("leaf"))
            return {
                "status": "stuck",
                "error": error,
                "last": job.get("leaf"),
            }
        if job["stuck"]:
            report("stuck", job["stuck"], job.get("leaf"))
            return {"status": "stuck", "error": job["stuck"], "last": job.get("leaf")}
        if job.get("test_reached") or job.get("tests"):
            test = job.get("test_reached") or (job["tests"][0] if job["tests"] else "")
            report("tests_pending", f"لم يكتمل الاختبار: {test}", job.get("leaf"))
            return {"status": "tests_pending", "last": job.get("leaf"),
                    "test": test,
                    "reason": "failed" if job.get("test_failed") else "unresolved"}
        return {"status": "finished", "last": job.get("leaf")}
    except Stopped:
        return {"status": "stopped", "last": _job.get().get("leaf")}
    except LoginFailed as exc:
        error = clean(exc)[:300]
        report("login_failed", error, _job.get().get("leaf"))
        return {"status": "login_failed", "error": error}
    except PageNotReady as exc:
        error = clean(exc)[:500]
        report("error", error, _job.get().get("leaf"))
        return {"status": "error", "error": error, "last": _job.get().get("leaf")}
    except PWTimeout as exc:
        error = "انتهت مهلة انتظار المنصة. " + clean(exc)[:200]
        report("error", error, _job.get().get("leaf"))
        return {"status": "error", "error": error, "last": _job.get().get("leaf")}
    except Exception as exc:  # noqa: BLE001
        error = clean(exc)[:300]
        report("error", error, _job.get().get("leaf"))
        return {"status": "error", "error": error, "last": _job.get().get("leaf")}

def main():
    """تشغيل محلي من سطر الأوامر (بدون بوت)."""
    answer_file = locate_answer_file()
    entries = parse_answer_file(answer_file)
    print(f"تم تحميل {len(entries)} سؤالًا من ملف الإجابات: {answer_file.name}")
    username = os.getenv("SITE_USER") or input("أدخل كود الدخول (ID): ").strip()
    password = os.getenv("SITE_PASS") or getpass.getpass("أدخل كلمة المرور: ")
    if not username or not password:
        raise ValueError("كود الدخول وكلمة المرور مطلوبان.")
    result = run_job(username, password, entries, headless=HEADLESS)
    print(result)
