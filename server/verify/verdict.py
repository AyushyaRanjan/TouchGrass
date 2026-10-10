"""Pure verdict logic (AGENTS.md rule 1: the VLM never decides pass/fail).

Precedence: retake > rejected > fail > unsure > pass.
"""

from ..config import DUPE_DISTANCE


def _hint(question: str, evidence) -> str:
    ev = " ".join(str(evidence or "").split()).rstrip(".")
    if not ev:
        ev = "nothing conclusive"
    q = " ".join(str(question or "").split())
    if q and not q.endswith(("?", "!", ".")):
        q += "."
    return f"I couldn't confirm: {q} I saw: {ev}."


def compute_verdict(quality: dict, dupe: dict | None, rubric_answers: list, criteria: list) -> dict:
    """Turn gate results into a verdict. Pure and synchronous.

    quality:  {"ok": bool, "reasons": [str]}
    dupe:     {"distance": int, "match": {...}} or None
    rubric:   [{"id", "answer", "evidence"}]
    criteria: [{"id", "question", "required"}]

    Returns {"verdict", "reasons", "hint"}.
    """
    # Retake beats everything; the VLM is not even consulted (AGENTS.md).
    if not quality.get("ok", False):
        return {"verdict": "retake", "reasons": list(quality.get("reasons", [])), "hint": ""}

    # Rejected beats the VLM's answers: distance <= 6 on a different task.
    if dupe and int(dupe.get("distance", 999)) <= DUPE_DISTANCE:
        match = dupe.get("match") or {}
        return {
            "verdict": "rejected",
            "reasons": [
                f"Duplicate of submission {match.get('submission_id', '?')} "
                f"on task {match.get('task_id', '?')} "
                f"(phash distance {dupe.get('distance')})"
            ],
            "hint": "",
        }

    answers = {
        a.get("id"): a
        for a in (rubric_answers or [])
        if isinstance(a, dict) and a.get("id") is not None
    }

    first_no = None
    first_unclear = None
    for criterion in criteria:
        # Optional criteria are logged only, never judged (AGENTS.md).
        if not criterion.get("required", True):
            continue
        item = answers.get(criterion["id"])
        answer = item.get("answer") if item else None
        evidence = item.get("evidence", "") if item else ""
        if answer == "yes":
            continue
        if answer == "no":
            if first_no is None:
                first_no = (criterion, evidence)
        elif first_unclear is None:
            # missing / unclear / unrecognized answers all count as unsure
            first_unclear = (criterion, evidence)

    if first_no:
        criterion, evidence = first_no
        question = criterion["question"]
        return {
            "verdict": "fail",
            "reasons": [f"Required criterion not met: {question}"],
            "hint": _hint(question, evidence),
        }
    if first_unclear:
        criterion, evidence = first_unclear
        question = criterion["question"]
        return {
            "verdict": "unsure",
            "reasons": [f"Could not confirm: {question}"],
            "hint": _hint(question, evidence),
        }
    return {"verdict": "pass", "reasons": ["All required criteria confirmed"], "hint": ""}
