"""Jira Cloud (REST API v3)."""

from __future__ import annotations

import re
from typing import Any, Dict, List

from ..errors import IntegrationError
from .base import Integration, RestClient, Tool, atlassian_base, dig, i, require, s, schema, scope_query, to_int

_KEY = re.compile(r"^[A-Z][A-Z0-9_]+-\d+$")
_PROJECT = re.compile(r"^[A-Z][A-Z0-9_]+$")
KEY = s("Issue key, e.g. ABC-123")


def adf_to_text(node: Any) -> str:
    if not isinstance(node, dict):
        return ""
    kind = node.get("type")
    if kind == "text":
        return node.get("text", "")
    if kind == "hardBreak":
        return "\n"
    inner = "".join(adf_to_text(c) for c in node.get("content", []))
    return inner + "\n" if kind in ("paragraph", "heading", "listItem", "codeBlock") else inner


def text_to_adf(text: str) -> Dict[str, Any]:
    paragraphs = [
        {"type": "paragraph", "content": [{"type": "text", "text": line}]}
        for line in text.splitlines()
        if line.strip()
    ]
    return {"type": "doc", "version": 1, "content": paragraphs or [{"type": "paragraph", "content": []}]}


def _row(issue: Dict[str, Any]) -> Dict[str, Any]:
    f = issue.get("fields") or {}
    return {
        "key": issue.get("key"),
        "summary": f.get("summary"),
        "status": dig(f, "status", "name"),
        "type": dig(f, "issuetype", "name"),
        "priority": dig(f, "priority", "name"),
        "assignee": dig(f, "assignee", "displayName"),
        "updated": f.get("updated"),
    }


