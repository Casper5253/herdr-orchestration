"""Tests for hx.profile and profile-aware preflight."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from hx import capability
from hx import profile as profile_mod
from test_opencode_overlay import evaluate


def _policy() -> dict:
    return json.loads(Path("policy.json").read_text(encoding="utf-8"))


def test_profiles_present() -> None:
    for name in ("research", "review", "implement", "release"):
        assert profile_mod.get(name, _policy()) is not None, name
    assert profile_mod.get("nope", _policy()) is None


def test_devin_config_render_carries_profile_and_secret_rules() -> None:
    named = profile_mod.get("implement", _policy())
    config = profile_mod.render_devin_config(named, {"permissions": {"allow": [], "deny": ["Write(.env*)", "Exec(git push)", "Read(~/.ssh/**)"]}})
    deny = config["permissions"]["deny"]
    assert "Exec(git push)" in deny
    assert "Write(.env*)" in deny and "Read(~/.ssh/**)" in deny


def test_opencode_permission_render_allows_carve_outs() -> None:
    named = profile_mod.get("implement", _policy())
    perm = profile_mod.render_opencode_permission(named)
    assert perm["bash"]["*"] == "deny"
    assert perm["bash"]["git push*"] == "deny"
    assert perm["bash"]["git checkout -b*"] == "allow"
    assert perm["bash"]["git status*"] == "allow"


def test_implement_allows_checkout_b_refuses_push(tmp_path: Path) -> None:
    brief = "Work on a new branch: `git checkout -b feat/x`, then run `python -m pytest`."
    assert capability.prompt_findings("devin", brief, tmp_path, _policy(), "implement") == []
    push = "Ship it: `git push origin feat/x`."
    rules = {f["rule"] for f in capability.prompt_findings("devin", push, tmp_path, _policy(), "implement")}
    assert "Exec(git push)" in rules


def test_research_refuses_a_write_path(tmp_path: Path) -> None:
    brief = "Read the repo and write findings to C:\\Users\\caspe\\out\\notes.md."
    findings = capability.prompt_findings("devin", brief, tmp_path, _policy(), "research")
    assert any(f["kind"] == "outside-cwd" for f in findings)


def test_research_denies_git_checkout_that_implement_allows(tmp_path: Path) -> None:
    brief = "Start with `git checkout -b feat/x`."
    rules = {f["rule"] for f in capability.prompt_findings("devin", brief, tmp_path, _policy(), "research")}
    assert "Exec(git checkout)" in rules
    assert capability.prompt_findings("devin", brief, tmp_path, _policy(), "implement") == []


def test_release_is_lead_only(tmp_path: Path) -> None:
    findings = capability.prompt_findings("devin", "do anything", tmp_path, _policy(), "release")
    assert findings and findings[0]["kind"] == "profile"


def test_static_behaviour_unchanged_without_profile(tmp_path: Path) -> None:
    brief = "Start with `git checkout -b feat/x`."
    rules = {f["rule"] for f in capability.prompt_findings("devin", brief, tmp_path, _policy())}
    assert "Exec(git checkout)" in rules


def test_prepare_run_renders_profiled_devin_config(tmp_path: Path) -> None:
    from hx import devin as devin_mod

    run_dir = tmp_path / "run"
    files = devin_mod.prepare_run(run_dir, "brief text", _policy(), profile="implement")
    config = json.loads(files["config"].read_text(encoding="utf-8"))
    deny = config["permissions"]["deny"]
    assert "Exec(git push)" in deny
    # `git checkout` is covered by the `git checkout -b` allow, so it moves to ask.
    assert "Exec(git checkout)" in config["permissions"]["ask"]
    assert "Write(.env*)" in deny
    prompt = files["prompt"].read_text(encoding="utf-8")
    assert "Exec(git push)" in prompt


# ---------- C5: profiles fail closed ----------

_BASE = {
    "permissions": {
        "allow": ["read", "grep", "glob", "edit", "exec", "mcp__jotarium__*"],
        "deny": ["Write(.env*)", "Exec(git push)", "Read(~/.ssh/**)"],
    }
}


def test_malformed_profile_is_an_error() -> None:
    assert profile_mod.validate({"name": "x"}) is not None
    assert profile_mod.validate({"deny": "Exec(git)", "allow": [], "write_roots": []}) is not None
    assert profile_mod.validate({"deny": [], "allow": [], "write_roots": []}) is None
    with pytest.raises(ValueError):
        profile_mod.render_devin_config({"name": "empty"}, _BASE)
    with pytest.raises(ValueError):
        profile_mod.render_opencode_permission({"name": "empty", "deny": [], "allow": []})


def test_malformed_profile_refuses_at_preflight(tmp_path: Path) -> None:
    policy = _policy()
    policy["profiles"]["broken"] = {"note": "no deny/allow lists"}
    findings = capability.prompt_findings("devin", "do things", tmp_path, policy, "broken")
    assert findings and findings[0]["kind"] == "profile"


def test_research_devin_config_is_read_only() -> None:
    named = profile_mod.get("research", _policy())
    config = profile_mod.render_devin_config(named, _BASE)
    perms = config["permissions"]
    assert "edit" not in perms["allow"] and "exec" not in perms["allow"]
    assert "edit" in perms["deny"]
    assert "Write(**)" in perms["deny"]
    # exec is ask: the profile's allow list is the whole exec surface, and a
    # specific Exec allow carves out of the broader ask.
    assert "exec" in perms["ask"]
    assert "Exec(git status)" in perms["allow"]
    assert "Exec(python -m pytest)" in perms["allow"]
    assert "Exec(git push)" in perms["deny"]
    assert "Write(.env*)" in perms["deny"] and "Read(~/.ssh/**)" in perms["deny"]


def test_implement_devin_config_carves_allows_out_of_ask() -> None:
    named = profile_mod.get("implement", _policy())
    config = profile_mod.render_devin_config(named, _BASE)
    perms = config["permissions"]
    # exec stays allowed for a write-capable profile; the deny list bounds it.
    assert "exec" in perms["allow"]
    # A deny an allow rule covers moves to ask so the allow can carve out.
    assert "Exec(git checkout)" not in perms["deny"]
    assert "Exec(git checkout)" in perms["ask"]
    assert "Exec(git checkout -b)" in perms["allow"]
    assert "Exec(git switch)" in perms["ask"]
    assert "Exec(git switch)" in perms["allow"]
    assert "Exec(git push)" in perms["deny"]
    # write_roots cwd only: the common outside-worktree escapes are denied.
    assert "Write(~/**)" in perms["deny"]
    assert "Write(../**)" in perms["deny"]


def test_opencode_implement_edit_scoped_to_worktree_and_secrets_denied() -> None:
    named = profile_mod.get("implement", _policy())
    perm = profile_mod.render_opencode_permission(named)
    assert perm["edit"]["*"] == "deny"
    assert perm["edit"]["**"] == "allow"
    assert perm["edit"]["**\\.env*"] == "deny"
    assert perm["edit"]["**\\.git\\**"] == "deny"
    assert perm["external_directory"]["*"] == "deny"
    assert perm["external_directory"][profile_mod.OPENCODE_SKILL_ROOTS[0]] == "allow"
    assert perm["read"]["~\\.ssh\\**"] == "deny"


def test_opencode_research_denies_edit_and_external() -> None:
    named = profile_mod.get("research", _policy())
    perm = profile_mod.render_opencode_permission(named)
    assert perm["edit"] == "deny"
    assert perm["external_directory"]["*"] == "deny"
    assert perm["bash"]["*"] == "deny"
    for root in profile_mod.OPENCODE_SKILL_ROOTS:
        assert perm["external_directory"][root] == "allow", root


# ---------- F: permission composition for one-off OpenCode runs ----------
#
# The matcher comes from the security review of the real 1.18.27 engine
# (`.hx/reports/kx-rev-F.md`): last match wins, backslashes become slashes,
# matching is case-insensitive, and external_directory is asked with
# `dirname(file)\*`, never the file.

HOME = str(Path.home())
MODES = ("research", "review", "implement", "baseline")


def _permission(mode: str) -> dict:
    if mode == "baseline":
        return profile_mod.render_opencode_baseline()["permission"]
    return profile_mod.render_opencode_permission(profile_mod.get(mode, _policy()))


def _directory_forms(directory: str) -> list[str]:
    base = HOME + "\\" + directory.replace("/", "\\")
    variants = [base + "\\*", base.replace("\\", "/") + "/*"]
    return variants + [value.lower() for value in variants] + [value.upper() for value in variants]


SECRET_ASK_DIRS = [
    ".codex",
    ".claude",
    ".config/opencode",
    "AppData/Roaming/devin",
    ".local/share/opencode",
]


def _secret_files() -> list[str]:
    return [
        HOME + "\\.codex\\auth.json",
        HOME + "\\.codex\\config.toml",
        HOME + "\\.codex\\trusted-dev.config.toml",
        HOME + "\\.claude\\.credentials.json",
        HOME + "\\.local\\share\\opencode\\auth.json",
        HOME + "\\.config\\opencode\\opencode.jsonc",
        HOME + "\\AppData\\Roaming\\devin\\credentials.toml",
        HOME + "\\.ssh\\id_ed25519",
        HOME + "\\Documents\\Projects\\herdr-orchestration\\.env",
    ]


def _relative(target: str) -> str:
    return "..\\..\\.." + target[len(HOME):]


def _drive_relative(target: str) -> str:
    return "Users\\caspe" + target[len(HOME):]


@pytest.mark.parametrize("mode", MODES)
def test_skill_reads_allowed_in_every_mode(mode: str) -> None:
    permission = _permission(mode)
    for root in profile_mod.OPENCODE_SKILL_ROOTS:
        assert evaluate("external_directory", permission, root) == "allow", (mode, root)
    for directory in (".agents/pstack", ".claude/skills", ".codex/skills",
                      "AppData/Roaming/devin/skills"):
        for form in _directory_forms(directory):
            assert evaluate("external_directory", permission, form) == "allow", (mode, form)


@pytest.mark.parametrize("mode", MODES)
def test_skill_edits_denied_in_every_mode(mode: str) -> None:
    edit = _permission(mode)["edit"]
    if isinstance(edit, str):
        assert edit == "deny", mode
        return
    for target in (
        HOME + "\\.agents\\pstack\\TEAM.md",
        HOME + "\\.claude\\skills\\poteto-mode\\SKILL.md",
        HOME + "\\.codex\\skills\\poteto-mode\\SKILL.md",
        HOME + "\\AppData\\Roaming\\devin\\skills\\poteto-mode\\SKILL.md",
    ):
        assert evaluate("edit", _permission(mode), _relative(target)) == "deny", (mode, target)
        assert evaluate("edit", _permission(mode), _drive_relative(target)) == "deny", (mode, target)


@pytest.mark.parametrize("mode", MODES)
def test_secret_directories_ask_or_deny_in_every_mode(mode: str) -> None:
    # A profiled run denies everything outside its roots; the unprofiled
    # baseline asks, because the same directory also holds skills the run needs.
    # SSH keys are denied in every mode.
    expected = "ask" if mode == "baseline" else "deny"
    permission = _permission(mode)
    for directory in SECRET_ASK_DIRS:
        for form in _directory_forms(directory):
            assert evaluate("external_directory", permission, form) == expected, (mode, form)
    for form in _directory_forms(".ssh"):
        assert evaluate("external_directory", permission, form) == "deny", (mode, form)


@pytest.mark.parametrize("mode", MODES)
def test_secret_reads_denied_in_every_mode(mode: str) -> None:
    permission = _permission(mode)
    for target in _secret_files():
        assert evaluate("read", permission, _relative(target)) == "deny", (mode, target)
    for target in (".env", ".env.local", "sub/.env"):
        assert evaluate("read", permission, target) == "deny", (mode, target)


@pytest.mark.parametrize("mode", MODES)
def test_secret_writes_denied_in_every_mode(mode: str) -> None:
    permission = _permission(mode)
    for target in _secret_files():
        assert evaluate("edit", permission, _relative(target)) == "deny", (mode, target)


@pytest.mark.parametrize("mode", MODES)
def test_writes_outside_the_repo_stay_denied(mode: str) -> None:
    permission = _permission(mode)
    for target in (
        "..\\..\\..\\Kintsugi\\scripts\\build.py",
        "Users\\caspe\\Kintsugi\\scripts\\build.py",
        "Users\\caspe\\.bashrc",
        "users\\CASPE\\Kintsugi\\scripts\\build.py",
    ):
        assert evaluate("edit", permission, target) == "deny", (mode, target)
    if mode in ("implement", "baseline"):
        assert evaluate("edit", permission, "hx\\profile.py") == "allow", mode


@pytest.mark.parametrize("mode", MODES)
def test_rule_keys_use_the_backslash_form_the_engine_matches(mode: str) -> None:
    # The engine normalises both sides, so a forward-slash target still matches
    # a backslash rule; rule keys stay backslash for one readable form.
    permission = _permission(mode)
    for name in ("external_directory", "read", "edit"):
        block = permission[name]
        if isinstance(block, dict):
            assert not [rule for rule in block if "/" in rule], (mode, name)
    for directory in SECRET_ASK_DIRS:
        forward = (HOME + "/" + directory + "/*").lower()
        expected = "ask" if mode == "baseline" else "deny"
        assert evaluate("external_directory", permission, forward) == expected, (mode, forward)
    for form in _directory_forms(".ssh"):
        assert evaluate("external_directory", permission, form) == "deny", (mode, form)


def test_unprofiled_baseline_keeps_the_team_bash_boundary() -> None:
    permission = _permission("baseline")
    assert evaluate("bash", permission, "herdr agent prompt dev-codex hi") == "deny"
    assert evaluate("bash", permission, "hc send t1 hi") == "ask"
    assert evaluate("bash", permission, "hc schema --json") == "allow"
    assert evaluate("bash", permission, "cat .env") == "deny"
    for target in (
        "C:/Users/caspe/.codex/auth.json",
        "C:/Users/caspe/.codex/config.toml",
        "C:/Users/caspe/.claude/.credentials.json",
        "C:/Users/caspe/.config/opencode/opencode.jsonc",
        "C:/Users/caspe/AppData/Roaming/devin/credentials.toml",
        "C:/Users/caspe/.ssh/id_ed25519",
    ):
        assert evaluate("bash", permission, "cat " + target) == "deny", target


# The external_directory asks from the log since 2 Nov. The two parent-directory
# entries are the review finding: a read of a file directly in `.codex` or
# `.config\opencode` asks (baseline) or is denied (profile) because those
# directories also hold a token.
MINED_ALLOW = [
    HOME + "\\.agents\\pstack\\*",
    HOME + "\\.agents\\skills\\kickoff\\*",
    HOME + "\\.agents\\skills\\herdr-team\\*",
    HOME + "\\.claude\\skills\\*",
    HOME + "\\.config\\herdr-app\\*",
    HOME + "\\.config\\opencode\\node_modules\\*",
    HOME + "\\.herdr-team\\state\\hc-bindings\\*",
    HOME + "\\.local\\bin\\*",
    HOME + "\\AppData\\Local\\Temp\\*",
    HOME + "\\AppData\\Local\\Temp\\claude\\C--Users-caspe\\6363b92e-c30b-4a81-a5d4-d382a2848927\\scratchpad\\artifact-files\\62569bbb-a8ca-4690-81af-ee10254dd452\\project\\slides\\*",
    HOME + "\\AppData\\Local\\herdr-core\\default\\*",
    HOME + "\\AppData\\Roaming\\herdr-core\\*",
    HOME + "\\Documents\\Notes\\FDE Knowledge\\*",
]
MINED_CROSS_PROJECT = [
    HOME + "\\Kintsugi\\*",
    HOME + "\\Kintsugi\\.hx\\briefs\\*",
    HOME + "\\Kintsugi\\.artifacts\\unity-slice\\*",
    HOME + "\\Documents\\Projects\\chill-game\\docs\\prompts\\*",
    "C:\\plugin\\dist\\*",
]
MINED_SECRET_PARENTS = [
    HOME + "\\.codex\\*",
    HOME + "\\.config\\opencode\\*",
]


@pytest.mark.parametrize("mode", MODES)
def test_mined_asks_resolve_as_designed(mode: str) -> None:
    permission = _permission(mode)
    for target in MINED_ALLOW:
        assert evaluate("external_directory", permission, target) == "allow", (mode, target)
    # The unprofiled baseline allows any external read; a profile denies
    # everything outside its roots.
    cross = "allow" if mode == "baseline" else "deny"
    for target in MINED_CROSS_PROJECT:
        assert evaluate("external_directory", permission, target) == cross, (mode, target)
    expected = "ask" if mode == "baseline" else "deny"
    for target in MINED_SECRET_PARENTS:
        assert evaluate("external_directory", permission, target) == expected, (mode, target)

