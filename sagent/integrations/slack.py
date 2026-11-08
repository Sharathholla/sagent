"""Slack Web API."""

from __future__ import annotations

import re
import time
from typing import Any, Dict, List, Optional, Set

from ..errors import IntegrationError
from .base import Integration, RestClient, Tool, dig, i, require, s, schema, to_int

_CHANNEL = re.compile(r"^[CGD][A-Z0-9]{6,}$")
_BROADCAST = re.compile(r"<!(channel|here|everyone)[^>]*>|@(channel|here|everyone)\b", re.I)
CHANNEL = s("Channel name (e.g. general or #general) or ID (e.g. C0123456789)")


class Slack(Integration):
    name = "slack"
    title = "Slack"
    section = "slack"
    config_help = "[slack]: token (xoxb-... or xoxp-...), allowed_channels"

    def __init__(self, cfg):
        super().__init__(cfg)
        self._client: Optional[RestClient] = None
        self._names: Optional[Dict[str, str]] = None
        self._allowed: Optional[Set[str]] = None

    def configured(self) -> bool:
        return bool(self.conf.get("token"))

    def _api(self, method: str, params: Optional[Dict[str, Any]] = None, post: bool = False) -> Dict[str, Any]:
        for attempt in range(3):
            try:
                return self._call(method, params, post)
            except IntegrationError as exc:
                wait = re.search(r"retry after (\d+)s", str(exc))
                if "rate limited" not in str(exc) or attempt == 2 or not wait:
                    raise
                time.sleep(min(int(wait.group(1)), 60))  # a 429 means nothing was processed
        raise IntegrationError("Slack: rate limited")

    def _call(self, method: str, params: Optional[Dict[str, Any]] = None, post: bool = False) -> Dict[str, Any]:
        if self._client is None:
            self._client = RestClient(
                "Slack", "https://slack.com/api", headers={"Authorization": f"Bearer {self.conf['token']}"}
            )
        if post:
            data = self._client.request("POST", f"/{method}", json_body=params or {})
        else:
            data = self._client.request("GET", f"/{method}", params=params)
        if not isinstance(data, dict) or not data.get("ok"):
            error = data.get("error", "unknown_error") if isinstance(data, dict) else "bad_response"
            hint = {
                "invalid_auth": "token is invalid",
                "not_authed": "no token supplied",
                "missing_scope": f"token lacks scope {data.get('needed', '?')}" if isinstance(data, dict) else "",
                "not_in_channel": "invite the bot to the channel first",
                "channel_not_found": "channel not found (check the name/ID; private channels need the bot invited)",
                "ratelimited": "rate limited, retry shortly",
            }.get(error, "")
            raise IntegrationError(f"Slack: {error}" + (f" ({hint})" if hint else ""))
        return data

    def _lookup(self, name: str) -> Optional[str]:
        """Find a channel ID by name. Private channels first (few), then public, stopping when found."""
        if self._names is None:
            self._names = {}
        for types in ("private_channel", "public_channel"):
            if name in self._names:
                break
            cursor = ""
            try:
                for _ in range(50):
                    params = {"types": types, "limit": 999, "exclude_archived": "true"}
                    if cursor:
                        params["cursor"] = cursor
                    data = self._api("conversations.list", params)
                    for c in data.get("channels", []):
                        self._names[str(c.get("name", "")).lower()] = c.get("id")
                    cursor = dig(data, "response_metadata", "next_cursor") or ""
                    if name in self._names or not cursor:
                        break
            except IntegrationError as exc:
                if "missing_scope" in str(exc) and types == "private_channel":
                    continue  # token cannot see private channels; try public
                raise
        return self._names.get(name)

    def _to_id(self, value: str) -> str:
        value = value.strip()
        if _CHANNEL.match(value):
            return value
        name = value.lstrip("#").lower()
        channel_id = self._lookup(name)
        if not channel_id:
            raise IntegrationError(
                f"Channel '#{name}' not found. Check the name; private channels need the bot invited "
                "and the groups:read scope."
            )
        return channel_id

    def _allowed_ids(self) -> Set[str]:
        if self._allowed is None:
            ids: Set[str] = set()
            for entry in self.cfg.get_list("slack", "allowed_channels"):
                try:
                    ids.add(self._to_id(entry))
                except IntegrationError:
                    continue  # an unresolvable entry grants nothing
            self._allowed = ids
        return self._allowed

    def _channel(self, a: Dict[str, Any], for_write: bool = False) -> str:
        channel = self._to_id(require(a, "channel"))
        allowed = self._allowed_ids()
        if for_write and not self.cfg.get_list("slack", "allowed_channels"):
            raise IntegrationError("Posting requires allowed_channels (names or IDs) in [slack] of ~/.sagent/config")
        if self.cfg.get_list("slack", "allowed_channels") and channel not in allowed:
            raise IntegrationError(f"Channel {channel} is not in allowed_channels")
        return channel

    @staticmethod
    def _msg(m: Dict[str, Any]) -> Dict[str, Any]:
        return {"ts": m.get("ts"), "user": m.get("user") or m.get("username"), "text": m.get("text"),
                "replies": m.get("reply_count")}

    # --- read --------------------------------------------------------------

    def list_channels(self, a):
        valid = {"public_channel", "private_channel"}
        wanted = [t.strip() for t in str(a.get("types") or "public_channel,private_channel").split(",") if t.strip()]
        if not wanted or not set(wanted) <= valid:
            raise IntegrationError("types must be public_channel and/or private_channel")
        limit = to_int(a.get("limit"), 200, 1, 1000)
        wanted.sort()  # private_channel sorts first: few, and must not be cut off by the limit
        restricted = bool(self.cfg.get_list("slack", "allowed_channels"))
        allowed = self._allowed_ids() if restricted else set()
        rows: List[Dict[str, Any]] = []
        skipped: List[str] = []
        for types in wanted:
            cursor = ""
            try:
                for _ in range(20):
                    params = {"types": types, "limit": min(limit, 999), "exclude_archived": "true"}
                    if cursor:
                        params["cursor"] = cursor
                    data = self._api("conversations.list", params)
                    for c in data.get("channels", []):
                        rows.append({
                            "id": c.get("id"), "name": c.get("name"), "private": bool(c.get("is_private")),
                            "bot_is_member": c.get("is_member"), "members": c.get("num_members"),
                            "allowed": (c.get("id") in allowed) if restricted else True,
                        })
                    cursor = dig(data, "response_metadata", "next_cursor") or ""
                    if not cursor or len(rows) >= limit:
                        break
            except IntegrationError as exc:
                if "missing_scope" not in str(exc):
                    raise
                skipped.append(types)
        result: Dict[str, Any] = {"channels": rows[:limit], "count": min(len(rows), limit)}
        if skipped:
            result["note"] = f"Token lacks the scope to list: {', '.join(skipped)}"
        if restricted:
            result["note_allowed"] = "Only channels with allowed=true can be read or posted to (allowed_channels)."
        return result

    def history(self, a):
        params = {"channel": self._channel(a), "limit": to_int(a.get("limit"), 20, 1, 100)}
        if a.get("oldest"):
            params["oldest"] = a["oldest"]
        return [self._msg(m) for m in self._api("conversations.history", params).get("messages", [])]

    def replies(self, a):
        params = {"channel": self._channel(a), "ts": require(a, "ts"), "limit": to_int(a.get("limit"), 50, 1, 200)}
        return [self._msg(m) for m in self._api("conversations.replies", params).get("messages", [])]

    def search(self, a):
        data = self._api("search.messages", {"query": require(a, "query"), "count": to_int(a.get("limit"), 10, 1, 50)})
        allowed = self._allowed_ids() if self.cfg.get_list("slack", "allowed_channels") else set()
        out = []
        for m in dig(data, "messages", "matches") or []:
            cid = dig(m, "channel", "id")
            if allowed and cid not in allowed:
                continue
            out.append({"channel": cid, "user": m.get("username"), "ts": m.get("ts"), "text": m.get("text"),
                        "link": m.get("permalink")})
        return out

    def user_info(self, a):
        u = self._api("users.info", {"user": require(a, "user")}).get("user", {})
        return {"id": u.get("id"), "name": u.get("real_name") or u.get("name"), "title": dig(u, "profile", "title"),
                "tz": u.get("tz")}

    # --- write -------------------------------------------------------------

    def post(self, a):
        body: Dict[str, Any] = {
            "channel": self._channel(a, for_write=True),
            "text": _BROADCAST.sub("", require(a, "text")),
            "link_names": False,
        }
        if a.get("thread_ts"):
            body["thread_ts"] = a["thread_ts"]
        data = self._api("chat.postMessage", body, post=True)
        return {"ok": True, "ts": data.get("ts")}

    def tools(self) -> List[Tool]:
        T = Tool
        return [
            T("slack_list_channels",
              "List channels the token can see (public, and private ones the bot is in). Each has an 'allowed' flag.",
              schema({"types": s("public_channel and/or private_channel (default both)"),
                      "limit": i("Max 1000, default 200")}), self.list_channels),
            T("slack_channel_history", "Read recent messages in a channel.",
              schema({"channel": CHANNEL, "limit": i("Max 100"), "oldest": s("Unix timestamp lower bound")},
                     ["channel"]), self.history),
            T("slack_thread_replies", "Read a thread.",
              schema({"channel": CHANNEL, "ts": s("Thread parent ts"), "limit": i("Max 200")}, ["channel", "ts"]),
              self.replies),
            T("slack_search_messages", "Search messages (needs a user token with search:read).",
              schema({"query": s("Slack search query"), "limit": i("Max 50")}, ["query"]), self.search),
            T("slack_user_info", "Look up a user by ID.", schema({"user": s("User ID, e.g. U0123")}, ["user"]),
              self.user_info),
            T("slack_post_message", "WRITE: post a message to an allowed channel (@channel/@here are stripped).",
              schema({"channel": CHANNEL, "text": s("Message text"), "thread_ts": s("Reply in this thread")},
                     ["channel", "text"]), self.post, write=True,
              summary=lambda a: f"Slack: post to {a.get('channel')}: {str(a.get('text'))[:300]!r}"),
        ]

    def check(self) -> str:
        who = self._api("auth.test")
        return f"authenticated as {who.get('user')} in {who.get('team')}"


INTEGRATION = Slack
