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
]


def test_opencode_config_with_its_token_is_not_pre_allowed():
    rules = _overlay()["permission"]["external_directory"]
    assert HOME + ".config\\opencode\\*" not in rules, "opencode.jsonc holds a token; allow subfolders only"
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


def test_worker_can_report_to_the_lead():
    # A brief ends with `hc send <lead> "[team report ...]"`; a prompt here means the lead never hears back.
    assert _overlay()["permission"]["bash"].get("hc send *") == "allow"
