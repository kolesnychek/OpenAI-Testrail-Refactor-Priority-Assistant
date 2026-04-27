from agents.main_refactor_agent import MainRefactorAgent


class _DummyJiraSubagent:
    name = "jira_ticket_priority_subagent"


class _DummyPrioritySkill:
    name = "priority_rules_skill"


def _agent() -> MainRefactorAgent:
    return MainRefactorAgent(
        refactor_case=lambda _raw: None,  # not used in this test
        refactored_model_cls=dict,
        validation_error_cls=Exception,
        jira_subagent=_DummyJiraSubagent(),
        priority_rules_skill=_DummyPrioritySkill(),
        priority_id_low=1,
        priority_id_medium=2,
        priority_id_high=3,
    )


def test_rebalances_section_to_two_high_one_low_and_rest_medium():
    agent = _agent()
    audits = [
        {
            "case_id": 1,
            "proposed_priority_id": 2,
            "current_priority_id": 2,
            "matched_acceptance_criteria_ids": ["1"],
            "selected_story_relevance": 0.11,
            "case_risk_score": 1,
            "case_impact_score": 1,
            "reasons": [],
        },
        {
            "case_id": 2,
            "proposed_priority_id": 2,
            "current_priority_id": 2,
            "matched_acceptance_criteria_ids": ["1", "2", "3"],
            "selected_story_relevance": 0.29,
            "case_risk_score": 2,
            "case_impact_score": 2,
            "reasons": [],
        },
        {
            "case_id": 3,
            "proposed_priority_id": 2,
            "current_priority_id": 2,
            "matched_acceptance_criteria_ids": [],
            "selected_story_relevance": 0.02,
            "case_risk_score": 0,
            "case_impact_score": 0,
            "reasons": [],
        },
        {
            "case_id": 4,
            "proposed_priority_id": 2,
            "current_priority_id": 2,
            "matched_acceptance_criteria_ids": ["1", "2"],
            "selected_story_relevance": 0.19,
            "case_risk_score": 1,
            "case_impact_score": 2,
            "reasons": [],
        },
    ]

    agent._rebalance_section_priority_distribution(audits)

    by_case_id = {audit["case_id"]: audit for audit in audits}
    assert by_case_id[2]["proposed_priority_id"] == 3
    assert by_case_id[4]["proposed_priority_id"] == 3
    assert by_case_id[3]["proposed_priority_id"] == 1
    assert by_case_id[1]["proposed_priority_id"] == 2

    assert any("assigned HIGH" in reason for reason in by_case_id[2]["reasons"])
    assert any("assigned LOW" in reason for reason in by_case_id[3]["reasons"])
    assert any("assigned MEDIUM" in reason for reason in by_case_id[1]["reasons"])


def test_two_case_section_gets_one_high_and_one_low():
    agent = _agent()
    audits = [
        {
            "case_id": 10,
            "proposed_priority_id": 2,
            "current_priority_id": 2,
            "matched_acceptance_criteria_ids": ["1", "2"],
            "selected_story_relevance": 0.24,
            "case_risk_score": 2,
            "case_impact_score": 2,
            "reasons": [],
        },
        {
            "case_id": 11,
            "proposed_priority_id": 2,
            "current_priority_id": 2,
            "matched_acceptance_criteria_ids": [],
            "selected_story_relevance": 0.01,
            "case_risk_score": 0,
            "case_impact_score": 0,
            "reasons": [],
        },
    ]

    agent._rebalance_section_priority_distribution(audits)

    by_case_id = {audit["case_id"]: audit for audit in audits}
    assert by_case_id[10]["proposed_priority_id"] == 3
    assert by_case_id[11]["proposed_priority_id"] == 1


def test_enforces_minimum_one_low_after_rule_application():
    agent = _agent()
    results = [
        {
            "status": "prepared_only",
            "raw_case": {"priority_id": 2},
            "refactored": {"priority_id": 3, "priority_reason": "rule kept high"},
            "priority_audit": {
                "case_id": 20,
                "section_forced_low_candidate": True,
                "reasons": [],
            },
        },
        {
            "status": "prepared_only",
            "raw_case": {"priority_id": 2},
            "refactored": {"priority_id": 2, "priority_reason": "rule kept medium"},
            "priority_audit": {
                "case_id": 21,
                "section_forced_low_candidate": False,
                "reasons": [],
            },
        },
    ]

    agent._enforce_mandatory_low_after_rule_application(results)

    low_case = results[0]
    assert low_case["refactored"]["priority_id"] == 1
    assert "Section mandatory LOW guardrail" in low_case["refactored"]["priority_reason"]
    assert low_case["priority_audit"]["applied_priority_id"] == 1
    assert low_case["priority_audit"]["application_rule"] == "Section mandatory LOW guardrail applied"
