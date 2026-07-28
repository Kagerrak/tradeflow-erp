# Issue tracker: GitHub

Issues and PRDs for this repository live as GitHub Issues. Use the `gh` CLI for all operations.

The repository must be initialized and connected to its GitHub remote before publishing issues.

## Conventions

- Create: `gh issue create --title "..." --body-file <file>`
- Read: `gh issue view <number> --comments`
- List: `gh issue list --state open --json number,title,body,labels,comments`
- Comment: `gh issue comment <number> --body "..."`
- Apply/remove labels: `gh issue edit <number> --add-label "..."` or `--remove-label "..."`
- Close: `gh issue close <number> --comment "..."`

Infer the repository from `git remote -v`.

## Skill instructions

When a skill says “publish to the issue tracker,” create a GitHub issue.

When a skill says “fetch the relevant ticket,” run `gh issue view <number> --comments`.
