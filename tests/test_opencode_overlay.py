"""The team OpenCode overlay must never stall a brief on a folder a worker always reads,
and must never open a token file.

Creator, 9 Nov: "I have to approve a million things a day for opencode. not very
autonomous". The matcher below copies OpenCode 1.19.5's behaviour as established by the
security review (`.hx/reports/kx-rev-F.md`): backslashes become slashes on both
sides, matching is case-insensitive, ``*`` spans separators, and the last matching
rule wins. The engine expands only ``~/``, a bare ``~``, ``$HOME/`` and a
``$HOME`` prefix; a ``~`` before a backslash stays literal. It asks
``external_directory`` with ``dirname(file)\\*``, never the file.
"""
import json
import re
from pathlib import Path

from hx import profile as profile_mod

OVERLAY = Path(__file__).resolve().parents[1] / "team" / "opencode-trusted-dev.jsonc"
HOME = "C:\\Users\\tester\\"

BS = chr(92)
DEFAULT_READ = {"*": "allow", "*.env": "ask", "*.env.*": "ask", "*.env.example": "allow"}


def match(target: str, pattern: str) -> bool:
    """The engine matcher, with its fromConfig expansion of home prefixes."""
    target = target.replace(BS, "/")
    home = HOME.rstrip(BS)
    if pattern.startswith("~/"):
        pattern = home + pattern[1:]
    elif pattern == "~":
        pattern = home
    elif pattern == "$HOME":
        pattern = home
    elif pattern.startswith("$HOME"):
        pattern = home + pattern[len("$HOME"):]
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
        "C:/Users/tester/.codex/auth.json",
        "C:/Users/tester/.codex/config.toml",
        "C:/Users/tester/.claude/.credentials.json",
        "C:/Users/tester/.config/opencode/opencode.jsonc",
        "C:/Users/tester/AppData/Roaming/devin/credentials.toml",
        "C:/Users/tester/.ssh/id_ed25519",
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


def test_tilde_backslash_is_literal_and_home_rules_expand():
    # OpenCode leaves a "~" followed by a backslash literal, so the overlay
    # must use a form the engine expands and the evaluator must reach it.
    target = HOME + ".codex" + BS + "sub" + BS + "file"
    assert not match(target, "~" + BS + ".codex" + BS + "*")
    assert match(target, "~/.codex/*")
    assert match(target, "$HOME" + BS + ".codex" + BS + "*")
    rules = _overlay()
    assert evaluate("external_directory", rules, target) == "ask", "the .codex ask rule must match"
    for block in ("external_directory", "read", "edit"):
        assert not [key for key in rules[block] if key.startswith("~")], block


def test_every_home_rule_matches_a_concrete_target():
    rules = _overlay()
    home = HOME.rstrip(BS)
    for block in ("external_directory", "read", "edit"):
        for key in rules[block]:
            if not key.startswith("$HOME"):
                continue
            target = home + key[len("$HOME"):].replace("*", "x")
            assert match(target, key), (block, key)


def test_drive_relative_edits_outside_the_repo_are_denied():
    rules = _overlay()
    for target in (
        "Users/tester/work-repo/a.cs",
        "Users/tester/.bashrc",
        "users/TESTER/work-repo/a.cs",
        "../../../work-repo/a.cs",
        "..\\..\\..\\.claude\\skills\\x\\SKILL.md",
    ):
        assert evaluate("edit", rules, target) == "deny", target
    assert evaluate("edit", rules, "hx/profile.py") == "allow"


def test_overlay_matches_the_one_off_permission_model():
    rules = _overlay()
    external = rules["external_directory"]
    home = str(Path.home())

    def portable(value: str) -> str:
        if value == home:
            return "$HOME"
        if value.startswith(home + BS) or value.startswith(home + "/"):
            return "$HOME" + value[len(home):]
        return value

    def target(value: str) -> str:
        if value == "$HOME":
            return HOME.rstrip(BS)
        if value.startswith("$HOME"):
            return HOME.rstrip(BS) + value[len("$HOME"):]
        return value

    for root in profile_mod.OPENCODE_READ_ROOTS:
        key = portable(root)
        assert external.get(key) == "allow", key
        assert evaluate("external_directory", rules, target(key)) == "allow", key
    for directory in profile_mod.OPENCODE_ASK_DIRS:
        key = portable(directory)
        assert external.get(key) == "ask", key
        assert evaluate("external_directory", rules, target(key)) == "ask", key
    for glob in profile_mod.OPENCODE_SECRET_GLOBS:
        assert external.get(portable(glob)) == "deny", glob
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
    "C:\\Users\\tester\\.agents\\pstack\\*": 7,
    "C:\\Users\\tester\\.agents\\skills\\herdr-team\\*": 1,
    "C:\\Users\\tester\\.agents\\skills\\kickoff\\*": 1,
    "C:\\Users\\tester\\.claude\\skills\\*": 1,
    "C:\\Users\\tester\\.config\\herdr-app\\*": 1,
    "C:\\Users\\tester\\.config\\opencode\\node_modules\\*": 1,
    "C:\\Users\\tester\\.herdr-team\\state\\hc-bindings\\*": 3,
    "C:\\Users\\tester\\.local\\bin\\*": 1,
    "C:\\Users\\tester\\AppData\\Local\\Temp\\*": 1,
    "C:\\Users\\tester\\AppData\\Local\\Temp\\claude\\0000000000000000\\30c7d0f5-5b60-4da6-9a8d-f77c8958cf05\\scratchpad\\*": 1,
    "C:\\Users\\tester\\AppData\\Local\\Temp\\claude\\C--Users-tester\\6363b92e-c30b-4a81-a5d4-d382a2848927\\scratchpad\\artifact-files\\62569bbb-a8ca-4690-81af-ee10254dd452\\project\\*": 1,
    "C:\\Users\\tester\\AppData\\Local\\Temp\\claude\\C--Users-tester\\6363b92e-c30b-4a81-a5d4-d382a2848927\\scratchpad\\artifact-files\\62569bbb-a8ca-4690-81af-ee10254dd452\\project\\slides\\*": 3,
    "C:\\Users\\tester\\AppData\\Local\\herdr-core\\default\\*": 1,
    "C:\\Users\\tester\\AppData\\Roaming\\herdr-core\\*": 1,
    "C:\\Users\\tester\\Documents\\Notes\\FDE Knowledge\\*": 1,
    "C:\\Users\\tester\\Documents\\Projects\\sample-app\\docs\\prompts\\*": 1,
    "C:\\Users\\tester\\work-repo\\*": 2,
    "C:\\Users\\tester\\work-repo\\.artifacts\\unity-slice\\*": 1,
    "C:\\Users\\tester\\work-repo\\.hx\\briefs\\*": 1,
    "C:\\plugin\\dist\\*": 1,
}
MINED_ASK_ONLY = {
    "C:\\Users\\tester\\.codex\\*": 1,
    "C:\\Users\\tester\\.config\\opencode\\*": 1,
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
