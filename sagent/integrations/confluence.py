"""Confluence Cloud (REST API v1 search + v2 content)."""

from __future__ import annotations

import html
import re
from typing import Any, Dict, List, Optional, Set

from ..errors import IntegrationError
from .base import Integration, RestClient, Tool, atlassian_base, dig, i, require, s, schema, scope_query, to_int

_ID = re.compile(r"^\d+$")
_SPACE = re.compile(r"^[A-Za-z0-9~_-]+$")


def _html_to_text(markup: str) -> str:
    text = re.sub(r"<(br|/p|/h[1-6]|/li|/tr)\s*/?>", "\n", markup or "")
    return html.unescape(re.sub(r"<[^>]+>", "", text)).strip()


def _text_to_html(text: str) -> str:
    return "".join(f"<p>{html.escape(p)}</p>" for p in text.split("\n\n") if p.strip()) or "<p></p>"


class Confluence(Integration):
    name = "confluence"
    title = "Confluence Cloud"
    section = "confluence"
    config_help = "[confluence]: base_url, email, api_token"

    def __init__(self, cfg):
        super().__init__(cfg)
        self._client: Optional[RestClient] = None
        self._space_ids: Optional[Set[str]] = None

    def configured(self) -> bool:
        return all(self.conf.get(k) for k in ("base_url", "email", "api_token"))

    def _c(self) -> RestClient:
        if self._client is None:
            self._client = RestClient(
                "Confluence",
                atlassian_base(self.conf, "Confluence", "/wiki"),
                auth=(str(self.conf["email"]), str(self.conf["api_token"])),
            )
        return self._client

    def _allowed_spaces(self) -> List[str]:
        return self.cfg.get_list("confluence", "allowed_spaces")

    def _space_id_allowed(self, space_id: Any) -> None:
        allowed = self._allowed_spaces()
        if not allowed:
            return
        if self._space_ids is None:
            ids: Set[str] = set()
            for key in allowed:
                if not _SPACE.match(key):
                    continue
                data = self._c().request("GET", "/api/v2/spaces", params={"keys": key})
                ids.update(str(r.get("id")) for r in data.get("results", []))
            self._space_ids = ids
        if str(space_id) not in self._space_ids:
            raise IntegrationError(f"That page is not in an allowed space ({', '.join(allowed)})")

    def _page_id(self, a: Dict[str, Any]) -> str:
        pid = require(a, "page_id")
        if not _ID.match(pid):
            raise IntegrationError("page_id must be numeric")
        return pid

    def _get_page(self, pid: str, body: bool) -> Dict[str, Any]:
        params = {"body-format": "storage"} if body else None
        page = self._c().request("GET", f"/api/v2/pages/{pid}", params=params)
        self._space_id_allowed(page.get("spaceId"))
        return page

    # --- read --------------------------------------------------------------

    def search(self, a):
        cql = require(a, "cql")
        allowed = self._allowed_spaces()
        if allowed:
            safe = [k for k in allowed if _SPACE.match(k)]
            cql = scope_query(cql, "space in (" + ",".join(f'"{k}"' for k in safe) + ")")
        data = self._c().request("GET", "/rest/api/search", params={"cql": cql, "limit": to_int(a.get("limit"), 10, 1, 50)})
        return [
            {
                "id": dig(r, "content", "id"),
                "type": dig(r, "content", "type"),
                "title": r.get("title"),
                "space": dig(r, "resultGlobalContainer", "title"),
                "url": r.get("url"),
                "modified": r.get("lastModified"),
                "excerpt": _html_to_text(r.get("excerpt", "")),
            }
            for r in data.get("results", [])
        ]

    def get_page(self, a):
        page = self._get_page(self._page_id(a), body=True)
        return {
            "id": page.get("id"),
            "title": page.get("title"),
            "version": dig(page, "version", "number"),
            "text": _html_to_text(dig(page, "body", "storage", "value") or ""),
        }

    def list_spaces(self, a):
        data = self._c().request("GET", "/api/v2/spaces", params={"limit": 50})
        allowed = {k.upper() for k in self._allowed_spaces()}
        rows = [{"key": r.get("key"), "name": r.get("name"), "id": r.get("id")} for r in data.get("results", [])]
        return [r for r in rows if not allowed or str(r["key"]).upper() in allowed]

    # --- write -------------------------------------------------------------

    def add_comment(self, a):
        pid = self._page_id(a)
        self._get_page(pid, body=False)
        self._c().request(
            "POST", "/api/v2/footer-comments",
            json_body={"pageId": pid, "body": {"representation": "storage", "value": _text_to_html(require(a, "text"))}},
        )
        return {"ok": True}

    def create_page(self, a):
        key = require(a, "space_key")
        if not _SPACE.match(key):
            raise IntegrationError("Invalid space_key")
        allowed = {k.upper() for k in self._allowed_spaces()}
        if allowed and key.upper() not in allowed:
            raise IntegrationError(f"Space '{key}' is not in allowed_spaces")
        spaces = self._c().request("GET", "/api/v2/spaces", params={"keys": key}).get("results", [])
        if not spaces:
            raise IntegrationError(f"Space '{key}' not found")
        body: Dict[str, Any] = {
            "spaceId": spaces[0]["id"],
            "status": "current",
            "title": require(a, "title"),
            "body": {"representation": "storage", "value": _text_to_html(a.get("body") or "")},
        }
        if a.get("parent_id"):
            body["parentId"] = a["parent_id"]
        page = self._c().request("POST", "/api/v2/pages", json_body=body)
        return {"id": page.get("id")}

    def tools(self) -> List[Tool]:
        T = Tool
        return [
            T("confluence_search", "Search content with CQL, e.g. text ~ \"deploy\" AND type = page.",
              schema({"cql": s("CQL query"), "limit": i("Max 50, default 10")}, ["cql"]), self.search),
            T("confluence_get_page", "Read a page as plain text.",
              schema({"page_id": s("Numeric page id")}, ["page_id"]), self.get_page),
            T("confluence_list_spaces", "List visible spaces.", schema({}), self.list_spaces),
            T("confluence_add_comment", "WRITE: add a footer comment to a page.",
              schema({"page_id": s("Numeric page id"), "text": s("Comment text")}, ["page_id", "text"]),
              self.add_comment, write=True,
              summary=lambda a: f"Confluence: comment on page {a.get('page_id')}: {str(a.get('text'))[:300]!r}"),
            T("confluence_create_page", "WRITE: create a page in a space.",
              schema({"space_key": s("Space key"), "title": s("Title"), "body": s("Plain text body"),
                      "parent_id": s("Optional parent page id")}, ["space_key", "title"]),
              self.create_page, write=True,
              summary=lambda a: f"Confluence: create page {a.get('title')!r} in space {a.get('space_key')}"),
        ]

    def check(self) -> str:
        data = self._c().request("GET", "/api/v2/spaces", params={"limit": 1})
        return f"authenticated ({len(data.get('results', []))} space(s) visible in first page)"


INTEGRATION = Confluence
