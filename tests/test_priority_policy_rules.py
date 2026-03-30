import importlib.util
from pathlib import Path


def _load_module():
    module_path = Path(__file__).resolve().parents[1] / "OpenAI-TestRail.py"
    spec = importlib.util.spec_from_file_location("openai_testrail", module_path)
    module = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    spec.loader.exec_module(module)
    return module


def _case(title: str) -> dict:
    return {
        "title": title,
        "steps": [{"action": title, "expected_result": ""}],
        "global_expected_result": "",
    }


def test_rejoin_emergency_mic_case_is_secondary_not_primary():
    mod = _load_module()
    refactored = _case("DU sees mic icon update when VIs mutes/unmutes after DU rejoins 911 Emergency session")
    acceptance_items = [
        {"id": "1", "text": "DU sees mic icon update when VIs mutes/unmutes after DU rejoins 911 Emergency session"}
    ]

    assert mod.detect_rejoin_secondary_case(refactored, acceptance_items) is True
    assert mod.detect_primary_acceptance_case(refactored, acceptance_items, relevance=0.25, acceptance_score=1) is False


def test_main_mic_case_in_portrait_and_landscape_is_primary():
    mod = _load_module()
    refactored = _case("DU sees mic icon change when VIs mutes/unmutes DU in Portrait and Landscape modes")
    acceptance_items = [
        {"id": "1", "text": "DU sees mic icon change when VIs mutes/unmutes DU in Portrait and Landscape modes"}
    ]

    assert mod.detect_rejoin_secondary_case(refactored, acceptance_items) is False
    assert mod.detect_primary_acceptance_case(refactored, acceptance_items, relevance=0.2, acceptance_score=1) is True


def test_ui_or_secondary_without_ac_match_goes_low():
    mod = _load_module()
    assert mod.should_set_low_for_unaligned_ui_or_secondary(
        acceptance_score=0,
        relevance=0.05,
        ui_only_case=True,
        secondary_variant_case=False,
        indirect_regression_case=False,
        non_core_case=False,
    ) is True


def test_regression_with_ac_match_is_not_forced_low():
    mod = _load_module()
    assert mod.should_set_low_for_unaligned_ui_or_secondary(
        acceptance_score=1,
        relevance=0.12,
        ui_only_case=False,
        secondary_variant_case=False,
        indirect_regression_case=True,
        non_core_case=False,
    ) is False


def test_secondary_without_ac_match_goes_low():
    mod = _load_module()
    assert mod.should_set_low_for_unaligned_ui_or_secondary(
        acceptance_score=0,
        relevance=0.03,
        ui_only_case=False,
        secondary_variant_case=True,
        indirect_regression_case=False,
        non_core_case=False,
    ) is False


def test_regression_with_weak_alignment_goes_low():
    mod = _load_module()
    assert mod.should_set_low_for_unaligned_ui_or_secondary(
        acceptance_score=1,
        relevance=0.09,
        ui_only_case=False,
        secondary_variant_case=False,
        indirect_regression_case=True,
        non_core_case=False,
    ) is False
