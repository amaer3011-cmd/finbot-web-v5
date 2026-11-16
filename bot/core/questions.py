"""وحدة questions — جزء من حزمة core (مقسومة من core.py)."""
from playwright.sync_api import Error as PWError
from playwright.sync_api import TimeoutError as PWTimeout
import re
import unicodedata

from .answers import (
    AnswerMatchError,
    extract_question_prompt,
    match_answer,
    normalize_text,
    resolve_answer,
)
from .config import (
    Stopped,
    CHECK_BUTTON,
    NEXT_BUTTON,
    REVIEW_BUTTON,
    REVIEW_CARD_BUTTON,
    WAIT_AFTER_ANSWER,
    WAIT_MEDIUM,
    WAIT_SHORT,
    _RADIOS_JS,
    _REVIEW_CARDS_JS,
    _TEXTS_JS,
    _VISIBLE_JS,
)
from .page import (
    _remember_unmatched,
    _wait_until,
    current_context,
    has_new_incorrect_feedback,
    main_content,
    wait_for_answer_feedback,
)
from .quiz import (
    scroll_quiz_page,
)
from .runtime import (
    _timed,
    say,
    say_debug,
)


@_timed
def solve_one_visible_question(page, entries, completed):
    content = main_content(page)
    radios = content.get_by_role("radio")
    count = radios.count()
    if not count:
        radios = page.get_by_role("radio")
        count = radios.count()
        if not count:
            return False

    try:
        radio_states = radios.evaluate_all(_RADIOS_JS)
    except Stopped:  # الإيقاف لا يُبلَع داخل محاولات الاستعادة
        raise
    except Exception:  # noqa: BLE001
        return False

    for index in range(min(count, len(radio_states))):
        state = radio_states[index]
        if not state["visible"] or state["disabled"] == "true":
            continue
        radio = radios.nth(index)

        # البحث عن البطاقة التي تحتوي على زر التحقق (هيكل المنصة الحالي)
        card = radio.locator(
            "xpath=ancestor::div[.//button[contains(normalize-space(.), 'راجع إجابتي') "
            "or contains(normalize-space(.), 'تحقق') "
            "or contains(normalize-space(.), 'أجب لعرض البقية')]][1]"
        )
        try:
            if not card.count():
                card = radio.locator(
                    "xpath=ancestor::div[count(.//*[@role='radio'] | "
                    ".//input[@type='radio']) >= 2][1]"
                )
            if not card.count():
                continue
            card_text = card.inner_text(timeout=2500)
        except PWTimeout:
            continue

        options = card.get_by_role("radio")
        try:
            option_texts = options.evaluate_all(_TEXTS_JS)
        except Stopped:  # الإيقاف لا يُبلَع داخل محاولات الاستعادة
            raise
        except Exception:  # noqa: BLE001
            option_texts = []

        prompt = extract_question_prompt(card_text, option_texts)

        # إزالة عناوين الدرس أو أرقام الأسئلة الزائدة إن وجدت
        prompt = re.sub(r"^(السؤال\s+(?:الأول|الاول|الثاني|الثالث|الرابع|الخامس|[0-9٠-٩]+)\s*)", "", prompt).strip()

        key = current_context(page) + "|" + normalize_text(prompt)
        if key in completed:
            continue

        try:
            selected_index, answer, source_question = resolve_answer(
                prompt, entries, option_texts
            )
        except AnswerMatchError as exc:
            say(
                f"تخطّي سؤال بلا تطابق مؤكد: {prompt[:80]} ({str(exc)[:60]})",
                "debug",
            )
            _remember_unmatched(prompt)
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

        submit = card.get_by_role("button", name=CHECK_BUTTON)
        if not submit.count():
            # محاولة بديلة بالبحث عن الزر بالنص
            submit = card.locator("button").filter(has_text=re.compile(r"راجع|تحقق|Answer", re.I))
            if not submit.count():
                raise RuntimeError("وجدت سؤال اختيار من متعدد دون زر تحقق واضح.")

        try:
            previous_text = content.inner_text(timeout=1500)
        except PWTimeout:
            previous_text = None
        submit.first.click(force=True, no_wait_after=True)

        if previous_text is None:
            page.wait_for_timeout(WAIT_AFTER_ANSWER)
        else:
            wait_for_answer_feedback(page, previous_text)
        try:
            # تحقق سريع من وجود رسالة خطأ
            result_text = content.inner_text(timeout=1500)
            if has_new_incorrect_feedback(previous_text, result_text):
                raise RuntimeError(
                    f"المنصة لم تقبل الإجابة المطابقة للسؤال: {source_question}"
                )
        except PWTimeout:
            pass

        completed.add(key)
        say(source_question[:100], "q_ok")
        return True
    return False

