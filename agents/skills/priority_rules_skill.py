from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class PriorityDecision:
    applied_priority_id: int | None
    changed: bool
    application_rule: str
    application_explanation: str


class PriorityRulesSkill:
    name = "priority_rules_skill"

    def __init__(self, priority_id_medium: int):
        self._priority_id_medium = priority_id_medium

    def resolve(
        self,
        *,
        source_priority_id: int | None,
        proposed_priority_id: int | None,
        matched_acceptance_criteria_ids: list[str],
        story_key: str,
        analyzer_reasons: list[str],
    ) -> PriorityDecision:
        normalized_story_key = (story_key or "").strip()
        normalized_ac_ids = [item.strip() for item in matched_acceptance_criteria_ids if item and item.strip()]
        normalized_reasons = [item.strip() for item in analyzer_reasons if item and item.strip()]
        ac_list = ", ".join(normalized_ac_ids) if normalized_ac_ids else "none"
        story_label = normalized_story_key or "N/A"
        has_story_evidence = bool(normalized_story_key and normalized_ac_ids)
        has_valid_proposal = proposed_priority_id is not None and proposed_priority_id > 0

        # Policy:
        # 1) If strong story evidence exists, apply analyzed priority for any source level
        #    (LOW/MEDIUM/HIGH/CRITICAL), so historical over/under-prioritization can be corrected.
        # 2) If no evidence, keep source as-is (conservative fallback).
        # 3) If source missing, allow analyzer proposal only when rationale exists.
        if has_valid_proposal and has_story_evidence and normalized_reasons:
            if proposed_priority_id == 4 and len(normalized_ac_ids) < 2:
                return PriorityDecision(
                    applied_priority_id=source_priority_id,
                    changed=False,
                    application_rule="Blocked CRITICAL escalation due to weak AC evidence",
                    application_explanation=(
                        f"Source priority={source_priority_id if source_priority_id is not None else 'N/A'}. "
                        f"Story={story_label}, matched AC={ac_list}. "
                        "At least two AC matches are required to promote to CRITICAL."
                    ),
                )
            changed = source_priority_id is None or proposed_priority_id != source_priority_id
            return PriorityDecision(
                applied_priority_id=proposed_priority_id,
                changed=changed,
                application_rule="Applied story-based recalculation (strong evidence)",
                application_explanation=(
                    f"Source priority={source_priority_id if source_priority_id is not None else 'N/A'}. "
                    f"Story={story_label}, matched AC={ac_list}. "
                    f"Analyzer reasons: {'; '.join(normalized_reasons)}"
                ),
            )

        if source_priority_id == self._priority_id_medium:
            return PriorityDecision(
                applied_priority_id=source_priority_id,
                changed=False,
                application_rule="Source priority was MEDIUM; kept MEDIUM due to missing AC evidence",
                application_explanation=(
                    f"Story={story_label}, matched AC={ac_list}. "
                    "Analyzer proposal was not applied because acceptance-criteria evidence is insufficient."
                ),
            )

        if source_priority_id is None and has_valid_proposal and normalized_reasons:
            return PriorityDecision(
                applied_priority_id=proposed_priority_id,
                changed=True,
                application_rule="Source priority missing; applied analyzed priority with rationale",
                application_explanation=(
                    f"Story={story_label}, matched AC={ac_list}. "
                    f"Analyzer reasons: {'; '.join(normalized_reasons)}"
                ),
            )

        return PriorityDecision(
            applied_priority_id=source_priority_id,
            changed=False,
            application_rule="No applicable priority rule (insufficient validated evidence)",
            application_explanation=(
                f"Story={story_label}, matched AC={ac_list}. "
                f"Analyzer reasons: {'; '.join(normalized_reasons) if normalized_reasons else 'none'}."
            ),
        )
