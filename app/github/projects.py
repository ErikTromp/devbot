from __future__ import annotations

from typing import Any, Callable

from app.jobs.models import PIPELINE_ORDER

GraphQL = Callable[..., dict[str, Any]]

DEVBOT_PROJECT_TITLE = "Devbot"
STATUS_FIELD_NAME = "Status"
OWNER_FIELD_NAME = "Owner"
STATUS_OPTIONS = tuple(phase.value for phase in PIPELINE_ORDER) + ("done",)
OPTION_COLORS = {
    "create": "GRAY",
    "implement": "BLUE",
    "test": "GREEN",
    "security": "ORANGE",
    "architect": "PURPLE",
    "document": "PINK",
    "done": "GREEN",
}

_VIEWER_QUERY = """
query DevbotViewerProjects($query: String!) {
  viewer {
    id
    projectsV2(first: 50, query: $query) {
      nodes {
        id
        title
        fields(first: 40) {
          nodes {
            __typename
            ... on ProjectV2FieldCommon {
              id
              name
              dataType
            }
            ... on ProjectV2SingleSelectField {
              id
              name
              options { id name }
            }
          }
        }
      }
    }
  }
}
"""

_PROJECT_QUERY = """
query DevbotProject($id: ID!) {
  node(id: $id) {
    ... on ProjectV2 {
      id
      title
      fields(first: 40) {
        nodes {
          __typename
          ... on ProjectV2FieldCommon {
            id
            name
            dataType
          }
          ... on ProjectV2SingleSelectField {
            id
            name
            options { id name }
          }
        }
      }
    }
  }
}
"""

_CREATE_PROJECT = """
mutation DevbotCreateProject($ownerId: ID!, $title: String!) {
  createProjectV2(input: {ownerId: $ownerId, title: $title}) {
    projectV2 { id title }
  }
}
"""

_UPDATE_STATUS_FIELD = """
mutation DevbotUpdateStatus($fieldId: ID!, $name: String!, $options: [ProjectV2SingleSelectFieldOptionInput!]!) {
  updateProjectV2Field(input: {fieldId: $fieldId, name: $name, singleSelectOptions: $options}) {
    projectV2Field {
      ... on ProjectV2SingleSelectField {
        id
        name
        options { id name }
      }
    }
  }
}
"""

_CREATE_OWNER_FIELD = """
mutation DevbotCreateOwnerField($projectId: ID!, $name: String!) {
  createProjectV2Field(input: {projectId: $projectId, dataType: TEXT, name: $name}) {
    projectV2Field {
      ... on ProjectV2FieldCommon { id name }
    }
  }
}
"""

_SET_OWNER = """
mutation DevbotSetOwner($projectId: ID!, $itemId: ID!, $fieldId: ID!, $text: String!) {
  updateProjectV2ItemFieldValue(input: {
    projectId: $projectId
    itemId: $itemId
    fieldId: $fieldId
    value: {text: $text}
  }) {
    projectV2Item { id }
  }
}
"""

_ADD_ITEM = """
mutation DevbotAddItem($projectId: ID!, $contentId: ID!) {
  addProjectV2ItemById(input: {projectId: $projectId, contentId: $contentId}) {
    item { id }
  }
}
"""

_SET_STATUS = """
mutation DevbotSetStatus($projectId: ID!, $itemId: ID!, $fieldId: ID!, $optionId: String!) {
  updateProjectV2ItemFieldValue(input: {
    projectId: $projectId
    itemId: $itemId
    fieldId: $fieldId
    value: {singleSelectOptionId: $optionId}
  }) {
    projectV2Item { id }
  }
}
"""


class ProjectSyncError(Exception):
    pass


def _nodes(value: Any) -> list[dict[str, Any]]:
    if isinstance(value, dict):
        nodes = value.get("nodes")
        if isinstance(nodes, list):
            return [item for item in nodes if isinstance(item, dict)]
    if isinstance(value, list):
        return [item for item in value if isinstance(item, dict)]
    return []


def _status_field(project: dict[str, Any]) -> dict[str, Any] | None:
    for field in _nodes((project.get("fields") or {})):
        if str(field.get("name") or "").lower() == STATUS_FIELD_NAME.lower() and field.get("options") is not None:
            return field
    return None


def _options_by_name(field: dict[str, Any]) -> dict[str, str]:
    return {
        str(option.get("name") or "").strip().lower(): str(option.get("id") or "")
        for option in field.get("options") or []
        if option.get("id") and option.get("name")
    }


def _field_by_name(project: dict[str, Any], name: str) -> dict[str, Any] | None:
    needle = name.lower()
    for field in _nodes((project.get("fields") or {})):
        if str(field.get("name") or "").lower() == needle:
            return field
    return None


def _project_payload(project: dict[str, Any]) -> dict[str, Any]:
    field = _status_field(project) or {}
    owner = _field_by_name(project, OWNER_FIELD_NAME) or {}
    return {
        "project_id": str(project.get("id") or ""),
        "status_field_id": str(field.get("id") or ""),
        "owner_field_id": str(owner.get("id") or ""),
        "options": _options_by_name(field),
        "field": field,
        "fields": project.get("fields"),
    }


def _load_project(graphql: GraphQL, project_id: str) -> dict[str, Any]:
    data = graphql(_PROJECT_QUERY, {"id": project_id})
    project = (data.get("node") or {}) if isinstance(data, dict) else {}
    if not project.get("id"):
        raise ProjectSyncError(f"GitHub project {project_id} was not found")
    return _project_payload(project)


