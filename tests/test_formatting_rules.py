import importlib.util
from pathlib import Path


def _load_module():
    module_path = Path(__file__).resolve().parents[1] / "OpenAI-TestRail.py"
    spec = importlib.util.spec_from_file_location("openai_testrail", module_path)
    module = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    spec.loader.exec_module(module)
    return module


def test_preconditions_keep_env_users_as_subitems():
    mod = _load_module()
    source = (
        "1. Have a set up shift in Teamwork for the current day and be clocked in when the shift starts.\n"
        "2. Have a VI User\n"
        "ENV: https://qa.convorelay-itc.click/\n"
        "USERS: https://example.com/users\n"
        "3. Figma design -> https://figma.example.com/file"
    )
    out = mod.normalize_preconditions(source)
    assert "2. Have a VI User" in out
    assert "   1) [ENV](" in out
    assert "   2) [USERS](" in out


def test_preconditions_force_uppercase_first_letter_for_each_item():
    mod = _load_module()
    source = (
        "log in as VI #1 and V #2 (creds here)\n"
        "as a VI #1 => go to Available at the 1st place\n"
        "as a VI #2 => go to Available at the 2nd place\n"
        "log in as DU (creds here)\n"
        "as DU place a call to HU"
    )
    out = mod.normalize_preconditions(source)
    assert "1. Log in as VI #1 and V #2 (creds here)" in out
    assert "2. As a VI #1 => go to Available at the 1st place" in out
    assert "3. As a VI #2 => go to Available at the 2nd place" in out
    assert "4. Log in as DU (creds here)" in out
    assert "5. As DU place a call to HU" in out


def test_merge_original_images_deduplicates_identical_markdown_image_urls():
    mod = _load_module()
    original = (
        "Expected result\n"
        "![](https://example.com/attachments/get/123)\n"
        "![](https://example.com/attachments/get/123)\n"
    )
    refactored = "User sees updated mic icon."
    out = mod.merge_original_images(refactored, original)
    assert out.count("![](https://example.com/attachments/get/123)") == 1


def test_existing_bold_is_preserved_in_expected_result():
    mod = _load_module()
    out = mod.normalize_expected_result_text('User taps **Mute** button and sees "**Connected**" status')
    assert "**Mute**" in out
    assert '"**Connected**"' in out


def test_button_and_icon_labels_are_bolded():
    mod = _load_module()
    out = mod.normalize_expected_result_text("User sees Mute button and audio icon is grey")
    assert "**Mute** button" in out
    assert "**audio** icon" in out


def test_preconditions_preserve_numbering_and_bullets_shape():
    mod = _load_module()
    source = (
        "1. Log in as VI #1 and V #2 (creds here)\n"
        "- As a VI #1 => go to Available at the 1st place\n"
        "- As a VI #2 => go to Available at the 2nd place\n"
        "2. Log in as DU (creds here)\n"
        "3. As DU place a call to HU"
    )
    out = mod.normalize_preconditions(source)
    assert "1. Log in as VI #1 and V #2" in out
    assert "- As a VI #1 => go to Available at the 1st place" in out
    assert "- As a VI #2 => go to Available at the 2nd place" in out
    assert "2. Log in as DU" in out
    assert "3. As DU place a call to HU" in out


def test_expected_preserves_multiline_1_paren_structure():
    mod = _load_module()
    source = "1) Portrait mode:\nфото\n2) Landscape mode:\nфото"
    out = mod.normalize_expected_result_text(source)
    assert "  1) Portrait mode:" in out
    assert "  2) Landscape mode:" in out
    assert "фото" in out
    assert "\n" in out


def test_expected_puts_parenthesized_items_on_new_lines_after_colon_and_pairs_media():
    mod = _load_module()
    source = (
        "All parties can hear each other\n"
        "DU (Deaf User) sees an audio icon is green: 1) Portrait mode:\n"
        "2) Landscape mode:\n"
        "фото\n"
        "фото"
    )
    out = mod.normalize_expected_result_text(source)
    assert "DU (Deaf User) sees an **audio** icon is green:" in out
    assert "  1) Portrait mode:\nфото\n  2) Landscape mode:\nфото" in out


def test_preconditions_continuation_line_continues_numbering():
    mod = _load_module()
    source = (
        "1. Log in as VI #1 and V #2 (creds here)\n"
        "As a VI #1 => go to Available at the 1st place\n"
        "As a VI #2 => go to Available at the 2nd place\n"
        "\n"
        "2. Log in as DU (creds here)\n"
        "3. As DU place a call to HU"
    )
    out = mod.normalize_preconditions(source)
    assert "1. Log in as VI #1 and V #2" in out
    assert "2. As a VI #1 => go to Available at the 1st place" in out
    assert "3. As a VI #2 => go to Available at the 2nd place" in out
    assert "4. Log in as DU" in out
    assert "5. As DU place a call to HU" in out


def test_preconditions_with_indented_lines_still_continue_numbering():
    mod = _load_module()
    source = (
        "1. Log in as VI #1 and V #2 (creds here)\n"
        " As a VI #1 => go to Available at the 1st place\n"
        " As a VI #2 => go to Available at the 2nd place\n"
        "2. Log in as DU (creds here)\n"
        "3. As DU place a call to HU"
    )
    out = mod.normalize_preconditions(source)
    assert "1. Log in as VI #1 and V #2" in out
    assert "2. As a VI #1 => go to Available at the 1st place" in out
    assert "3. As a VI #2 => go to Available at the 2nd place" in out
    assert "4. Log in as DU" in out
    assert "5. As DU place a call to HU" in out


