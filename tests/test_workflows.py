"""Static guards for the release pipeline: the key must be unreachable from pull requests."""

from __future__ import annotations

import re

import yaml

from harness import ROOT

WORKFLOWS = ROOT / ".github" / "workflows"


def load(name: str) -> tuple[dict, str]:
    text = (WORKFLOWS / name).read_text(encoding="utf-8")
    return yaml.safe_load(text), text


def triggers(doc: dict) -> dict:
    return doc.get(True) or doc.get("on")  # YAML 1.1 reads the key `on` as True


def test_no_workflow_uses_dangerous_triggers():
    for path in WORKFLOWS.glob("*.yml"):
        doc, text = load(path.name)
        assert "pull_request_target" not in triggers(doc), path.name
        assert "workflow_run" not in triggers(doc), path.name
        assert doc["permissions"] == {"contents": "read"}, f"{path.name}: default token must be read-only"


def test_ci_has_no_secrets_and_runs_on_pull_requests():
    doc, text = load("ci.yml")
    assert "pull_request" in triggers(doc)
    assert "secrets." not in text and "environment:" not in text


def test_release_runs_only_on_tags_and_only_sign_job_sees_the_key():
    doc, text = load("release.yml")
    assert set(triggers(doc)) == {"push"} and triggers(doc)["push"] == {"tags": ["v*"]}
    holders = [name for name, job in doc["jobs"].items() if "secrets.PIXELREF_RELEASE_KEY" in yaml.safe_dump(job)]
    assert holders == ["sign"], holders
    assert doc["jobs"]["sign"]["environment"] == "release"
    assert "secrets." not in yaml.safe_dump({k: v for k, v in doc["jobs"].items() if k != "sign"})
    writers = [name for name, job in doc["jobs"].items() if job.get("permissions", {}).get("contents") == "write"]
    assert writers == ["publish"]
    assert doc["jobs"]["publish"]["needs"] == "sign" and doc["jobs"]["sign"]["needs"] == "build"


def test_untrusted_values_never_expand_inside_shell_scripts():
    """Tag names, branch names and secrets reach scripts only through env vars, never `${{ }}` in `run`."""
    for path in WORKFLOWS.glob("*.yml"):
        doc, _ = load(path.name)
        for job in doc["jobs"].values():
            for step in job["steps"]:
                assert "${{" not in step.get("run", ""), (path.name, step.get("name"))


def test_actions_are_versioned_and_publish_verifies_before_releasing():
    for path in WORKFLOWS.glob("*.yml"):
        doc, _ = load(path.name)
        for job in doc["jobs"].values():
            for step in job["steps"]:
                if "uses" in step:
                    assert re.match(r"^[\w.-]+/[\w.-]+@(v\d+|[0-9a-f]{40})$", step["uses"]), step["uses"]
    publish = load("release.yml")[0]["jobs"]["publish"]["steps"]
    names = [s.get("name", "") for s in publish]
    draft = next(i for i, n in enumerate(names) if n.startswith("Create a draft"))
    verify = next(i for i, n in enumerate(names) if n.startswith("Re-download"))
    final = next(i for i, n in enumerate(names) if n.startswith("Publish"))
    assert draft < verify < final
    assert "--draft" in publish[draft]["run"] and "--draft=false" in publish[final]["run"]
