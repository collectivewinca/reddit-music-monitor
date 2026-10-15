# AGENTS.md

## Purpose
Python Reddit monitor that tracks indie artists and music posts across subreddits.

## Configuration
Copy `.env.example` to `.env` and fill values locally. Required names are listed in README → Configuration & secrets. Secret status: SECURITY-NOTES.md.

## Hard rules
- Never commit secrets: no `.env` files, API keys, tokens, passwords, private keys or service-account JSON.
- Read credentials from environment variables (see README → Configuration & secrets).
- Check `.gitignore` and `git status` before committing; never force-add ignored files.
- No production deploys, outreach, or history rewrites without Alet's approval.
