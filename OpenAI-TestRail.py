import asyncio
import json
import os
import re
from collections import Counter
from datetime import datetime, timezone

import aiohttp
from dotenv import load_dotenv
from pydantic import BaseModel, ConfigDict, Field, ValidationError
from tenacity import retry, stop_after_attempt, wait_exponential
from agents.main_refactor_agent import MainRefactorAgent
from agents.skills.priority_rules_skill import PriorityRulesSkill
from agents.subagents.jira_ticket_priority_subagent import JiraTicketPrioritySubagent
from reports.execution_report import build_execution_report, render_execution_report_markdown


class TestCaseStep(BaseModel):
    model_config = ConfigDict(extra="forbid")

    action: str = Field(description="The test step action.")
    expected_result: str = Field(default="", description="Expected result for this step.")


class RefactoredTestCase(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title: str
    preconditions: str
    steps: list[TestCaseStep]
    global_expected_result: str = ""
    violations: list[str] = Field(default_factory=list)
    priority_id: int | None = None
    priority_reason: str = ""


load_dotenv()

required_vars = [
    "TESTRAIL_URL",
    "TESTRAIL_EMAIL",
    "TESTRAIL_API_KEY",
    "TESTRAIL_SECTION_ID",
]
for var in required_vars:
    if not os.getenv(var):
        raise ValueError(f"Env var {var} is not set!")

TESTRAIL_URL = os.getenv("TESTRAIL_URL")
TESTRAIL_EMAIL = os.getenv("TESTRAIL_EMAIL")
TESTRAIL_API_KEY = os.getenv("TESTRAIL_API_KEY")
TESTRAIL_PROJECT_ID = os.getenv("TESTRAIL_PROJECT_ID")
SECTION_ID = int(os.getenv("TESTRAIL_SECTION_ID"))

REQUIRE_MANUAL_APPROVAL = os.getenv("REQUIRE_MANUAL_APPROVAL", "yes").lower() in {"1", "true", "yes", "y"}
USE_ALL_CASES_IN_SECTION = os.getenv("USE_ALL_CASES_IN_SECTION", "no").lower() in {"1", "true", "yes", "y"}
CREATE_IN_TESTRAIL = os.getenv("CREATE_IN_TESTRAIL", "yes").lower() in {"1", "true", "yes", "y"}
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
OUTPUT_DIR_RAW = os.getenv("OUTPUT_DIR", "./output").strip()
OUTPUT_DIR = OUTPUT_DIR_RAW if os.path.isabs(OUTPUT_DIR_RAW) else os.path.join(SCRIPT_DIR, OUTPUT_DIR_RAW)
WRITE_MODE = "create"
PRIORITY_ANALYZER_MODE = os.getenv("PRIORITY_ANALYZER_MODE", "auto").strip().lower()
PRIORITY_DEFAULT_ID = os.getenv("PRIORITY_DEFAULT_ID", "").strip()
ENABLE_EPIC_ANALYSIS = os.getenv("ENABLE_EPIC_ANALYSIS", "no").lower() in {"1", "true", "yes", "y"}
ENABLE_PR_ANALYSIS = os.getenv("ENABLE_PR_ANALYSIS", "no").lower() in {"1", "true", "yes", "y"}
COPY_SOURCE_PRIORITY = os.getenv("COPY_SOURCE_PRIORITY", "yes").lower() in {"1", "true", "yes", "y"}
JIRA_BASE_URL = os.getenv("JIRA_BASE_URL", "").strip().rstrip("/")
JIRA_USER_EMAIL = os.getenv("JIRA_USER_EMAIL", "").strip()
JIRA_API_TOKEN = os.getenv("JIRA_API_TOKEN", "").strip()
JIRA_BEARER_TOKEN = os.getenv("JIRA_BEARER_TOKEN", "").strip()
_JIRA_ACCEPTANCE_FIELD_IDS: list[str] | None = None
_JIRA_ACCEPTANCE_FIELDS_META: list[dict] | None = None

PRIORITY_ID_LOW = int(os.getenv("PRIORITY_ID_LOW", "1"))
PRIORITY_ID_MEDIUM = int(os.getenv("PRIORITY_ID_MEDIUM", "2"))
PRIORITY_ID_HIGH = int(os.getenv("PRIORITY_ID_HIGH", "3"))
PRIORITY_ID_CRITICAL = int(os.getenv("PRIORITY_ID_CRITICAL", "4"))
VERBOSE_TERMINAL = os.getenv("VERBOSE_TERMINAL", "no").lower() in {"1", "true", "yes", "y"}

if PRIORITY_ANALYZER_MODE not in {"auto", "yes", "no"}:
    raise ValueError("PRIORITY_ANALYZER_MODE must be one of: auto | yes | no")


def log(message: str):
    if VERBOSE_TERMINAL:
        print(message)


log("Config loaded OK")


def safe_str(value):
    return (value or "").strip()


def load_prompt(path: str) -> str:
    base = os.path.dirname(os.path.abspath(__file__))
    with open(os.path.join(base, path), encoding="utf-8") as f:
        return f.read()


REFACTOR_RULES_TEXT = load_prompt("prompts/refactoring_rules.md")
BUSINESS_LOGIC_TEXT = load_prompt("prompts/business_logic.md")
MAIN_AGENT_TEXT = load_prompt("prompts/agent_main_refactor.md")
PRIORITY_AGENT_TEXT = load_prompt("prompts/agent_priority_audit.md")
PRIORITY_RULES_SKILL_TEXT = load_prompt("prompts/agent_priority_rules_skill.md")


def ensure_output_dir() -> str:
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    return OUTPUT_DIR


def json_dump(path: str, payload: dict | list):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)


def json_load(path: str):
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def safe_int(value, default: int | None = None) -> int | None:
    try:
        if value is None or value == "":
            return default
        return int(value)
    except (TypeError, ValueError):
        return default


def priority_name(priority_id: int | None) -> str:
    mapping = {
        PRIORITY_ID_LOW: "LOW",
        PRIORITY_ID_MEDIUM: "MEDIUM",
        PRIORITY_ID_HIGH: "HIGH",
        PRIORITY_ID_CRITICAL: "CRITICAL",
    }
    if priority_id is None:
        return "N/A"
    return mapping.get(priority_id, str(priority_id))


def jira_auth_config() -> tuple[aiohttp.BasicAuth | None, dict]:
    if JIRA_BEARER_TOKEN:
        return None, {"Authorization": f"Bearer {JIRA_BEARER_TOKEN}"}
    if JIRA_USER_EMAIL and JIRA_API_TOKEN:
        return aiohttp.BasicAuth(JIRA_USER_EMAIL, JIRA_API_TOKEN), {}
    return None, {}


ABBREVIATION_EXPANSIONS = {
    "DU": "Deaf User",
    "HU": "Hearing User",
    "VI": "Video Interpreter",
    "VRS": "Video Relay Service",
    "VRI": "Video Remote Interpreting",
    "TDN": "Ten Digit Number",
    "URD": "User Registration Database",
    "URDID": "identifier assigned by RL after successful URD registration",
    "EOS": "End of Shift",
}
ACTIVE_ABBREVIATION_EXPANSIONS = dict(ABBREVIATION_EXPANSIONS)


def protect_links_and_urls(text: str) -> tuple[str, list[str]]:
    if not text:
        return "", []

    placeholders: list[str] = []
    markdown_image_pattern = re.compile(r"!\[[^\]]*\]\([^)]+\)")
    markdown_link_pattern = re.compile(r"\[[^\]]+\]\(https?://[^\s)]+\)")
    raw_url_pattern = re.compile(r"https?://[^\s)]+")

    def stash(match: re.Match) -> str:
        placeholders.append(match.group(0))
        return f"@@URL{len(placeholders)-1}@@"

    protected = markdown_image_pattern.sub(stash, text)
    protected = markdown_link_pattern.sub(stash, protected)
    protected = raw_url_pattern.sub(stash, protected)
    return protected, placeholders


def protect_markdown_bold(text: str) -> tuple[str, list[str]]:
    if not text:
        return "", []

    placeholders: list[str] = []
    pattern = re.compile(r"\*\*[^*\n]+?\*\*")

    def stash(match: re.Match) -> str:
        placeholders.append(match.group(0))
        return f"@@BOLD{len(placeholders)-1}@@"

    return pattern.sub(stash, text), placeholders


def restore_placeholders(text: str, placeholders: list[str], token: str = "URL") -> str:
    out = text
    for idx, original in enumerate(placeholders):
        out = out.replace(f"@@{token}{idx}@@", original)
    return out


def parse_abbreviations_from_text(text: str) -> dict[str, str]:
    if not text:
        return {}
    found: dict[str, str] = {}
    for line in re.split(r"\r?\n", text):
        match = re.match(r'^\s*([A-Za-z][A-Za-z0-9]{1,15})\s*=\s*(.+?)\s*$', line.strip())
        if not match:
            continue
        abbr = match.group(1).upper()
        meaning = match.group(2).strip().rstrip(".")
        if meaning:
            found[abbr] = meaning
    return found


def apply_section_abbreviations(section_description: str):
    parsed = parse_abbreviations_from_text(section_description or "")
    if not parsed:
        return
    ACTIVE_ABBREVIATION_EXPANSIONS.update(parsed)
    log(f"Loaded section abbreviations: {', '.join(sorted(parsed.keys()))}")


def expand_abbreviations_first_use(text: str) -> str:
    out = text or ""
    markdown_link_pattern = re.compile(r"\[[^\]]+\]\(https?://[^\s)]+\)")
    placeholders: list[str] = []

    def stash(match: re.Match) -> str:
        placeholders.append(match.group(0))
        return f"@@MDLINK{len(placeholders)-1}@@"

    out = markdown_link_pattern.sub(stash, out)
    for abbr, full in ACTIVE_ABBREVIATION_EXPANSIONS.items():
        if abbr in {"VI", "HU", "DU"}:
            pattern = re.compile(rf"\b{re.escape(abbr)}\b(?!\s*\()(?!(\s+user\b))", flags=re.IGNORECASE)
        else:
            pattern = re.compile(rf"\b{re.escape(abbr)}\b(?!\s*\()", flags=re.IGNORECASE)
        out, count = pattern.subn(f"{abbr} ({full})", out, count=1)
        if count:
            continue

    for idx, original in enumerate(placeholders):
        out = out.replace(f"@@MDLINK{idx}@@", original)
    return out


def improve_clarity(text: str) -> str:
    out = text or ""
    # Light grammar/style normalization without changing business meaning.
    out = re.sub(r"\b[Yy]ou should\b", "", out)
    out = re.sub(r"\b[Pp]lease\b", "", out)
    out = re.sub(r"\b[Tt]he user should\b", "User", out)
    out = re.sub(r"\b[Yy]ou\b", "User", out)
    out = re.sub(r"[ \t]{2,}", " ", out)
    return out.strip()


def normalize_common_grammar(text: str) -> str:
    out = text or ""
    replacements = [
        (r"\b[Cc]an not\b", "cannot"),
        (r"\blog ?in\b", "log in"),
        (r"\bset[\s-]?up\b", "set up"),
        (r"\bteh\b", "the"),
        (r"\bwich\b", "which"),
        (r"\bthier\b", "their"),
        (r"\brecieve\b", "receive"),
        (r"\bmak\s+sre\b", "make sure"),
        (r"\bmake\s+shure\b", "make sure"),
        (r"\bteat\s+case\b", "test case"),
        (r"\bobser\b", "observe"),
        (r"\bmins\b", "minutes"),
        (r"\bshfit\b", "shift"),
        (r"\bsome\s+times\b", "sometimes"),
        (r"\buser\s+see\b", "User sees"),
        (r"\b1\s+minutes\b", "1 minute"),
        (r"\bsucces(s|ful|fully)?\b", "successful"),
        (r"\bdoesnt\b", "does not"),
        (r"\bdont\b", "do not"),
    ]
    for pattern, repl in replacements:
        out = re.sub(pattern, repl, out, flags=re.IGNORECASE)
    out = re.sub(r"[ \t]+([,.;:!?])", r"\1", out)
    out = re.sub(r"([.?!])([A-Za-z])", r"\1 \2", out)
    # Keep original line breaks; compress only in-line spaces/tabs.
    out = re.sub(r"[ \t]{2,}", " ", out)
    out = re.sub(r"\n{3,}", "\n\n", out)
    return out.strip()


