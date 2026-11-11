"""GitHub REST API v3. Scope is limited to approved repositories and organizations."""

from __future__ import annotations

import re
from typing import Any, Callable, Dict, List, Optional, Set, Tuple
from urllib.parse import quote

from ..errors import IntegrationError
from .base import Integration, RestClient, Tool, dig, i, require, s, schema, to_int

API = "https://api.github.com"
_REPO_PATTERN = re.compile(r"^[A-Za-z0-9]([A-Za-z0-9._-]*[A-Za-z0-9])?$")

OWNER = s("Repository owner (username or organization)")
REPO = s("Repository name")
BRANCH = s("Branch name")


def _owner_repo(value: str) -> Tuple[str, str]:
    """Parse 'owner/repo' format."""
    if "/" not in value:
        raise IntegrationError(f"Invalid repository format: {value!r} (expected 'owner/repo')")
    owner, repo = value.split("/", 1)
    if not owner.strip() or not repo.strip():
        raise IntegrationError(f"Invalid repository format: {value!r} (owner and repo cannot be empty)")
    return owner.strip(), repo.strip()


def _repo_row(r: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "name": r.get("name"),
        "owner": dig(r, "owner", "login"),
        "private": r.get("private"),
        "description": r.get("description"),
        "main_branch": r.get("default_branch"),
        "url": r.get("html_url"),
        "updated": r.get("updated_at"),
    }


def _pr_row(p: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "number": p.get("number"),
        "title": p.get("title"),
        "state": p.get("state"),
        "author": dig(p, "user", "login"),
        "head": dig(p, "head", "ref"),
        "base": dig(p, "base", "ref"),
        "updated": p.get("updated_at"),
        "comments": p.get("comments"),
    }


def _branch_row(b: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "name": b.get("name"),
        "commit": (dig(b, "commit", "sha") or "")[:12],
        "protected": b.get("protected"),
    }


def _commit_row(c: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "sha": (c.get("sha") or "")[:12],
        "date": dig(c, "commit", "author", "date"),
        "author": dig(c, "commit", "author", "name"),
        "message": (dig(c, "commit", "message") or "").strip()[:300],
    }


def _issue_row(i: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "number": i.get("number"),
        "title": i.get("title"),
        "state": i.get("state"),
        "author": dig(i, "user", "login"),
        "labels": [l.get("name") for l in (i.get("labels") or [])],
        "updated": i.get("updated_at"),
        "comments": i.get("comments"),
    }


