"""Verdict rule tests: every branch of server/verify/verdict.py (AGENTS.md V1)."""

from server.verify.verdict import compute_verdict

QUALITY_OK = {"ok": True, "reasons": []}
QUALITY_BAD = {"ok": False, "reasons": ["Too blurry, hold still"]}


def crit(cid, question, required=True):
    return {"id": cid, "question": question, "required": required}


def ans(cid, answer, evidence="green leaves"):
    return {"id": cid, "answer": answer, "evidence": evidence}


CRITERIA = [crit("c1", "Are leaves yellow, orange or red?")]


# --- pass -----------------------------------------------------------------

def test_pass_all_required_yes():
    r = compute_verdict(QUALITY_OK, None, [ans("c1", "yes")], CRITERIA)
    assert r["verdict"] == "pass"
    assert r["hint"] == ""


def test_pass_with_no_dupe_entry():
    r = compute_verdict({"ok": True, "reasons": []}, None, [ans("c1", "yes")], CRITERIA)
    assert r["verdict"] == "pass"


def test_empty_criteria_passes_vacuously():
    r = compute_verdict(QUALITY_OK, None, [], [])
    assert r["verdict"] == "pass"


# --- fail -----------------------------------------------------------------

def test_fail_single_no():
    r = compute_verdict(QUALITY_OK, None, [ans("c1", "no")], CRITERIA)
    assert r["verdict"] == "fail"
    assert r["reasons"] == ["Required criterion not met: Are leaves yellow, orange or red?"]


def test_fail_hint_uses_question_and_evidence():
    r = compute_verdict(
        QUALITY_OK, None, [ans("c1", "no", "green leaves")], CRITERIA
    )
    assert r["hint"] == (
        "I couldn't confirm: Are leaves yellow, orange or red? I saw: green leaves."
    )


def test_fail_hint_with_empty_evidence_falls_back():
    r = compute_verdict(QUALITY_OK, None, [ans("c1", "no", "")], CRITERIA)
    assert r["hint"].endswith("I saw: nothing conclusive.")


def test_fail_beats_unsure():
    criteria = [crit("c1", "q1?"), crit("c2", "q2?")]
    answers = [ans("c1", "unclear"), ans("c2", "no")]
    r = compute_verdict(QUALITY_OK, None, answers, criteria)
    assert r["verdict"] == "fail"


# --- unsure ---------------------------------------------------------------

def test_unsure_single_unclear():
    r = compute_verdict(QUALITY_OK, None, [ans("c1", "unclear")], CRITERIA)
    assert r["verdict"] == "unsure"


def test_unsure_hint_format():
    r = compute_verdict(
        QUALITY_OK, None, [ans("c1", "unclear", "all leaves look green")], CRITERIA
    )
    assert r["hint"] == (
        "I couldn't confirm: Are leaves yellow, orange or red? I saw: all leaves look green."
    )


def test_empty_answers_are_unsure():
    r = compute_verdict(QUALITY_OK, None, [], CRITERIA)
    assert r["verdict"] == "unsure"


def test_missing_answer_for_one_required_is_unsure():
    criteria = [crit("c1", "q1?"), crit("c2", "q2?")]
    r = compute_verdict(QUALITY_OK, None, [ans("c1", "yes")], criteria)
    assert r["verdict"] == "unsure"


def test_invalid_answer_string_is_unsure():
    r = compute_verdict(QUALITY_OK, None, [ans("c1", "maybe")], CRITERIA)
    assert r["verdict"] == "unsure"


# --- optional / unknown criteria -----------------------------------------

def test_optional_no_is_ignored():
    criteria = [crit("c1", "required?"), crit("c2", "optional?", required=False)]
    answers = [ans("c1", "yes"), ans("c2", "no")]
    r = compute_verdict(QUALITY_OK, None, answers, criteria)
    assert r["verdict"] == "pass"


def test_optional_unclear_is_ignored():
    criteria = [crit("c1", "required?"), crit("c2", "optional?", required=False)]
    answers = [ans("c1", "yes"), ans("c2", "unclear")]
    r = compute_verdict(QUALITY_OK, None, answers, criteria)
    assert r["verdict"] == "pass"


def test_unknown_criterion_ids_in_answers_are_ignored():
    answers = [ans("c1", "yes"), ans("zzz", "no"), {"id": None, "answer": "no"}]
    r = compute_verdict(QUALITY_OK, None, answers, CRITERIA)
    assert r["verdict"] == "pass"


# --- retake precedence ----------------------------------------------------

def test_retake_precedence_over_dupe_and_fail():
    dupe = {"distance": 0, "match": {"submission_id": "s1", "task_id": "t2"}}
    answers = [ans("c1", "no")]
    r = compute_verdict(QUALITY_BAD, dupe, answers, CRITERIA)
    assert r["verdict"] == "retake"


def test_retake_reasons_come_from_quality():
    r = compute_verdict(QUALITY_BAD, None, [], CRITERIA)
    assert r["verdict"] == "retake"
    assert r["reasons"] == ["Too blurry, hold still"]
    assert r["hint"] == ""


# --- rejected precedence --------------------------------------------------

def test_rejected_precedence_over_rubric_pass():
    dupe = {"distance": 3, "match": {"submission_id": "s1", "task_id": "t2"}}
    r = compute_verdict(QUALITY_OK, dupe, [ans("c1", "yes")], CRITERIA)
    assert r["verdict"] == "rejected"
    assert "s1" in r["reasons"][0]
    assert "distance 3" in r["reasons"][0]


def test_rejected_at_distance_zero():
    dupe = {"distance": 0, "match": {"submission_id": "s9", "task_id": "t9"}}
    r = compute_verdict(QUALITY_OK, dupe, [ans("c1", "no")], CRITERIA)
    assert r["verdict"] == "rejected"


def test_rejected_at_threshold_six():
    dupe = {"distance": 6, "match": {"submission_id": "s9", "task_id": "t9"}}
    r = compute_verdict(QUALITY_OK, dupe, [], CRITERIA)
    assert r["verdict"] == "rejected"


def test_not_rejected_above_threshold():
    dupe = {"distance": 7, "match": {"submission_id": "s1", "task_id": "t2"}}
    r = compute_verdict(QUALITY_OK, dupe, [ans("c1", "yes")], CRITERIA)
    assert r["verdict"] == "pass"