def expand_common_short_forms(text: str) -> str:
    out = text or ""
    out = re.sub(r"\b(\d+)\s*m\b", r"\1 minutes", out, flags=re.IGNORECASE)
    out = re.sub(r"\b(\d+)\s*min\b", r"\1 minutes", out, flags=re.IGNORECASE)
    out = re.sub(r"\b(\d+)\s*mins\b", r"\1 minutes", out, flags=re.IGNORECASE)
    out = re.sub(r"\beos\s*-\s*end\s+of\s+shift\b", "EOS (End of Shift)", out, flags=re.IGNORECASE)
    return out


def split_inline_note(text: str) -> tuple[str, str | None]:
    if not text:
        return "", None

    wrapped = re.search(r"(?is)_\s*note\s*:\s*(.*?)_", text)
    if wrapped:
        note = wrapped.group(1).strip()
        main = (text[: wrapped.start()] + " " + text[wrapped.end() :]).strip(" -\t_")
        note = note.replace("\n_", " ").replace("_\n", " ")
        note = re.sub(r"[_\s-]+$", "", note).strip()
        return main.strip(), note.strip()

    parts = re.split(r"(?i)_?note\s*:\s*", text, maxsplit=1)
    if len(parts) < 2:
        return text.strip(), None
    main = parts[0].strip(" -\t_")
    note = parts[1].strip().strip("_").strip()
    note = note.replace("\n_", " ").replace("_\n", " ")
    note = re.sub(r"[_\s-]+$", "", note).strip()
    return main, note


def render_note_block(note: str | None) -> str:
    if not note:
        return ""
    return f"_NOTE: {note}_"


def choose_link_anchor(prefix: str) -> tuple[str | None, int | None]:
    token_matches = list(re.finditer(r"[A-Za-z0-9][A-Za-z0-9_/-]*", prefix))
    if not token_matches:
        return None, None

    last = token_matches[-1]
    last_word = last.group(0)
    last_start = last.start()

    # Prefer meaningful in-text anchors (e.g. "here", "VI user") instead of generic "Link".
    if last_word.lower() == "link" and len(token_matches) >= 2:
        prev = token_matches[-2]
        return prev.group(0), prev.start()

    if len(token_matches) >= 2:
        prev = token_matches[-2]
        pair = f"{prev.group(0)} {last_word}"
        if last_word.lower() in {"user", "card", "details", "view", "contact", "page"}:
            return pair, prev.start()
        if prev.group(0).isupper():
            return pair, prev.start()

    return last_word, last_start


def linkify_urls_with_context(text: str) -> str:
    if not text:
        return ""

    markdown_link_pattern = re.compile(r"\[[^\]]+\]\(https?://[^\s)]+\)")
    placeholders: list[str] = []

    def stash(match: re.Match) -> str:
        placeholders.append(match.group(0))
        return f"@@MDLINK{len(placeholders)-1}@@"

    protected = markdown_link_pattern.sub(stash, text)
    url_pattern = re.compile(r"https?://[^\s)]+")
    lines = protected.splitlines()

    for i, line in enumerate(lines):
        matches = list(url_pattern.finditer(line))
        if not matches:
            continue

        for m in reversed(matches):
            url = m.group(0)
            prefix = line[: m.start()]
            suffix = line[m.end() :]
            anchor, anchor_start = choose_link_anchor(prefix)

            if anchor and anchor_start is not None:
                line = prefix[:anchor_start] + f"[{anchor}]({url})" + suffix
            else:
                line = prefix + f"[{url}]({url})" + suffix
        lines[i] = line

    restored = "\n".join(lines)
    for idx, original in enumerate(placeholders):
        restored = restored.replace(f"@@MDLINK{idx}@@", original)
    return restored


def linkify_arrow_urls_short(text: str) -> str:
    if not text:
        return ""
    out = text
    pattern = re.compile(r"(?P<prefix>.+?)\s*->\s*(?P<url>https?://[^\s]+)")
    lines = out.splitlines()
    for i, line in enumerate(lines):
        m = pattern.search(line)
        if not m:
            continue
        prefix = m.group("prefix").rstrip(" .,-")
        url = m.group("url")
        anchor, anchor_start = choose_link_anchor(prefix)
        if anchor and anchor_start is not None:
            replaced = prefix[:anchor_start] + f"[{anchor}]({url})"
        else:
            replaced = f"[Link]({url})"
        lines[i] = replaced
    return "\n".join(lines)


def normalize_text(text: str, *, expand_roles: bool = False) -> str:
    converted = convert_html_links_to_markdown(text or "")
    converted = convert_html_images_to_markdown(converted)
    converted = normalize_attachment_urls(converted)
    protected, placeholders = protect_links_and_urls(converted)
    protected, bold_placeholders = protect_markdown_bold(protected)
    protected = improve_clarity(protected)
    protected = normalize_common_grammar(protected)
    protected = expand_common_short_forms(protected)
    if expand_roles:
        protected = expand_abbreviations_first_use(protected)
    # Re-fix attachment URL shape if grammar spacing touched it.
    protected = normalize_attachment_urls(protected)
    protected = restore_placeholders(protected, bold_placeholders, token="BOLD")
    return restore_placeholders(protected, placeholders, token="URL")


def normalize_text_with_links(text: str, *, expand_roles: bool = False) -> str:
    main, note = split_inline_note(normalize_text(text, expand_roles=expand_roles))
    main = linkify_urls_with_context(main)
    if note:
        return (main + "\n\n" + render_note_block(note)).strip()
    return main.strip()


def place_images_on_new_line(text: str) -> str:
    if not text:
        return ""

    out = text
    # Markdown images: move inline image to a new line.
    out = re.sub(r"\s+(!\[[^\]]*\]\([^)]+\))", r"\n\1", out)
    # HTML image tags: move inline image to a new line.
    out = re.sub(r"\s+(<img\b[^>]*>)", r"\n\1", out, flags=re.IGNORECASE)
    # Plain image markers used in legacy steps/results.
    out = re.sub(r"\s+(Image\s+[^\n]+)$", r"\n\1", out, flags=re.IGNORECASE)
    return out


def bold_ui_elements(text: str) -> str:
    if not text:
        return ""

    def repl(match: re.Match) -> str:
        verb = match.group("verb").capitalize()
        label = match.group("label").strip().strip("\"")
        return f"{verb} on **{label}** button"

    pattern = re.compile(
        r"(?i)\b(?P<verb>click|tap|press)\s+(?:on\s+)?(?P<label>\"?[A-Za-z0-9][A-Za-z0-9 _/\-]{0,60}\"?)\s+button\b"
    )
    return pattern.sub(repl, text)


def bold_quoted_ui_labels(text: str) -> str:
    if not text:
        return ""

    out = text

    def should_bold(label: str) -> bool:
        candidate = (label or "").strip()
        if not candidate:
            return False
        if "http://" in candidate or "https://" in candidate:
            return False
        if len(candidate) > 80:
            return False
        return True

    def repl_double(match: re.Match) -> str:
        inner = match.group("inner")
        if not should_bold(inner):
            return match.group(0)
        return f'**"{inner}"**'

    def repl_single(match: re.Match) -> str:
        inner = match.group("inner")
        if not should_bold(inner):
            return match.group(0)
        return f"**'{inner}'**"

    def repl_curly_double(match: re.Match) -> str:
        inner = match.group("inner")
        if not should_bold(inner):
            return match.group(0)
        return f"**“{inner}”**"

    def repl_curly_single(match: re.Match) -> str:
        inner = match.group("inner")
        if not should_bold(inner):
            return match.group(0)
        return f"**‘{inner}’**"

    # Do not touch labels already inside bold markers.
    out = re.sub(r'(?<![A-Za-z0-9*])"(?!\*)(?P<inner>[^"\n]{1,80})(?<!\*)"(?![A-Za-z0-9*])', repl_double, out)
    out = re.sub(r"(?<![A-Za-z0-9*])'(?!\*)(?P<inner>[^'\n]{1,80})(?<!\*)'(?![A-Za-z0-9*])", repl_single, out)
    out = re.sub(r"(?<![A-Za-z0-9*])“(?P<inner>[^”\n]{1,80})”(?![A-Za-z0-9*])", repl_curly_double, out)
    out = re.sub(r"(?<![A-Za-z0-9*])‘(?P<inner>[^’\n]{1,80})’(?![A-Za-z0-9*])", repl_curly_single, out)
    return out


def bold_control_labels(text: str) -> str:
    if not text:
        return ""

    def repl(match: re.Match) -> str:
        label = (match.group("label") or "").strip()
        control = (match.group("control") or "").strip()
        if not label or "**" in label or len(label) > 60:
            return match.group(0)
        return f"**{label}** {control}"

    pattern = re.compile(
        r"(?i)\b(?P<label>(?!(?:user|users|see|sees|click|clicks|tap|taps|press|presses|and|or|the|a|an|is|are)\b)"
        r"[A-Za-z][A-Za-z0-9/_-]{0,30}(?:\s+[A-Za-z][A-Za-z0-9/_-]{0,30})?)\s+(?P<control>button|icon)\b"
    )
    return pattern.sub(repl, text)


def enforce_existing_bold_from_source(processed_text: str, source_text: str) -> str:
    out = processed_text or ""
    source = source_text or ""
    bold_tokens = re.findall(r"\*\*([^*\n]+?)\*\*", source)
    for token in bold_tokens:
        label = token.strip()
        if not label:
            continue
        if f"**{label}**" in out:
            continue
        if label in out:
            out = out.replace(label, f"**{label}**", 1)
    return out


def ensure_imperative_action(text: str) -> str:
    if not text:
        return ""

    out = text.strip()
    out = re.sub(r"(?i)\bplease\b", "", out)
    out = re.sub(r"(?i)\byou should\b", "", out)
    out = re.sub(r"(?i)\byou need to\b", "", out)
    out = re.sub(r"(?i)\bit is necessary to\b", "", out)
    out = re.sub(r"(?i)^the user\s+", "", out)
    out = re.sub(r"(?i)^user\s+", "", out)
    out = re.sub(r"(?i)^user is able to\s+", "", out)
    out = re.sub(r"(?i)^is able to\s+", "", out)
    out = re.sub(r"(?i)^should be able to\s+", "", out)
    out = re.sub(r"(?i)^should\s+", "", out)
    out = re.sub(r"(?i)^needs to\s+", "", out)
    out = re.sub(r"(?i)^need to\s+", "", out)
    out = re.sub(r"(?i)^has to\s+", "", out)
    out = re.sub(r"(?i)^can\s+", "", out)

    phrase_rewrites = [
        (r"(?i)^logs?\s+in\b", "Log in"),
        (r"(?i)^signs?\s+in\b", "Sign in"),
        (r"(?i)^tries?\s+to\b", "Attempt to"),
        (r"(?i)^clicks?\b", "Click"),
        (r"(?i)^taps?\b", "Tap"),
        (r"(?i)^press(?:es)?\b", "Press"),
        (r"(?i)^opens?\b", "Open"),
        (r"(?i)^navigates?\s+to\b", "Open"),
        (r"(?i)^go(?:es)?\s+to\b", "Open"),
        (r"(?i)^enters?\b", "Enter"),
        (r"(?i)^selects?\b", "Select"),
        (r"(?i)^chooses?\b", "Choose"),
        (r"(?i)^checks?\b", "Confirm"),
        (r"(?i)^verifies?\b", "Confirm"),
    ]
    for pattern, replacement in phrase_rewrites:
        out = re.sub(pattern, replacement, out)

    out = re.sub(r"\s{2,}", " ", out).strip()
    if out:
        out = out[0].upper() + out[1:]
    return out


def is_action_oriented_step(text: str) -> bool:
    if not text:
        return False
    leading = (text or "").strip()
    if not leading:
        return False
    first = re.split(r"\s+", leading)[0].lower().strip(" .,:;")
    action_verbs = {
        "open", "click", "tap", "press", "enter", "select", "choose",
        "confirm", "wait", "hover", "navigate", "go", "log", "set",
        "drag", "resize", "move", "observe", "change",
        "create", "update", "remove", "switch", "scroll", "expand", "collapse",
    }
    return first in action_verbs or leading.lower().startswith("log in")


