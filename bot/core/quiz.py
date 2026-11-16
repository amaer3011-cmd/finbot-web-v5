"""وحدة quiz — جزء من حزمة core (مقسومة من core.py)."""
from playwright.sync_api import Error as PWError
from playwright.sync_api import TimeoutError as PWTimeout
import re
import time

from .answers import (
    AnswerMatchError,
    normalize_text,
    resolve_answer,
)
from .config import (
    QUIZ_ANSWER_INSTRUCTION,
    QUIZ_CONFIRM_BUTTON,
    QUIZ_DIALOG_CONFIRM_BUTTON,
    QUIZ_NEXT_BUTTON,
    QUIZ_QUESTION_TIMEOUT,
    QUIZ_RESULT_MARKERS,
    QUIZ_RESUME_BUTTON,
    QUIZ_SCROLL_JS,
    QUIZ_START_BUTTON,
    QUIZ_SUBMIT_BUTTON,
    Stopped,
    TEST_MARKERS,
    TEST_PAGE_MARKERS,
    WAIT_AFTER_ANSWER,
    WAIT_MEDIUM,
    WAIT_SHORT,
    _RADIOS_JS,
    _TEXTS_JS,
    _job,
)
from .page import (
    _activity_view_snapshot,
    _remember_unmatched,
    _wait_until,
    has_new_incorrect_feedback,
    main_content,
    wait_for_answer_feedback,
)
from .runtime import (
    _timed,
    check_cancel,
    say,
    say_debug,
)


def is_graded_test(label):
    """هل النشاط اختبار بدرجة؟ (المراجعات العادية مش اختبارات)."""
    normalized_label = normalize_text(label)
    return any(normalize_text(marker) in normalized_label for marker in TEST_MARKERS)

def on_test_page(page):
    """هل الصفحة الحالية شاشة اختبار (بدء/تقديم/درجة نجاح)؟"""
    try:
        text = main_content(page).inner_text(timeout=1500)
    except Stopped:  # الإيقاف لا يُبلَع داخل محاولات الاستعادة
        raise
    except Exception:  # noqa: BLE001
        return False
    return any(marker in text for marker in TEST_PAGE_MARKERS)

def has_resumable_quiz_attempt(text):
    normalized = normalize_text(text)
    return (
        normalize_text("استكمال الاختبار") in normalized
        and normalize_text("جاري التنفيذ") in normalized
    )

def is_quiz_result_page(text):
    normalized = normalize_text(text)
    if has_resumable_quiz_attempt(text):
        return False

    has_result_summary = (
        "رجوع للاختبار" in normalized
        and any(normalize_text(marker) in normalized for marker in QUIZ_RESULT_MARKERS)
    )
    if has_result_summary:
        return True

    if normalize_text("الدرجة محسوبة") in normalized or (
        normalize_text("إجاباتك") in normalized
        and normalize_text("درجة النجاح") in normalized
        and any(
            normalize_text(marker) in normalized
            for marker in ("ناجح", "راسب", "لم تنجح")
        )
    ):
        return True

    # بعد التسليم تعرض المنصة سجل المحاولات وزر إعادة البدء، وقد لا تعرض
    # عبارة «رجوع للاختبار»؛ لا تخلط هذه الشاشة بصفحة اختبار جديدة.
    has_attempt_history = (
        normalize_text("المحاولة رقم") in normalized
        or normalize_text("محاولات سابقة") in normalized
    )
    has_score = re.search(r"[0-9٠-٩]{1,3}\s*%", str(text))
    return (
        normalize_text("ابدأ الاختبار") in normalized
        and has_attempt_history
        and has_score is not None
    )

def is_quiz_failed_result(text):
    normalized = normalize_text(text)
    return any(
        normalize_text(marker) in normalized
        for marker in ("راسب", "لم تنجح", "لم تجتز", "failed")
    )

def parse_quiz_submission_summary(payload):
    """Extract only the aggregate pass/fail flag from a test-submission response."""
    if not isinstance(payload, dict) or payload.get("success") is not True:
        return None
    data = payload.get("data")
    response = data.get("response") if isinstance(data, dict) else None
    if not isinstance(response, dict) or type(response.get("passed")) is not bool:
        return None
    correct_count = response.get("correct_count")
    total_count = response.get("total_count")
    if (
        type(correct_count) is not int
        or type(total_count) is not int
        or total_count <= 0
        or not 0 <= correct_count <= total_count
    ):
        return None
    return {"passed": response["passed"]}

