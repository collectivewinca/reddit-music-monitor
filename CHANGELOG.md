# Changelog

## [Unreleased]

### Security
- Removed committed secret files / inline credentials from the tree and added secret patterns to `.gitignore`; affected keys must be rotated (history still contains old values).
- Docs: added SECURITY-NOTES.md, README "Configuration & secrets" section and AGENTS.md secret rules for the 2026-10-15 secrets cleanup.
