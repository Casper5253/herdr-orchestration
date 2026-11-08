"""Tests for hx.profile and profile-aware preflight."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from hx import capability
from hx import profile as profile_mod


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
    assert perm["edit"]["**/.env*"] == "deny"
    assert perm["edit"]["**/.git/**"] == "deny"
    assert perm["external_directory"] == "deny"
    assert perm["read"]["~/.ssh/**"] == "deny"


def test_opencode_research_denies_edit_and_external() -> None:
    named = profile_mod.get("research", _policy())
    perm = profile_mod.render_opencode_permission(named)
    assert perm["edit"] == "deny"
    assert perm["external_directory"] == "deny"
    assert perm["bash"]["*"] == "deny"