def _capture_quiz_submission_response(response):
    """Capture aggregate grading only; ignore answer, identity, and session details."""
    job = _job.get()
    if job is None:
        return
    try:
        if response.status >= 400:
            return
        if "json" not in response.headers.get("content-type", "").casefold():
            return
        payload = response.json()
    except (PWError, ValueError):
        return
    summary = parse_quiz_submission_summary(payload)
    if summary is not None:
        job["quiz_submission_result"] = summary

def visible_page_text(page, main_text=None):
    """Combine the rendered main area and body so result overlays are not missed."""
    texts = [main_text] if main_text else []
    if not texts:
        try:
            main_text = main_content(page).inner_text(timeout=2000)
        except Stopped:  # الإيقاف لا يُبلَع داخل محاولات الاستعادة
            raise
        except Exception:  # noqa: BLE001
            main_text = ""
        if main_text:
            texts.append(main_text)
    try:
        body_text = page.locator("body").inner_text(timeout=2000)
    except Stopped:  # الإيقاف لا يُبلَع داخل محاولات الاستعادة
        raise
    except Exception:  # noqa: BLE001
        body_text = ""
    if body_text and body_text not in texts:
        texts.append(body_text)
    return "\n".join(texts)

def wait_for_quiz_question(page, content):
    """Wait for quiz options in short cancellable intervals."""
    deadline = time.monotonic() + QUIZ_QUESTION_TIMEOUT / 1000
    radios = content.get_by_role("radio")
    while True:
        check_cancel()
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            return False
        try:
            radios.first.wait_for(
                state="visible",
                timeout=min(250, max(1, int(remaining * 1000))),
            )
            return True
        except PWTimeout:
            check_cancel()
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return False
            page.wait_for_timeout(min(50, max(1, int(remaining * 1000))))

def start_or_resume_quiz(page, content):
    """Prefer resuming an existing attempt; only start a new attempt if offered."""
    for label, pattern in (
        ("استكمال", QUIZ_RESUME_BUTTON),
        ("بدء", QUIZ_START_BUTTON),
    ):
        buttons = content.get_by_role("button", name=pattern)
        for index in range(buttons.count()):
            button = buttons.nth(index)
            if not button.is_visible():
                continue
            say("🏁 بيستكمل الاختبار…" if label == "استكمال" else "🏁 بيبدأ الاختبار…", "log")
            button.click(force=True, no_wait_after=True)
            return True
        text_matches = content.get_by_text(pattern)
        for index in range(text_matches.count()):
            text = text_matches.nth(index)
            if not text.is_visible():
                continue
            say("🏁 بيستكمل الاختبار…" if label == "استكمال" else "🏁 بيبدأ الاختبار…", "log")
            text.click(force=True, no_wait_after=True)
            return True
    return False

def quiz_question_position(text):
    match = re.search(
        r"السؤال\s*([0-9٠-٩]+)\s*من\s*([0-9٠-٩]+)",
        text,
    )
    if not match:
        return None
    to_ascii = str.maketrans("٠١٢٣٤٥٦٧٨٩", "0123456789")
    return tuple(int(value.translate(to_ascii)) for value in match.groups())

def scroll_quiz_page(page):
    """Scroll the document and nested scrollable quiz containers toward their bottoms."""
    try:
        return bool(page.evaluate(QUIZ_SCROLL_JS))
    except Stopped:  # الإيقاف لا يُبلَع داخل محاولات الاستعادة
        raise
    except Exception:  # noqa: BLE001
        return False

def find_quiz_button(page, content, name, max_scrolls=40):
    """Find a visible quiz control, scrolling the page when long content hides its footer."""
    for attempt in range(max_scrolls + 1):
        check_cancel()
        for scope in (content, page):
            buttons = scope.get_by_role("button", name=name)
            for index in range(buttons.count()):
                button = buttons.nth(index)
                if button.is_visible():
                    button.scroll_into_view_if_needed(timeout=2000)
                    return button
        if attempt == max_scrolls or not scroll_quiz_page(page):
            break
        page.wait_for_timeout(WAIT_SHORT)
    return None