def test_step_action_is_converted_to_imperative():
    mod = _load_module()
    out = mod.normalize_step_action("The user clicks on Save button")
    assert out.startswith("Click")


def test_roles_not_expanded_in_preconditions_and_steps():
    mod = _load_module()
    pre = mod.normalize_preconditions("1. Log in as DU and call HU")
    step = mod.normalize_step_action("As DU clicks on Call button")
    assert "(Deaf User)" not in pre
    assert "(Hearing User)" not in pre
    assert "(Deaf User)" not in step
    assert "(Hearing User)" not in step


def test_roles_expanded_in_expected_results():
    mod = _load_module()
    out = mod.normalize_expected_result_text("DU sees HU on the call screen")
    assert "DU (Deaf User)" in out
    assert "HU (Hearing User)" in out


def test_extract_acceptance_criteria_from_acceptance_block_bullets():
    mod = _load_module()
    text = "Acceptance Criteria\n- User can open settings\n- User sees save button"
    items = mod.extract_acceptance_criteria_items(text)
    ids = [item["id"] for item in items]
    assert ids == ["1", "2"]


def test_step_action_preserves_leading_du_role():
    mod = _load_module()
    out = mod.normalize_step_action("As DU tap on Call button")
    assert out.startswith("As DU")


def test_preconditions_qA_environment_links_only_environment_word():
    mod = _load_module()
    source = "1. QA environment -> https://qa.example.com"
    out = mod.normalize_preconditions(source)
    assert "[environment](https://qa.example.com)" in out
    assert "[QA environment](https://qa.example.com)" not in out


def test_refactor_case_splits_alternating_steps_and_moves_global_expected_to_last_step():
    mod = _load_module()
    raw_case = {
        "title": "Team support and transfer flow",
        "custom_preconds": "LLL is the lead VI\nSSS is the support VI\nNN1 is next VI\n2 Voice numbers are needed",
        "custom_steps": (
            "Convo User calls voice number\n"
            "Vi #LLL answers the call\n"
            "and VI #LLL calls to the voice number written in dialpad\n"
            "and hearing User answers the call\n"
            "VI #LLL calls vco-3 way\n"
            "and VCO-3 way answers the call\n"
            "VI #LLL clicks on team support button\n"
            "and VI #SSS answers the team request\n"
            "and VI #SSS clicks on ready button"
        ),
        "custom_expected": (
            "Then support VI is hidden(cannot be heard) for voice(hearing user), convo user and vco-3way(hearing user), and support VI can hear everybody\n"
            "and support VI can see customer, but customer cannot see support VI\n"
            "and lead vi can hear everybody, and lead VI can see customer and customer can see lead VI\n"
            "and chat is working"
        ),
    }

    out = mod.refactor_case_locally(raw_case)
    steps = out["steps"]

    assert len(steps) == 5
    assert steps[0]["action"] == "Convo User calls voice number"
    assert "answers the call" in steps[0]["expected_result"]
    assert not steps[1]["action"].lower().startswith("and ")
    assert "1. Support VI" in steps[-1]["expected_result"]
    assert "4. Chat is working" in steps[-1]["expected_result"]
    assert out["global_expected_result"] == ""


def test_detect_template_forces_steps_even_for_steps_separated_source():
    mod = _load_module()
    case = {"custom_steps_separated": [{"content": "A", "expected": "B"}]}
    assert mod.detect_template(case) == "steps_separated"


def test_build_payload_in_steps_separated_template_uses_custom_steps_separated():
    mod = _load_module()
    ai_result = {
        "title": "Refactored",
        "preconditions": "1. P",
        "steps": [
            {"action": "DU calls voice number", "expected_result": "VI #LLL answers the call"},
            {"action": "VI #SSS clicks on ready button", "expected_result": "1. Chat is working"},
        ],
        "global_expected_result": "",
    }
    payload = mod.build_testrail_payload(ai_result, "steps_separated")
    assert "custom_steps_separated" in payload
    assert "custom_steps" not in payload
    assert "custom_expected" not in payload
    assert payload["custom_steps_separated"][0]["content"] == "DU calls voice number"
    assert payload["custom_steps_separated"][0]["expected"] == "VI #LLL answers the call"


def test_refactor_case_pairs_place_call_with_watch_radar_expected():
    mod = _load_module()
    raw_case = {
        "title": "911 flow",
        "custom_preconds": "1. Preconditions",
        "custom_steps": (
            "Place a 911 call from Convo Link to Convo Interpreter\n"
            "watch radar when the call is answered\n"
            "and VI #SSS clicks on ready button"
        ),
        "custom_expected": "and chat is working",
    }

    out = mod.refactor_case_locally(raw_case)
    steps = out["steps"]

    assert len(steps) >= 2
    assert steps[0]["action"].lower().startswith("place a 911 call")
    assert "watch radar" in steps[0]["expected_result"].lower()
    assert "answered" in steps[0]["expected_result"].lower()
    assert not steps[1]["action"].lower().startswith("and ")


def test_tail_expected_keeps_photo_unumbered_and_numbers_text_only():
    mod = _load_module()
    raw_case = {
        "title": "Photo in final expected",
        "custom_preconds": "1. Preconditions",
        "custom_steps": "VI clicks on ready button",
        "custom_expected": "support VI can hear everybody\nphoto\nchat is working",
    }

    out = mod.refactor_case_locally(raw_case)
    last_expected = out["steps"][-1]["expected_result"]

    assert "1. Support VI" in last_expected
    assert "\nphoto\n" in f"\n{last_expected}\n"
    assert "2. Chat is working" in last_expected
    assert "2. photo" not in last_expected
