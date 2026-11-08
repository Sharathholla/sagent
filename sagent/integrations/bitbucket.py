"""Bitbucket Cloud (REST API 2.0). Scope is limited to approved workspaces/projects."""

from __future__ import annotations

import re
from typing import Any, Callable, Dict, List, Set, Tuple
from urllib.parse import quote

from ..errors import IntegrationError
from .base import Integration, RestClient, Tool, dig, i, require, s, schema, to_int

API = "https://api.bitbucket.org/2.0"
_SLUG = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")

WS = s("Workspace slug (must be in allowed_workspaces; optional if only one is configured)")
REPO = s("Repository slug")


def _seg(value: Any, label: str) -> str:
    if not isinstance(value, str) or not _SLUG.match(value.strip()):
        raise IntegrationError(f"Invalid {label}: {value!r}")
    return quote(value.strip(), safe="")


def _q(value: str) -> str:
    return value.replace('"', "").replace("\\", "")


def _repo_row(r: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "slug": r.get("slug"),
        "name": r.get("name"),
        "project": dig(r, "project", "key"),
        "private": r.get("is_private"),
        "main_branch": dig(r, "mainbranch", "name"),
        "updated": r.get("updated_on"),
    }


def _pr_row(p: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "id": p.get("id"),
        "title": p.get("title"),
        "state": p.get("state"),
        "author": dig(p, "author", "display_name"),
        "source": dig(p, "source", "branch", "name"),
        "destination": dig(p, "destination", "branch", "name"),
        "updated": p.get("updated_on"),
        "comments": p.get("comment_count"),
    }


def _branch_row(b: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "name": b.get("name"),
        "commit": (dig(b, "target", "hash") or "")[:12],
        "date": dig(b, "target", "date"),
        "author": dig(b, "target", "author", "raw"),
    }


def _commit_row(c: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "hash": (c.get("hash") or "")[:12],
        "date": c.get("date"),
        "author": dig(c, "author", "raw"),
        "message": (c.get("message") or "").strip()[:300],
    }


def _comment_row(c: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "id": c.get("id"),
        "author": dig(c, "user", "display_name"),
        "created": c.get("created_on"),
        "text": dig(c, "content", "raw"),
        "file": dig(c, "inline", "path"),
    }


def _pipeline_row(p: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "number": p.get("build_number"),
        "state": dig(p, "state", "name"),
        "result": dig(p, "state", "result", "name"),
        "branch": dig(p, "target", "ref_name"),
        "created": p.get("created_on"),
    }