def extract_quiz_prompt(card):
    """Find the question text in nearby containers when options are nested separately."""
    current = card
    fallback = ""
    for _ in range(8):
        try:
            text = current.inner_text(timeout=1500)
        except PWTimeout:
            text = ""
        has_answer_instruction = QUIZ_ANSWER_INSTRUCTION.search(text) is not None
        prompt = QUIZ_ANSWER_INSTRUCTION.split(text, maxsplit=1)[0].strip()
        prompt = clean_quiz_prompt(prompt)
        if len(normalize_text(prompt)) > len(normalize_text(fallback)):
            fallback = prompt
        if len(normalize_text(prompt)) >= 20 and (
            has_answer_instruction or re.search(r"[؟?]\s*$", prompt)
        ):
            return prompt
        parent = current.locator("xpath=..")
        if not parent.count():
            break
        current = parent
    return fallback

def clean_quiz_prompt(prompt):
    """Remove quiz progress counters that may be included in the extracted question."""
    prompt = re.sub(
        r"^\s*(?:(?:السؤال\s*[0-9٠-٩]+(?:\s*من\s*[0-9٠-٩]+)?)|"
        r"[0-9٠-٩]+\s*/\s*[0-9٠-٩]+)\s*",
        "",
        prompt,
    )
    return prompt.strip()

def advance_quiz_question(page, content, question_text):
    """Advance after answering, or report whether advancement is unexpectedly blocked."""
    find_quiz_button(page, content, QUIZ_NEXT_BUTTON)
    next_buttons = content.get_by_role("button", name=QUIZ_NEXT_BUTTON)
    if not any(next_buttons.nth(i).is_visible() for i in range(next_buttons.count())):
        next_buttons = page.get_by_role("button", name=QUIZ_NEXT_BUTTON)
    enabled_buttons = []
    exact_text_buttons = []
    for index in range(next_buttons.count()):
        button = next_buttons.nth(index)
        if not button.is_visible() or not button.is_enabled():
            continue
        enabled_buttons.append(button)
        try:
            label = normalize_text(button.inner_text(timeout=500))
        except Stopped:  # الإيقاف لا يُبلَع داخل محاولات الاستعادة
            raise
        except Exception:  # noqa: BLE001
            label = ""
        if label in ("التالي", "next"):
            exact_text_buttons.append(button)

    if exact_text_buttons or enabled_buttons:
        # Prefer the labeled question-advance button over pagination arrow controls.
        button = exact_text_buttons[-1] if exact_text_buttons else enabled_buttons[-1]
        button.scroll_into_view_if_needed(timeout=2000)
        button.click(force=True, no_wait_after=True)
        previous_position = quiz_question_position(question_text)

        def question_changed():
            text = _activity_view_snapshot(page)["text"].strip()
            current_position = quiz_question_position(text)
            if previous_position and current_position:
                return current_position != previous_position
            return text != question_text.strip()

        if not _wait_until(page, question_changed, 1500, poll_interval=50):
            return "blocked"
        return "advanced"

    position = quiz_question_position(question_text)
    if position and position[0] >= position[1]:
        return "last"
    submit_button = find_quiz_button(page, content, QUIZ_SUBMIT_BUTTON)
    if submit_button is not None and submit_button.is_enabled():
        return "last"
    return "blocked"

@_timed
def submit_quiz(page, content):
    """Submit a quiz and observe the platform's aggregate result response."""
    job = _job.get()
    if job is not None:
        job["quiz_submission_result"] = None

    register = getattr(page, "on", None)
    remove = getattr(page, "remove_listener", None)
    listening = callable(register) and callable(remove)
    if listening:
        register("response", _capture_quiz_submission_response)
    try:
        return _submit_quiz_with_confirmation(page, content)
    finally:
        if listening:
            remove("response", _capture_quiz_submission_response)

