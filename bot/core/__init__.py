"""حزمة core — محرك حل منصة الثقافة المالية (كانت ملف core.py واحدًا).

الوحدات (بترتيب الطبقات من الأدنى للأعلى):
  config      إعدادات البيئة والثوابت والـ regex والاستثناءات
  runtime     الإبلاغ (say) والإلغاء والتشخيص والقياس الزمني
  answers     تحميل بنك الإجابات ومطابقة السؤال والخيار
  page        أدوات الصفحة والانتظار الذكي
  quiz        الاختبارات المقيّمة (بدء/تنقل/تقديم)
  questions   حل سؤال ظاهر والنقاط المهمة والتالي
  curriculum  بيانات المنهج من الـ API والخطة
  session     الدخول وفتح المنهج وإغلاق النوافذ
  navigation  الرجوع للمنهج وفتح النشاط التالي
  flow        دورة الحل الكاملة وتشغيل المهمة

للتوافق مع bot.py والاختبارات، كل الأسماء متاحة مباشرة من `core`."""
import difflib  # noqa: F401  (متاحة للاختبارات)
import logging  # noqa: F401
import time  # noqa: F401  (الاختبارات تعدّل core.time)

from PIL import Image  # noqa: F401
from playwright.sync_api import Error as PWError  # noqa: F401
from playwright.sync_api import TimeoutError as PWTimeout  # noqa: F401

from .config import (  # noqa: F401
    ANSWER_LABELS,
    BLOCK_ASSETS,
    CHECK_BUTTON,
    FEEDBACK_MARKERS,
    HEADLESS,
    INCORRECT_FEEDBACK_PHRASES,
    LoginFailed,
    MAX_STEPS,
    NEXT_BUTTON,
    PageNotReady,
    QUESTION_RE,
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
    RETRIES,
    REVIEW_BUTTON,
    REVIEW_CARD_BUTTON,
    SESSION_RECOVERY_LIMIT,
    SITE_URL,
    Stopped,
    TARGET_URL,
    TEST_MARKERS,
    TEST_PAGE_MARKERS,
    WAIT_AFTER_ANSWER,
    WAIT_MEDIUM,
    WAIT_SHORT,
    _BUTTONS_JS,
    _LEAVES_JS,
    _RADIOS_JS,
    _REVIEW_CARDS_JS,
    _TEXTS_JS,
    _VISIBLE_JS,
    _cancel,
    _hook,
    _job,
    _log,
    _report_consent,
    _timing,
)
from .runtime import (  # noqa: F401
    _diagnostic_report,
    _redact_screenshot_bytes,
    _redacted_screenshot,
    _send_diagnostic,
    _timed,
    check_cancel,
    say,
    say_debug,
)
from .answers import (  # noqa: F401
    AnswerIndex,
    AnswerMatchError,
    _fuzzy_candidates,
    _option_variants,
    answer_option_index,
    extract_question_prompt,
    locate_answer_file,
    match_answer,
    normalize_text,
    parse_answer_file,
    parse_markdown_answer_file,
    resolve_answer,
)
from .page import (  # noqa: F401
    _activity_view_snapshot,
    _remember_unmatched,
    _wait_for_url,
    _wait_until,
    current_context,
    has_new_incorrect_feedback,
    main_content,
    wait_for_answer_feedback,
)
from .quiz import (  # noqa: F401
    _capture_quiz_submission_response,
    _submit_quiz_with_confirmation,
    advance_quiz_question,
    clean_quiz_prompt,
    extract_quiz_prompt,
    find_quiz_button,
    has_resumable_quiz_attempt,
    is_graded_test,
    is_quiz_failed_result,
    is_quiz_result_page,
    on_test_page,
    parse_quiz_submission_summary,
    quiz_question_position,
    scroll_quiz_page,
    solve_quiz,
    start_or_resume_quiz,
    submit_quiz,
    visible_page_text,
    wait_for_quiz_question,
)
from .questions import (  # noqa: F401
    answer_text_question_if_supported,
    click_next,
    mark_review_points,
    solve_one_visible_question,
)
from .curriculum import (  # noqa: F401
    CURRICULUM_API,
    _UNIT_COUNT,
    _is_test_item,
    _read_units,
    _side_size,
    _unit_items,
    expand_next_unit,
    fetch_curriculum,
    next_pending_item,
    summarize_curriculum,
)
from .session import (  # noqa: F401
    INTRO_BUTTON,
    OVERLAY_CLOSE_BUTTON,
    _finance_card_action_index,
    _open_finance_card,
    _open_subject_continue_button,
    _try_dismiss_active_dialog,
    _try_dismiss_navigation_overlay,
    announce_plan,
    describe_page,
    dismiss_intro,
    is_login_page,
    login,
    open_curriculum,
)
from .navigation import (  # noqa: F401
    _activity_is_current,
    _expand_collapsed_sidebar,
    _expand_unit_named,
    _fallback_open,
    _visible_activity_matches,
    _visible_matches,
    _wait_for_activity_view_change,
    open_next_available_activity,
    open_next_via_api,
    return_to_curriculum,
)
from .flow import (  # noqa: F401
    _attempt,
    main,
    run_job,
    run_learning_flow,
)
