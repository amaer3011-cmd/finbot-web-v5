"""وحدة config — جزء من حزمة core (مقسومة من core.py)."""
import contextvars
import logging
import os
import re
import sys



"""
محرّك حل المنصة (Playwright) — مبني على السكريبت الأصلي، ومعدّل ليشتغل من البوت:
- أحداث تقدّم عبر hook بدل print
- إيقاف آمن عبر threading.Event
- تتبّع الدروس المكتملة قبل كده / اللي خلّصها البوت
"""

_log = logging.getLogger(__name__)

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8")

SITE_URL = os.getenv("SITE_URL", "https://egy.app.spx-learning-square.com/login")

TARGET_URL = os.getenv(
    "TARGET_URL", "https://egy.app.spx-learning-square.com/subjects/7/curriculum"
)

HEADLESS = os.getenv("HEADLESS", "false").lower() == "true"

MAX_STEPS = int(os.getenv("MAX_STEPS", "5000"))  # 654 سؤال + تنقل ومراجعات يتجاوزون 500 خطوة

# الاختبارات المقيّمة تُعالج كأنشطة؛ لا يتم تقديمها قبل حل كل أسئلتها.
RETRIES = int(os.getenv("RETRIES", "2"))  # إعادة تشغيل المتصفح والمتابعة عند انقطاع/مهلة

BLOCK_ASSETS = os.getenv("BLOCK_ASSETS", "false").lower() == "true"

_hook = contextvars.ContextVar("hook", default=None)

_timing = contextvars.ContextVar("timing", default=None)

_cancel = contextvars.ContextVar("cancel", default=None)

_job = contextvars.ContextVar("job", default=None)

_report_consent = contextvars.ContextVar("report_consent", default=False)

class Stopped(RuntimeError):
    pass

class LoginFailed(RuntimeError):
    pass

class PageNotReady(RuntimeError):
    pass

QUESTION_RE = re.compile(
    r"^السؤال\s+(?:الأول|الاول|الثاني|الثالث|الرابع|الخامس|السادس|السابع|"
    r"الثامن|التاسع|العاشر|[0-9٠-٩]+)(?:\s+(.*))?$"
)

ANSWER_LABELS = {"الاجابة", "الإجابة"}

REVIEW_BUTTON = re.compile(r"^علّم كمراجع$")

REVIEW_CARD_BUTTON = re.compile(r"النقاط المهمة", re.IGNORECASE)

CHECK_BUTTON = re.compile(
    r"^(راجع إجابتي|تحقق|إجابة|Answer|Submit|تحقق من الإجابة|"
    r"أجب\s+لعرض\s+البقية\.?)$",
    re.IGNORECASE,
)

NEXT_BUTTON = re.compile(
    r"^[\s←→⟵⟶›‹»«><❯❮▶◀]*(التالي|التالى|إكمال|اكمال|كمّل|كمل|"
    r"كمّل التعلم|كمل التعلم|متابعة|تابع|Next|Complete|Continue)"
    r"[\s←→⟵⟶›‹»«><❯❮▶◀]*$",
    re.IGNORECASE,
)

TEST_MARKERS = (
    "اختبار",
    "امتحان",
    "quiz",
    "exam",
    "assessment",
)

# علامات صفحة الاختبار نفسها (البدء/الاستكمال/التقديم/درجة النجاح)
TEST_PAGE_MARKERS = ("ابدأ الاختبار", "استكمال الاختبار", "تقديم الاختبار", "درجة النجاح")

QUIZ_RESULT_MARKERS = ("نتيجة", "ناجح", "راسب")

# مهلات أقصر وأكثر تكيفًا لتسريع الحل بدون التضحية بالاستقرار
WAIT_SHORT = int(os.getenv("WAIT_SHORT", "150"))

WAIT_MEDIUM = int(os.getenv("WAIT_MEDIUM", "250"))

WAIT_AFTER_ANSWER = int(os.getenv("WAIT_AFTER_ANSWER", "250"))

QUIZ_QUESTION_TIMEOUT = 10000

SESSION_RECOVERY_LIMIT = 2

QUIZ_NEXT_BUTTON = re.compile(r"(?:التالي|Next)", re.IGNORECASE)

QUIZ_RESUME_BUTTON = re.compile(r"استكمال\s+الاختبار", re.IGNORECASE)

QUIZ_START_BUTTON = re.compile(r"ابدأ\s+الاختبار", re.IGNORECASE)