def normalize_step_action(action: str) -> str:
    cleaned_action = re.sub(
        r"(?i)^\s*as\s+(vi|du|hu|video interpreter|deaf user|hearing user)(\s*\([^)]+\))?\s*[,:\-]?\s*",
        "",
        action or "",
    )
    cleaned_action = re.sub(r"(?i)^\s*(vi|du|hu)(\s*\([^)]+\))?\s*[,:\-]\s*", "", cleaned_action)
    text = normalize_text_with_links(cleaned_action, expand_roles=False)
    text = strip_html_markup(text)

    # Preserve meaning but make chained arrow actions readable.
    text = re.sub(r"\s*->\s*", " -> ", text)
    text = re.sub(r"(?i)\s*->\s*(click|tap|press|open|enter|select|choose)\b", r". Then \1", text)
    text = re.sub(r"->\s*([A-ZА-ЯІЇЄ])", lambda m: "-> " + m.group(1).lower(), text)

    text = bold_ui_elements(text)
    text = bold_quoted_ui_labels(text)
    text = bold_control_labels(text)
    text = place_images_on_new_line(text)
    text = re.sub(r"->\s*([A-ZА-ЯІЇЄ])", lambda m: "-> " + m.group(1).lower(), text)
    text = re.sub(r"(?i)^\s*(user|vi|du|hu)\s+(should\s+)?", "", text)
    text = re.sub(r"(?i)^\s*(the\s+user)\s+(should\s+)?", "", text)
    text = re.sub(r"(?i)^\s*(need|needs)\s+to\s+", "", text)
    text = re.sub(r"(?i)^\s*(make sure|ensure that)\s+", "Ensure ", text)
    text = re.sub(r"(?i)^\s*(go to)\s+", "Open ", text)
    text = re.sub(r"(?i)^\s*(navigate to)\s+", "Open ", text)
    text = re.sub(r"(?i)^\s*(must)\s+", "", text)
    text = re.sub(r"(?i)^\s*(check|verify)\b", "Confirm", text)
    text = re.sub(r"(?i)\bclick on\b", "Click", text)
    text = re.sub(r"(?i)\bpress on\b", "Press", text)
    text = re.sub(r"(?i)\btap on\b", "Tap", text)
    text = re.sub(r"\s{2,}", " ", text).strip()
    text = ensure_imperative_action(text)

    main, note = split_inline_note(text)
    out = (main + "\n\n" + render_note_block(note)).strip() if note else main.strip()
    return enforce_existing_bold_from_source(out, action or "")


