from __future__ import annotations

from app.github.projects import DEVBOT_PROJECT_TITLE, STATUS_OPTIONS, ProjectSyncError, sync_pipeline_project


class FakeGraphQL:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict]] = []
        self.projects = {}
        self.viewer_id = "USER_1"
        self.fail_scopes = False

    def __call__(self, query: str, variables: dict | None = None) -> dict:
        variables = variables or {}
        self.calls.append((query, variables))
        if self.fail_scopes:
            raise ProjectSyncError("GitHub token is missing the project scope")
        if "DevbotViewerProjects" in query:
            return {
                "viewer": {
                    "id": self.viewer_id,
                    "projectsV2": {"nodes": list(self.projects.values())},
                }
            }
        if "DevbotProject" in query:
            project = self.projects.get(variables["id"])
            return {"node": project or {}}
        if "DevbotCreateProject" in query:
            project = {
                "id": "PVT_1",
                "title": variables["title"],
                "fields": {
                    "nodes": [
                        {
                            "__typename": "ProjectV2SingleSelectField",
                            "id": "FIELD_1",
                            "name": "Status",
                            "options": [
                                {"id": "OPT_TODO", "name": "Todo"},
                                {"id": "OPT_DONE", "name": "Done"},
                            ],
                        }
                    ]
                },
            }
            self.projects["PVT_1"] = project
            return {"createProjectV2": {"projectV2": {"id": "PVT_1", "title": DEVBOT_PROJECT_TITLE}}}
        if "DevbotUpdateStatus" in query:
            options = [
                {"id": item.get("id") or f"OPT_{item['name'].upper()}", "name": item["name"]}
                for item in variables["options"]
            ]
            self.projects["PVT_1"]["fields"]["nodes"][0]["options"] = options
            return {"updateProjectV2Field": {"projectV2Field": self.projects["PVT_1"]["fields"]["nodes"][0]}}
        if "DevbotAddItem" in query:
            return {"addProjectV2ItemById": {"item": {"id": "ITEM_1"}}}
        if "DevbotCreateOwnerField" in query:
            field = {"id": "FIELD_OWNER", "name": "Owner", "dataType": "TEXT"}
            project = self.projects.get("PVT_1") or self.projects.get(variables.get("projectId"))
            if project:
                project.setdefault("fields", {}).setdefault("nodes", []).append(field)
            return {"createProjectV2Field": {"projectV2Field": field}}
        if "DevbotSetOwner" in query:
            return {"updateProjectV2ItemFieldValue": {"projectV2Item": {"id": variables["itemId"]}}}
        if "DevbotSetStatus" in query:
            return {"updateProjectV2ItemFieldValue": {"projectV2Item": {"id": variables["itemId"]}}}
        raise AssertionError(query)


def test_sync_creates_project_columns_and_moves_card() -> None:
    gql = FakeGraphQL()
    ids = sync_pipeline_project(gql, issue_node_id="ISSUE_1", status="create")
    assert ids == {"github_project_id": "PVT_1", "github_project_item_id": "ITEM_1"}
    names = {call[0] for call in gql.calls}
    assert any("DevbotCreateProject" in name for name in names)
    assert any("DevbotUpdateStatus" in name for name in names)
    assert any("DevbotAddItem" in name for name in names)
    assert any("DevbotSetStatus" in name for name in names)
    stored = {option["name"].lower() for option in gql.projects["PVT_1"]["fields"]["nodes"][0]["options"]}
    assert stored.issuperset(STATUS_OPTIONS)
    assert "todo" in stored

    gql.calls.clear()
    again = sync_pipeline_project(
        gql,
        issue_node_id="ISSUE_1",
        status="implement",
        stored_project_id="PVT_1",
        stored_item_id="ITEM_1",
    )
    assert again["github_project_item_id"] == "ITEM_1"
    assert not any("DevbotCreateProject" in name for name, _ in gql.calls)
    assert not any("DevbotAddItem" in name for name, _ in gql.calls)
    set_status = next(variables for name, variables in gql.calls if "DevbotSetStatus" in name)
    assert set_status["optionId"] == "OPT_IMPLEMENT"


def test_sync_uses_pinned_project() -> None:
    gql = FakeGraphQL()
    gql.projects["PVT_PIN"] = {
        "id": "PVT_PIN",
        "title": "Existing",
        "fields": {
            "nodes": [
                {
                    "__typename": "ProjectV2SingleSelectField",
                    "id": "FIELD_1",
                    "name": "Status",
                    "options": [{"id": f"OPT_{name.upper()}", "name": name} for name in STATUS_OPTIONS],
                }
            ]
        },
    }
    ids = sync_pipeline_project(gql, issue_node_id="ISSUE_1", status="done", pinned_project_id="PVT_PIN")
    assert ids["github_project_id"] == "PVT_PIN"
    assert not any("DevbotCreateProject" in name for name, _ in gql.calls)


def test_sync_sets_owner_field() -> None:
    gql = FakeGraphQL()
    sync_pipeline_project(gql, issue_node_id="ISSUE_1", status="create", owner="Erik")
    assert any("DevbotCreateOwnerField" in name for name, _ in gql.calls)
    owner = next(variables for name, variables in gql.calls if "DevbotSetOwner" in name)
    assert owner["text"] == "Erik"


def test_sync_missing_issue_node() -> None:
    gql = FakeGraphQL()
    try:
        sync_pipeline_project(gql, issue_node_id="", status="create")
    except ProjectSyncError as exc:
        assert "node id" in str(exc)
    else:
        raise AssertionError("expected ProjectSyncError")
