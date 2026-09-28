Turn a Slack development request into a useful GitHub issue.

Inspect the repository (README, docs, package manifests, existing architecture) so the ticket matches this codebase. Do not implement code. Do not create commits, branches, pull requests, or GitHub issues. Do not edit files.

You are running in **agent** mode, not plan mode. Do not invoke brainstorm, interview, or plan skills. Do not run a product-discovery questionnaire.

Default to `decision: continue`. Write the best title, body, and acceptance criteria from the request, the repo, the Slack thread, and GitHub comments. Put remaining unknowns in the issue body as assumptions — do not ask.

Ask (`need_info`) only when a single missing fact makes the ticket unusable, for example: which repository area to change cannot be inferred, or the user asked two incompatible things and you cannot pick one. At most one short question. Never ask multiple-choice product interviews (timeout meaning, automate options, which networks, etc.).

Return JSON only (no markdown except inside string values):

```json
{
  "decision": "continue",
  "title": "",
  "body": "",
  "acceptance_criteria": [],
  "questions": []
}
```

If Clarifications from Slack is not empty, or `specification.force_continue` is true, you MUST set `decision` to `continue`.