class Jira(Integration):
    name = "jira"
    title = "Jira Cloud"
    section = "jira"
    config_help = "[jira]: base_url, email, api_token"

    def __init__(self, cfg):
        super().__init__(cfg)
        self._client = None

    def configured(self) -> bool:
        return all(self.conf.get(k) for k in ("base_url", "email", "api_token"))

    def _c(self) -> RestClient:
        if self._client is None:
            self._client = RestClient(
                "Jira",
                atlassian_base(self.conf, "Jira"),
                auth=(str(self.conf["email"]), str(self.conf["api_token"])),
            )
        return self._client

    def _allowed_projects(self) -> List[str]:
        return [p.upper() for p in self.cfg.get_list("jira", "allowed_projects")]

    def _issue_key(self, a: Dict[str, Any]) -> str:
        key = require(a, "key").upper()
        if not _KEY.match(key):
            raise IntegrationError(f"Invalid issue key: {key!r}")
        self._check_project(key.rsplit("-", 1)[0])
        return key

    def _check_project(self, project: str) -> str:
        project = project.upper()
        if not _PROJECT.match(project):
            raise IntegrationError(f"Invalid project key: {project!r}")
        allowed = self._allowed_projects()
        if allowed and project not in allowed:
            raise IntegrationError(f"Project '{project}' is not in allowed_projects ({', '.join(allowed)})")
        return project

    # --- read --------------------------------------------------------------

    def search(self, a):
        jql = require(a, "jql")
        allowed = self._allowed_projects()
        if allowed:
            jql = scope_query(jql, "project in (" + ",".join(allowed) + ")")
        params = {
            "jql": jql,
            "maxResults": to_int(a.get("max_results"), 20, 1, 50),
            "fields": "summary,status,issuetype,priority,assignee,updated",
        }
        if a.get("next_page_token"):
            params["nextPageToken"] = a["next_page_token"]
        data = self._c().request("GET", "/rest/api/3/search/jql", params=params)
        return {
            "issues": [_row(x) for x in data.get("issues", [])],
            "next_page_token": data.get("nextPageToken"),
        }

    def get_issue(self, a):
        key = self._issue_key(a)
        data = self._c().request(
            "GET", f"/rest/api/3/issue/{key}",
            params={"fields": "summary,status,issuetype,priority,assignee,reporter,labels,created,updated,description"},
        )
        f = data.get("fields") or {}
        row = _row(data)
        row.update(
            reporter=dig(f, "reporter", "displayName"),
            labels=f.get("labels"),
            created=f.get("created"),
            description=adf_to_text(f.get("description")).strip(),
        )
        return row

    def comments(self, a):
        key = self._issue_key(a)
        data = self._c().request(
            "GET", f"/rest/api/3/issue/{key}/comment", params={"maxResults": to_int(a.get("max_results"), 20, 1, 100)}
        )
        return [
            {"author": dig(c, "author", "displayName"), "created": c.get("created"),
             "text": adf_to_text(c.get("body")).strip()}
            for c in data.get("comments", [])
        ]

    def transitions(self, a):
        key = self._issue_key(a)
        data = self._c().request("GET", f"/rest/api/3/issue/{key}/transitions")
        return [{"id": t.get("id"), "name": t.get("name"), "to": dig(t, "to", "name")} for t in data.get("transitions", [])]

    def projects(self, a):
        data = self._c().request("GET", "/rest/api/3/project/search", params={"maxResults": 50})
        allowed = self._allowed_projects()
        rows = [{"key": p.get("key"), "name": p.get("name")} for p in data.get("values", [])]
        return [r for r in rows if not allowed or str(r["key"]).upper() in allowed]

    # --- write -------------------------------------------------------------

    def add_comment(self, a):
        key = self._issue_key(a)
        self._c().request("POST", f"/rest/api/3/issue/{key}/comment", json_body={"body": text_to_adf(require(a, "text"))})
        return {"ok": True}

    def transition(self, a):
        key = self._issue_key(a)
        self._c().request(
            "POST", f"/rest/api/3/issue/{key}/transitions",
            json_body={"transition": {"id": require(a, "transition_id")}},
        )
        return {"ok": True}

    def create_issue(self, a):
        project = self._check_project(require(a, "project_key"))
        fields: Dict[str, Any] = {
            "project": {"key": project},
            "summary": require(a, "summary"),
            "issuetype": {"name": a.get("issue_type") or "Task"},
        }
        if a.get("description"):
            fields["description"] = text_to_adf(a["description"])
        data = self._c().request("POST", "/rest/api/3/issue", json_body={"fields": fields})
        return {"key": data.get("key")}

    def tools(self) -> List[Tool]:
        T = Tool
        return [
            T("jira_search_issues", "Search issues with JQL.",
              schema({"jql": s("JQL query"), "max_results": i("Max 50, default 20"),
                      "next_page_token": s("Token from a previous page")}, ["jql"]), self.search),
            T("jira_get_issue", "Get one issue with description.", schema({"key": KEY}, ["key"]), self.get_issue),
            T("jira_list_comments", "List comments on an issue.",
              schema({"key": KEY, "max_results": i("Max 100")}, ["key"]), self.comments),
            T("jira_list_transitions", "List workflow transitions available for an issue.",
              schema({"key": KEY}, ["key"]), self.transitions),
            T("jira_list_projects", "List visible projects.", schema({}), self.projects),
            T("jira_add_comment", "WRITE: add a comment to an issue.",
              schema({"key": KEY, "text": s("Comment text")}, ["key", "text"]), self.add_comment, write=True,
              summary=lambda a: f"Jira: comment on {a.get('key')}: {str(a.get('text'))[:300]!r}"),
            T("jira_transition_issue", "WRITE: move an issue through its workflow.",
              schema({"key": KEY, "transition_id": s("Id from jira_list_transitions")}, ["key", "transition_id"]),
              self.transition, write=True,
              summary=lambda a: f"Jira: transition {a.get('key')} using transition id {a.get('transition_id')}"),
            T("jira_create_issue", "WRITE: create an issue.",
              schema({"project_key": s("Project key"), "summary": s("Summary"),
                      "issue_type": s("Issue type name, default Task"), "description": s("Description")},
                     ["project_key", "summary"]), self.create_issue, write=True,
              summary=lambda a: f"Jira: create {a.get('issue_type') or 'Task'} in {a.get('project_key')}: {a.get('summary')!r}"),
        ]

    def check(self) -> str:
        me = self._c().request("GET", "/rest/api/3/myself")
        return f"authenticated as {me.get('displayName')}"


INTEGRATION = Jira
