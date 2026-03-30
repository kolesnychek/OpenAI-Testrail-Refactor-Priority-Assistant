from __future__ import annotations

import os
from typing import Any, Awaitable, Callable

import aiohttp

from agents.skills.priority_rules_skill import PriorityRulesSkill
from agents.subagents.jira_ticket_priority_subagent import JiraTicketPrioritySubagent

VERBOSE_TERMINAL = os.getenv("VERBOSE_TERMINAL", "no").lower() in {"1", "true", "yes", "y"}


def log(message: str):
    if VERBOSE_TERMINAL:
        print(message)


class MainRefactorAgent:
    name = "main_refactor_agent"

    def __init__(
        self,
        refactor_case: Callable[[dict], Awaitable[dict]],
        refactored_model_cls: Any,
        validation_error_cls: type[Exception],
        jira_subagent: JiraTicketPrioritySubagent,
        priority_rules_skill: PriorityRulesSkill,
        priority_id_high: int,
    ):
        self._refactor_case = refactor_case
        self._refactored_model_cls = refactored_model_cls
        self._validation_error_cls = validation_error_cls
        self.jira_subagent = jira_subagent
        self.priority_rules_skill = priority_rules_skill
        self.priority_id_high = priority_id_high
        self.subagent_name = jira_subagent.name

    async def process_case(self, case_id: int, raw_case: dict) -> dict:
        refactored = await self._refactor_case(raw_case)

        try:
            validated = self._refactored_model_cls(**refactored)
        except self._validation_error_cls as err:
            log(f"[AGENT:MAIN][C{case_id}] Validation failed:\n{err}")
            return {
                "case_id": case_id,
                "status": "validation_failed",
                "error": str(err),
                "raw_case": raw_case,
                "refactored": refactored,
            }

        verdict = "yes" if not validated.violations else "no"
        log(f"[AGENT:MAIN][C{case_id}] Local validation result: {verdict}")

        return {
            "case_id": case_id,
            "status": "prepared_only",
            "created_case_id": None,
            "raw_case": raw_case,
            "refactored": validated.model_dump(),
        }

    async def process_section(self, raw_cases: list[dict]) -> list[dict]:
        log(f"[AGENT:MAIN] Processing {len(raw_cases)} case(s)")
        results: list[dict] = []
        for item in raw_cases:
            results.append(await self.process_case(item["case_id"], item["raw_case"]))
        return results

    async def run_priority_subagent(
        self,
        session: aiohttp.ClientSession,
        results: list[dict],
        output_dir: str,
        ts: str,
        *,
        should_run_priority_analyzer: Callable[[list[dict]], bool],
        require_manual_approval: bool,
        ask_run_priority_for_section: Callable[[], str],
        ask_priority_validation: Callable[[list[dict]], str],
        json_dump: Callable[[str, dict | list], None],
    ) -> tuple[list[dict], bool, bool, bool]:
        priority_audits: list[dict] = []
        priority_stage_enabled = should_run_priority_analyzer(results)
        priority_run_confirmed = priority_stage_enabled
        priority_approved = False

        if priority_stage_enabled and require_manual_approval:
            priority_run_confirmed = ask_run_priority_for_section() == "yes"

        if priority_stage_enabled and priority_run_confirmed:
            log("[AGENT:MAIN] Delegating story analysis to subagent")
            priority_audits = await self.jira_subagent.analyze_section(session, results)
            self._ensure_at_least_one_high_with_best_ac_overlap(priority_audits)

            by_case_id = {a.get("case_id"): a for a in priority_audits}
            for result in results:
                if result.get("status") != "prepared_only":
                    continue
                audit = by_case_id.get(result.get("case_id"))
                if audit:
                    result["priority_audit"] = audit

            priority_approved = True
            if require_manual_approval:
                priority_approved = ask_priority_validation(priority_audits) == "yes"

            if priority_approved:
                for result in results:
                    if result.get("status") != "prepared_only":
                        continue
                    audit = result.get("priority_audit") or {}
                    source_priority_id = self._to_int((result.get("raw_case") or {}).get("priority_id"))
                    proposed_priority_id = self._to_int(audit.get("proposed_priority_id"))
                    decision = self.priority_rules_skill.resolve(
                        source_priority_id=source_priority_id,
                        proposed_priority_id=proposed_priority_id,
                        matched_acceptance_criteria_ids=audit.get("matched_acceptance_criteria_ids", []),
                        story_key=str(audit.get("selected_story_key") or ""),
                        analyzer_reasons=audit.get("reasons", []),
                    )
                    applied_priority_id = decision.applied_priority_id

                    if applied_priority_id is not None:
                        result["refactored"]["priority_id"] = applied_priority_id
                        result["refactored"]["priority_reason"] = (
                            f"{decision.application_rule}; {decision.application_explanation}"
                        )
                        audit["applied_priority_id"] = applied_priority_id
                        audit["changed"] = decision.changed
                        audit["application_rule"] = decision.application_rule
                        audit["application_explanation"] = decision.application_explanation

            json_dump(os.path.join(output_dir, f"jira-analysis-{ts}.json"), priority_audits)
            log(f"Saved Jira priority analysis JSON: jira-analysis-{ts}.json")

        return priority_audits, priority_stage_enabled, priority_run_confirmed, priority_approved

    def _ensure_at_least_one_high_with_best_ac_overlap(self, audits: list[dict]) -> None:
        if not audits:
            return

        eligible: list[dict] = [
            audit for audit in audits
            if isinstance(audit.get("matched_acceptance_criteria_ids"), list)
            and len(audit.get("matched_acceptance_criteria_ids", [])) > 0
        ]
        if not eligible:
            return

        already_high = any(self._to_int(audit.get("proposed_priority_id")) == self.priority_id_high for audit in eligible)
        if already_high:
            return

        best = max(
            eligible,
            key=lambda audit: (
                len(audit.get("matched_acceptance_criteria_ids", [])),
                float(audit.get("selected_story_relevance") or 0.0),
                int(audit.get("case_risk_score") or 0),
                int(audit.get("case_impact_score") or 0),
            ),
        )
        best["proposed_priority_id"] = self.priority_id_high
        current_priority = self._to_int(best.get("current_priority_id"))
        if current_priority is not None:
            best["changed"] = current_priority != self.priority_id_high
        else:
            best["changed"] = True
        reasons = best.get("reasons")
        if not isinstance(reasons, list):
            reasons = []
            best["reasons"] = reasons
        reasons.append("Section guardrail: ensured at least one HIGH case with strongest AC overlap")

    @staticmethod
    def _to_int(value: Any) -> int | None:
        try:
            if value is None or value == "":
                return None
            return int(value)
        except (TypeError, ValueError):
            return None
