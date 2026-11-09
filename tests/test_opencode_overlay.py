"""The team OpenCode overlay must never stall a brief on a folder a worker always reads,
and must never open a token file.

Creator, 9 Nov: "I have to approve a million things a day for opencode. not very
autonomous". The matcher below is OpenCode 1.18.27's, as established by the security
review (`.hx/reports/kx-rev-F.md`): backslashes become slashes on both sides, matching
is case-insensitive, ``*`` spans separators, and the last matching rule wins. The
engine asks ``external_directory`` with ``dirname(file)\\*``, never the file.
"""
import json
import re
from pathlib import Path

from hx import profile as profile_mod

OVERLAY = Path(__file__).resolve().parents[1] / "team" / "opencode-trusted-dev.jsonc"
HOME = "C:\\Users\\caspe\\"

BS = chr(92)
DEFAULT_READ = {"*": "allow", "*.env": "ask", "*.env.*": "ask", "*.env.example": "allow"}


def match(target: str, pattern: str) -> bool:
    """The engine matcher, copied from the 1.18.27 behaviour."""
    target = target.replace(BS, "/")
    pattern = pattern.replace(BS, "/")
    regex = re.escape(pattern).replace(re.escape("*"), ".*").replace(re.escape("?"), ".")
    tail = re.escape(" ") + ".*"
    if regex.endswith(tail):
        regex = regex[: -len(tail)] + "( .*)?"
    return re.match("^" + regex + "$", target, re.S | re.I) is not None


def evaluate(permission: str, rules: dict, target: str) -> str:
    """Resolve one permission target: last match wins, default ask."""
    seq: list[tuple[str, str]] = []
    if permission == "read":
        seq += list(DEFAULT_READ.items())
    block = rules.get(permission)
    if isinstance(block, str):
        seq.append(("*", block))
    elif block:
        seq += list(block.items())
    result = "ask"
    for pattern, action in seq:
        if match(target, pattern):
            result = action
    return result


def _overlay() -> dict:
    text = OVERLAY.read_text(encoding="utf-8")
    return json.loads(re.sub(r"^\s*//.*$", "", text, flags=re.M))["permission"]


def _directory_patterns(directory: str) -> list[str]:
    """The exact forms the engine may send for a file in this directory."""
    base = HOME + directory
    variants = [base + BS + "*", base.replace(BS, "/") + "/*"]
    return variants + [value.lower() for value in variants] + [value.upper() for value in variants]


HERDR_FOLDERS = [
    ".config\\herdr-app\\*",
    "AppData\\Local\\herdr-core\\*",
    "AppData\\Roaming\\herdr-core\\*",
    "AppData\\Roaming\\herdr\\*",
    "AppData\\Local\\Programs\\herdr-core\\*",
    "AppData\\Local\\Programs\\herdr-app\\*",
]
READ_WITHOUT_ASKING = [
    ".agents\\*", ".claude\\skills\\*", ".codex\\skills\\*",
    "AppData\\Roaming\\devin\\skills\\*",
    ".herdr-team\\*", ".herdr-team\\state\\hc-bindings\\*",
    "AppData\\Local\\Temp\\*", ".local\\bin\\*",
    ".config\\opencode\\agents\\*", ".config\\opencode\\node_modules\\*",
    ".config\\opencode\\plugins\\*", "Documents\\Notes\\FDE Knowledge\\*",
    *HERDR_FOLDERS,
]

SECRET_DIRS = [
    ".codex\\*",
    ".claude\\*",
    ".config\\opencode\\*",
    "AppData\\Roaming\\devin\\*",
    ".local\\share\\opencode\\*",
    ".ssh\\*",
]

# The engine asks with the directory, so a secret file is reached through its
# parent directory. These are the parents that hold a token file.
SECRET_FILE_DIRS = [
    ".codex",
    ".claude",
    ".config/opencode",
    "AppData/Roaming/devin",
    ".local/share/opencode",
    ".ssh",
]


def test_worker_reads_team_folders_without_a_prompt():
    rules = _overlay()
    missing = [
        d for d in READ_WITHOUT_ASKING
        for form in _directory_patterns(d[:-2])
        if evaluate("external_directory", rules, form) != "allow"
    ]
    assert not missing, f"add these to external_directory as allow in {OVERLAY.name}: {missing}"


def test_secret_directories_ask_for_both_separators_and_case():
    rules = _overlay()
    for directory in SECRET_FILE_DIRS:
        for form in _directory_patterns(directory):
            assert evaluate("external_directory", rules, form) != "allow", form


def test_bash_cat_of_a_secret_path_is_not_allowed():
    rules = _overlay()
    for directory in SECRET_FILE_DIRS:
        for form in _directory_patterns(directory):
            assert evaluate("external_directory", rules, form) != "allow", form
    for target in (
        "C:/Users/caspe/.codex/auth.json",
        "C:/Users/caspe/.codex/config.toml",
        "C:/Users/caspe/.claude/.credentials.json",
        "C:/Users/caspe/.config/opencode/opencode.jsonc",
        "C:/Users/caspe/AppData/Roaming/devin/credentials.toml",
        "C:/Users/caspe/.ssh/id_ed25519",
        ".env",
    ):
        assert evaluate("bash", rules, "cat " + target) == "deny", target
    assert evaluate("bash", rules, "cat sub/.env") == "deny"


def test_grep_and_glob_over_secret_directories_are_not_allowed():
    # grep and glob gate external reads through the same external_directory ask.
    rules = _overlay()
    for directory in SECRET_FILE_DIRS:
        for form in _directory_patterns(directory):
            assert evaluate("external_directory", rules, form) != "allow", form


