Inspect the change on this branch and update documentation only when users or operators would otherwise be misled.

If existing docs already cover the change, do nothing.
If docs are required, make the smallest accurate edit and explain why.

Do not run `git commit`, `git push`, or create GitHub issues/PRs — devbot commits after your step.

If you need information from the human, stop and ask.

End with JSON:

```json
{
  "decision": "continue",
  "status": "PASS",
  "files_changed": [],
  "questions": [],
  "summary": ""
}
```

`decision` is `continue` or `need_info`.
