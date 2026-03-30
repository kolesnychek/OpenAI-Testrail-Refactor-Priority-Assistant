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
