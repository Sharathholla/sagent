---
name: github
description: Inspect GitHub repositories, branches, commits, pull requests and issues in approved repos/organizations; comment on or create issues with confirmation.
integrations: github
keywords: repo, repository, repositories, pull request, pull requests, pr, prs, branch, branches, commit, commits, issue, issues, code review, diff
---

# GitHub skills

## Purpose
Help the user understand and review code hosted on GitHub: find repositories, summarise pull requests,
review diffs, check issues, and (only when asked) leave comments or create issues.

## Allowed operations
Read-only (no confirmation needed):
- `github_list_repos`, `github_list_branches`, `github_list_commits`
- `github_list_pull_requests`, `github_get_pull_request`, `github_get_pull_request_diff`,
  `github_list_pull_request_comments`
- `github_list_issues`, `github_get_issue`
- `github_get_file`

State-changing (always requires the user's confirmation, and `allow_writes = true` in config):
- `github_comment_on_pull_request`
- `github_create_issue`

Not available and must never be attempted: merging PRs, closing/deleting issues, branch operations,
force-pushing, changing repository settings, webhooks, deploy keys, or permissions.

## Procedures
1. **Scope check.** Use only repositories (owner/repo) or organizations approved in `~/.sagent/config`.
   If the user names anything else, stop and explain which repos/orgs are approved.
2. **Read first.** Gather facts with read-only tools before proposing anything. Prefer small pages
   (`page_size` 10-25) and narrow queries.
3. **Review a pull request.** `get_pull_request` -> `get_pull_request_diff` -> `list_pull_request_comments`.
   Summarise intent, risky changes, missing tests, and open questions. Do not invent content that is not in the diff.
4. **Write actions.** Before calling a write tool, show a short plan:
   - what will change, in which repo / PR, and the exact text to be posted
   - why it is needed
   Then call the tool; the human is prompted to confirm. If the result is `DECLINED`, `Refused` or `[DRY RUN]`,
   stop and report; do not retry or rephrase to get around it.
5. **Dry run.** In dry-run mode, present the plan only.

## Output format
- Start with a one-line answer or verdict.
- Then a compact table or bullet list (repo / PR id / title / author / state / updated).
- For reviews: sections **Summary**, **Risks**, **Suggestions**, each with file references.
- Quote at most a few lines of code; never paste whole files.
- End write proposals with "Proposed action:" followed by the exact payload.

## Guardrails
- Refuse write operations if `allow_writes = false` (the default).
- Refuse any access to unapproved repos or organizations.
- Do not modify repository settings, branch protection rules, or workflows.
- Do not attempt to merge, decline, or delete anything.
- Respect GitHub's API rate limits and pagination; aim for efficient, scoped queries.
