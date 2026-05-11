# EviTrace — Changelog Rules

## CHANGELOG.md is permanent

`CHANGELOG.md` at the repo root **must never be deleted**. It is the
authoritative record of significant changes to the codebase.

---

## When to add an entry

Add a brief entry to `CHANGELOG.md` whenever any of the following occur:

- A spec is implemented (feature or bugfix)
- A steering document is added, updated, or removed
- A README file is rewritten or significantly updated
- Production modules are renamed, moved, or deleted
- Public APIs change (function signatures, return types, exported names)
- Config keys are added, renamed, or removed
- Test files are renamed or reorganised in a way that changes the test layout
- Any other change a future developer would want to know about when reading
  git history

You do **not** need an entry for:
- Routine bug fixes that don't change any API or architecture
- Minor docstring or comment edits
- Formatting-only changes

---

## Entry format

```markdown
## [YYYY-MM] — Short title (`spec/name` if applicable)

One or two sentences describing what changed and why. Then bullet points
for the specific things that were added, removed, or renamed. Keep it
brief — enough for a future reader to understand what happened and where
to look, not a full spec summary.
```

---

## What NOT to do

- Do not delete `CHANGELOG.md`
- Do not truncate or overwrite existing entries
- Do not add entries for changes that are already covered by a more recent
  entry in the same session
- Do not copy the full spec requirements into the changelog — summarise