@_timed
def answer_text_question_if_supported(page, entries, completed):
    content = main_content(page)
    fields = content.locator("textarea, input[type='text'], input[type='number']")
    count = fields.count()
    if not count:
        return False

    for index in range(count):
        field = fields.nth(index)
        try:
            if not field.is_visible():
                continue
        except PWTimeout:
            continue

        card = field.locator(
            "xpath=ancestor::div[.//button[contains(., 'Answer') or "
            "contains(., 'إجابة') or contains(., 'تحقق') or contains(., 'راجع')]][1]"
        )
        if not card.count():
            continue

        try:
            prompt = card.inner_text(timeout=2000).strip()
        except PWTimeout:
            continue

        key = current_context(page) + "|" + normalize_text(prompt)
        if key in completed:
            continue

        try:
            answer, source_question = match_answer(prompt, entries)
        except AnswerMatchError:
            continue

        field.fill(answer)
        submit = card.get_by_role("button", name=CHECK_BUTTON)
        if not submit.count():
            submit = card.locator("button").filter(
                has_text=re.compile(r"راجع|تحقق|Answer|إجابة", re.I)
            )
            if not submit.count():
                continue

        try:
            previous_text = content.inner_text(timeout=1500)
        except PWTimeout:
            previous_text = None
        submit.first.click(force=True, no_wait_after=True)
        if previous_text is None:
            page.wait_for_timeout(WAIT_AFTER_ANSWER)
        else:
            wait_for_answer_feedback(page, previous_text)
        completed.add(key)
        say(source_question[:100], "q_ok")
        return True
    return False

@_timed
def mark_review_points(page):
    content = main_content(page)
    buttons = content.get_by_role("button", name=REVIEW_BUTTON)
    if buttons.count():
        button = buttons.first
        button.scroll_into_view_if_needed()
        before_count = buttons.count()
        button.click(force=True, no_wait_after=True)
        page.wait_for_timeout(WAIT_SHORT)
        if content.get_by_role("button", name=REVIEW_BUTTON).count() < before_count:
            return True
        return False

    def progress():
        try:
            text = content.inner_text(timeout=1500)
        except PWTimeout:
            return None
        text = unicodedata.normalize("NFKC", text)
        text = re.sub(r"[\u064B-\u065F\u0670]", "", text)
        text = text.translate(str.maketrans("٠١٢٣٤٥٦٧٨٩۰۱۲۳۴۵۶۷۸۹", "01234567890123456789"))
        match = re.search(
            r"(?:(\d+)\s*/\s*(\d+)\s*(?:متعلم|تعلمت|تم التعلم)|"
            r"(?:متعلم|تعلمت|تم التعلم)\s*(\d+)\s*/\s*(\d+))",
            text,
        )
        if not match:
            return None
        done, total = match.group(1, 2) if match.group(1) else match.group(3, 4)
        return int(done), int(total)

    cards = content.get_by_role("button", name=REVIEW_CARD_BUTTON)
    count = cards.count()
    if not count:
        return False
    try:
        card_states = cards.evaluate_all(_REVIEW_CARDS_JS)
        if len(card_states) != count:
            card_states = None
    except Stopped:  # الإيقاف لا يُبلَع داخل محاولات الاستعادة
        raise
    except Exception:  # noqa: BLE001
        card_states = None

    def is_marked(card):
        for attribute in ("aria-pressed", "aria-checked", "data-state"):
            try:
                value = (card.get_attribute(attribute) or "").casefold()
            except PWTimeout:
                continue
            if value in ("true", "checked", "selected", "complete", "completed"):
                return True
        return False

    current_progress = progress()
    if current_progress and current_progress[1] and current_progress[0] >= current_progress[1]:
        return False

    for index in range(count):
        card = cards.nth(index)
        state = card_states[index] if card_states is not None else None
        if state is not None:
            visible = state["visible"]
            was_marked = state["marked"]
            disabled = state["disabled"]
        else:
            visible = card.is_visible()
            was_marked = is_marked(card)
            disabled = False
        if not visible or disabled:
            continue
        if was_marked:
            continue
        before = progress()
        card.scroll_into_view_if_needed()
        card.click(force=True, no_wait_after=True)

        def marked_progress():
            current = progress()
            return bool(before and current and current[0] > before[0])

        _wait_until(
            page,
            marked_progress if before else lambda: is_marked(card),
            max(1, WAIT_SHORT * 5),
            poll_interval=50,
        )
        after = progress()
        if before and after and after[0] > before[0]:
            return True
        if is_marked(card):
            return True
        if before and after and after[0] < before[0]:
            card.click(force=True, no_wait_after=True)
            page.wait_for_timeout(WAIT_SHORT)
            restored = progress()
            if not restored or restored[0] < before[0]:
                say_debug("تعذّر استعادة علامة مراجعة كانت محددة مسبقًا؛ توقفت لحماية التقدم.")
                return False
            break
        else:
            say_debug("لم يتغير عدّاد النقاط المهمة بعد الضغط؛ لم أعتبر العنصر مكتملًا.")
            return False
    return False