def _submit_quiz_with_confirmation(page, content):
    """Submit answered quiz and confirm the platform's final-submit dialog."""
    submit_button = find_quiz_button(page, content, QUIZ_SUBMIT_BUTTON)
    if submit_button is None or not submit_button.is_enabled():
        return False

    say("📤 بيقدّم الاختبار…", "log")
    submit_button.click(force=True, no_wait_after=True)
    page.wait_for_timeout(WAIT_MEDIUM)

    confirm_clicked = False
    dialogs = page.get_by_role("dialog")
    for index in range(dialogs.count()):
        dialog = dialogs.nth(index)
        if not dialog.is_visible():
            continue
        confirm_button = dialog.get_by_role("button", name=QUIZ_DIALOG_CONFIRM_BUTTON)
        if confirm_button.count() and confirm_button.first.is_visible():
            confirm_button.first.click(force=True, no_wait_after=True)
            confirm_clicked = True
            break

    if not confirm_clicked:
        # Some platform builds render the confirmation as an overlay without dialog semantics.
        confirm_buttons = page.get_by_role("button", name=QUIZ_CONFIRM_BUTTON)
        for index in range(confirm_buttons.count()):
            button = confirm_buttons.nth(index)
            if button.is_visible():
                button.click(force=True, no_wait_after=True)
                confirm_clicked = True
                break

    if not confirm_clicked:
        # Other builds repeat the original label on the confirmation button.
        visible_buttons = []
        all_submit_buttons = page.get_by_role("button", name=QUIZ_SUBMIT_BUTTON)
        for index in range(all_submit_buttons.count()):
            button = all_submit_buttons.nth(index)
            if button.is_visible():
                visible_buttons.append(button)
        if len(visible_buttons) > 1:
            visible_buttons[-1].click(force=True, no_wait_after=True)
            confirm_clicked = True

    deadline = time.monotonic() + (15 if confirm_clicked else 3)
    while True:
        check_cancel()
        try:
            result_text = main_content(page).inner_text(timeout=2000)
        except Stopped:  # الإيقاف لا يُبلَع داخل محاولات الاستعادة
            raise
        except Exception:  # noqa: BLE001
            result_text = ""
        if is_quiz_result_page(result_text):
            return True
        if (_job.get() or {}).get("quiz_submission_result") is not None:
            say_debug("وصل تأكيد إرسال الاختبار من المنصة رغم عدم ظهور صفحة النتيجة.")
            return True
        if time.monotonic() >= deadline:
            break
        page.wait_for_timeout(WAIT_MEDIUM)

    if confirm_clicked:
        say_debug("تم الضغط على تأكيد التقديم لكن صفحة نتيجة الاختبار لم تظهر.")
    else:
        say_debug("ظهر زر التقديم الأول لكن لم أجد زر تأكيد ظاهرًا أو نتيجة للاختبار.")
    if (_job.get() or {}).get("quiz_submission_result") is not None:
        return True
    return False