def normalize_expected_result_text(expected: str) -> str:
    # Expand role abbreviations only in expected results.
    text = normalize_text_with_links(expected, expand_roles=True)
    text = strip_html_markup(text)
    text = bold_ui_elements(text)
    text = bold_quoted_ui_labels(text)
    text = bold_control_labels(text)
    text = place_images_on_new_line(text)
    # Keep line breaks in expected blocks; compress only spaces/tabs.
    text = re.sub(r"(?m)(?<=\S)[ \t]{2,}", " ", text).strip("\n\r\t")
    # Ensure "Check that" blocks are readable and numbered on separate lines.
    text = re.sub(r"(?i)^check that:\s*(\d+\.)", r"Check that:\n\n\1", text)
    text = re.sub(r"[ \t]+(\d+\.)[ \t]*", r"\n\1 ", text)
    text = re.sub(r"(?i)\bmake sure that\b", "confirm that", text)
    text = re.sub(r"(?i)\bmake sure\b", "confirm", text)
    text = re.sub(r"(?i)\bwould be\b", "is", text)
    text = re.sub(r"(?i)\bis shown with\b", "shows", text)
    text = re.sub(r"(?i)^\s*(should|must)\s+", "", text)
    text = re.sub(r"(?i)^user\s+", "User ", text)
    text = re.sub(r"(?i)^the system\s+", "", text)
    text = re.sub(r"(?i)^system\s+the\s+tooltip\s+is\s+shown", "Tooltip is shown", text)
    text = re.sub(r"(?i)^system the ", "", text)
    main, note = split_inline_note(text)
    if main and not re.match(r"(?i)^(user|error|status|check that)\b", main):
        main = main[0].upper() + main[1:] if len(main) > 1 else main.upper()
    text = (main + "\n\n" + render_note_block(note)).strip() if note else main.strip()
    if text:
        text = text[0].upper() + text[1:]
    text = re.sub(r"(?i)^system\s+", "", text)
    text = re.sub(r"(?i)^check that:\s*", "Check that:\n", text)
    text = re.sub(r"(?i)^shows?\s+that\s+user\s+sees", "User sees", text)
    # Split inline enumerations like "...account2. DU..." into readable list lines.
    text = re.sub(r"(?<!^)\s+(\d+\.)\s*", r"\n\1 ", text)
    text = re.sub(r"(?<!^)\s+(\d+\))\s*", r"\n  \1 ", text)
    text = re.sub(r"\n(\d+\.)\s*", r"\n\1 ", text)
    text = re.sub(r"\n(\d+\))\s*", r"\n  \1 ", text)
    # Keep list blocks readable after colon.
    text = re.sub(r":\s*(\d+\))", r":\n  \1", text)
    # Force media markers onto dedicated lines.
    text = re.sub(r"(?i)[ \t]+(фото|photo|image)\b", r"\n\1", text)
    # If media markers are provided in trailing lines, pair them with 1)/2) items in order.
    if re.search(r"(?m)^\s*\d+\)\s+", text):
        raw_lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
        media_tokens = {"фото", "photo", "image"}
        items: list[str] = []
        media: list[str] = []
        prefix: list[str] = []

        for ln in raw_lines:
            if re.match(r"^\s*\d+\)\s+", ln):
                items.append(ln)
            elif ln.lower() in media_tokens:
                media.append(ln)
            else:
                prefix.append(ln)

        if items and media:
            rebuilt: list[str] = []
            if prefix:
                rebuilt.extend(prefix)
            media_idx = 0
            for item in items:
                rebuilt.append(item)
                if media_idx < len(media):
                    rebuilt.append(media[media_idx])
                    media_idx += 1
            while media_idx < len(media):
                rebuilt.append(media[media_idx])
                media_idx += 1
            text = "\n".join(rebuilt)
    # Convert unordered bullets to numbered list for consistency in expected results.
    lines = text.splitlines()
    if any(re.match(r"^\s*[-•]\s+", ln) for ln in lines):
        numbered: list[str] = []
        n = 1
        for ln in lines:
            if re.match(r"^\s*[-•]\s+", ln):
                body = re.sub(r"^\s*[-•]\s+", "", ln).strip()
                if body:
                    numbered.append(f"{n}. {body}")
                    n += 1
                else:
                    numbered.append(ln)
            else:
                numbered.append(ln.strip())
        text = "\n".join(numbered)
    # Keep parenthesized mode items visually nested under the preceding step/result line.
    text = re.sub(r"(?m)^\s*(\d+\)\s+)", r"  \1", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    text = re.sub(r"(?m)(?<=\S)[ \t]{2,}", " ", text).strip("\n\r\t")
    return enforce_existing_bold_from_source(text, expected or "")


def normalize_title(title: str) -> str:
    cleaned = safe_str(title)
    cleaned = re.sub(r"^\s*verify\s+", "", cleaned, flags=re.IGNORECASE).strip()
    cleaned = re.sub(r"(?i)^\s*test case\s*[:-]\s*", "", cleaned)
    cleaned = re.sub(r"(?i)^\s*checking\b", "Check", cleaned)
    cleaned = re.sub(r"(?i)^\s*check if\b", "Check", cleaned)
    cleaned = re.sub(r"(?i)^\s*ensure\b", "Confirm", cleaned)
    cleaned = re.sub(r"(?i)\bplease\b", "", cleaned)
    cleaned = re.sub(r"(?i)\byou should\b", "", cleaned)
    cleaned = normalize_text(cleaned, expand_roles=False)
    cleaned = re.sub(r"^([A-Za-z]+)\)\s+", r"\1 ", cleaned)
    cleaned = re.sub(r"\s*->\s*", " - ", cleaned)
    cleaned = re.sub(r"\s{2,}", " ", cleaned).strip(" ._-")
    cleaned = re.sub(r"(?i)\bmak\s*sre\b", "make sure", cleaned)
    cleaned = re.sub(r"(?i)\bteat\b", "test", cleaned)
    cleaned = re.sub(r"(?i)\bshfit\b", "shift", cleaned)
    cleaned = re.sub(r"(?i)\btool ?chest\b", "Tool Chest", cleaned)
    cleaned = re.sub(r"(?i)^\s*(vi|du|hu)(\s*\([^)]+\))?['’]?[s]?\s+", "", cleaned)
    cleaned = re.sub(r"(?i)^user\s+", "", cleaned)
    cleaned = re.sub(r"(?i)^the user\s+", "", cleaned)
    # Prefer action-oriented title starts.
    if re.match(r"(?i)^when\b", cleaned):
        cleaned = "Validate " + re.sub(r"(?i)^when\s+", "", cleaned)
    if re.match(r"(?i)^for\b", cleaned):
        cleaned = "Validate " + re.sub(r"(?i)^for\s+", "", cleaned)
    if re.match(r"(?i)^is\b", cleaned):
        cleaned = "Validate " + cleaned
    if cleaned and not is_action_oriented_title(cleaned):
        cleaned = f"Validate {cleaned}"
    if cleaned:
        cleaned = cleaned[0].upper() + cleaned[1:]
    return cleaned


def is_action_oriented_title(text: str) -> bool:
    if not text:
        return False
    first = re.split(r"\s+", text.strip())[0].lower().strip(" .,:;")
    allowed = {
        "open", "check", "confirm", "validate", "create", "update", "delete",
        "verify", "set", "log", "navigate", "view", "show", "hide", "submit",
        "move", "resize", "drag", "observe", "change",
    }
    return first in allowed or text.lower().startswith("log in")


def normalize_preconditions(preconditions: str) -> str:
    text = strip_html_markup(normalize_text_with_links(preconditions, expand_roles=False))
    text = bold_quoted_ui_labels(text)
    text = bold_control_labels(text)
    # Split inline numbered preconditions into separate lines:
    # ".... starts. 2. Have a VI User ..." -> "\n2. Have a VI User ..."
    text = re.sub(r"(?<!^)(?<=\S)\s+(\d+[.)]\s+)", r"\n\1", text)
    # Normalize compact ENV/USERS fragment when both are glued to step 2.
    text = re.sub(r"(?i)\bhave a vi user\s+env\s*[-:]\s*users\b", "Have a VI User\nENV:\nUSERS:", text)
    text = linkify_arrow_urls_short(text)
    raw_lines = [ln.rstrip() for ln in re.split(r"\r?\n", text) if ln.strip()]
    has_explicit_numbering = any(re.match(r"^\s*\d+[.)]\s+", ln) for ln in raw_lines)
    lines = [ln.strip() for ln in raw_lines]

    if not lines:
        return ""

    result: list[str] = []
    idx = 1

    def uppercase_first_letter(value: str) -> str:
        text = (value or "").strip()
        if not text:
            return text
        for i, char in enumerate(text):
            if char.isalpha():
                return text[:i] + char.upper() + text[i + 1 :]
        return text

    def add_numbered_subitem(parent_line: str, subitem_text: str) -> str:
        existing = [int(num) for num in re.findall(r"(?m)^\s+(\d+)\)\s+", parent_line)]
        sub_idx = max(existing, default=0) + 1
        return f"{parent_line}\n   {sub_idx}) {uppercase_first_letter(subitem_text)}"

    def normalize_line_body(line_body: str) -> str:
        line_body = re.sub(r"^([A-Za-z]+)\)\s+", r"\1 ", line_body)
        line_body = re.sub(r"(?i)\bmake sure\b", "Ensure", line_body)
        line_body = re.sub(r"(?i)\bsee here\s*->\s*(https?://[^\s]+)", r"[See here](\1)", line_body)
        line_body = rewrite_admin_precondition(line_body)
        line_body = cleanup_figma_reference(line_body)
        return line_body.strip()

    if has_explicit_numbering:
        for raw_line in lines:
            line = normalize_line_body(raw_line)

            if re.match(r"^_NOTE:", line):
                if result and result[-1] != "":
                    result.append("")
                result.append(line)
                continue

            # Keep ENV/USERS as sub-lines of previous numbered item.
            if re.match(r"(?i)^(env|users)\s*:", line) or re.match(r"(?i)^\[(env|users)\]\(", line):
                if result and not result[-1].startswith("_NOTE:"):
                    result[-1] = add_numbered_subitem(result[-1], line)
                else:
                    result.append(f"{idx}. {line}")
                    idx += 1
                continue

            body = re.sub(r"^\d+[.)]\s*", "", line).strip()
            body = re.sub(r"^\s*[-•]\s*", "", body).strip()
            if body:
                body = uppercase_first_letter(body)
            result.append(f"{idx}. {body}".rstrip())
            idx += 1
        out_text = "\n".join(result).rstrip()
        out_text = re.sub(r"(?m)^(\d+\.\s*)[•·▪◦●\-]\s*", r"\1", out_text)
        return enforce_existing_bold_from_source(out_text, preconditions or "")

    for line in lines:
        line = normalize_line_body(line)

        if re.match(r"^_NOTE:", line):
            if not result or result[-1] != "":
                result.append("")
            result.append(line)
            continue

        while re.match(r"^\d+[.)]\s*", line):
            line = re.sub(r"^\d+[.)]\s*", "", line).strip()

        # Keep ENV/USERS as sub-lines of the previous precondition for readability.
        if re.match(r"(?i)^(env|users)\s*:", line) or re.match(r"(?i)^\[(env|users)\]\(", line):
            if result and not result[-1].startswith("_NOTE:"):
                result[-1] = add_numbered_subitem(result[-1], line)
            else:
                result.append(f"{idx}. {line}")
                idx += 1
            continue

        if line:
            line = uppercase_first_letter(line)
        result.append(f"{idx}. {line}")
        idx += 1

    out_text = "\n".join(result).rstrip()
    out_text = re.sub(r"(?m)^(\d+\.\s*)[•·▪◦●\-]\s*", r"\1", out_text)
    return enforce_existing_bold_from_source(out_text, preconditions or "")


def split_numbered_lines(text: str) -> list[str]:
    if not text:
        return []

    lines = [ln.strip() for ln in re.split(r"\r?\n", text) if ln.strip()]
    if not lines:
        return []

    out: list[str] = []
    for line in lines:
        line = re.sub(r"^\d+[.)]\s*", "", line).strip()
        if line:
            out.append(line)
    return out


def extract_markdown_images(text: str) -> list[str]:
    if not text:
        return []
    return re.findall(r"!\[[^\]]*\]\([^)]+\)", text)


def extract_media_markers(text: str) -> list[str]:
    if not text:
        return []
    markers: list[str] = []
    normalized_text = normalize_attachment_urls(text)

    # Keep multiplicity and order for all media sources.
    for img in re.findall(r"!\[[^\]]*\]\([^)]+\)", normalized_text):
        markers.append(img)

    for match in re.finditer(r'(?is)<img[^>]*src\s*=\s*["\'](?P<src>[^"\']+)["\'][^>]*>', normalized_text):
        src = normalize_attachment_urls((match.group("src") or "").strip())
        if src:
            markers.append(f"![]({src})")

    for url in re.findall(r"(?:https?://[^\s)]+/attachments/get/\d+|index\.php\?/attachments/get/\d+)", normalized_text):
        clean = normalize_attachment_urls(url.strip())
        if clean:
            markers.append(f"![]({clean})")

    for line in normalized_text.splitlines():
        ln = line.strip()
        if not ln:
            continue
        if re.fullmatch(r"(?i)(фото|photo|image)\b\.?", ln):
            normalized = "фото" if ln.lower().startswith("фото") else "photo"
            # Keep multiplicity for plain photo markers (one per mode/item).
            markers.append(normalized)
    return markers


def remove_markdown_images(text: str) -> str:
    if not text:
        return ""
    cleaned = re.sub(r"!\[[^\]]*\]\([^)]+\)", "", text)
    cleaned = re.sub(r"\n{3,}", "\n\n", cleaned)
    return cleaned.strip()


def merge_original_images(refactored_text: str, original_text: str) -> str:
    images = extract_media_markers(original_text)
    if not images:
        return refactored_text

    out = remove_markdown_images(refactored_text)
    def normalize_media_token(value: str) -> str:
        token = (value or "").strip()
        if not token:
            return ""
        if re.fullmatch(r"(?i)(фото|photo|image)\b\.?", token):
            return "фото" if token.lower().startswith("фото") else "photo"
        m = re.match(r"!\[[^\]]*\]\(([^)]+)\)", token)
        if m:
            return f"![]({normalize_attachment_urls(m.group(1).strip())})"
        m = re.search(r'(?is)<img[^>]*src\s*=\s*["\']([^"\']+)["\']', token)
        if m:
            src = normalize_attachment_urls(m.group(1).strip())
            return f"![]({src})" if src else ""
        if re.fullmatch(r"(?:https?://[^\s)]+/attachments/get/\d+|index\.php\?/attachments/get/\d+)", token):
            return f"![]({normalize_attachment_urls(token)})"
        return token

    existing_counts = Counter(
        normalize_media_token(ln)
        for ln in out.splitlines()
        if normalize_media_token(ln)
    )
    expected_counts = Counter()
    seen_url_media: set[str] = set()
    missing_images: list[str] = []
    for img in images:
        canonical = normalize_media_token(img)
        if not canonical:
            continue
        # Avoid duplicate insertion of the exact same image URL marker.
        # Keep plain "photo/фото" multiplicity for mode-specific expectations.
        if canonical.startswith("!["):
            if canonical in seen_url_media:
                continue
            seen_url_media.add(canonical)
        expected_counts[canonical] += 1
        if existing_counts.get(canonical, 0) < expected_counts[canonical]:
            missing_images.append(canonical)

    if not missing_images:
        return out

    # If expected has "1) ... / 2) ..." modes, place images directly under those items.
    numbered_mode_lines = [ln for ln in out.splitlines() if re.match(r"^\d+\)\s+", ln.strip())]
    if numbered_mode_lines:
        lines = out.splitlines()
        item_idx = [i for i, ln in enumerate(lines) if re.match(r"^\d+\)\s+", ln.strip())]
        media_tokens = {"фото", "photo", "image"}

        def is_media_line(value: str) -> bool:
            stripped = normalize_media_token(value).lower()
            return (
                stripped in media_tokens
                or stripped.startswith("![")
                or stripped.startswith("<img")
            )

        items_missing_media: list[int] = []
        for pos in item_idx:
            probe = pos + 1
            while probe < len(lines) and not lines[probe].strip():
                probe += 1
            has_media = probe < len(lines) and is_media_line(lines[probe])
            if not has_media:
                items_missing_media.append(pos)

        insert_plan: list[tuple[int, str]] = []
        mi = 0
        for pos in items_missing_media:
            if mi >= len(missing_images):
                break
            insert_plan.append((pos + 1, missing_images[mi]))
            mi += 1
        while mi < len(missing_images) and item_idx:
            # Fallback: append remaining missing media under the last mode item.
            insert_plan.append((item_idx[-1] + 1, missing_images[mi]))
            mi += 1

        for insert_at, marker in sorted(insert_plan, key=lambda x: x[0], reverse=True):
            lines.insert(insert_at, marker)
        return "\n".join(lines).strip()

    for img in missing_images:
        out = (out + "\n" + img).strip() if out else img
    return out


def rewrite_admin_precondition(line: str) -> str:
    working = line.strip()
    lower = working.lower()

    # Collapse duplicated admin login sentence.
    if "have admin creds" in lower and "team" in lower and "work" in lower:
        urls = re.findall(r"https?://[^\s)]+", working)
        md_urls = re.findall(r"\[[^\]]+\]\((https?://[^\s)]+)\)", working)
        if not urls and md_urls:
            urls = md_urls
        if urls:
            return f"Log in as Admin using [admin credentials]({urls[0]})"
        return "Log in as Admin using admin credentials"

    if re.search(r"(before.*test case.*(make sure|ensure).*(set[\s-]?up shift).*(teamwork))|(set[\s-]?up shift.*teamwork.*last.*day)", lower):
        return "Ensure the User has a TeamWork shift set for today, and it is the last shift of the day"

    return working


def cleanup_figma_reference(line: str) -> str:
    return (line or "").strip()


def strip_html_markup(text: str) -> str:
    if not text:
        return ""
    out = text
    out = re.sub(r"(?i)<br\s*/?>", "\n", out)
    out = re.sub(r"(?i)</p>", "\n", out)
    out = re.sub(r"(?i)<p[^>]*>", "", out)
    out = re.sub(r"(?i)</li>", "\n", out)
    out = re.sub(r"(?i)<li[^>]*>", "- ", out)
    out = re.sub(r"(?i)</?ol[^>]*>", "", out)
    out = re.sub(r"(?i)</?ul[^>]*>", "", out)
    out = re.sub(r"(?i)</?span[^>]*>", "", out)
    out = re.sub(r"(?i)</?div[^>]*>", "", out)
    out = re.sub(r"<[^>]+>", "", out)
    out = re.sub(r"[ \t]+\n", "\n", out)
    out = re.sub(r"\n{3,}", "\n\n", out)
    return out.strip()


def normalize_attachment_urls(text: str) -> str:
    if not text:
        return ""
    out = text
    out = re.sub(r"(?i)index\.\s*php\?/", "index.php?/", out)
    out = re.sub(r"(?i)index\.\s*php\?", "index.php?", out)
    out = re.sub(r"!\[\]\(\s*index\.php", "![](index.php", out, flags=re.IGNORECASE)
    return out


def convert_html_links_to_markdown(text: str) -> str:
    if not text:
        return ""

    def repl(match: re.Match) -> str:
        url = (match.group("url") or "").strip()
        label = strip_html_markup(match.group("label") or "").strip()
        if not label:
            label = "Link"
        return f"[{label}]({url})" if url else label

    out = re.sub(r'(?is)<a[^>]*href\s*=\s*["\'](?P<url>[^"\']+)["\'][^>]*>(?P<label>.*?)</a>', repl, text)
    return out


def convert_html_images_to_markdown(text: str) -> str:
    if not text:
        return ""

    def repl(match: re.Match) -> str:
        src = (match.group("src") or "").strip()
        src = normalize_attachment_urls(src)
        if not src:
            return ""
        return f"![]({src})"

    out = re.sub(r'(?is)<img[^>]*src\s*=\s*["\'](?P<src>[^"\']+)["\'][^>]*>', repl, text)
    return out


def extract_steps(raw_case: dict) -> tuple[list[dict], str]:
    if raw_case.get("custom_steps_separated"):
        steps = []
        for item in raw_case["custom_steps_separated"]:
            action = normalize_text(safe_str(item.get("content")))
            expected = normalize_text(safe_str(item.get("expected")))
            if action:
                steps.append({"action": action, "expected_result": expected})
        return steps, ""

    step_lines = split_numbered_lines(safe_str(raw_case.get("custom_steps")))
    expected_lines = split_numbered_lines(safe_str(raw_case.get("custom_expected")))

    steps: list[dict] = []
    for idx, action in enumerate(step_lines):
        expected = expected_lines[idx] if idx < len(expected_lines) else ""
        steps.append({"action": action, "expected_result": expected})

    global_expected = ""
    if len(expected_lines) > len(step_lines):
        global_expected = "\n".join(expected_lines[len(step_lines):]).strip()

    return steps, normalize_text(global_expected)


def refactor_case_locally(raw_case: dict) -> dict:
    steps, global_expected = extract_steps(raw_case)
    preconditions = normalize_preconditions(safe_str(raw_case.get("custom_preconds")))

    normalized_steps: list[dict] = []
    for step in steps:
        original_action = step.get("action", "")
        original_expected = step.get("expected_result", "")

        action = normalize_step_action(original_action)
        expected = normalize_expected_result_text(original_expected)
        action = merge_original_images(action, original_action)
        expected = merge_original_images(expected, original_expected)
        if action:
            normalized_steps.append({"action": action, "expected_result": expected})
    steps = normalized_steps

    violations: list[str] = []
    normalized_title = normalize_title(safe_str(raw_case.get("title")))
    if not preconditions:
        violations.append("Preconditions are missing")
    if not steps:
        violations.append("Steps are missing or not parseable")
    if not is_action_oriented_title(normalized_title):
        violations.append("Title is not action-oriented after normalization")

    weak_patterns = [
        r"(?i)\byou should\b",
        r"(?i)\bplease\b",
        r"(?i)\bit is necessary to\b",
        r"(?i)\buser should\b",
    ]
    for idx, step in enumerate(steps, start=1):
        action = step.get("action", "")
        if not is_action_oriented_step(action):
            violations.append(f"Step {idx} is not imperative/action-oriented")
        for pattern in weak_patterns:
            if re.search(pattern, action):
                violations.append(f"Step {idx} contains weak conversational language")
                break

    return {
        "title": normalized_title,
        "preconditions": preconditions,
        "steps": steps,
        "global_expected_result": global_expected,
        "violations": violations,
    }


async def refactor_case_with_agent(raw_case: dict) -> dict:
    return refactor_case_locally(raw_case)


def detect_template(case: dict) -> str:
    if case.get("custom_steps_separated") is not None:
        return "steps_separated"
    return "steps"


def build_testrail_payload(ai_result: dict, template: str) -> dict:
    payload = {
        "title": ai_result["title"],
        "custom_preconds": ai_result["preconditions"],
    }

    if template == "steps_separated":
        steps_payload = []
        for step in ai_result["steps"]:
            action = (step.get("action") or "").strip()
            expected = (step.get("expected_result") or "").strip()
            if action:
                steps_payload.append({"content": action, "expected": expected})

        global_expected = (ai_result.get("global_expected_result") or "").strip()
        if global_expected and steps_payload:
            if steps_payload[-1]["expected"]:
                steps_payload[-1]["expected"] += "\n\n" + global_expected
            else:
                steps_payload[-1]["expected"] = global_expected

        payload["custom_steps_separated"] = steps_payload
    else:
        step_lines = []
        expected_lines = []

        for idx, step in enumerate(ai_result["steps"], start=1):
            action = (step.get("action") or "").strip()
            expected = (step.get("expected_result") or "").strip()
            if action:
                step_lines.append(f"{idx}. {action}")
            if expected:
                expected_lines.append(f"{idx}. {expected}")

        global_expected = (ai_result.get("global_expected_result") or "").strip()
        if global_expected:
            expected_lines.append(global_expected)

        payload["custom_steps"] = "\n".join(step_lines)
        payload["custom_expected"] = "\n".join(expected_lines).strip()

    return payload


@retry(stop=stop_after_attempt(4), wait=wait_exponential(multiplier=1.5, min=2, max=15))
async def get_case(session: aiohttp.ClientSession, case_id: int) -> dict:
    url = f"{TESTRAIL_URL}/index.php?/api/v2/get_case/{case_id}"
    async with session.get(url) as r:
        r.raise_for_status()
        return await r.json()


@retry(stop=stop_after_attempt(4), wait=wait_exponential(multiplier=1.5, min=2, max=15))
async def create_refactored_case(
    session: aiohttp.ClientSession,
    source_case_id: int,
    raw_case: dict,
    ai_result: dict,
    priority_audit: dict | None = None,
):
    template = detect_template(raw_case)
    template_id = raw_case.get("template_id")

    payload = build_testrail_payload(ai_result, template)
    payload["title"] = f"[AI refactored from C{source_case_id}] {ai_result['title']}"
    payload["template_id"] = template_id
    payload["refs"] = build_refs_for_created_case((raw_case.get("refs") or "").strip(), priority_audit or {})
    selected_priority_id = safe_int(ai_result.get("priority_id"))
    if selected_priority_id is None and COPY_SOURCE_PRIORITY:
        selected_priority_id = safe_int(raw_case.get("priority_id"))
    if selected_priority_id is not None:
        payload["priority_id"] = selected_priority_id

    url = f"{TESTRAIL_URL}/index.php?/api/v2/add_case/{SECTION_ID}"
    async with session.post(url, json=payload) as r:
        r.raise_for_status()
        created = await r.json()
        log(f"[C{source_case_id}] Created new case C{created['id']}")
        return created["id"]


def build_refs_for_created_case(source_refs: str, priority_audit: dict) -> str:
    refs = source_refs.strip()
    if not refs:
        refs = ""

    story_key = safe_str(priority_audit.get("selected_story_key"))
    if not story_key:
        return refs

    has_story_key = bool(re.search(rf"\b{re.escape(story_key)}\b", refs, flags=re.IGNORECASE))
    canonical_story_url = f"{JIRA_BASE_URL}/browse/{story_key}" if JIRA_BASE_URL else story_key
    has_story_url = canonical_story_url.lower() in refs.lower() if canonical_story_url else False

    if has_story_key or has_story_url:
        return refs
    if not refs:
        return canonical_story_url
    return refs + "\n" + canonical_story_url


async def get_cases_in_section(session: aiohttp.ClientSession, section_id: int) -> list[int]:
    section_url = f"{TESTRAIL_URL}/index.php?/api/v2/get_section/{section_id}"
    async with session.get(section_url) as r:
        r.raise_for_status()
        section = await r.json()
    apply_section_abbreviations(section.get("description", ""))

    project_id = section.get("project_id") or TESTRAIL_PROJECT_ID
    suite_id = section.get("suite_id")
    if not project_id:
        project_id = await resolve_project_id_from_suite(session, suite_id)
    if not project_id:
        raise ValueError("Cannot resolve TestRail project_id. Set TESTRAIL_PROJECT_ID in .env.")

    url = f"{TESTRAIL_URL}/index.php?/api/v2/get_cases/{project_id}&section_id={section_id}"
    if suite_id:
        url += f"&suite_id={suite_id}"

    async with session.get(url) as r:
        r.raise_for_status()
        payload = await r.json()
        cases = payload.get("cases", []) if isinstance(payload, dict) else payload
        return [case["id"] for case in cases]


async def resolve_project_id_from_suite(session: aiohttp.ClientSession, suite_id: int | None) -> str | None:
    if not suite_id:
        return None

    projects_url = f"{TESTRAIL_URL}/index.php?/api/v2/get_projects"
    async with session.get(projects_url) as r:
        r.raise_for_status()
        projects_payload = await r.json()

    projects = projects_payload.get("projects", []) if isinstance(projects_payload, dict) else projects_payload
    for project in projects:
        project_id = project.get("id")
        if not project_id:
            continue

        suites_url = f"{TESTRAIL_URL}/index.php?/api/v2/get_suites/{project_id}"
        async with session.get(suites_url) as r:
            if r.status != 200:
                continue
            suites_payload = await r.json()
        suites = suites_payload.get("suites", []) if isinstance(suites_payload, dict) else suites_payload

        for suite in suites:
            if suite.get("id") == suite_id:
                return str(project_id)

    return None


def ask_section_validation(results: list[dict]) -> str:
    ready = [r for r in results if r.get("status") == "prepared_only"]
    failed = [r for r in results if r.get("status") == "validation_failed"]
    violations = [r for r in ready if (r.get("refactored", {}).get("violations") or [])]

    log("\n[SECTION] Manual validation required")
    log(f"[SECTION] Prepared cases: {len(ready)}")
    log(f"[SECTION] Validation failures: {len(failed)}")
    log(f"[SECTION] Cases with violations: {len(violations)}")

    if violations:
        log("[SECTION] Violations detected in:")
        for item in violations:
            log(f"  - C{item['case_id']}: {', '.join(item.get('refactored', {}).get('violations', []))}")

    return ask_yes_no("[SECTION] Approve creating refactored cases for the whole section? (yes/no): ", default_yes=True)


def collect_story_refs(text: str) -> list[str]:
    refs = safe_str(text)
    if not refs:
        return []

    urls = re.findall(r"https?://[^\s,;]+", refs)
    issue_keys = re.findall(r"\b[A-Z][A-Z0-9]+-\d+\b", refs, flags=re.IGNORECASE)
    combined = []
    seen: set[str] = set()
    for item in urls + [key.upper() for key in issue_keys]:
        if item not in seen:
            seen.add(item)
            combined.append(item)
    return combined


def extract_jira_issue_keys(story_refs: list[str]) -> list[str]:
    keys: list[str] = []
    seen: set[str] = set()

    def add_key(value: str):
        key = value.upper()
        if key not in seen:
            seen.add(key)
            keys.append(key)

    for ref in story_refs:
        for key in re.findall(r"\b[A-Z][A-Z0-9]+-\d+\b", ref, flags=re.IGNORECASE):
            add_key(key)
        match = re.search(r"/browse/([A-Za-z][A-Za-z0-9]+-\d+)", ref, flags=re.IGNORECASE)
        if match:
            add_key(match.group(1))

    return keys


def map_jira_priority_name_to_testrail(priority_name: str | None) -> int:
    name = safe_str(priority_name).lower()
    if any(token in name for token in ["highest", "critical", "blocker"]):
        return PRIORITY_ID_CRITICAL
    if any(token in name for token in ["high", "major"]):
        return PRIORITY_ID_HIGH
    if any(token in name for token in ["lowest", "low", "minor", "trivial"]):
        return PRIORITY_ID_LOW
    return PRIORITY_ID_MEDIUM


def derive_priority_from_acceptance(
    relevance: float,
    case_score: int,
    case_risk_score: int,
    acceptance_score: int,
    is_ui_case: bool,
) -> int:
    total = case_score + case_risk_score + acceptance_score

    # Priority is derived from acceptance criteria + case content only.
    # Jira story priority is intentionally ignored.
    # Team policy:
    # - UI-only cases without meaningful AC alignment -> LOW
    # - Main functional path in AC -> HIGH
    # - Other covered checks -> MEDIUM
    # - CRITICAL -> rare, explicit severe-path only
    if is_ui_case and acceptance_score <= 0 and relevance < 0.08:
        return PRIORITY_ID_LOW

    if relevance >= 0.10 and acceptance_score >= 2:
        proposed = PRIORITY_ID_HIGH
    else:
        proposed = PRIORITY_ID_MEDIUM

    # Rare CRITICAL: only when relevance is very high and risk signals are strong.
    if relevance >= 0.25 and acceptance_score >= 3 and case_risk_score >= 3 and total >= 8:
        proposed = PRIORITY_ID_CRITICAL

    return proposed


def nudge_priority(base_priority_id: int, shift: int) -> int:
    ladder = [PRIORITY_ID_LOW, PRIORITY_ID_MEDIUM, PRIORITY_ID_HIGH, PRIORITY_ID_CRITICAL]
    if base_priority_id not in ladder:
        return base_priority_id
    idx = ladder.index(base_priority_id)
    target = min(max(idx + shift, 0), len(ladder) - 1)
    return ladder[target]


async def fetch_jira_issue_fields(session: aiohttp.ClientSession, issue_key: str) -> dict | None:
    if not JIRA_BASE_URL:
        return None

    auth, headers = jira_auth_config()
    if not auth and not headers:
        return None

    url = f"{JIRA_BASE_URL}/rest/api/3/issue/{issue_key}"
    acceptance_field_ids = await fetch_jira_acceptance_field_ids(session)
    fields = ["summary", "priority", "issuetype", "status", "labels", "parent", "description", *acceptance_field_ids]
    params = {"fields": ",".join(fields)}
    try:
        async with session.get(url, params=params, auth=auth, headers=headers) as r:
            if r.status != 200:
                return None
            payload = await r.json()
            return payload.get("fields", {})
    except aiohttp.ClientError:
        return None


async def fetch_jira_acceptance_field_ids(session: aiohttp.ClientSession) -> list[str]:
    global _JIRA_ACCEPTANCE_FIELD_IDS, _JIRA_ACCEPTANCE_FIELDS_META
    if _JIRA_ACCEPTANCE_FIELD_IDS is not None:
        return _JIRA_ACCEPTANCE_FIELD_IDS

    if not JIRA_BASE_URL:
        _JIRA_ACCEPTANCE_FIELD_IDS = []
        _JIRA_ACCEPTANCE_FIELDS_META = []
        return _JIRA_ACCEPTANCE_FIELD_IDS

    auth, headers = jira_auth_config()
    if not auth and not headers:
        _JIRA_ACCEPTANCE_FIELD_IDS = []
        _JIRA_ACCEPTANCE_FIELDS_META = []
        return _JIRA_ACCEPTANCE_FIELD_IDS

    url = f"{JIRA_BASE_URL}/rest/api/3/field"
    try:
        async with session.get(url, auth=auth, headers=headers) as r:
            if r.status != 200:
                _JIRA_ACCEPTANCE_FIELD_IDS = []
                _JIRA_ACCEPTANCE_FIELDS_META = []
                return _JIRA_ACCEPTANCE_FIELD_IDS
            payload = await r.json()
    except aiohttp.ClientError:
        _JIRA_ACCEPTANCE_FIELD_IDS = []
        _JIRA_ACCEPTANCE_FIELDS_META = []
        return _JIRA_ACCEPTANCE_FIELD_IDS

    field_ids: list[str] = []
    field_meta: list[dict] = []
    for item in payload if isinstance(payload, list) else []:
        field_id = safe_str(item.get("id"))
        field_name_raw = safe_str(item.get("name"))
        field_name = field_name_raw.lower()
        if not field_id or not field_name_raw:
            continue
        if ("acceptance" in field_name and "criteria" in field_name) or "acceptance criteria" in field_name:
            field_ids.append(field_id)
            field_meta.append({"id": field_id, "name": field_name_raw})

    _JIRA_ACCEPTANCE_FIELD_IDS = field_ids
    _JIRA_ACCEPTANCE_FIELDS_META = field_meta
    return _JIRA_ACCEPTANCE_FIELD_IDS


def get_jira_acceptance_fields_meta() -> list[dict]:
    return list(_JIRA_ACCEPTANCE_FIELDS_META or [])


def flatten_jira_field_value(value) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, list):
        return "\n".join(part for part in (flatten_jira_field_value(item) for item in value) if part)
    if isinstance(value, dict):
        return flatten_adf_text(value).strip()
    return ""