@_timed
def click_next(page):
    content = main_content(page)

    # 1) حوار إكمال الدرس
    completion_dialog = page.get_by_role("dialog")
    if completion_dialog.count():
        for pattern in (
            r"الدرس اللي بعده|Next lesson|الدرس التالي|كمّل|Continue",
            r"التالي|Next",
        ):
            lesson_button = completion_dialog.get_by_role(
                "button", name=re.compile(f"^({pattern})$", re.IGNORECASE)
            )
            if lesson_button.count() and lesson_button.first.is_visible():
                lesson_button.first.click(force=True, no_wait_after=True)
                page.wait_for_timeout(WAIT_MEDIUM)
                return True

    # 2) إنهاء المراجعة
    finish_review = content.get_by_role(
        "button", name=re.compile(r"^(إنهاء المراجعة|Finish review)$", re.IGNORECASE)
    )
    if finish_review.count() and finish_review.first.is_visible():
        finish_review.first.click(force=True, no_wait_after=True)
        page.wait_for_timeout(WAIT_MEDIUM)
        return True

    # 3) زر التالي / إكمال / كمّل (باستخدام الثابت الموحّد)
    next_button = content.get_by_role("button", name=NEXT_BUTTON)
    if next_button.count() and next_button.first.is_visible():
        next_button.first.click(force=True, no_wait_after=True)
        page.wait_for_timeout(WAIT_MEDIUM)
        return True

    # 4) محاولة أخيرة بالبحث النصي داخل الأزرار الظاهرة
    for btn in content.get_by_role("button").all():
        try:
            if not btn.is_visible():
                continue
            text = " ".join(btn.inner_text().split())
            if re.match(NEXT_BUTTON, text):
                btn.click(force=True, no_wait_after=True)
                page.wait_for_timeout(WAIT_MEDIUM)
                return True
        except Stopped:  # الإيقاف لا يُبلَع داخل محاولات الاستعادة
            raise
        except Exception:  # noqa: BLE001
            continue

    controls = content.locator("button, a, [role='button'], [role='link']")
    for attempt in range(4):
        try:
            rows = controls.evaluate_all(
                "(els) => els.map((el, i) => ({ "
                "i, text: (el.innerText || el.getAttribute('aria-label') || "
                "el.getAttribute('title') || '').trim(), "
                "visible: (" + _VISIBLE_JS + ")(el) }))"
            )
        except PWError:
            rows = []
        for row in rows:
            label = " ".join(row["text"].split())
            if row["visible"] and NEXT_BUTTON.fullmatch(label):
                button = controls.nth(row["i"])
                button.scroll_into_view_if_needed(timeout=1500)
                button.click(force=True, no_wait_after=True)
                page.wait_for_timeout(WAIT_MEDIUM)
                return True
        if attempt == 3:
            break
        if not scroll_quiz_page(page):
            break
        page.wait_for_timeout(WAIT_SHORT)

    return False