QUIZ_SUBMIT_BUTTON = re.compile(r"(تقديم\s+الاختبار|Submit)", re.IGNORECASE)

QUIZ_ANSWER_INSTRUCTION = re.compile(
    r"اخت(?:ار|ر) إجابة واحدة|Choose one answer!?|Answer the question",
    re.IGNORECASE,
)

QUIZ_CONFIRM_BUTTON = re.compile(
    r"(?:تأكيد\s+(?:تقديم\s+الاختبار|التقديم|الإرسال)|إرسال\s+الاختبار|"
    r"Confirm(?:\s+submission)?)",
    re.IGNORECASE,
)

QUIZ_SCROLL_JS = (
    "() => { const root = document.scrollingElement; "
    "const nodes = [root, ...document.querySelectorAll('*')].filter((el) => { "
    "if (!el || el.clientHeight <= 0 || el.scrollHeight <= el.clientHeight + 1) return false; "
    "const overflow = getComputedStyle(el).overflowY; "
    "return overflow === 'auto' || overflow === 'scroll' || el === root; }); "
    "let moved = false; for (const el of nodes) { const before = el.scrollTop; "
    "el.scrollTop = Math.min(before + Math.max(el.clientHeight * 0.85, 250), el.scrollHeight); "
    "moved = moved || el.scrollTop > before; } "
    "window.scrollTo(0, Math.min(window.scrollY + Math.max(window.innerHeight * 0.85, 250), "
    "document.documentElement.scrollHeight)); return moved; }"
)

QUIZ_DIALOG_CONFIRM_BUTTON = re.compile(
    r"(?:تأكيد\s+(?:تقديم\s+الاختبار|التقديم|الإرسال)|إرسال\s+الاختبار|"
    r"Confirm(?:\s+submission)?|Submit)",
    re.IGNORECASE,
)

FEEDBACK_MARKERS = re.compile(
    r"إجابة\s+خاطئة|إجابتك\s+غير\s+صحيحة|حاول\s+مرة\s+أخرى|غير\s+صحيح|"
    r"إجابة\s+صحيحة|إجابتك\s+صحيحة|أحسنت|ممتاز|wrong\s+answer|incorrect|"
    r"correct\s+answer|well\s+done",
    re.IGNORECASE,
)

INCORRECT_FEEDBACK_PHRASES = (
    "إجابة خاطئة",
    "إجابتك غير صحيحة",
    "إجابة غير صحيحة",
    "حاول مرة أخرى",
    "غير صحيح",
    "wrong answer",
    "incorrect",
)

# قراءة كل عناصر محدد معين في طلب واحد للمتصفح بدل طلبين أو أكثر لكل عنصر
_VISIBLE_JS = (
    "(el) => { const r = el.getBoundingClientRect(); const s = getComputedStyle(el); "
    "return r.width > 0 && r.height > 0 && s.visibility !== 'hidden'; }"
)

_LEAVES_JS = (
    "(els) => els.map((el, i) => ({ i, title: el.getAttribute('title') || '', "
    "text: (el.innerText || '').trim(), visible: (" + _VISIBLE_JS + ")(el) }))"
)

_BUTTONS_JS = (
    "(els) => els.map((el, i) => ({ i, label: (el.innerText || '').trim(), "
    "disabled: el.matches(':disabled') || el.getAttribute('aria-disabled') === 'true' }))"
)

_RADIOS_JS = (
    "(els) => els.map((el) => ({ visible: (" + _VISIBLE_JS + ")(el), "
    "disabled: el.getAttribute('aria-disabled'), "
    "checked: el.getAttribute('aria-checked') === 'true' || el.checked === true || "
    "!!el.querySelector('input[type=\"radio\"]:checked') }))"
)

_REVIEW_CARDS_JS = (
    "(els) => els.map((el) => { "
    "const state = ['aria-pressed', 'aria-checked', 'data-state']"
    ".map((name) => (el.getAttribute(name) || '').toLowerCase()); "
    "return { visible: (" + _VISIBLE_JS + ")(el), "
    "marked: state.some((value) => ['true', 'checked', 'selected', 'complete', 'completed'].includes(value)), "
    "disabled: el.matches(':disabled') || el.getAttribute('aria-disabled') === 'true' }; })"
)

_TEXTS_JS = "(els) => els.map((el) => el.innerText || '')"