def collect_acceptance_text(fields: dict) -> str:
    chunks: list[str] = []
    description_text = flatten_adf_text(fields.get("description")).strip()
    if description_text:
        chunks.append(description_text)

    for field_id in _JIRA_ACCEPTANCE_FIELD_IDS or []:
        text = flatten_jira_field_value(fields.get(field_id))
        if text:
            chunks.append(text)

    return "\n".join(chunk for chunk in chunks if chunk)


def score_priority_signals(case_text: str, refs_text: str, apply_ui_penalty: bool = True) -> tuple[int, list[str]]:
    text = f"{case_text}\n{refs_text}".lower()
    score = 0
    reasons: list[str] = []

    critical_terms = [
        "security", "payment", "billing", "auth", "login", "credential",
        "registration", "identity", "compliance", "fcc", "data loss", "crash",
        "iserror", "error state",
    ]
    high_terms = [
        "signup", "port-in", "port out", "port-out", "termination",
        "transfer", "blocked account", "active account", "redirect", "vrs", "urd",
        "clock out", "clock in", "shift history", "total hours",
    ]
    lower_terms = [
        "tooltip", "info icon", "icon", "visual", "label only",
    ]

    if any(term in text for term in critical_terms):
        score += 2
        reasons.append("Contains critical business/regulatory/authentication signals")
    if any(term in text for term in high_terms):
        score += 1
        reasons.append("Contains high-impact service flow signals")
    if re.search(r"\b[A-Z][A-Z0-9]+-\d+\b", refs_text):
        score += 1
        reasons.append("Has linked Jira story key")
    if apply_ui_penalty and any(term in text for term in lower_terms):
        score -= 1
        reasons.append("UI-only signal detected (tooltip/icon/visual)")

    return score, reasons


