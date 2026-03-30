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
        priority_id_high=3,
    )


def test_ensures_one_high_for_best_ac_overlap_when_none_high():
    agent = _agent()
    audits = [
        {
            "case_id": 1,
            "proposed_priority_id": 2,
            "matched_acceptance_criteria_ids": ["1"],
            "selected_story_relevance": 0.14,
            "case_risk_score": 1,
            "case_impact_score": 1,
            "reasons": [],
        },
        {
            "case_id": 2,
            "proposed_priority_id": 2,
            "current_priority_id": 2,
            "matched_acceptance_criteria_ids": ["1", "2", "3"],
            "selected_story_relevance": 0.21,
            "case_risk_score": 2,
            "case_impact_score": 2,
            "reasons": [],
        },
    ]

    agent._ensure_at_least_one_high_with_best_ac_overlap(audits)

    assert audits[1]["proposed_priority_id"] == 3
    assert audits[1]["changed"] is True
    assert any("ensured at least one HIGH" in reason for reason in audits[1]["reasons"])


def test_does_not_override_when_high_already_exists():
    agent = _agent()
    audits = [
        {
            "case_id": 1,
            "proposed_priority_id": 3,
            "matched_acceptance_criteria_ids": ["1"],
            "selected_story_relevance": 0.12,
            "case_risk_score": 1,
            "case_impact_score": 1,
            "reasons": [],
        },
        {
            "case_id": 2,
            "proposed_priority_id": 2,
            "matched_acceptance_criteria_ids": ["1", "2", "3", "4"],
            "selected_story_relevance": 0.31,
            "case_risk_score": 3,
            "case_impact_score": 2,
            "reasons": [],
        },
    ]

    agent._ensure_at_least_one_high_with_best_ac_overlap(audits)

    assert audits[0]["proposed_priority_id"] == 3
    assert audits[1]["proposed_priority_id"] == 2
