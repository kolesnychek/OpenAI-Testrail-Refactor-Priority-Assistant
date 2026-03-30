from __future__ import annotations


PRIORITY_LABELS = {
    1: "Low",
    2: "Medium",
    3: "High",
    4: "Critical",
}


def _priority_label(value) -> str:
    try:
        num = int(value)
    except (TypeError, ValueError):
        return str(value or "N/A")
    return PRIORITY_LABELS.get(num, str(num))


def _first_reason_line(text: str) -> str:
    raw = (text or "").strip()
    if not raw:
        return "No reason provided"
    parts = [p.strip() for p in raw.split(";") if p.strip()]
    if not parts:
        return raw
    keep = parts[0]
    lower = raw.lower()
    if "no applicable priority rule" in lower or "matched ac=none" in lower:
        return "No AC match; priority unchanged"
    if "secondary/regression/non-core path with low relevance and weak ac coverage -> low by policy" in lower:
        return "Indirect regression scenario with weak AC alignment -> Low"
    if "non-core but relevant acceptance coverage -> medium by policy" in lower:
        return "Matched AC; non-core but relevant coverage -> Medium"
    return keep


def _coverage_type(case: dict) -> str:
    reason = (case.get("priority_reason") or "").lower()
    if "data sync/integration" in reason:
        return "Core"
    if "indirect/regression" in reason or "regression" in reason:
        return "Regression"
    if "non-core" in reason or "secondary" in reason:
        return "Non-core"
    if case.get("matched_acceptance_criteria_ids"):
        return "Core"
    return "No AC coverage"


def _notes(case: dict) -> str:
    before = _priority_label(case.get("priority_before"))
    after = _priority_label(case.get("priority_after"))
    if case.get("priority_before") == case.get("priority_after"):
        return "Priority unchanged after review."
    return f"Priority recalculated: {before} -> {after}."


def _format_ac_match(ac_ids: list[str]) -> str:
    if not ac_ids:
        return "No AC coverage"
    normalized: list[str] = []
    for ac in ac_ids:
        token = str(ac).strip()
        if not token:
            continue
        if token.upper().startswith("AC-"):
            normalized.append(token.upper())
        else:
            normalized.append(f"AC-{token}")
    return ", ".join(normalized) if normalized else "No AC coverage"


def _story_label(report: dict) -> str:
    stories = []
    for case in report.get("cases", []):
        key = case.get("selected_story_key")
        if key and key not in stories:
            stories.append(key)
    if not stories:
        summary = report.get("summary", {})
        section = summary.get("section_id")
        return f"Section {section}" if section else "N/A"
    return ", ".join(stories)


def build_execution_report(ts: str, summary: dict, results: list[dict]) -> dict:
    cases: list[dict] = []
    for item in results:
        status = (item.get("status") or "").strip()
        audit = item.get("priority_audit") or {}
        refactored = item.get("refactored") or {}
        cases.append(
            {
                "case_id": item.get("case_id"),
                "status": status,
                "updated": "updated" in status,
                "created_case_id": item.get("created_case_id"),
                "priority_before": audit.get("current_priority_id"),
                "priority_after": refactored.get("priority_id"),
                "priority_changed": audit.get("changed", False),
                "priority_reason": refactored.get("priority_reason") or "; ".join(audit.get("reasons", [])),
                "selected_story_key": audit.get("selected_story_key"),
                "selected_story_relevance": audit.get("selected_story_relevance"),
                "selected_story_jira_priority": audit.get("selected_story_jira_priority"),
                "matched_acceptance_criteria_ids": audit.get("matched_acceptance_criteria_ids", []),
                "case_impact_score": audit.get("case_impact_score"),
                "case_risk_score": audit.get("case_risk_score"),
                "is_ui_tooltip_case": audit.get("is_ui_tooltip_case"),
                "priority_decision_basis": audit.get("priority_decision_basis", {}),
                "jira_story_evidence": audit.get("story_evidence", []),
                "acceptance_criteria_evidence": audit.get("acceptance_criteria_evidence", []),
                "acceptance_fields_meta": audit.get("acceptance_fields_meta", []),
                "application_rule": audit.get("application_rule"),
                "application_explanation": audit.get("application_explanation"),
            }
        )

    return {
        "generated_at_utc": ts,
        "summary": summary,
        "cases": cases,
    }


def render_execution_report_markdown(report: dict) -> str:
    summary = report.get("summary", {})
    cases = report.get("cases", [])
    changed_cases = [c for c in cases if c.get("priority_before") != c.get("priority_after")]
    created_cases = [c for c in cases if c.get("status") == "created"]
    story_label = _story_label(report)

    lines = [
        "Summary",
        f"- Total: {summary.get('total', 0)}",
        f"- Created: {summary.get('created', 0)}",
        f"- Priority changed: {summary.get('priority_changed', 0)}",
        f"- Story: {story_label}",
        "",
        "Priority Changes",
    ]

    if not changed_cases:
        lines.extend([
            "- Case ID: none",
            "  Reason: No priority changes in this run.",
            "  AC match: N/A",
        ])
    else:
        for case in changed_cases:
            before = _priority_label(case.get("priority_before"))
            after = _priority_label(case.get("priority_after"))
            ac_ids = case.get("matched_acceptance_criteria_ids", [])
            ac_text = _format_ac_match(ac_ids)
            lines.extend([
                f"- Case ID: C{case.get('case_id')}: {before} -> {after}",
                f"  Reason: {_first_reason_line(case.get('priority_reason') or '')}",
                f"  AC match: {ac_text}",
            ])

    lines.extend([
        "",
        "Created Cases Overview",
    ])

    if not created_cases:
        lines.extend([
            "- Case ID: none",
            "  Final priority: N/A",
            "  Coverage type: N/A",
            "  Notes: No created cases in this run.",
        ])
    else:
        for case in created_cases:
            lines.extend([
                f"- Case ID: C{case.get('case_id')}",
                f"  Final priority: {_priority_label(case.get('priority_after'))}",
                f"  Coverage type: {_coverage_type(case)}",
                f"  Notes: {_notes(case)}",
            ])

    ac_matched_count = sum(1 for c in cases if c.get("matched_acceptance_criteria_ids"))
    no_ac_count = len(cases) - ac_matched_count
    lines.extend([
        "",
        "Observations",
        f"- AC-aligned cases: {ac_matched_count}",
        f"- Cases without AC match: {no_ac_count}",
    ])
    if changed_cases:
        lines.append("- Priority updates were driven by story alignment and coverage type.")
    else:
        lines.append("- No priority changes were applied in this run.")

    needs_review = [c for c in cases if not c.get("matched_acceptance_criteria_ids") and c.get("priority_after") in (3, 4)]
    if needs_review:
        review_ids = ", ".join(f"C{c.get('case_id')}" for c in needs_review)
        assessment = (
            "Prioritization is overall reasonable and consistent with AC alignment and coverage type. "
            f"Manual review is recommended for {review_ids} due to missing AC coverage despite high priority."
        )
    else:
        assessment = "Prioritization is overall reasonable and consistent with AC alignment and coverage type."
    lines.extend([
        "",
        "Final Assessment",
        f"- {assessment}",
    ])

    return "\n".join(lines).strip() + "\n"