def score_case_risk_profile(case_text: str) -> tuple[int, list[str]]:
    text = (case_text or "").lower()
    score = 0
    reasons: list[str] = []

    if re.search(r'iserror[^\\n]{0,40}(equal to|=)\\s*"?true"?', text):
        score += 2
        reasons.append('Case validates "isError=true" path')
    if any(term in text for term in ["clock in", "clock out", "total hours", "shift history"]):
        score += 1
        reasons.append("Case touches core shift/timing flow")
    if any(term in text for term in ["tooltip", "info icon", "hover the info icon"]):
        score -= 1
        reasons.append("Case focuses on UI-only signal")

    return score, reasons


def is_ui_tooltip_case(case_text: str) -> bool:
    text = (case_text or "").lower()
    return any(term in text for term in ["tooltip", "info icon", "hover the info icon"])


def flatten_adf_text(node) -> str:
    if node is None:
        return ""
    if isinstance(node, str):
        return node
    if isinstance(node, list):
        return "\n".join(part for part in (flatten_adf_text(item) for item in node) if part)
    if isinstance(node, dict):
        node_type = safe_str(node.get("type")).lower()
        parts: list[str] = []
        text_val = node.get("text")
        if isinstance(text_val, str):
            parts.append(text_val)
        content = node.get("content")
        if content is not None:
            parts.append(flatten_adf_text(content))
        merged = " ".join(p for p in parts if p).strip()
        if node_type in {"paragraph", "heading", "listitem"} and merged:
            return merged + "\n"
        return merged
    return ""


def tokenize_for_overlap(text: str) -> set[str]:
    raw = re.findall(r"[a-zA-Z][a-zA-Z0-9_-]{2,}", (text or "").lower())
    stop = {
        "the", "and", "for", "with", "from", "that", "this", "user", "system",
        "case", "test", "step", "expected", "result", "when", "then", "into", "have",
    }
    return {t for t in raw if t not in stop}


def text_overlap_ratio(a: str, b: str) -> float:
    a_tokens = tokenize_for_overlap(a)
    b_tokens = tokenize_for_overlap(b)
    if not a_tokens or not b_tokens:
        return 0.0
    inter = len(a_tokens & b_tokens)
    return inter / max(1, len(a_tokens))


def strip_media_noise(text: str) -> str:
    if not text:
        return ""
    out = text
    out = re.sub(r"!\[[^\]]*\]\([^)]+\)", " ", out)
    out = re.sub(r"(?is)<img\b[^>]*>", " ", out)
    out = re.sub(r"(?:https?://[^\s)]+/attachments/get/\d+|index\.php\?/attachments/get/\d+)", " ", out)
    out = re.sub(r"(?i)\b(фото|photo|image)\b\.?", " ", out)
    out = re.sub(r"[ \t]{2,}", " ", out)
    out = re.sub(r"\n{3,}", "\n\n", out)
    return out.strip()


def extract_acceptance_criteria_items(text: str) -> list[dict]:
    lines = [line.strip() for line in re.split(r"\r?\n", text or "") if line.strip()]
    criteria: list[dict] = []
    seen_ids: set[str] = set()
    in_acceptance_block = False

    for line in lines:
        if re.search(r"(?i)acceptance criteria", line):
            in_acceptance_block = True
            continue

        ac_match = re.match(r"(?i)^(AC[\s_-]?\d+)\s*[:.)-]?\s*(.+)$", line)
        if ac_match:
            raw = re.sub(r"\s+", "", ac_match.group(1).upper()).replace("_", "-")
            digit_match = re.search(r"(\d+)$", raw)
            ac_id = digit_match.group(1) if digit_match else raw
            if ac_id not in seen_ids and ac_match.group(2).strip():
                criteria.append({"id": ac_id, "text": ac_match.group(2).strip()})
                seen_ids.add(ac_id)
            continue

        numbered_match = re.match(r"^(\d{1,2})[.)-]\s*(.+)$", line)
        if numbered_match:
            ac_id = numbered_match.group(1)
            if ac_id not in seen_ids and numbered_match.group(2).strip():
                criteria.append({"id": ac_id, "text": numbered_match.group(2).strip()})
                seen_ids.add(ac_id)
            continue

        bullet_match = re.match(r"^[-*]\s+(.+)$", line)
        if in_acceptance_block and bullet_match:
            # Use only explicit numbered criteria as requested.
            continue

    if not criteria:
        # Fallback for stories where criteria are written as plain paragraphs under "Flow:"
        # without explicit "1./2./3." markers.
        paragraphs = [p.strip() for p in re.split(r"\n\s*\n", text or "") if p.strip()]
        flow_started = False
        numbered: list[str] = []
        numbered_tokens: list[set[str]] = []
        for paragraph in paragraphs:
            low = paragraph.lower()
            if low.startswith("flow:"):
                flow_started = True
                continue
            if not flow_started and low.startswith("as a "):
                continue
            if not flow_started and len(paragraph) < 30:
                continue
            tokens = tokenize_for_overlap(paragraph)
            if not tokens:
                continue
            is_duplicate = False
            for seen_tokens in numbered_tokens:
                overlap = len(tokens & seen_tokens) / max(1, len(tokens | seen_tokens))
                if overlap >= 0.4:
                    is_duplicate = True
                    break
            if is_duplicate:
                continue
            numbered.append(paragraph)
            numbered_tokens.append(tokens)

        for idx, paragraph in enumerate(numbered[:3], start=1):
            ac_id = str(idx)
            criteria.append({"id": ac_id, "text": paragraph})

    return criteria


def evaluate_acceptance_criteria_relevance(case_text: str, criteria_items: list[dict]) -> tuple[list[str], list[dict]]:
    scored_items: list[dict] = []
    for item in criteria_items:
        relevance = text_overlap_ratio(case_text, item.get("text", ""))
        if relevance <= 0:
            continue
        scored_items.append(
            {
                "id": item.get("id"),
                "text": item.get("text"),
                "relevance": round(relevance, 3),
            }
        )

    scored_items.sort(key=lambda x: x.get("relevance", 0), reverse=True)
    matched = [item["id"] for item in scored_items if item.get("relevance", 0) >= 0.05]
    if matched and all(str(item).isdigit() for item in matched):
        matched = sorted(set(matched), key=lambda x: int(x))
    return matched, scored_items


def detect_secondary_number_variant(refactored: dict, acceptance_items: list[dict]) -> bool:
    title_lower = safe_str(refactored.get("title")).lower()
    steps = refactored.get("steps") or []
    first_step_lower = safe_str((steps[0] or {}).get("action") if steps else "").lower()
    case_lower = (title_lower + "\n" + first_step_lower).lower()
    acceptance_text = " ".join((item.get("text") or "") for item in acceptance_items).lower()

    case_has_english = "english number" in case_lower or re.search(r"\benglish\b", case_lower) is not None
    case_login_english = "login with english number" in case_lower or "log in with english number" in case_lower
    case_login_spanish = "login with spanish number" in case_lower or "log in with spanish number" in case_lower
    case_switch_spanish = "switches to the spanish number" in case_lower or "switch to the spanish number" in case_lower

    ac_has_spanish = "spanish number" in acceptance_text or re.search(r"\bspanish\b", acceptance_text) is not None
    ac_has_english = "english number" in acceptance_text or re.search(r"\benglish\b", acceptance_text) is not None

    # Secondary-path heuristic:
    # story ACs focus on Spanish flow, but the case validates English-number behavior.
    return (
        (case_login_english or case_has_english)
        and not case_login_spanish
        and not case_switch_spanish
        and ac_has_spanish
        and not ac_has_english
    )


def detect_indirect_regression_case(refactored: dict, acceptance_items: list[dict], relevance: float) -> bool:
    title = safe_str(refactored.get("title"))
    step_actions = [safe_str(step.get("action")) for step in (refactored.get("steps") or [])]
    head_text = "\n".join([title, *step_actions[:2]])
    ac_text = "\n".join((item.get("text") or "") for item in acceptance_items)

    head_tokens = tokenize_for_overlap(head_text)
    ac_tokens = tokenize_for_overlap(ac_text)
    if not head_tokens or not ac_tokens:
        return False

    direct_head_overlap = len(head_tokens & ac_tokens) / max(1, len(head_tokens))
    case_full_text = (title + "\n" + "\n".join(step_actions)).lower()
    regression_terms = [
        "regression", "after", "reopen", "again", "doesn't", "does not",
        "cannot", "can't", "not receive", "fallback", "alternative",
    ]
    has_regression_signal = any(term in case_full_text for term in regression_terms)

    # Generic policy: if case is mostly regression/indirect and weakly maps to AC head,
    # do not elevate to HIGH.
    return has_regression_signal and relevance < 0.15 and direct_head_overlap < 0.30


def detect_data_sync_case(refactored: dict, acceptance_items: list[dict], relevance: float) -> bool:
    title = safe_str(refactored.get("title"))
    step_actions = [safe_str(step.get("action")) for step in (refactored.get("steps") or [])]
    step_expected = [safe_str(step.get("expected_result")) for step in (refactored.get("steps") or [])]
    case_text = "\n".join(
        [
            title,
            *step_actions,
            *step_expected,
            safe_str(refactored.get("global_expected_result")),
        ]
    ).lower()
    acceptance_text = "\n".join((item.get("text") or "") for item in acceptance_items).lower()
    combined_text = f"{case_text}\n{acceptance_text}"

    sync_terms = [
        "sync", "synchron", "synchroniz", "integration", "integrat",
        "database", "db", "replication", "propagation", "ums api",
        "api call", "backend", "third-party", "external app", "other app",
        "cross-app", "data refresh", "reload", "disappear after reload",
        "de-assigned", "deassigned",
    ]
    has_sync_signal = any(term in combined_text for term in sync_terms)

    # Story-linked confidence for sync/integration scenarios:
    # apply only when case has at least moderate AC overlap to avoid unrelated boosts.
    return has_sync_signal and relevance >= 0.08


