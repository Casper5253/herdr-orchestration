"""The team OpenCode overlay must never stall a brief on a folder a worker always reads."""
import json
import re
from pathlib import Path

OVERLAY = Path(__file__).resolve().parents[1] / "team" / "opencode-trusted-dev.jsonc"
HOME = "C:\\Users\\caspe\\"

READ_WITHOUT_ASKING = [
    ".agents\\*",
    ".claude\\skills\\*",
    ".config\\herdr-app\\*",
    "AppData\\Local\\herdr-core\\*",
    "AppData\\Local\\Temp\\*",
]
NEVER_EDIT = [
    ".agents\\*",
    ".claude\\skills\\*",
    ".config\\herdr-app\\*",
    "AppData\\Local\\herdr-core\\*",
]


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