class Bitbucket(Integration):
    name = "bitbucket"
    title = "Bitbucket Cloud"
    section = "bitbucket"
    config_help = (
        "[bitbucket]: email + api_token (or username + app_password, or access_token) "
        "and allowed_workspaces"
    )

    def __init__(self, cfg):
        super().__init__(cfg)
        self._client = None
        self._project_ok: Set[Tuple[str, str]] = set()

    def configured(self) -> bool:
        c = self.conf
        return bool(
            c.get("access_token")
            or ((c.get("email") or c.get("username")) and (c.get("api_token") or c.get("app_password")))
        )

    def _c(self) -> RestClient:
        if self._client is None:
            c = self.conf
            if c.get("access_token"):
                self._client = RestClient(
                    "Bitbucket", API, headers={"Authorization": f"Bearer {c['access_token']}"}
                )
            else:
                user = c.get("email") or c.get("username")
                secret = c.get("api_token") or c.get("app_password")
                self._client = RestClient("Bitbucket", API, auth=(str(user), str(secret)))
        return self._client

    # --- scope enforcement -------------------------------------------------

    def _allowed_workspaces(self) -> List[str]:
        allowed = self.cfg.get_list("bitbucket", "allowed_workspaces")
        if not allowed:
            raise IntegrationError(
                'No approved workspaces. Add allowed_workspaces = ["my-workspace"] to [bitbucket] in ~/.sagent/config.'
            )
        return allowed

    def _workspace(self, args: Dict[str, Any]) -> str:
        allowed = self._allowed_workspaces()
        ws = str(args.get("workspace") or (allowed[0] if len(allowed) == 1 else "")).strip()
        if not ws:
            raise IntegrationError(f"workspace is required (approved: {', '.join(allowed)})")
        if ws.lower() not in {a.lower() for a in allowed}:
            raise IntegrationError(f"Workspace '{ws}' is not approved (allowed_workspaces: {', '.join(allowed)})")
        return _seg(ws, "workspace")

    def _repo(self, args: Dict[str, Any]) -> Tuple[str, str]:
        ws = self._workspace(args)
        repo = _seg(args.get("repo"), "repo")
        projects = {p.upper() for p in self.cfg.get_list("bitbucket", "allowed_projects")}
        if projects and (ws, repo) not in self._project_ok:
            info = self._c().request("GET", f"/repositories/{ws}/{repo}")
            key = str(dig(info, "project", "key") or "").upper()
            if key not in projects:
                raise IntegrationError(
                    f"Repository '{repo}' is in project '{key or 'none'}', which is not in allowed_projects"
                )
            self._project_ok.add((ws, repo))
        return ws, repo

    def _page(self, path: str, args: Dict[str, Any], row: Callable, params: Dict[str, Any] = None) -> Dict[str, Any]:
        query = {"pagelen": to_int(args.get("page_size"), 25, 1, 50), "page": to_int(args.get("page"), 1, 1, 10000)}
        query.update(params or {})
        data = self._c().request("GET", path, params=query)
        return {"items": [row(v) for v in data.get("values", [])], "page": query["page"], "has_more": bool(data.get("next"))}

    # --- read tools --------------------------------------------------------

    def list_repos(self, a):
        ws = self._workspace(a)
        allowed_projects = [p for p in self.cfg.get_list("bitbucket", "allowed_projects")]
        clauses = []
        pk = a.get("project_key")
        if pk:
            if allowed_projects and pk.upper() not in {p.upper() for p in allowed_projects}:
                raise IntegrationError(f"Project '{pk}' is not in allowed_projects")
            clauses.append(f'project.key = "{_q(pk)}"')
        elif allowed_projects:
            clauses.append("(" + " OR ".join(f'project.key = "{_q(p)}"' for p in allowed_projects) + ")")
        if a.get("name_contains"):
            clauses.append(f'name ~ "{_q(a["name_contains"])}"')
        params = {"q": " AND ".join(clauses)} if clauses else {}
        return self._page(f"/repositories/{ws}", a, _repo_row, params)

    def list_branches(self, a):
        ws, repo = self._repo(a)
        params = {"q": f'name ~ "{_q(a["name_contains"])}"'} if a.get("name_contains") else {}
        return self._page(f"/repositories/{ws}/{repo}/refs/branches", a, _branch_row, params)

    def list_commits(self, a):
        ws, repo = self._repo(a)
        branch = quote(a["branch"], safe="") if a.get("branch") else ""
        return self._page(f"/repositories/{ws}/{repo}/commits/{branch}".rstrip("/"), a, _commit_row)

    def list_prs(self, a):
        ws, repo = self._repo(a)
        state = str(a.get("state") or "OPEN").upper()
        if state not in ("OPEN", "MERGED", "DECLINED", "SUPERSEDED"):
            raise IntegrationError("state must be OPEN, MERGED, DECLINED or SUPERSEDED")
        return self._page(f"/repositories/{ws}/{repo}/pullrequests", a, _pr_row, {"state": state})

    def get_pr(self, a):
        ws, repo = self._repo(a)
        pr_id = to_int(a.get("id"), 0, 1, 10**9)
        p = self._c().request("GET", f"/repositories/{ws}/{repo}/pullrequests/{pr_id}")
        row = _pr_row(p)
        row["description"] = p.get("description")
        row["reviewers"] = [
            {"name": dig(x, "user", "display_name"), "approved": x.get("approved")}
            for x in p.get("participants", [])
        ]
        return row

    def pr_diff(self, a):
        ws, repo = self._repo(a)
        pr_id = to_int(a.get("id"), 0, 1, 10**9)
        return self._c().request(
            "GET", f"/repositories/{ws}/{repo}/pullrequests/{pr_id}/diff", text=True, headers={"Accept": "*/*"}
        )

    def pr_comments(self, a):
        ws, repo = self._repo(a)
        pr_id = to_int(a.get("id"), 0, 1, 10**9)
        return self._page(f"/repositories/{ws}/{repo}/pullrequests/{pr_id}/comments", a, _comment_row)

    def get_file(self, a):
        ws, repo = self._repo(a)
        path = require(a, "path").lstrip("/")
        if ".." in path.split("/"):
            raise IntegrationError("path must not contain '..'")
        ref = a.get("ref")
        if not ref:
            info = self._c().request("GET", f"/repositories/{ws}/{repo}")
            ref = dig(info, "mainbranch", "name") or "main"
        return self._c().request(
            "GET",
            f"/repositories/{ws}/{repo}/src/{quote(str(ref), safe='')}/{quote(path, safe='/')}",
            text=True,
            headers={"Accept": "*/*"},
        )

    def list_pipelines(self, a):
        ws, repo = self._repo(a)
        return self._page(f"/repositories/{ws}/{repo}/pipelines/", a, _pipeline_row, {"sort": "-created_on"})

    # --- write tools -------------------------------------------------------

    def comment_pr(self, a):
        ws, repo = self._repo(a)
        pr_id = to_int(a.get("id"), 0, 1, 10**9)
        c = self._c().request(
            "POST",
            f"/repositories/{ws}/{repo}/pullrequests/{pr_id}/comments",
            json_body={"content": {"raw": require(a, "text")}},
        )
        return {"comment_id": c.get("id"), "ok": True}

    def create_pr(self, a):
        ws, repo = self._repo(a)
        body: Dict[str, Any] = {
            "title": require(a, "title"),
            "source": {"branch": {"name": require(a, "source_branch")}},
        }
        if a.get("destination_branch"):
            body["destination"] = {"branch": {"name": a["destination_branch"]}}
        if a.get("description"):
            body["description"] = a["description"]
        p = self._c().request("POST", f"/repositories/{ws}/{repo}/pullrequests", json_body=body)
        return {"id": p.get("id"), "url": dig(p, "links", "html", "href")}

    # --- registry ----------------------------------------------------------

    def tools(self) -> List[Tool]:
        paged = {"page": i("Page number (default 1)"), "page_size": i("Items per page, max 50")}
        pr_id = {"workspace": WS, "repo": REPO, "id": i("Pull request id")}
        T = Tool
        return [
            T("bitbucket_list_repos", "List repositories in an approved workspace.",
              schema({"workspace": WS, "project_key": s("Filter by project key"),
                      "name_contains": s("Filter by name substring"), **paged}), self.list_repos),
            T("bitbucket_list_branches", "List branches of a repository.",
              schema({"workspace": WS, "repo": REPO, "name_contains": s("Filter by name"), **paged}, ["repo"]),
              self.list_branches),
            T("bitbucket_list_commits", "List recent commits, optionally on one branch.",
              schema({"workspace": WS, "repo": REPO, "branch": s("Branch name"), **paged}, ["repo"]),
              self.list_commits),
            T("bitbucket_list_pull_requests", "List pull requests (default state OPEN).",
              schema({"workspace": WS, "repo": REPO, "state": s("OPEN, MERGED, DECLINED or SUPERSEDED"), **paged},
                     ["repo"]), self.list_prs),
            T("bitbucket_get_pull_request", "Get one pull request with description and reviewers.",
              schema(pr_id, ["repo", "id"]), self.get_pr),
            T("bitbucket_get_pull_request_diff", "Get the unified diff of a pull request (truncated if large).",
              schema(pr_id, ["repo", "id"]), self.pr_diff),
            T("bitbucket_list_pull_request_comments", "List comments on a pull request.",
              schema({**pr_id, **paged}, ["repo", "id"]), self.pr_comments),
            T("bitbucket_get_file", "Read a file from a repository at a branch/commit (default: main branch).",
              schema({"workspace": WS, "repo": REPO, "path": s("File path in the repo"),
                      "ref": s("Branch, tag or commit")}, ["repo", "path"]), self.get_file),
            T("bitbucket_list_pipelines", "List recent pipeline runs.",
              schema({"workspace": WS, "repo": REPO, **paged}, ["repo"]), self.list_pipelines),
            T("bitbucket_comment_on_pull_request", "WRITE: add a comment to a pull request.",
              schema({**pr_id, "text": s("Comment text (Markdown)")}, ["repo", "id", "text"]),
              self.comment_pr, write=True,
              summary=lambda a: f"Bitbucket: comment on PR #{a.get('id')} in {a.get('repo')}: {str(a.get('text'))[:300]!r}"),
            T("bitbucket_create_pull_request", "WRITE: open a pull request.",
              schema({"workspace": WS, "repo": REPO, "title": s("PR title"),
                      "source_branch": s("Source branch"),
                      "destination_branch": s("Destination branch (default: repo main branch)"),
                      "description": s("PR description")}, ["repo", "title", "source_branch"]),
              self.create_pr, write=True,
              summary=lambda a: f"Bitbucket: create PR {a.get('title')!r} ({a.get('source_branch')} -> "
                                f"{a.get('destination_branch') or 'default'}) in {a.get('repo')}"),
        ]

    def check(self) -> str:
        try:
            who = self._c().request("GET", "/user")
            return f"authenticated as {who.get('display_name')}"
        except IntegrationError:
            # Repository/workspace access tokens cannot call /user.
            ws = _seg(self._allowed_workspaces()[0], "workspace")
            info = self._c().request("GET", f"/workspaces/{ws}")
            return f"workspace '{info.get('slug')}' reachable"


INTEGRATION = Bitbucket