def _find_or_create_project(graphql: GraphQL, pinned_project_id: str = "") -> dict[str, Any]:
    if pinned_project_id.strip():
        return _load_project(graphql, pinned_project_id.strip())
    data = graphql(_VIEWER_QUERY, {"query": DEVBOT_PROJECT_TITLE})
    viewer = (data.get("viewer") or {}) if isinstance(data, dict) else {}
    owner_id = str(viewer.get("id") or "")
    for project in _nodes(viewer.get("projectsV2")):
        if str(project.get("title") or "") == DEVBOT_PROJECT_TITLE and project.get("id"):
            return _project_payload(project)
    if not owner_id:
        raise ProjectSyncError("GitHub viewer id is missing; cannot create the Devbot project")
    created = graphql(_CREATE_PROJECT, {"ownerId": owner_id, "title": DEVBOT_PROJECT_TITLE})
    project_id = str(((created.get("createProjectV2") or {}).get("projectV2") or {}).get("id") or "")
    if not project_id:
        raise ProjectSyncError("GitHub did not return a project id after createProjectV2")
    return _load_project(graphql, project_id)


def _ensure_status_options(graphql: GraphQL, project: dict[str, Any]) -> dict[str, Any]:
    field = project.get("field") or {}
    field_id = str(project.get("status_field_id") or field.get("id") or "")
    options = dict(project.get("options") or {})
    missing = [name for name in STATUS_OPTIONS if name not in options]
    if not missing:
        return project
    if not field_id:
        raise ProjectSyncError("Devbot project has no Status field")
    payload: list[dict[str, str]] = []
    for option in field.get("options") or []:
        name = str(option.get("name") or "").strip()
        if not name:
            continue
        item = {
            "name": name,
            "color": OPTION_COLORS.get(name.lower(), "GRAY"),
            "description": "",
        }
        if option.get("id"):
            item["id"] = str(option["id"])
        payload.append(item)
    for name in missing:
        payload.append({"name": name, "color": OPTION_COLORS.get(name, "GRAY"), "description": ""})
    updated = graphql(_UPDATE_STATUS_FIELD, {"fieldId": field_id, "name": STATUS_FIELD_NAME, "options": payload})
    field = ((updated.get("updateProjectV2Field") or {}).get("projectV2Field") or {}) if isinstance(updated, dict) else {}
    if not field.get("id"):
        refreshed = _load_project(graphql, str(project.get("project_id") or ""))
        if any(name not in refreshed.get("options", {}) for name in STATUS_OPTIONS):
            raise ProjectSyncError("Could not add pipeline columns to the Devbot project Status field")
        return refreshed
    return {
        "project_id": str(project.get("project_id") or ""),
        "status_field_id": str(field.get("id") or field_id),
        "owner_field_id": str(project.get("owner_field_id") or ""),
        "options": _options_by_name(field),
        "field": field,
        "fields": project.get("fields"),
    }


def _ensure_owner_field(graphql: GraphQL, project: dict[str, Any]) -> dict[str, Any]:
    if project.get("owner_field_id"):
        return project
    project_id = str(project.get("project_id") or "")
    created = graphql(_CREATE_OWNER_FIELD, {"projectId": project_id, "name": OWNER_FIELD_NAME})
    field_id = str(((created.get("createProjectV2Field") or {}).get("projectV2Field") or {}).get("id") or "")
    if not field_id:
        refreshed = _load_project(graphql, project_id)
        field_id = str(refreshed.get("owner_field_id") or "")
        if not field_id:
            raise ProjectSyncError("Could not add an Owner field to the Devbot project")
        return refreshed
    return {**project, "owner_field_id": field_id}


def sync_pipeline_project(
    graphql: GraphQL,
    *,
    issue_node_id: str,
    status: str,
    pinned_project_id: str = "",
    stored_project_id: str = "",
    stored_item_id: str = "",
    owner: str = "",
) -> dict[str, str]:
    if not issue_node_id:
        raise ProjectSyncError("Issue node id is required to add a card to the Devbot project")
    project = _load_project(graphql, stored_project_id) if stored_project_id else _find_or_create_project(graphql, pinned_project_id)
    project = _ensure_status_options(graphql, project)
    if owner.strip():
        project = _ensure_owner_field(graphql, project)
    project_id = str(project.get("project_id") or "")
    item_id = stored_item_id
    if not item_id:
        added = graphql(_ADD_ITEM, {"projectId": project_id, "contentId": issue_node_id})
        item_id = str(((added.get("addProjectV2ItemById") or {}).get("item") or {}).get("id") or "")
        if not item_id:
            raise ProjectSyncError("GitHub did not return a project item id")
    option_id = (project.get("options") or {}).get(status.strip().lower())
    field_id = str(project.get("status_field_id") or "")
    if option_id and field_id:
        graphql(_SET_STATUS, {"projectId": project_id, "itemId": item_id, "fieldId": field_id, "optionId": option_id})
    owner_name = owner.strip()
    owner_field_id = str(project.get("owner_field_id") or "")
    if owner_name and owner_field_id:
        graphql(
            _SET_OWNER,
            {"projectId": project_id, "itemId": item_id, "fieldId": owner_field_id, "text": owner_name[:80]},
        )
    return {"github_project_id": project_id, "github_project_item_id": item_id}
