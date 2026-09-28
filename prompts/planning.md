Write an implementation plan for this GitHub issue. Do not implement code. Do not create commits, branches, pull requests, or GitHub issues.

You are in **plan** mode. The deliverable is a full markdown implementation plan: files to touch, approach, risks, and what "done" looks like. Write that plan to `.devbot/plan.md` in this worktree. If you have a CreatePlan tool, put the same full markdown in that tool's `plan` field. A status update ("researching", "I have enough context", "plan.md is missing") is not a plan and will be discarded.

Do not invoke brainstorm or interview skills. Do not ask optional product questions.

Read the Slack thread and the entire GitHub issue comments in this prompt. Treat later comments as the latest instructions.

Default to `decision: continue`. Document assumptions in the plan. Ask (`need_info`) only if you are blocked on a fact that is not in the repo, Slack thread, or GitHub comments.

After the plan is written, return JSON only. `plan` must be the same full markdown, not a status sentence:

```json
{
  "decision": "continue",
  "plan": "",
  "questions": []
}
```
