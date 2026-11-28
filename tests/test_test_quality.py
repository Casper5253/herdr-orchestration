"""The shared pytest test quality gate (Q-1189)."""

from pathlib import Path

from hx import test_quality


def write_test(root: Path, name: str, text: str) -> Path:
    path = root / name
    path.write_text(text, encoding="utf-8")
    return path


def failures(root: Path, name: str = "test_sample.py") -> list[dict]:
    result = test_quality.check(root, files=[name])
    return result["failures"]


def test_a_test_with_no_assertion_is_flagged(tmp_path: Path) -> None:
    write_test(tmp_path, "test_sample.py", "def test_x():\n    call()\n")
    assert [failure["rules"] for failure in failures(tmp_path)] == [["no-assertion"]]


def test_an_asserting_helper_counts_as_an_assertion(tmp_path: Path) -> None:
    write_test(
        tmp_path,
        "test_sample.py",
        "def check(value):\n    assert value == 1\n\ndef test_x():\n    check(1)\n",
    )
    assert failures(tmp_path) == []


def test_truthiness_and_not_none_are_flagged(tmp_path: Path) -> None:
    write_test(
        tmp_path,
        "test_sample.py",
        "def test_a():\n    assert value\n\ndef test_b():\n    assert value is not None\n",
    )
    assert [failure["rules"] for failure in failures(tmp_path)] == [
        ["truthiness-only"],
        ["truthiness-only"],
    ]


def test_a_concrete_comparison_passes(tmp_path: Path) -> None:
    write_test(tmp_path, "test_sample.py", "def test_x():\n    assert value == 3\n")
    assert failures(tmp_path) == []


def test_pytest_raises_counts_as_an_assertion(tmp_path: Path) -> None:
    write_test(
        tmp_path,
        "test_sample.py",
        "import pytest\n\ndef test_x():\n    with pytest.raises(ValueError):\n        call()\n",
    )
    assert failures(tmp_path) == []


def test_isinstance_is_a_concrete_check(tmp_path: Path) -> None:
    write_test(tmp_path, "test_sample.py", "def test_x():\n    assert isinstance(value, int)\n")
    assert failures(tmp_path) == []


def test_patching_the_subject_module_is_flagged(tmp_path: Path) -> None:
    write_test(
        tmp_path,
        "test_sample.py",
        "def test_x(monkeypatch):\n    monkeypatch.setattr(\"pkg.sample\", object())\n    assert sample.value() == 1\n",
    )
    assert [failure["rules"] for failure in failures(tmp_path)] == [["patches-subject"]]


def test_patching_a_function_in_the_subject_passes(tmp_path: Path) -> None:
    write_test(
        tmp_path,
        "test_sample.py",
        "def test_x(monkeypatch):\n    monkeypatch.setattr(\"pkg.sample.value\", lambda: 1)\n    assert sample.value() == 1\n",
    )
    assert failures(tmp_path) == []


def test_a_class_method_and_self_assert_are_checked(tmp_path: Path) -> None:
    write_test(
        tmp_path,
        "test_sample.py",
        "class TestThing:\n    def test_x(self):\n        self.assertTrue(value)\n",
    )
    records = failures(tmp_path)
    assert [record["name"] for record in records] == ["TestThing::test_x"]
    assert records[0]["rules"] == ["truthiness-only"]


def test_self_assert_equal_passes(tmp_path: Path) -> None:
    write_test(
        tmp_path,
        "test_sample.py",
        "class TestThing:\n    def test_x(self):\n        self.assertEqual(value, 1)\n",
    )
    assert failures(tmp_path) == []


def test_the_allowlist_suppresses_a_failure(tmp_path: Path) -> None:
    write_test(tmp_path, "test_sample.py", "def test_x():\n    assert value\n")
    result = test_quality.check(
        tmp_path,
        files=["test_sample.py"],
        allowlist=[
            {"file": "test_sample.py", "test": "test_x", "reason": "the absent value is the contract"}
        ],
    )
    assert result["failures"] == []
    assert result["allowlisted"] == 1


def test_an_allowlist_entry_needs_a_reason(tmp_path: Path) -> None:
    (tmp_path / "test-quality-allowlist.json").write_text(
        '[{"file": "test_sample.py", "test": "test_x"}]', encoding="utf-8"
    )
    entries, problems = test_quality.load_allowlist(tmp_path)
    assert entries == []
    assert len(problems) == 1
    assert "has no reason" in problems[0]


def test_a_stale_allowlist_entry_is_reported(tmp_path: Path) -> None:
    write_test(tmp_path, "test_sample.py", "def test_x():\n    assert value\n")
    result = test_quality.check(
        tmp_path,
        files=["test_sample.py"],
        allowlist=[{"file": "test_sample.py", "test": "removed", "reason": "old exception"}],
    )
    assert len(result["stale"]) == 1
    assert result["stale"][0]["test"] == "removed"


def test_testpaths_come_from_pyproject(tmp_path: Path) -> None:
    (tmp_path / "pyproject.toml").write_text(
        '[tool.pytest.ini_options]\ntestpaths = ["cases"]\n', encoding="utf-8"
    )
    (tmp_path / "cases").mkdir()
    (tmp_path / "tests").mkdir()
    (tmp_path / "cases" / "test_one.py").write_text("def test_x():\n    assert value == 1\n", encoding="utf-8")
    (tmp_path / "tests" / "test_other.py").write_text("def test_y():\n    assert value == 1\n", encoding="utf-8")
    assert test_quality.test_files(tmp_path) == ["cases/test_one.py"]


def test_a_bom_prefixed_test_file_is_read(tmp_path: Path) -> None:
    (tmp_path / "test_bom.py").write_text(
        "def test_x():\n    assert value == 1\n", encoding="utf-8-sig"
    )
    result = test_quality.check(tmp_path, files=["test_bom.py"])
    assert result["failures"] == []


def test_a_typical_empty_agent_test_is_rejected(tmp_path: Path) -> None:
    write_test(
        tmp_path,
        "test_sample.py",
        "def test_adds():\n    result = add(2, 2)\n    assert result is not None\n",
    )
    records = failures(tmp_path)
    assert [record["rules"] for record in records] == [["truthiness-only"]]