def detect_non_core_case(refactored: dict, acceptance_items: list[dict], relevance: float, acceptance_score: int) -> bool:
    title = safe_str(refactored.get("title")).lower()
    step_actions = [safe_str(step.get("action")).lower() for step in (refactored.get("steps") or [])]
    text = "\n".join([title, *step_actions])
    acceptance_text = "\n".join((item.get("text") or "") for item in acceptance_items).lower()

    non_core_terms = [
        "regression", "fallback", "alternative", "secondary", "optional",
        "negative path", "edge case", "non-core", "reopen", "again",
        "not receive", "doesn't", "does not", "cannot", "can't",
    ]
    has_non_core_signal = any(term in text for term in non_core_terms)
    ac_has_primary_signal = any(term in acceptance_text for term in ["main flow", "primary", "core flow"])

    # Non-core downgrade should be conservative:
    # weak AC linkage + low relevance + non-core/regression signal.
    return has_non_core_signal and relevance < 0.10 and acceptance_score <= 1 and not ac_has_primary_signal


def detect_rejoin_secondary_case(refactored: dict, acceptance_items: list[dict]) -> bool:
    title = safe_str(refactored.get("title")).lower()
    step_actions = [safe_str(step.get("action")).lower() for step in (refactored.get("steps") or [])]
    step_expected = [safe_str(step.get("expected_result")).lower() for step in (refactored.get("steps") or [])]
    case_text = "\n".join([title, *step_actions, *step_expected]).lower()
    acceptance_text = "\n".join((item.get("text") or "") for item in acceptance_items).lower()

    has_rejoin_signal = any(token in case_text for token in ["rejoin", "rejoins", "re-join"])
    has_after_signal = "after" in case_text
    has_emergency_context = any(token in (case_text + "\n" + acceptance_text) for token in ["911", "emergency"])
    has_mic_flow = all(token in case_text for token in ["mic icon", "mute", "unmute"])

    # Rejoin scenario is treated as secondary/regression coverage (MEDIUM),
    # even if acceptance text exists for this condition.
    return has_rejoin_signal and (has_after_signal or has_emergency_context) and has_mic_flow


def detect_primary_acceptance_case(
    refactored: dict,
    acceptance_items: list[dict],
    relevance: float,
    acceptance_score: int,
) -> bool:
    title = safe_str(refactored.get("title")).lower()
    step_actions = [safe_str(step.get("action")).lower() for step in (refactored.get("steps") or [])]
    step_expected = [safe_str(step.get("expected_result")).lower() for step in (refactored.get("steps") or [])]
    case_head = "\n".join([title, *step_actions[:2], *step_expected[:1]])
    case_text = "\n".join([title, *step_actions, *step_expected])
    acceptance_text = "\n".join((item.get("text") or "") for item in acceptance_items).lower()

    head_overlap = text_overlap_ratio(case_head, acceptance_text)
    case_has_mic_flow = all(token in case_text for token in ["mic icon", "mute", "unmute"])
    case_has_modes = all(token in case_text for token in ["portrait", "landscape"])
    has_secondary_signal = any(token in case_text for token in ["rejoin", "rejoins", "after", "regression", "fallback", "secondary"])

    # Main-path signal: direct AC alignment around core mic/mute flow + both modes.
    return (
        acceptance_score >= 1
        and (relevance >= 0.10 or head_overlap >= 0.18)
        and case_has_mic_flow
        and case_has_modes
        and not has_secondary_signal
    )


def should_set_low_for_unaligned_ui_or_secondary(
    *,
    acceptance_score: int,
    relevance: float,
    ui_only_case: bool,
    secondary_variant_case: bool,
    indirect_regression_case: bool,
    non_core_case: bool,
) -> bool:
    ui_unaligned = (
        acceptance_score <= 0
        and relevance < 0.08
        and ui_only_case
    )
    weak_non_core = (
        relevance < 0.10
        and acceptance_score <= 1
        and non_core_case
    )
    return ui_unaligned or weak_non_core


async def audit_priority_for_case(session: aiohttp.ClientSession, case_id: int, raw_case: dict, refactored: dict) -> dict:
    story_refs = collect_story_refs(raw_case.get("refs", ""))
    jira_issue_keys = extract_jira_issue_keys(story_refs)
    current_priority_id = safe_int(raw_case.get("priority_id"), PRIORITY_ID_MEDIUM)

    steps_text = "\n".join(step.get("action", "") for step in refactored.get("steps", []))
    case_text = "\n".join(
        [
            safe_str(refactored.get("title")),
            safe_str(refactored.get("preconditions")),
            steps_text,
            safe_str(refactored.get("global_expected_result")),
        ]
    )
    case_text = strip_media_noise(case_text)
    refs_text = "\n".join(story_refs)
    case_score, case_reasons = score_priority_signals(case_text, "")
    case_risk_score, case_risk_reasons = score_case_risk_profile(case_text)
    ui_only_case = is_ui_tooltip_case(case_text)

    reasons: list[str] = []
    analysis: list[str] = []
    jira_payloads: list[dict] = []
    story_evidence: list[dict] = []
    acceptance_criteria_evidence: list[dict] = []
    matched_acceptance_criteria_ids: list[str] = []
    proposed_priority_id: int | None = None
    selected_story_key = ""
    selected_story_relevance = 0.0
    selected_story_jira_priority = ""
    acceptance_fields_meta = get_jira_acceptance_fields_meta()
    secondary_variant_case = False
    indirect_regression_case = False
    data_sync_case = False
    non_core_case = False
    rejoin_secondary_case = False
    primary_acceptance_case = False

    for key in jira_issue_keys:
        fields = await fetch_jira_issue_fields(session, key)
        if not fields:
            continue
        jira_payloads.append({"key": key, "fields": fields})

    if jira_payloads:
        ranked: list[tuple[float, int, int, dict]] = []
        for issue in jira_payloads:
            priority_name = safe_str((issue["fields"].get("priority") or {}).get("name"))
            issue_type = safe_str((issue["fields"].get("issuetype") or {}).get("name"))
            status_name = safe_str((issue["fields"].get("status") or {}).get("name"))
            summary_text = safe_str(issue["fields"].get("summary"))
            parent_key = safe_str((issue["fields"].get("parent") or {}).get("key"))
            description_text = flatten_adf_text(issue["fields"].get("description"))
            acceptance_source_text = collect_acceptance_text(issue["fields"])
            acceptance_items = extract_acceptance_criteria_items(acceptance_source_text)
            acceptance_text = "\n".join(item.get("text", "") for item in acceptance_items if item.get("text"))
            relevance = text_overlap_ratio(case_text, acceptance_text)
            matched_ac_for_story, scored_ac_for_story = evaluate_acceptance_criteria_relevance(case_text, acceptance_items)
            acceptance_score = len(matched_ac_for_story)
            is_secondary_variant = detect_secondary_number_variant(refactored, acceptance_items)
            is_indirect_regression = detect_indirect_regression_case(refactored, acceptance_items, relevance)
            is_data_sync_case = detect_data_sync_case(refactored, acceptance_items, relevance)
            is_non_core_case = detect_non_core_case(refactored, acceptance_items, relevance, acceptance_score)
            is_rejoin_secondary_case = detect_rejoin_secondary_case(refactored, acceptance_items)
            is_primary_acceptance_case = detect_primary_acceptance_case(
                refactored,
                acceptance_items,
                relevance,
                acceptance_score,
            )
            ranked.append((relevance, acceptance_score, len(acceptance_text), issue))
            story_evidence.append(
                {
                    "issue_key": issue["key"],
                    "summary": summary_text,
                    "acceptance_excerpt": (description_text[:300] + "...") if len(description_text) > 300 else description_text,
                    "status": status_name,
                    "issue_type": issue_type,
                    "jira_priority": priority_name,
                    "acceptance_score": acceptance_score,
                    "matched_acceptance_criteria_ids": matched_ac_for_story,
                    "is_secondary_variant_case": is_secondary_variant,
                    "is_indirect_regression_case": is_indirect_regression,
                    "is_data_sync_case": is_data_sync_case,
                    "is_non_core_case": is_non_core_case,
                    "is_rejoin_secondary_case": is_rejoin_secondary_case,
                    "is_primary_acceptance_case": is_primary_acceptance_case,
                    "parent_key": parent_key,
                    "relevance_to_case": round(relevance, 3),
                }
            )
            acceptance_criteria_evidence.append(
                {
                    "issue_key": issue["key"],
                    "criteria": scored_ac_for_story[:10],
                }
            )
            analysis.append(
                f"{issue['key']}: type={issue_type or 'N/A'}, status={status_name or 'N/A'}, relevance={relevance:.3f}, acceptance_score={acceptance_score}"
            )
        ranked.sort(key=lambda x: (x[0], x[1], x[2]), reverse=True)
        top_relevance, top_acceptance_score, _, top_issue = ranked[0]
        selected_story_key = safe_str(top_issue.get("key"))
        selected_story_relevance = float(top_relevance)
        selected_story_jira_priority = safe_str((top_issue["fields"].get("priority") or {}).get("name"))
        matched_acceptance_criteria_ids = []
        for evidence in story_evidence:
            if safe_str(evidence.get("issue_key")) == selected_story_key:
                matched_acceptance_criteria_ids = evidence.get("matched_acceptance_criteria_ids", [])
                secondary_variant_case = bool(evidence.get("is_secondary_variant_case"))
                indirect_regression_case = bool(evidence.get("is_indirect_regression_case"))
                data_sync_case = bool(evidence.get("is_data_sync_case"))
                non_core_case = bool(evidence.get("is_non_core_case"))
                rejoin_secondary_case = bool(evidence.get("is_rejoin_secondary_case"))
                primary_acceptance_case = bool(evidence.get("is_primary_acceptance_case"))
                break
        proposed_priority_id = derive_priority_from_acceptance(
            top_relevance,
            case_score,
            case_risk_score,
            top_acceptance_score,
            ui_only_case,
        )
        if data_sync_case and proposed_priority_id < PRIORITY_ID_HIGH:
            proposed_priority_id = PRIORITY_ID_HIGH
        if secondary_variant_case or indirect_regression_case:
            proposed_priority_id = PRIORITY_ID_MEDIUM
        low_for_unaligned = should_set_low_for_unaligned_ui_or_secondary(
            acceptance_score=top_acceptance_score,
            relevance=top_relevance,
            ui_only_case=ui_only_case,
            secondary_variant_case=secondary_variant_case,
            indirect_regression_case=indirect_regression_case,
            non_core_case=non_core_case,
        )
        if low_for_unaligned:
            proposed_priority_id = PRIORITY_ID_LOW
        reasons.append("Priority derived from story acceptance criteria + case content")
        reasons.append(
            f"Matched acceptance criteria for selected story: "
            f"{', '.join(matched_acceptance_criteria_ids) if matched_acceptance_criteria_ids else 'none'}"
        )
        if secondary_variant_case:
            reasons.append("Secondary number-variant flow detected -> MEDIUM by policy")
        if indirect_regression_case:
            reasons.append("Indirect/regression scenario detected -> MEDIUM by policy (even with AC overlap)")
        if non_core_case:
            reasons.append("Non-core/secondary scenario with weak AC coverage detected")
        if low_for_unaligned:
            reasons.append("UI/non-core scenario with weak story-AC alignment -> LOW by policy")
        if data_sync_case:
            reasons.append("Data sync/integration scenario with story alignment detected -> HIGH by policy")
        if ui_only_case:
            reasons.append("UI/tooltip-focused case -> LOW by policy")
        elif proposed_priority_id == PRIORITY_ID_HIGH:
            reasons.append("Main functional path covered in acceptance criteria -> HIGH by policy")
        elif proposed_priority_id == PRIORITY_ID_MEDIUM:
            reasons.append("Non-core but relevant acceptance coverage -> MEDIUM by policy")
        elif proposed_priority_id == PRIORITY_ID_CRITICAL:
            reasons.append("Rare severe-path conditions met -> CRITICAL by policy")
        reasons.append(
            f"Primary story for this case: {selected_story_key} "
            f"(relevance={top_relevance:.3f}, acceptance_score={top_acceptance_score}, "
            f"case_score={case_score}, case_risk_score={case_risk_score})"
        )
    else:
        score, heuristic_reasons = score_priority_signals(case_text, refs_text)
        proposed_priority_id = PRIORITY_ID_MEDIUM
        if score >= 3:
            proposed_priority_id = PRIORITY_ID_HIGH
        elif score >= 2:
            proposed_priority_id = PRIORITY_ID_MEDIUM
        elif score <= 0:
            proposed_priority_id = PRIORITY_ID_LOW
        reasons.extend(heuristic_reasons)
        reasons.append("Fallback heuristic used (acceptance criteria unavailable)")
    reasons.extend(case_reasons)
    reasons.extend(case_risk_reasons)

    if ENABLE_EPIC_ANALYSIS:
        epic_keys = re.findall(r"\b[A-Z][A-Z0-9]+-\d+\b", refs_text, flags=re.IGNORECASE)
        if epic_keys:
            analysis.append(f"Epic references: {', '.join(sorted(set(epic_keys)))}")
    if ENABLE_PR_ANALYSIS:
        pr_urls = [u for u in story_refs if "/pull/" in u or "pull-requests" in u]
        if pr_urls:
            analysis.append(f"PR references: {len(pr_urls)}")

    if proposed_priority_id == current_priority_id:
        reasons.append("Priority unchanged after audit")
    if ui_only_case and proposed_priority_id == PRIORITY_ID_HIGH:
        reasons.append("CRITICAL is blocked for UI/tooltip-focused cases")

    return {
        "case_id": case_id,
        "story_refs": story_refs,
        "jira_issue_keys": jira_issue_keys,
        "current_priority_id": current_priority_id,
        "proposed_priority_id": proposed_priority_id,
        "changed": proposed_priority_id != current_priority_id,
        "reasons": reasons,
        "selected_story_key": selected_story_key,
        "selected_story_relevance": round(selected_story_relevance, 3),
        "selected_story_jira_priority": selected_story_jira_priority,
        "matched_acceptance_criteria_ids": matched_acceptance_criteria_ids,
        "case_impact_score": case_score,
        "case_risk_score": case_risk_score,
        "is_ui_tooltip_case": ui_only_case,
        "is_data_sync_case": data_sync_case,
        "is_non_core_case": non_core_case,
        "priority_decision_basis": {
            "selected_story_key": selected_story_key,
            "selected_story_relevance": round(selected_story_relevance, 3),
            "selected_story_jira_priority": selected_story_jira_priority,
            "matched_acceptance_criteria_ids": matched_acceptance_criteria_ids,
            "case_impact_score": case_score,
            "case_risk_score": case_risk_score,
            "is_ui_tooltip_case": ui_only_case,
            "is_data_sync_case": data_sync_case,
            "is_non_core_case": non_core_case,
            "rules": reasons,
        },
        "story_evidence": story_evidence,
        "acceptance_criteria_evidence": acceptance_criteria_evidence,
        "acceptance_fields_meta": acceptance_fields_meta,
        "optional_analysis": analysis,
    }


