# Security notes

## 2026-10-15 — Committed secrets removed from the tree

- Removed from the current tree: `.env`.
- `.gitignore` now blocks `.env`, `.env.*` (except `.env.example`), service-account JSON and OpenClaw secret paths.

### Keys that must be rotated
- Webshare API key — rotate at the webshare.io dashboard (API keys)

### Status
- **Git history still contains the old values.** Treat every key above as exposed until it is rotated.
- History purge (`git filter-repo` / BFG + force-push): **decision pending** (owner: Alet).

### Environment variables now required (names only)
- `WEBSHARE_API_KEY`
- `REDDIT_CLIENT_ID`
- `REDDIT_CLIENT_SECRET`
- `REDDIT_USER_AGENT`
