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
        priority_id_low: int,
        priority_id_medium: int,
        priority_id_high: int,
    ):
        self._refactor_case = refactor_case
        self._refactored_model_cls = refactored_model_cls
        self._validation_error_cls = validation_error_cls
        self.jira_subagent = jira_subagent
        self.priority_rules_skill = priority_rules_skill
        self.priority_id_low = priority_id_low
        self.priority_id_medium = priority_id_medium
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
            self._rebalance_section_priority_distribution(priority_audits)

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

                self._enforce_mandatory_low_after_rule_application(results)

            json_dump(os.path.join(output_dir, f"jira-analysis-{ts}.json"), priority_audits)
            log(f"Saved Jira priority analysis JSON: jira-analysis-{ts}.json")

        return priority_audits, priority_stage_enabled, priority_run_confirmed, priority_approved

    def _rebalance_section_priority_distribution(self, audits: list[dict]) -> None:
        if not audits:
            return

        normalized_audits = [audit for audit in audits if isinstance(audit, dict)]
        if not normalized_audits:
            return

        def ac_match_count(audit: dict) -> int:
            matches = audit.get("matched_acceptance_criteria_ids")
            return len(matches) if isinstance(matches, list) else 0

        def relevance_score(audit: dict) -> float:
            raw = audit.get("selected_story_relevance")
            try:
                return float(raw or 0.0)
            except (TypeError, ValueError):
                return 0.0

        def risk_score(audit: dict) -> int:
            try:
                return int(audit.get("case_risk_score") or 0)
            except (TypeError, ValueError):
                return 0

        def impact_score(audit: dict) -> int:
            try:
                return int(audit.get("case_impact_score") or 0)
            except (TypeError, ValueError):
                return 0

        def case_id_score(audit: dict) -> int:
            try:
                return int(audit.get("case_id") or 0)
            except (TypeError, ValueError):
                return 0

        ranked_strongest = sorted(
            normalized_audits,
            key=lambda audit: (
                ac_match_count(audit),
                relevance_score(audit),
                risk_score(audit),
                impact_score(audit),
                -case_id_score(audit),
            ),
            reverse=True,
        )
        ranked_weakest = sorted(
            normalized_audits,
            key=lambda audit: (
                ac_match_count(audit),
                relevance_score(audit),
                risk_score(audit),
                impact_score(audit),
                case_id_score(audit),
            ),
        )

        total = len(normalized_audits)
        if total >= 3:
            target_high_count = 2
        elif total == 2:
            target_high_count = 1
        else:
            target_high_count = 0

        low_case = ranked_weakest[0]
        high_cases: list[dict] = []
        for candidate in ranked_strongest:
            if candidate is low_case:
                continue
            high_cases.append(candidate)
            if len(high_cases) >= target_high_count:
                break

        for audit in normalized_audits:
            if audit is low_case:
                target_priority = self.priority_id_low
                distribution_reason = (
                    "Section distribution rule: assigned LOW to the weakest acceptance-criteria match."
                )
                audit["section_forced_low_candidate"] = True
            elif any(audit is high_case for high_case in high_cases):
                target_priority = self.priority_id_high
                distribution_reason = (
                    "Section distribution rule: assigned HIGH to one of the strongest acceptance-criteria matches."
                )
                audit["section_forced_low_candidate"] = False
            else:
                target_priority = self.priority_id_medium
                distribution_reason = (
                    "Section distribution rule: assigned MEDIUM because case is neither strongest nor weakest AC match."
                )
                audit["section_forced_low_candidate"] = False

            audit["proposed_priority_id"] = target_priority
            current_priority = self._to_int(audit.get("current_priority_id"))
            if current_priority is not None:
                audit["changed"] = current_priority != target_priority
            else:
                audit["changed"] = True

            reasons = audit.get("reasons")
            if not isinstance(reasons, list):
                reasons = []
                audit["reasons"] = reasons
            reasons.append(distribution_reason)

    def _enforce_mandatory_low_after_rule_application(self, results: list[dict]) -> None:
        prepared = [result for result in results if result.get("status") == "prepared_only"]
        if not prepared:
            return

        already_has_low = any(
            self._to_int((result.get("refactored") or {}).get("priority_id")) == self.priority_id_low
            for result in prepared
        )
        if already_has_low:
            return

        low_candidate_result = next(
            (
                result
                for result in prepared
                if bool((result.get("priority_audit") or {}).get("section_forced_low_candidate"))
            ),
            None,
        )
        if low_candidate_result is None:
            return

        audit = low_candidate_result.get("priority_audit") or {}
        refactored = low_candidate_result.get("refactored") or {}
        current_priority = self._to_int((low_candidate_result.get("raw_case") or {}).get("priority_id"))

        refactored["priority_id"] = self.priority_id_low
        existing_reason = str(refactored.get("priority_reason") or "").strip()
        guardrail_reason = "Section mandatory LOW guardrail: ensured minimum one LOW case in section"
        if existing_reason:
            refactored["priority_reason"] = f"{existing_reason}; {guardrail_reason}"
        else:
            refactored["priority_reason"] = guardrail_reason
        low_candidate_result["refactored"] = refactored

        audit["applied_priority_id"] = self.priority_id_low
        audit["application_rule"] = "Section mandatory LOW guardrail applied"
        audit["application_explanation"] = (
            "Rules stage removed LOW downgrade, so section-level minimum LOW policy was enforced."
        )
        if current_priority is not None:
            audit["changed"] = current_priority != self.priority_id_low
        else:
            audit["changed"] = True
        reasons = audit.get("reasons")
        if not isinstance(reasons, list):
            reasons = []
            audit["reasons"] = reasons
        reasons.append("Section mandatory LOW guardrail enforced after rule application")
        low_candidate_result["priority_audit"] = audit

    @staticmethod
    def _to_int(value: Any) -> int | None:
        try:
            if value is None or value == "":
                return None
            return int(value)
        except (TypeError, ValueError):
            return None
