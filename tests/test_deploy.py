"""The prepared deployment cannot drift from the code it would run.

Nothing here touches AWS. The template is read as JSON and held to the properties
``deploy/README.md`` claims for it: the handler names a callable that exists, CORS is locked to
one origin and one method, concurrency is reserved and small, the IAM grant names one action on
the configured model and nothing else, the budget and the alarm exist, and no secret or address
is committed. The README and the build script are checked for the claims a reader would act on.
"""

from __future__ import annotations

import importlib
import json
from pathlib import Path
from typing import Any

import pytest

from disclosed.ask import service

ROOT = Path(__file__).resolve().parent.parent
DEPLOY = ROOT / "deploy"


@pytest.fixture(scope="module")
def template() -> dict[str, Any]:
    return dict(json.loads((DEPLOY / "template.json").read_text(encoding="utf-8")))


@pytest.fixture(scope="module")
def function(template: dict[str, Any]) -> dict[str, Any]:
    return dict(template["Resources"]["AskFunction"]["Properties"])


class TestTheFunction:
    def test_the_handler_names_a_callable_that_exists(self, function: dict[str, Any]) -> None:
        module_name, attribute = function["Handler"].rsplit(".", 1)
        module = importlib.import_module(module_name)
        assert callable(getattr(module, attribute))
        assert getattr(module, attribute) is service.lambda_handler

    def test_concurrency_is_reserved_and_small(self, function: dict[str, Any]) -> None:
        assert 1 <= function["ReservedConcurrentExecutions"] <= 2

    def test_cors_is_locked_to_one_origin_and_post(self, function: dict[str, Any]) -> None:
        cors = function["FunctionUrlConfig"]["Cors"]
        assert cors["AllowOrigins"] == [{"Ref": "PagesOrigin"}]
        assert cors["AllowMethods"] == ["POST"]
        assert [h.lower() for h in cors["AllowHeaders"]] == ["content-type"]
        assert function["FunctionUrlConfig"]["AuthType"] == "NONE"

    def test_the_origin_parameter_defaults_to_the_pages_origin_the_code_defaults_to(
        self, template: dict[str, Any]
    ) -> None:
        assert template["Parameters"]["PagesOrigin"]["Default"] == service.DEFAULT_ORIGIN

    def test_the_environment_names_every_variable_the_service_reads(
        self, function: dict[str, Any]
    ) -> None:
        env = function["Environment"]["Variables"]
        assert env["DISCLOSED_ASK_PROVIDER"] == "bedrock"
        assert env["DISCLOSED_ASK_MODEL"] == {"Ref": "ModelId"}
        assert env["DISCLOSED_ASK_ORIGIN"] == {"Ref": "PagesOrigin"}
        assert env["DISCLOSED_ASK_PER_CLIENT_PER_HOUR"] == {"Ref": "PerClientPerHour"}
        assert env["DISCLOSED_ASK_PER_DAY"] == {"Ref": "PerDay"}
        assert env["DISCLOSED_ROOT"] == "/var/task"
        assert "ANTHROPIC_API_KEY" not in env

    def test_iam_grants_one_action_on_the_configured_model_only(
        self, function: dict[str, Any]
    ) -> None:
        (policy,) = function["Policies"]
        (statement,) = policy["Statement"]
        assert statement["Action"] == ["bedrock:InvokeModel"]
        assert all("${ModelId}" in r["Fn::Sub"] for r in statement["Resource"])
        assert statement["Effect"] == "Allow"

    def test_runtime_and_limits_are_as_documented(self, function: dict[str, Any]) -> None:
        assert function["Runtime"] == "python3.12"
        assert function["Architectures"] == ["arm64"]
        assert function["Timeout"] <= 60 and function["MemorySize"] <= 1024
        assert function["CodeUri"] == "../build/package/"


class TestTheBounds:
    def test_a_budget_and_an_alarm_exist(self, template: dict[str, Any]) -> None:
        resources = template["Resources"]
        budget = resources["SpendBudget"]["Properties"]["Budget"]
        assert budget["BudgetType"] == "COST" and budget["TimeUnit"] == "MONTHLY"
        assert set(budget["CostFilters"]["Service"]) == {"Amazon Bedrock", "AWS Lambda"}
        alarm = resources["InvocationsAlarm"]["Properties"]
        assert alarm["MetricName"] == "Invocations" and alarm["Threshold"] == {"Ref": "PerDay"}
        assert resources["AskLogGroup"]["Properties"]["RetentionInDays"] <= 30

    def test_the_notification_address_has_no_default(self, template: dict[str, Any]) -> None:
        assert "Default" not in template["Parameters"]["BudgetEmail"]

    def test_the_model_default_is_the_one_this_account_could_reach(
        self, template: dict[str, Any]
    ) -> None:
        assert template["Parameters"]["ModelId"]["Default"] == "global.anthropic.claude-sonnet-4-6"

    def test_nothing_secret_or_personal_is_committed(self) -> None:
        for path in DEPLOY.iterdir():
            text = path.read_text(encoding="utf-8")
            assert "AKIA" not in text and "sk-ant-" not in text, path
            assert "@" not in text.replace("@media", ""), path


class TestTheDocuments:
    def test_the_readme_says_it_is_not_applied_and_lists_the_decisions(self) -> None:
        text = (DEPLOY / "README.md").read_text(encoding="utf-8")
        assert "Nothing in this directory has been applied" in text
        assert "## Decisions this does not make" in text
        assert "Whether to deploy at all" in text
        assert "subprocessor" in text
        assert "--ask-endpoint" in text

    def test_the_build_script_copies_what_the_evidence_store_reads(self) -> None:
        text = (DEPLOY / "build.sh").read_text(encoding="utf-8")
        for needed in (
            "sample.json",
            "report.json",
            "HD*.zip",
            "IC*.zip",
            "census/scorecard.json",
            "snapshots",
            "corpus",
        ):
            assert needed in text, needed
        assert "manylinux2014_aarch64" in text
        assert "sam deploy" not in text and "aws " not in text, (
            "the build script never talks to AWS"
        )

    def test_the_template_description_says_prepared_not_applied(
        self, template: dict[str, Any]
    ) -> None:
        assert "PREPARED, NOT APPLIED" in template["Description"]