def test_root_env_is_denied_on_read():
    rules = _overlay()
    for target in (".env", ".env.local", ".env.example", "sub/.env", "..\\..\\..\\.env"):
        assert evaluate("read", rules, target) == "deny", target
    assert evaluate("read", rules, "..\\..\\..\\.agents\\pstack\\TEAM.md") == "allow"


def test_drive_relative_edits_outside_the_repo_are_denied():
    rules = _overlay()
    for target in (
        "Users/caspe/Kintsugi/a.cs",
        "Users/caspe/.bashrc",
        "users/CASPE/Kintsugi/a.cs",
        "../../../Kintsugi/a.cs",
        "..\\..\\..\\.claude\\skills\\x\\SKILL.md",
    ):
        assert evaluate("edit", rules, target) == "deny", target
    assert evaluate("edit", rules, "hx/profile.py") == "allow"


def test_overlay_matches_the_one_off_permission_model():
    rules = _overlay()
    external = rules["external_directory"]
    for root in profile_mod.OPENCODE_READ_ROOTS:
        assert external.get(root) == "allow", root
        assert evaluate("external_directory", rules, root) == "allow", root
    for directory in profile_mod.OPENCODE_ASK_DIRS:
        assert external.get(directory) == "ask", directory
    for glob in profile_mod.OPENCODE_SECRET_GLOBS:
        assert external.get(glob) == "deny", glob
    assert rules["bash"] == profile_mod.OPENCODE_TEAM_BASH


def test_hc_mutations_ask_and_reads_are_free():
    rules = _overlay()
    assert evaluate("bash", rules, "hc send t1 hi") == "ask"
    for read in ("hc ls", "hc get t1", "hc read t1", "hc wait t1"):
        assert evaluate("bash", rules, read) == "allow", read


def test_raw_herdr_control_is_denied_and_reads_stay_free():
    rules = _overlay()
    assert evaluate("bash", rules, "herdr agent prompt dev-codex hi") == "deny"
    for read in ("herdr agent list", "herdr agent get dev-codex", "herdr --skill"):
        assert evaluate("bash", rules, read) == "allow", read


def test_worker_can_report_to_the_lead_with_hx_say():
    rules = _overlay()
    assert evaluate("bash", rules, "hx say dev-claude hi") == "allow"


# Every external_directory and bash ask in the log since 2 Nov, parsed with no
# secret content. Counts are the number of log entries. The two parent-directory
# asks are the review's finding 1: a read of a file directly in `.codex` or
# `.config\opencode` now asks, because those directories also hold a token.
MINED_EXTERNAL = {
    "C:\\Users\\caspe\\.agents\\pstack\\*": 7,
    "C:\\Users\\caspe\\.agents\\skills\\herdr-team\\*": 1,
    "C:\\Users\\caspe\\.agents\\skills\\kickoff\\*": 1,
    "C:\\Users\\caspe\\.claude\\skills\\*": 1,
    "C:\\Users\\caspe\\.config\\herdr-app\\*": 1,
    "C:\\Users\\caspe\\.config\\opencode\\node_modules\\*": 1,
    "C:\\Users\\caspe\\.herdr-team\\state\\hc-bindings\\*": 3,
    "C:\\Users\\caspe\\.local\\bin\\*": 1,
    "C:\\Users\\caspe\\AppData\\Local\\Temp\\*": 1,
    "C:\\Users\\caspe\\AppData\\Local\\Temp\\claude\\6c78b54081e86add\\30c7d0f5-5b60-4da6-9a8d-f77c8958cf05\\scratchpad\\*": 1,
    "C:\\Users\\caspe\\AppData\\Local\\Temp\\claude\\C--Users-caspe\\6363b92e-c30b-4a81-a5d4-d382a2848927\\scratchpad\\artifact-files\\62569bbb-a8ca-4690-81af-ee10254dd452\\project\\*": 1,
    "C:\\Users\\caspe\\AppData\\Local\\Temp\\claude\\C--Users-caspe\\6363b92e-c30b-4a81-a5d4-d382a2848927\\scratchpad\\artifact-files\\62569bbb-a8ca-4690-81af-ee10254dd452\\project\\slides\\*": 3,
    "C:\\Users\\caspe\\AppData\\Local\\herdr-core\\default\\*": 1,
    "C:\\Users\\caspe\\AppData\\Roaming\\herdr-core\\*": 1,
    "C:\\Users\\caspe\\Documents\\Notes\\FDE Knowledge\\*": 1,
    "C:\\Users\\caspe\\Documents\\Projects\\chill-game\\docs\\prompts\\*": 1,
    "C:\\Users\\caspe\\Kintsugi\\*": 2,
    "C:\\Users\\caspe\\Kintsugi\\.artifacts\\unity-slice\\*": 1,
    "C:\\Users\\caspe\\Kintsugi\\.hx\\briefs\\*": 1,
    "C:\\plugin\\dist\\*": 1,
}
MINED_ASK_ONLY = {
    "C:\\Users\\caspe\\.codex\\*": 1,
    "C:\\Users\\caspe\\.config\\opencode\\*": 1,
}
MINED_BASH = {
    "rg -n \"\\[\\\"\"": "allow",
    "hc schema --json": "allow",
    "herdr agent prompt --help": "deny",
    "herdr agent": "deny",
    "herdr pane\" hx/": "deny",
}


def test_mined_asks_resolve_as_designed():
    rules = _overlay()
    for pattern in MINED_EXTERNAL:
        assert evaluate("external_directory", rules, pattern) == "allow", pattern
    for pattern in MINED_ASK_ONLY:
        assert evaluate("external_directory", rules, pattern) == "ask", pattern
    for command, expected in MINED_BASH.items():
        assert evaluate("bash", rules, command) == expected, command
