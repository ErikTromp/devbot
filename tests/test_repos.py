from __future__ import annotations

from app.repos import parse_allowed_repos


def test_parse_owner_repo_and_owner_alias() -> None:
    catalog = parse_allowed_repos("acme/web;web,northwind;shop")
    assert [(e.alias, e.repository) for e in catalog.entries] == [
        ("web", "acme/web"),
        ("shop", "northwind/shop"),
    ]
    assert catalog.resolve("web") == "acme/web"
    assert catalog.resolve("shop") == "northwind/shop"
    assert catalog.resolve("acme/web") == "acme/web"
    assert catalog.resolve("unknown") is None
    assert catalog.allows("acme/web")
    assert catalog.allows("web")


def test_plain_names_keep_working() -> None:
    catalog = parse_allowed_repos("myapp,acme/other", "acme")
    assert catalog.resolve("myapp") == "acme/myapp"
    assert catalog.resolve("other") == "acme/other"


def test_alias_for_and_matches() -> None:
    catalog = parse_allowed_repos("acme/frontend-app;web,northwind;shop")
    assert catalog.alias_for("acme/frontend-app") == "web"
    assert catalog.alias_for("web") == "web"
    assert catalog.alias_for("acme/unknown") == "unknown"
    assert catalog.matches("acme/frontend-app", "web")
    assert catalog.matches("acme/frontend-app", "acme/frontend-app")
    assert catalog.matches("acme/frontend-app", "frontend-app")
    assert not catalog.matches("acme/frontend-app", "shop")
    empty = parse_allowed_repos("")
    assert empty.alias_for("acme/myapp") == "myapp"
    assert empty.matches("acme/myapp", "myapp")
    assert empty.matches("acme/myapp", "acme/myapp")