class GitHub(Integration):
    name = "github"
    title = "GitHub"
    section = "github"
    config_help = "[github]: pat (personal access token) and optional allowed_repos/allowed_orgs"

    def __init__(self, cfg):
        super().__init__(cfg)
        self._client = None
        self._repo_ok: Set[str] = set()

    def configured(self) -> bool:
        return bool(self.conf.get("pat"))

    def _c(self) -> RestClient:
        if self._client is None:
            pat = self.conf.get("pat")
            if not pat:
                raise IntegrationError(
                    "GitHub PAT not configured. Add pat = 'github_pat_...' to [github] in ~/.sagent/config"
                )
            self._client = RestClient(
                "GitHub",
                API,
                headers={
                    "Authorization": f"token {pat}",
                    "Accept": "application/vnd.github.v3+json",
                },
            )
        return self._client

    # --- scope enforcement -------------------------------------------------

    def _allowed_repos(self) -> List[str]:
        """Returns list of allowed repos in 'owner/repo' format."""
        return self.cfg.get_list("github", "allowed_repos")

    def _allowed_orgs(self) -> List[str]:
        """Returns list of allowed organizations."""
        return self.cfg.get_list("github", "allowed_orgs")

    def _check_repo_access(self, owner: str, repo: str) -> None:
        """Verify repo is in allowed list."""
        allowed_repos = self._allowed_repos()
        allowed_orgs = self._allowed_orgs()

        full_name = f"{owner}/{repo}"

        if allowed_repos and full_name.lower() not in {r.lower() for r in allowed_repos}:
            # Check if owner is in allowed orgs
            if allowed_orgs and owner.lower() not in {o.lower() for o in allowed_orgs}:
                raise IntegrationError(
                    f"Repository '{full_name}' is not approved. "
                    f"Allowed repos: {', '.join(allowed_repos) or 'none'}. "
                    f"Allowed orgs: {', '.join(allowed_orgs) or 'none'}"
                )
        elif not allowed_repos and not allowed_orgs:
            raise IntegrationError(
                "No approved repositories or organizations configured. "
                "Add allowed_repos or allowed_orgs to [github] in ~/.sagent/config"
            )

        self._repo_ok.add(full_name)

    def check(self) -> str:
        """Verify authentication by fetching authenticated user info."""
        try:
            user = self._c().request("GET", "/user")
            return f"GitHub user: {user.get('login')}"
        except Exception as e:
            raise IntegrationError(f"Failed to authenticate with GitHub: {e}")

    # --- tools -------------------------------------------------

    def tools(self) -> List[Tool]:
        return [
            Tool(
                name="github_list_repos",
                description="List repositories for the authenticated user or specified organization",
                schema=schema(
                    {
                        "owner": s("Repository owner (username or organization); defaults to authenticated user"),
                        "page_size": i("Results per page (1-100, default: 30)"),
                    }
                ),
                handler=self._list_repos,
            ),
            Tool(
                name="github_list_branches",
                description="List branches in a repository",
                schema=schema(
                    {
                        "owner": OWNER,
                        "repo": REPO,
                        "page_size": i("Results per page (1-100, default: 30)"),
                    },
                    ["owner", "repo"],
                ),
                handler=self._list_branches,
            ),
            Tool(
                name="github_list_commits",
                description="List commits in a repository branch",
                schema=schema(
                    {
                        "owner": OWNER,
                        "repo": REPO,
                        "branch": s("Branch name (default: repository default branch)"),
                        "page_size": i("Results per page (1-100, default: 30)"),
                    },
                    ["owner", "repo"],
                ),
                handler=self._list_commits,
            ),
            Tool(
                name="github_list_pull_requests",
                description="List pull requests in a repository",
                schema=schema(
                    {
                        "owner": OWNER,
                        "repo": REPO,
                        "state": s("PR state: 'open', 'closed', or 'all' (default: 'open')"),
                        "page_size": i("Results per page (1-100, default: 30)"),
                    },
                    ["owner", "repo"],
                ),
                handler=self._list_pull_requests,
            ),
            Tool(
                name="github_get_pull_request",
                description="Get details of a specific pull request",
                schema=schema(
                    {
                        "owner": OWNER,
                        "repo": REPO,
                        "number": i("Pull request number"),
                    },
                    ["owner", "repo", "number"],
                ),
                handler=self._get_pull_request,
            ),
            Tool(
                name="github_get_pull_request_diff",
                description="Get the diff for a pull request",
                schema=schema(
                    {
                        "owner": OWNER,
                        "repo": REPO,
                        "number": i("Pull request number"),
                    },
                    ["owner", "repo", "number"],
                ),
                handler=self._get_pull_request_diff,
            ),
            Tool(
                name="github_list_pull_request_comments",
                description="List comments on a pull request",
                schema=schema(
                    {
                        "owner": OWNER,
                        "repo": REPO,
                        "number": i("Pull request number"),
                        "page_size": i("Results per page (1-100, default: 30)"),
                    },
                    ["owner", "repo", "number"],
                ),
                handler=self._list_pull_request_comments,
            ),
            Tool(
                name="github_list_issues",
                description="List issues in a repository",
                schema=schema(
                    {
                        "owner": OWNER,
                        "repo": REPO,
                        "state": s("Issue state: 'open', 'closed', or 'all' (default: 'open')"),
                        "page_size": i("Results per page (1-100, default: 30)"),
                    },
                    ["owner", "repo"],
                ),
                handler=self._list_issues,
            ),
            Tool(
                name="github_get_issue",
                description="Get details of a specific issue",
                schema=schema(
                    {
                        "owner": OWNER,
                        "repo": REPO,
                        "number": i("Issue number"),
                    },
                    ["owner", "repo", "number"],
                ),
                handler=self._get_issue,
            ),
            Tool(
                name="github_get_file",
                description="Get contents of a file from a repository",
                schema=schema(
                    {
                        "owner": OWNER,
                        "repo": REPO,
                        "path": s("File path in repository"),
                        "branch": s("Branch name (default: repository default branch)"),
                    },
                    ["owner", "repo", "path"],
                ),
                handler=self._get_file,
            ),
            Tool(
                name="github_comment_on_pull_request",
                description="Add a comment to a pull request (requires allow_writes=true)",
                schema=schema(
                    {
                        "owner": OWNER,
                        "repo": REPO,
                        "number": i("Pull request number"),
                        "body": s("Comment text"),
                    },
                    ["owner", "repo", "number", "body"],
                ),
                handler=self._comment_on_pull_request,
                write=True,
                summary=lambda args: f"Comment on PR #{args.get('number')}: {(args.get('body') or '')[:100]}",
                validate=self._validate_write,
            ),
            Tool(
                name="github_create_issue",
                description="Create an issue in a repository (requires allow_writes=true)",
                schema=schema(
                    {
                        "owner": OWNER,
                        "repo": REPO,
                        "title": s("Issue title"),
                        "body": s("Issue description"),
                        "labels": s("Comma-separated list of labels"),
                    },
                    ["owner", "repo", "title"],
                ),
                handler=self._create_issue,
                write=True,
                summary=lambda args: f"Create issue: {args.get('title')}",
                validate=self._validate_write,
            ),
        ]

    def _validate_write(self, args: Dict[str, Any]) -> None:
        """Ensure writes are enabled."""
        if not self.writes_enabled():
            raise IntegrationError("Writes are not enabled. Set allow_writes = true in [github] config.")

    # --- handlers -------------------------------------------------

    def _list_repos(self, args: Dict[str, Any]) -> Any:
        owner = args.get("owner")
        page_size = to_int(args.get("page_size"), 30)

        if owner:
            url = f"/users/{owner}/repos"
        else:
            url = "/user/repos"

        repos = self._c().request("GET", url, params={"per_page": page_size})
        return [_repo_row(r) for r in repos]

    def _list_branches(self, args: Dict[str, Any]) -> Any:
        owner = require(args, "owner")
        repo = require(args, "repo")
        self._check_repo_access(owner, repo)
        page_size = to_int(args.get("page_size"), 30)

        branches = self._c().request(
            "GET",
            f"/repos/{owner}/{repo}/branches",
            params={"per_page": page_size},
        )
        return [_branch_row(b) for b in branches]

    def _list_commits(self, args: Dict[str, Any]) -> Any:
        owner = require(args, "owner")
        repo = require(args, "repo")
        self._check_repo_access(owner, repo)
        branch = args.get("branch", "")
        page_size = to_int(args.get("page_size"), 30)

        params = {"per_page": page_size}
        if branch:
            params["sha"] = branch

        commits = self._c().request(
            "GET",
            f"/repos/{owner}/{repo}/commits",
            params=params,
        )
        return [_commit_row(c) for c in commits]

    def _list_pull_requests(self, args: Dict[str, Any]) -> Any:
        owner = require(args, "owner")
        repo = require(args, "repo")
        self._check_repo_access(owner, repo)
        state = args.get("state", "open").strip() or "open"
        page_size = to_int(args.get("page_size"), 30)

        prs = self._c().request(
            "GET",
            f"/repos/{owner}/{repo}/pulls",
            params={"state": state, "per_page": page_size},
        )
        return [_pr_row(p) for p in prs]

    def _get_pull_request(self, args: Dict[str, Any]) -> Any:
        owner = require(args, "owner")
        repo = require(args, "repo")
        self._check_repo_access(owner, repo)
        number = require(args, "number")

        pr = self._c().request("GET", f"/repos/{owner}/{repo}/pulls/{number}")
        return pr

    def _get_pull_request_diff(self, args: Dict[str, Any]) -> Any:
        owner = require(args, "owner")
        repo = require(args, "repo")
        self._check_repo_access(owner, repo)
        number = require(args, "number")

        diff = self._c().request(
            "GET",
            f"/repos/{owner}/{repo}/pulls/{number}",
            headers={"Accept": "application/vnd.github.v3.diff"},
        )
        return diff

    def _list_pull_request_comments(self, args: Dict[str, Any]) -> Any:
        owner = require(args, "owner")
        repo = require(args, "repo")
        self._check_repo_access(owner, repo)
        number = require(args, "number")
        page_size = to_int(args.get("page_size"), 30)

        comments = self._c().request(
            "GET",
            f"/repos/{owner}/{repo}/issues/{number}/comments",
            params={"per_page": page_size},
        )
        return comments

    def _list_issues(self, args: Dict[str, Any]) -> Any:
        owner = require(args, "owner")
        repo = require(args, "repo")
        self._check_repo_access(owner, repo)
        state = args.get("state", "open").strip() or "open"
        page_size = to_int(args.get("page_size"), 30)

        issues = self._c().request(
            "GET",
            f"/repos/{owner}/{repo}/issues",
            params={"state": state, "per_page": page_size},
        )
        return [_issue_row(i) for i in issues]

    def _get_issue(self, args: Dict[str, Any]) -> Any:
        owner = require(args, "owner")
        repo = require(args, "repo")
        self._check_repo_access(owner, repo)
        number = require(args, "number")

        issue = self._c().request("GET", f"/repos/{owner}/{repo}/issues/{number}")
        return issue

    def _get_file(self, args: Dict[str, Any]) -> Any:
        owner = require(args, "owner")
        repo = require(args, "repo")
        path = require(args, "path")
        self._check_repo_access(owner, repo)
        branch = args.get("branch", "")

        url = f"/repos/{owner}/{repo}/contents/{path}"
        params = {}
        if branch:
            params["ref"] = branch

        return self._c().request("GET", url, params=params)

    def _comment_on_pull_request(self, args: Dict[str, Any]) -> Any:
        owner = require(args, "owner")
        repo = require(args, "repo")
        self._check_repo_access(owner, repo)
        number = require(args, "number")
        body = require(args, "body")

        result = self._c().request(
            "POST",
            f"/repos/{owner}/{repo}/issues/{number}/comments",
            json_body={"body": body},
        )
        return result

    def _create_issue(self, args: Dict[str, Any]) -> Any:
        owner = require(args, "owner")
        repo = require(args, "repo")
        self._check_repo_access(owner, repo)
        title = require(args, "title")
        body = args.get("body", "")
        labels = args.get("labels", "")

        payload: Dict[str, Any] = {"title": title}
        if body:
            payload["body"] = body
        if labels:
            payload["labels"] = [l.strip() for l in labels.split(",")]

        result = self._c().request(
            "POST",
            f"/repos/{owner}/{repo}/issues",
            json_body=payload,
        )
        return result


INTEGRATION = GitHub