def should_run_priority_analyzer(results: list[dict]) -> bool:
    if PRIORITY_ANALYZER_MODE == "no":
        return False
    if PRIORITY_ANALYZER_MODE == "yes":
        return True

    prepared = [r for r in results if r.get("status") == "prepared_only"]
    if not prepared:
        return False

    priority_ids = [safe_int(r.get("raw_case", {}).get("priority_id"), PRIORITY_ID_MEDIUM) for r in prepared]
    unique = {p for p in priority_ids if p is not None}
    if PRIORITY_DEFAULT_ID:
        default_id = safe_int(PRIORITY_DEFAULT_ID)
        return bool(default_id is not None and len(unique) == 1 and default_id in unique)
    return len(unique) <= 1


def ask_priority_validation(priority_audits: list[dict]) -> str:
    changed = [a for a in priority_audits if a.get("changed")]
    eligible = [a for a in changed if (a.get("matched_acceptance_criteria_ids") or a.get("current_priority_id") is None)]
    print("\n[PRIORITY] Manual validation required")
    print(f"[PRIORITY] Audited cases: {len(priority_audits)}")
    print(f"[PRIORITY] Cases with proposed priority changes (analyzer): {len(changed)}")
    print(f"[PRIORITY] Proposed changes eligible for apply (with AC evidence): {len(eligible)}")
    for item in changed[:20]:
        before = safe_int(item.get("current_priority_id"))
        after = safe_int(item.get("proposed_priority_id"))
        print(f"  - C{item['case_id']}: {priority_name(before)} -> {priority_name(after)}")
        reasons = item.get("reasons", [])
        if reasons:
            print(f"    reason: {reasons[0]}")
        if not item.get("matched_acceptance_criteria_ids"):
            print("    note: No AC match; final priority may remain unchanged after rule application")

    return ask_yes_no("[PRIORITY] Approve applying proposed priorities? (yes/no): ", default_yes=True)


def ask_run_priority_for_section() -> str:
    return ask_yes_no(
        "[PRIORITY] Run priority analyzer for all prepared cases in this section? (yes/no): ",
        default_yes=True,
    )


def ask_yes_no(prompt: str, *, default_yes: bool) -> str:
    accepted_yes = {"yes", "y", "так", "т", "taк"}
    accepted_no = {"no", "n", "ні", "нi", "н"}
    fallback = "yes" if default_yes else "no"

    while True:
        try:
            answer = input(prompt).strip().lower()
        except EOFError:
            log(f"{prompt} <non-interactive mode: default={fallback}>")
            return fallback
        if answer in accepted_yes:
            return "yes"
        if answer in accepted_no:
            return "no"
        log("Please enter 'yes/no' or 'так/ні'.")


def resolve_case_ids() -> list[int]:
    raw_case_ids = os.getenv("CASE_IDS", "").strip()
    if not raw_case_ids:
        return [14175]
    return [int(chunk.strip()) for chunk in raw_case_ids.split(",") if chunk.strip()]


async def collect_raw_cases(session: aiohttp.ClientSession, case_ids: list[int]) -> list[dict]:
    raw_cases: list[dict] = []
    for case_id in case_ids:
        log(f"[C{case_id}] Fetching")
        raw_case = await get_case(session, case_id)
        raw_cases.append({"case_id": case_id, "raw_case": raw_case})
    return raw_cases


async def main():
    output_dir = ensure_output_dir()
    ts = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    jira_subagent = JiraTicketPrioritySubagent(audit_priority_for_case=audit_priority_for_case)
    priority_rules_skill = PriorityRulesSkill(priority_id_medium=PRIORITY_ID_MEDIUM)
    main_agent = MainRefactorAgent(
        refactor_case=refactor_case_with_agent,
        refactored_model_cls=RefactoredTestCase,
        validation_error_cls=ValidationError,
        jira_subagent=jira_subagent,
        priority_rules_skill=priority_rules_skill,
        priority_id_low=PRIORITY_ID_LOW,
        priority_id_medium=PRIORITY_ID_MEDIUM,
        priority_id_high=PRIORITY_ID_HIGH,
    )

    auth = aiohttp.BasicAuth(TESTRAIL_EMAIL, TESTRAIL_API_KEY)
    async with aiohttp.ClientSession(auth=auth) as session:
        case_ids = await get_cases_in_section(session, SECTION_ID) if USE_ALL_CASES_IN_SECTION else resolve_case_ids()
        print(f"Section {SECTION_ID}: processing {len(case_ids)} case(s)")
        log(f"Processing {len(case_ids)} case(s)")

        raw_cases = await collect_raw_cases(session, case_ids)
        raw_bundle_path = os.path.join(output_dir, f"raw-bundle-{ts}.json")
        json_dump(raw_bundle_path, raw_cases)
        log(f"Saved raw cases JSON: raw-bundle-{ts}.json")
        raw_cases = json_load(raw_bundle_path)
        log("[AGENT:MAIN] Loaded raw cases from JSON for local analysis")

        results = await main_agent.process_section(raw_cases)
        priority_audits, priority_stage_enabled, priority_run_confirmed, priority_approved = await main_agent.run_priority_subagent(
            session,
            results,
            output_dir,
            ts,
            should_run_priority_analyzer=should_run_priority_analyzer,
            require_manual_approval=REQUIRE_MANUAL_APPROVAL,
            ask_run_priority_for_section=ask_run_priority_for_section,
            ask_priority_validation=ask_priority_validation,
            json_dump=json_dump,
        )

        section_approved = True
        if REQUIRE_MANUAL_APPROVAL:
            section_approved = ask_section_validation(results) == "yes"

        if section_approved and CREATE_IN_TESTRAIL:
            for result in results:
                if result.get("status") != "prepared_only":
                    continue
                created_case_id = await create_refactored_case(
                    session,
                    result["case_id"],
                    result["raw_case"],
                    result["refactored"],
                    result.get("priority_audit"),
                )
                result["created_case_id"] = created_case_id
                result["status"] = "created"
        elif not section_approved:
            for result in results:
                if result.get("status") == "prepared_only":
                    result["status"] = "skipped_by_user"

    json_dump(os.path.join(output_dir, f"bundle-{ts}.json"), results)

    summary = {
        "generated_at_utc": ts,
        "total": len(results),
        "updated": 0,
        "created": sum(1 for r in results if "created" in safe_str(r.get("status"))),
        "prepared_only": sum(1 for r in results if r.get("status") == "prepared_only"),
        "skipped_by_user": sum(1 for r in results if r.get("status") == "skipped_by_user"),
        "validation_failed": sum(1 for r in results if r.get("status") == "validation_failed"),
        "create_in_testrail": CREATE_IN_TESTRAIL,
        "write_mode": "create",
        "manual_approval": REQUIRE_MANUAL_APPROVAL,
        "priority_analyzer_mode": PRIORITY_ANALYZER_MODE,
        "priority_stage_enabled": priority_stage_enabled and priority_run_confirmed,
        "priority_changed": sum(1 for r in results if (r.get("priority_audit") or {}).get("changed")),
        "priority_after_distribution": {
            str(pid): sum(1 for r in results if safe_int((r.get("refactored") or {}).get("priority_id")) == pid)
            for pid in [PRIORITY_ID_LOW, PRIORITY_ID_MEDIUM, PRIORITY_ID_HIGH, PRIORITY_ID_CRITICAL]
        },
        "priority_case_decisions": [
            {
                "case_id": r.get("case_id"),
                "before": (r.get("priority_audit") or {}).get("current_priority_id"),
                "after": safe_int((r.get("refactored") or {}).get("priority_id")),
                "selected_story": (r.get("priority_audit") or {}).get("selected_story_key"),
                "relevance": (r.get("priority_audit") or {}).get("selected_story_relevance"),
                "case_impact_score": (r.get("priority_audit") or {}).get("case_impact_score"),
                "case_risk_score": (r.get("priority_audit") or {}).get("case_risk_score"),
                "matched_acceptance_criteria_ids": (r.get("priority_audit") or {}).get("matched_acceptance_criteria_ids", []),
                "application_rule": (r.get("priority_audit") or {}).get("application_rule"),
                "rules": (r.get("priority_audit") or {}).get("reasons", []),
            }
            for r in results
            if r.get("status") in {"prepared_only", "created"}
        ],
        "epic_analysis_enabled": ENABLE_EPIC_ANALYSIS,
        "pr_analysis_enabled": ENABLE_PR_ANALYSIS,
        "agents": {
            "main": main_agent.name,
            "subagents": [main_agent.subagent_name],
            "skills": [priority_rules_skill.name],
            "tree": {
                "main_refactor_agent": {
                    "subagent": "jira_ticket_priority_subagent",
                    "skill": "priority_rules_skill",
                }
            },
        },
        "priority_approved": priority_approved,
        "section_id": SECTION_ID,
    }
    json_dump(os.path.join(output_dir, f"analysis-{ts}.json"), summary)
    execution_report = build_execution_report(ts, summary, results)
    json_dump(os.path.join(output_dir, f"execution-report-{ts}.json"), execution_report)
    with open(os.path.join(output_dir, f"execution-report-{ts}.md"), "w", encoding="utf-8") as f:
        f.write(render_execution_report_markdown(execution_report))

    print(f"Execution report (MD): {os.path.join(output_dir, f'execution-report-{ts}.md')}")
    print(f"Execution report (JSON): {os.path.join(output_dir, f'execution-report-{ts}.json')}")
    for item in summary.get("priority_case_decisions", []):
        print(
            f"C{item.get('case_id')}: priority {item.get('before')} -> {item.get('after')} | "
            f"story={item.get('selected_story') or 'N/A'}"
        )
    if VERBOSE_TERMINAL:
        print(json.dumps(summary, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    asyncio.run(main())