@_timed
def solve_quiz(page, entries):
    """
    يحل اختبار المستوى / اختبار المعرفة تلقائيًا:
    1) يستكمل محاولة قائمة، أو يبدأ اختبارًا جديدًا لو لم توجد محاولة.
    2) يحل كل أسئلة الاختبار (radio) بنفس منطق solve_one_visible_question.
    3) يضغط «تقديم الاختبار» في النهاية.
    يرجّع True لو عولج الاختبار، False لو تعذّر حل كل الأسئلة بأمان.
    """
    content = main_content(page)

    # ─── الخطوة 1: استكمل المحاولة القائمة قبل عرض خيار البدء من جديد ───────
    start_or_resume_quiz(page, content)

    if not wait_for_quiz_question(page, content):
        say_debug("لم تظهر خيارات الاختبار بعد انتظار تحميلها؛ لم يتم العثور على سؤال قابل للحل.")
        return False

    # ─── الخطوة 2: حل الأسئلة ────────────────────────────────────────────────
    completed = set()
    answered_any = False
    missed_question = False
    MAX_QUIZ_STEPS = 200

    for _step in range(MAX_QUIZ_STEPS):
        check_cancel()
        try:
            full_text = content.inner_text(timeout=2000)
        except Stopped:  # الإيقاف لا يُبلَع داخل محاولات الاستعادة
            raise
        except Exception:  # noqa: BLE001
            break

        position = quiz_question_position(full_text)
        if position:
            say(f"{position[0]}|{position[1]}", "quiz_progress")

        # انتهى الاختبار؟ (ظهرت نتيجة أو اختفت أسئلة وأزرار التحقق)
        if "تقديم الاختبار" not in full_text and "ابدأ الاختبار" not in full_text:
            radios = content.get_by_role("radio")
            if not radios.count():
                break

        # حاول تحل سؤال ظاهر
        try:
            radios = content.get_by_role("radio")
            count = radios.count()
        except Stopped:  # الإيقاف لا يُبلَع داخل محاولات الاستعادة
            raise
        except Exception:  # noqa: BLE001
            break

        if not count:
            break

        try:
            radio_states = radios.evaluate_all(_RADIOS_JS)
        except Stopped:  # الإيقاف لا يُبلَع داخل محاولات الاستعادة
            raise
        except Exception:  # noqa: BLE001
            break

        solved_this_round = False
        for index in range(min(count, len(radio_states))):
            state = radio_states[index]
            if not state["visible"] or state["disabled"] == "true":
                continue
            radio = radios.nth(index)

            card = radio.locator(
                "xpath=ancestor::div[count(.//*[@role='radio'] | .//input[@type='radio']) >= 2][1]"
            )
            if not card.count():
                card = radio.locator(
                    "xpath=ancestor::div[.//button[contains(normalize-space(.), 'راجع إجابتي') "
                    "or contains(normalize-space(.), 'تحقق') "
                    "or contains(normalize-space(.), 'Answer')]"
                    "or .//button[contains(normalize-space(.), 'تقديم الاختبار')]][1]"
                )
            if not card.count():
                # في الاختبارات بعض الأسئلة مش مغلّفة ببطاقة مع زر تحقق فردي
                # نحاول نجيب البطاقة الأكبر اللي فيها السؤال
                card = radio.locator("xpath=ancestor::div[contains(@class,'question') or contains(@class,'card')][1]")
                if not card.count():
                    continue

            prompt = extract_quiz_prompt(card)

            key = "quiz|" + normalize_text(prompt)
            if key in completed:
                continue

            options = card.get_by_role("radio")
            try:
                option_texts = options.evaluate_all(_TEXTS_JS)
            except Stopped:  # الإيقاف لا يُبلَع داخل محاولات الاستعادة
                raise
            except Exception:  # noqa: BLE001
                continue

            try:
                selected_index, answer, source_question = resolve_answer(
                    prompt, entries, option_texts
                )
            except AnswerMatchError as exc:
                say(
                    f"تخطّي سؤال اختبار بلا تطابق: {prompt[:80]} ({str(exc)[:60]})",
                    "debug",
                )
                _remember_unmatched(prompt)
                missed_question = True
                continue

            try:
                option_states = options.evaluate_all(_RADIOS_JS)
            except Stopped:  # الإيقاف لا يُبلَع داخل محاولات الاستعادة
                raise
            except Exception:  # noqa: BLE001
                option_states = []
            already_selected = (
                selected_index < len(option_states)
                and bool(option_states[selected_index].get("checked"))
            )
            if not already_selected:
                try:
                    options.nth(selected_index).click(force=True, timeout=4000)
                except PWTimeout:
                    continue

            # زر تحقق فردي لو موجود (بعض الاختبارات بيتحقق سؤال سؤال)
            submitted_individually = False
            try:
                submit_one = card.get_by_role("button", name=re.compile(r"راجع إجابتي|تحقق|Answer", re.IGNORECASE))
                if (
                    submit_one.count()
                    and submit_one.first.is_visible()
                    and submit_one.first.is_enabled()
                ):
                    submit_one.first.click(force=True, no_wait_after=True)
                    wait_for_answer_feedback(page, full_text, WAIT_MEDIUM)
                    submitted_individually = True
            except Stopped:
                raise
            except PWError as exc:
                raise RuntimeError(
                    f"تعذّر تأكيد إجابة سؤال الاختبار: {source_question}"
                ) from exc
            if not submitted_individually and not already_selected:
                wait_for_answer_feedback(page, full_text, WAIT_AFTER_ANSWER)
            try:
                result_text = content.inner_text(timeout=1500)
            except PWTimeout:
                result_text = full_text
            if has_new_incorrect_feedback(full_text, result_text):
                raise RuntimeError(
                    f"المنصة لم تقبل الإجابة المطابقة لسؤال الاختبار: {source_question}"
                )

            completed.add(key)
            say(source_question[:100], "q_ok")
            answered_any = True
            advancement = advance_quiz_question(page, content, full_text)
            if advancement == "blocked":
                say_debug("تمت الإجابة لكن زر الانتقال غير متاح قبل آخر سؤال؛ لم أقدّم الاختبار جزئيًا.")
                _remember_unmatched("تعذّر الانتقال بين أسئلة الاختبار.")
                return False
            solved_this_round = advancement == "advanced"
            break  # ابدأ جولة جديدة بعد كل سؤال (الصفحة ممكن تتحدّث)

        if not solved_this_round:
            break

    if not answered_any or missed_question:
        return False

    # ─── الخطوة 3: قدّم الاختبار وأكّد نافذة التأكيد إن ظهرت ───────────────
    page.wait_for_timeout(WAIT_MEDIUM)
    if not submit_quiz(page, content):
        say_debug("زر تقديم الاختبار غير ظاهر بعد الإجابة؛ لن أعتبر الاختبار منتهيًا.")
        return False

    return True
