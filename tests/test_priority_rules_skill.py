from agents.skills.priority_rules_skill import PriorityRulesSkill


def test_recalculates_any_source_to_low_medium_high_with_strong_story_evidence():
    skill = PriorityRulesSkill(priority_id_medium=2)
    priorities = [1, 2, 3]  # LOW, MEDIUM, HIGH

    for source in priorities:
        for target in priorities:
            decision = skill.resolve(
                source_priority_id=source,
                proposed_priority_id=target,
                matched_acceptance_criteria_ids=["1"],
                story_key="ABC-100",
                analyzer_reasons=["story evidence present"],
            )
            assert decision.applied_priority_id == target
            assert decision.changed is (source != target)
            assert "strong evidence" in decision.application_rule


def test_recalculates_non_medium_when_story_evidence_exists():
    skill = PriorityRulesSkill(priority_id_medium=2)
    decision = skill.resolve(
        source_priority_id=3,
        proposed_priority_id=1,
        matched_acceptance_criteria_ids=["1"],
        story_key="ABC-101",
        analyzer_reasons=["rule"],
    )

    assert decision.applied_priority_id == 1
    assert decision.changed is True
    assert "story-based recalculation" in decision.application_rule


def test_recalculates_medium_using_acceptance_criteria():
    skill = PriorityRulesSkill(priority_id_medium=2)
    decision = skill.resolve(
        source_priority_id=2,
        proposed_priority_id=3,
        matched_acceptance_criteria_ids=["2", "4"],
        story_key="ABC-102",
        analyzer_reasons=["matched core AC"],
    )

    assert decision.applied_priority_id == 3
    assert decision.changed is True
    assert "story-based recalculation" in decision.application_rule
    assert "2, 4" in decision.application_explanation


def test_keeps_medium_when_no_acceptance_criteria_evidence():
    skill = PriorityRulesSkill(priority_id_medium=2)
    decision = skill.resolve(
        source_priority_id=2,
        proposed_priority_id=3,
        matched_acceptance_criteria_ids=[],
        story_key="ABC-103",
        analyzer_reasons=["insufficient evidence"],
    )

    assert decision.applied_priority_id == 2
    assert decision.changed is False
    assert "missing AC evidence" in decision.application_rule


def test_recalculates_medium_down_to_low_when_story_evidence_exists():
    skill = PriorityRulesSkill(priority_id_medium=2)
    decision = skill.resolve(
        source_priority_id=2,
        proposed_priority_id=1,
        matched_acceptance_criteria_ids=["3"],
        story_key="ABC-105",
        analyzer_reasons=["secondary/indirect path"],
    )

    assert decision.applied_priority_id == 1
    assert decision.changed is True
    assert "strong evidence" in decision.application_rule


def test_recalculates_medium_up_to_critical_when_story_evidence_exists():
    skill = PriorityRulesSkill(priority_id_medium=2)
    decision = skill.resolve(
        source_priority_id=2,
        proposed_priority_id=4,
        matched_acceptance_criteria_ids=["1", "2", "3"],
        story_key="ABC-106",
        analyzer_reasons=["severe path strongly covered by AC"],
    )

    assert decision.applied_priority_id == 4
    assert decision.changed is True
    assert "strong evidence" in decision.application_rule


def test_blocks_critical_escalation_with_single_ac_match():
    skill = PriorityRulesSkill(priority_id_medium=2)
    decision = skill.resolve(
        source_priority_id=3,
        proposed_priority_id=4,
        matched_acceptance_criteria_ids=["1"],
        story_key="ABC-108",
        analyzer_reasons=["possible severe scenario"],
    )

    assert decision.applied_priority_id == 3
    assert decision.changed is False
    assert "Blocked CRITICAL escalation" in decision.application_rule


def test_keeps_non_medium_when_no_story_evidence():
    skill = PriorityRulesSkill(priority_id_medium=2)
    decision = skill.resolve(
        source_priority_id=4,
        proposed_priority_id=2,
        matched_acceptance_criteria_ids=[],
        story_key="ABC-104",
        analyzer_reasons=["insufficient evidence"],
    )

    assert decision.applied_priority_id == 4
    assert decision.changed is False
    assert "insufficient validated evidence" in decision.application_rule


def test_keeps_all_sources_when_no_story_evidence():
    skill = PriorityRulesSkill(priority_id_medium=2)
    priorities = [1, 2, 3, 4]

    for source in priorities:
        decision = skill.resolve(
            source_priority_id=source,
            proposed_priority_id=4 if source != 4 else 1,
            matched_acceptance_criteria_ids=[],
            story_key="ABC-107",
            analyzer_reasons=["insufficient evidence"],
        )
        assert decision.applied_priority_id == source
        assert decision.changed is False


def test_does_not_apply_story_recalculation_without_story_key():
    skill = PriorityRulesSkill(priority_id_medium=2)
    decision = skill.resolve(
        source_priority_id=1,
        proposed_priority_id=3,
        matched_acceptance_criteria_ids=["1", "2"],
        story_key="",
        analyzer_reasons=["matched core AC"],
    )

    assert decision.applied_priority_id == 1
    assert decision.changed is False
