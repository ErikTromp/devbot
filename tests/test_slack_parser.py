from __future__ import annotations

from app.jobs.models import SlackCommand
from app.repos import parse_allowed_repos
from app.slack.parser import parse_slack_text


def test_parse_create_mention() -> None:
    parsed = parse_slack_text("<@U123> repo=myapp create redesign the landing page", "acme")
    assert parsed.command == SlackCommand.CREATE
    assert parsed.repository == "acme/myapp"
    assert parsed.request == "redesign the landing page"
    assert parsed.issue_number is None
    assert parsed.skip is False


def test_parse_commit_issue() -> None:
    parsed = parse_slack_text("<@U123> commit 49")
    assert parsed.command == SlackCommand.COMMIT
    assert parsed.issue_number == 49
    assert parsed.skip is False


def test_parse_implement_issue_number() -> None:
    parsed = parse_slack_text("<@U123> implement 9")
    assert parsed.command == SlackCommand.IMPLEMENT
    assert parsed.issue_number == 9
    assert parsed.request == ""


def test_parse_hash_issue_and_skip() -> None:
    parsed = parse_slack_text("@devbot test skip #12")
    assert parsed.command == SlackCommand.TEST
    assert parsed.skip is True
    assert parsed.issue_number == 12


def test_rejects_dev_id_for_pipeline() -> None:
    parsed = parse_slack_text("test DEV-9")
    assert parsed.command == SlackCommand.TEST
    assert parsed.used_dev_id is True
    assert parsed.issue_number is None


def test_unknown_text_is_not_implement() -> None:
    parsed = parse_slack_text("<@U123> redesign the landing page")
    assert parsed.command is None
    assert parsed.request == "redesign the landing page"


def test_parse_full_repo() -> None:
    parsed = parse_slack_text("repo=acme/other create fix login", "ignored")
    assert parsed.repository == "acme/other"


def test_rejects_path_traversal_repo() -> None:
    parsed = parse_slack_text("repo=../etc create x", "acme")
    assert parsed.repository is None


def test_mid_sentence_create_is_not_a_command() -> None:
    parsed = parse_slack_text(
        "<@U123> The connection dies. Just create the ticket, no more questions"
    )
    assert parsed.command is None
    assert "Just create the ticket" in parsed.request


def test_repo_then_create_still_works() -> None:
    parsed = parse_slack_text("repo=myapp create redesign the landing page", "acme")
    assert parsed.command == SlackCommand.CREATE
    assert parsed.repository == "acme/myapp"
    assert parsed.request == "redesign the landing page"


def test_parse_remove_dev_id() -> None:
    parsed = parse_slack_text("<@U123> remove DEV-1")
    assert parsed.command == SlackCommand.REMOVE
    assert parsed.used_dev_id is True
    assert parsed.job_id == 1
    assert parsed.issue_number is None


def test_parse_alias_after_create() -> None:
    catalog = parse_allowed_repos("acme/web;web,northwind;shop")
    parsed = parse_slack_text("<@U123> create web the X timeout", catalog=catalog)
    assert parsed.command == SlackCommand.CREATE
    assert parsed.repository == "acme/web"
    assert parsed.request == "the X timeout"


def test_parse_alias_before_create() -> None:
    catalog = parse_allowed_repos("acme/web;web,northwind;shop")
    parsed = parse_slack_text("shop create fix login", catalog=catalog)
    assert parsed.command == SlackCommand.CREATE
    assert parsed.repository == "northwind/shop"
    assert parsed.request == "fix login"


def test_parse_owner_name_without_prefix() -> None:
    parsed = parse_slack_text("create acme/other fix login", "ignored")
    assert parsed.command == SlackCommand.CREATE
    assert parsed.repository == "acme/other"
    assert parsed.request == "fix login"


def test_parse_control_commands() -> None:
    assert parse_slack_text("cancel 7").command == SlackCommand.CANCEL
    assert parse_slack_text("remove 9").command == SlackCommand.REMOVE
    assert parse_slack_text("delete #9").command == SlackCommand.REMOVE
    assert parse_slack_text("delete #9").issue_number == 9
    assert parse_slack_text("retry #7").issue_number == 7
    assert parse_slack_text("status").command == SlackCommand.STATUS
    assert parse_slack_text("status 3").issue_number == 3
    assert parse_slack_text("<@U123> ping").command == SlackCommand.PING
    assert parse_slack_text("hello").command == SlackCommand.PING
    assert parse_slack_text("help").command == SlackCommand.HELP
    assert parse_slack_text("<@U123|devbot> help").command == SlackCommand.HELP
    assert parse_slack_text("@devbot help").command == SlackCommand.HELP
    assert parse_slack_text("security 4").command == SlackCommand.SECURITY
    assert parse_slack_text("architect 4").command == SlackCommand.ARCHITECT
    assert parse_slack_text("document skip 4").skip is True
    autopilot = parse_slack_text("autopilot acme/other fix login", "ignored")
    assert autopilot.command == SlackCommand.AUTOPILOT
    assert autopilot.repository == "acme/other"
    assert autopilot.request == "fix login"
    assert parse_slack_text("autopilot 9").issue_number == 9


def test_parse_create_named_user() -> None:
    catalog = parse_allowed_repos("acme/web;web")
    names = {"erik": "Erik"}
    forms = (
        "<@U123> create Erik web the X timeout",
        "web create Erik the X timeout",
        "create web Erik the X timeout",
        "Erik web create the X timeout",
        "Erik create web the X timeout",
    )
    for text in forms:
        parsed = parse_slack_text(text, catalog=catalog, user_names=names)
        assert parsed.command == SlackCommand.CREATE, text
        assert parsed.credential_user == "Erik", text
        assert parsed.repository == "acme/web", text
        assert parsed.request == "the X timeout", text


def test_unknown_word_after_create_stays_in_request() -> None:
    catalog = parse_allowed_repos("acme/web;web")
    parsed = parse_slack_text(
        "create NotAUser web the X timeout",
        catalog=catalog,
        user_names={"erik": "Erik"},
    )
    assert parsed.credential_user is None
    assert parsed.repository == "acme/web"
    assert parsed.request == "NotAUser the X timeout"


def test_implement_ignores_named_user() -> None:
    parsed = parse_slack_text("implement Erik 9", user_names={"erik": "Erik"})
    assert parsed.command == SlackCommand.IMPLEMENT
    assert parsed.credential_user is None
    assert parsed.issue_number == 9
