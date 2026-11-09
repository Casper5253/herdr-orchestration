"""The team OpenCode overlay must never stall a brief on a folder a worker always reads."""
import json
import re
from pathlib import Path

OVERLAY = Path(__file__).resolve().parents[1] / "team" / "opencode-trusted-dev.jsonc"
HOME = "C:\\Users\\caspe\\"

HERDR_FOLDERS = [
    ".config\\herdr-app\\*",
    "AppData\\Local\\herdr-core\\*",
    "AppData\\Roaming\\herdr-core\\*",
    "AppData\\Roaming\\herdr\\*",
    "AppData\\Local\\Programs\\herdr-core\\*",
    "AppData\\Local\\Programs\\herdr-app\\*",
]
READ_WITHOUT_ASKING = [
    ".agents\\*", ".claude\\skills\\*", "AppData\\Local\\Temp\\*",
    ".config\\opencode\\node_modules\\*", ".config\\opencode\\plugins\\*", *HERDR_FOLDERS,
    ".herdr-team\\state\\hc-bindings\\*",
]


SECRET_FOLDERS = {
    ".ssh\\*": "deny",
    ".config\\opencode\\*": "ask",
    ".codex\\*": "ask",
    "AppData\\Roaming\\devin\\*": "ask",
    ".claude\\*": "ask",
}


def test_reading_outside_the_repo_is_free_except_secret_folders():
    # Owner, 9 Nov: "i dont like having to approve all this stuff".
    rules = _overlay()["permission"]["external_directory"]
    assert rules.get("*") == "allow"
    wrong = {d: rules.get(HOME + d) for d, want in SECRET_FOLDERS.items() if rules.get(HOME + d) != want}
    assert not wrong, f"secret folders must ask or deny in {OVERLAY.name}: {wrong}"
    keys = list(rules)
    # last match wins: each secret folder's rule must come before any narrower allow inside it
    for d in SECRET_FOLDERS:
        for inner in (k for k in keys if k.startswith(HOME + d[:-1]) and k != HOME + d):
            assert keys.index(HOME + d) < keys.index(inner), f"{inner} must follow {d}"


NEVER_EDIT = [".agents\\*", ".claude\\skills\\*", *HERDR_FOLDERS]


def _overlay() -> dict:
    text = OVERLAY.read_text(encoding="utf-8")
    return json.loads(re.sub(r"^\s*//.*$", "", text, flags=re.M))


def test_worker_reads_team_folders_without_a_prompt():
    rules = _overlay()["permission"]["external_directory"]
    missing = [d for d in READ_WITHOUT_ASKING if rules.get(HOME + d) != "allow"]
    assert not missing, f"add these to external_directory as allow in {OVERLAY.name}: {missing}"


def test_worker_never_edits_skills_or_app_state():
    rules = _overlay()["permission"]["edit"]
    missing = [d for d in NEVER_EDIT if rules.get(HOME + d) != "deny"]
    assert not missing, f"add these to edit as deny in {OVERLAY.name}: {missing}"


def test_hc_mutations_ask_and_reads_are_free():
    bash = _overlay()["permission"]["bash"]
    assert bash.get("hc *") == "ask"
    for read in ("hc ls*", "hc get *", "hc read *", "hc wait *"):
        assert bash.get(read) == "allow", read


def test_worker_can_report_to_the_lead_with_hx_say():
    # A brief ends with `hx say claude "[team report ...]"`; `hx say` is covered by
    # the "*" allow and enforces the team boundary and the outbox.
    bash = _overlay()["permission"]["bash"]
    assert bash.get("*") == "allow"
    assert "hc send *" not in bash


def test_raw_hc_send_asks_again():
    bash = _overlay()["permission"]["bash"]
    assert bash.get("hc *") == "ask"
    assert bash.get("hc send *") is None, "raw `hc send` must fall through to the ask"


def test_raw_herdr_control_is_denied_and_reads_stay_free():
    bash = _overlay()["permission"]["bash"]
    keys = list(bash)
    assert bash["herdr *"] == "deny", "herdr agent prompt/start must never reach the creator as a prompt"
    for read in ("herdr agent list*", "herdr agent get *", "herdr agent read *", "herdr --skill*"):
        assert bash[read] == "allow"
        assert keys.index(read) > keys.index("herdr *"), f"{read} must come after the deny (last match wins)"
