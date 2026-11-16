"""Offline tests for question and answer-option matching."""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import core  # noqa: E402
from core import (  # noqa: E402
    AnswerIndex,
    AnswerMatchError,
    answer_option_index,
    extract_question_prompt,
    resolve_answer,
)

BANK = [
    ("ما هو الهدف الأساسي من وضع ميزانية شخصية شهرية؟", "التحكم في الدخل والمصروفات"),
    ("ما هي نسبة الادخار الموصى بها من الدخل الشهري؟", "20%"),
    ("ما هي نسبة الادخار الموصى بها من الدخل السنوي؟", "10%"),
    ("أي مما يلي يعتبر من الأصول؟", "الأسهم العادية"),
    ("أي مما يلي يعتبر من الأصول؟", "أسهم عادية"),
    ("هل التضخم يؤثر على القوة الشرائية للنقود بمرور الوقت؟", "نعم"),
    ("التضخم هو", "ارتفاع عام ومستمر في الأسعار"),
    ("سؤال متعارض حقًا يحتاج حسمًا من المنصة", "الخيار الأول"),
    ("سؤال متعارض حقًا يحتاج حسمًا من المنصة", "الخيار الثاني"),
]


@pytest.fixture(scope="module")
def idx():
    return AnswerIndex(BANK)


def test_duplicate_question_with_reworded_answer_is_resolved_by_options(idx):
    option, _answer, _question = resolve_answer(
        "أي مما يلي يعتبر من الأصول؟",
        idx,
        ["الأسهم العادية", "القروض", "الالتزامات"],
    )
    assert option == 0


def test_true_conflict_still_stops(idx):
    with pytest.raises(AnswerMatchError):
        resolve_answer(
            "سؤال متعارض حقًا يحتاج حسمًا من المنصة",
            idx,
            ["الخيار الأول", "الخيار الثاني"],
        )


def test_similar_questions_keep_their_own_answers(idx):
    options = ["10%", "20%", "30%"]
    assert resolve_answer(
        "ما هي نسبة الادخار الموصى بها من الدخل الشهري؟", idx, options
    )[0] == 1
    assert resolve_answer(
        "ما هي نسبة الادخار الموصى بها من الدخل السنوي؟", idx, options
    )[0] == 0


def test_unknown_question_is_not_guessed(idx):
    with pytest.raises(AnswerMatchError):
        resolve_answer(
            "ما عاصمة دولة غير مذكورة في البنك إطلاقًا؟", idx, ["أ", "ب"]
        )


def test_short_bank_answer_matches_longer_platform_option():
    options = [
        "ارتفاع عام ومستمر في مستوى الأسعار على مدى فترة",
        "انخفاض الأسعار",
        "ثبات الأسعار",
    ]
    assert answer_option_index(options, "ارتفاع عام ومستمر في الأسعار") == 0


def test_token_overlap_does_not_pick_when_ambiguous():
    options = [
        "ارتفاع عام ومستمر في الأسعار المحلية",
        "ارتفاع عام ومستمر في الأسعار العالمية",
    ]
    with pytest.raises(AnswerMatchError):
        answer_option_index(options, "ارتفاع عام ومستمر في الأسعار")


def test_arabic_letter_prefix_is_ignored():
    assert answer_option_index(["أ) نعم", "ب) لا"], "لا") == 1
    assert answer_option_index(["أ. التحكم", "ب. الإنفاق"], "الإنفاق") == 1


def test_short_bank_options_do_not_corrupt_question_text():
    card = "ما هي أهمية الادخار المنتظم لتحقيق الأمان المالي؟ اختر إجابة واحدة لا نعم"
    prompt = extract_question_prompt(card, ["لا", "نعم"])
    assert "الادخار" in prompt


def test_numeric_option_does_not_strip_number_from_question():
    card = "ما نسبة الادخار الموصى بها من الدخل الشهري عند 10 سنوات؟ 10 20 5"
    prompt = extract_question_prompt(card, ["10", "20", "5"])
    assert "10 سنوات" in prompt


def test_duplicate_options_do_not_remove_question_text():
    question = "هل لا يؤثر التضخم على القوة الشرائية بمرور الوقت؟"
    card = f"{question} لا نعم لا"
    prompt = extract_question_prompt(card, ["لا", "نعم", "لا"])
    assert core.normalize_text(prompt) == core.normalize_text(question)


def test_instruction_variants_are_removed():
    for instruction in (
        "اختر إجابة واحدة",
        "اختار إجابة واحدة",
        "اختر الإجابة الصحيحة",
    ):
        card = f"ما هي حالة “التضخم المفرط”؟ {instruction} أول خيار طويل ثاني خيار طويل"
        prompt = extract_question_prompt(card, ["أول خيار طويل", "ثاني خيار طويل"])
        assert core.normalize_text(prompt) == core.normalize_text(
            "ما هي حالة “التضخم المفرط”؟"
        )


def test_arabic_question_mark_normalizes_consistently():
    assert core.normalize_text("الإجمالي”؟") == core.normalize_text("الإجمالي؟")


def test_parser_stops_answer_at_unit_heading(tmp_path):
    answer_file = tmp_path / "answers.txt"
    answer_file.write_text(
        "السؤال الاول\n\nما هو سؤال تجريبي طويل بما يكفي؟\n\n"
        "الاجابة\n\nإجابة تجريبية\n\nالوحدة الثالثة\n\nالتمويل الشخصي\n\n"
        "السؤال الثاني\n\nسؤال ثان تجريبي طويل؟\n\nالاجابة\n\nإجابة ثانية\n",
        encoding="utf-8",
    )
    index = core.parse_answer_file(answer_file)
    assert index.entries[0][1] == "إجابة تجريبية"


@pytest.mark.parametrize(
    "label",
    ["التالي", "التالي ←", "← التالي", "كمل", "كمّل", "اكمال", "Next"],
)
def test_next_button_accepts_arrows_and_spelling_variants(label):
    assert core.NEXT_BUTTON.fullmatch(label)


@pytest.mark.parametrize("label", ["→ رجوع", "تقديم الاختبار", "التالي سؤال"])
def test_next_button_does_not_match_other_controls(label):
    assert not core.NEXT_BUTTON.fullmatch(label)


def test_documented_quiz_result_screen_is_detected():
    result = (
        "نتيجة الاختبار 100% ناجح! درجة النجاح: 80% 10 من 10 صح "
        "الدرجة محسوبة حسب وزن كل سؤال إجاباتك :Q1"
    )
    assert core.is_quiz_result_page(result)
    assert core.is_quiz_result_page("ناجح! درجة النجاح 80% إجاباتك")
    landing = "ابدأ الاختبار عدد الأسئلة 10 درجة النجاح 80% مفيش محاولات لسه"
    assert not core.is_quiz_result_page(landing)


def test_all_answer_bank_entries_resolve_to_their_exact_option():
    answer_file = Path(__file__).resolve().parents[1] / "answers.txt"
    index = core.parse_answer_file(answer_file)

    for number, (question, answer) in enumerate(index):
        for variant in (question, f"{question}؟"):
            for displayed_answer in (answer, f"أ) {answer}"):
                options = [
                    displayed_answer,
                    f"ب) خيار تجريبي غير مطابق {number}",
                ]
                option, _matched_answer, _source_question = resolve_answer(
                    variant, index, options
                )
                assert option == 0, question
