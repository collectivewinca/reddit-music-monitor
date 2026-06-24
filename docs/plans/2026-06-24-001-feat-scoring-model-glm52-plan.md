---
title: "feat: Switch scoring model to glm-5.2 (per-project, verified)"
type: feat
date: 2026-06-24
status: ready
depth: lightweight
---

# feat: Switch the relevance-scoring model to glm-5.2

## Summary
Change the LLM that scores each gated Reddit post 0–100 in `score_with_llm`
(`l30d_monitor.py`) from `deepseek-v4-flash` to **`glm-5.2`**, scoped to *only*
this repo via a per-project last30days config override, then verify the new model
returns the `{index: score}` JSON the scorer parses. Add a lightweight LLM-coverage
canary so a model that silently under-scores can't publish an inverted ranking.

## Problem Frame
The scoring model is **not hardcoded in this repo**. `score_with_llm` calls
`l30_prov.resolve_runtime(cfg, "quick").rerank_model`, where `cfg` comes from
last30days' config resolver. Today that resolves to `deepseek-v4-flash` via the
`openrouter` provider pointed at **Ollama Cloud** (`https://ollama.com/v1/chat/completions`).
The goal is to swap the model to `glm-5.2` without (a) editing the shared
last30days skill, (b) affecting other last30days consumers on this machine, or
(c) breaking the JSON contract `score_with_llm` depends on.

## Key Technical Decisions

- **Model = `glm-5.2`.** Verified present in the Ollama Cloud model list
  (`GET ollama.com/v1/models` → includes `glm-5.2`, `glm-5.1`, `glm-5`, `glm-4.7`).
  The user's alternate "Gemini Flash 3.5" does **not** exist (Ollama Cloud's Gemini
  is `gemini-3-flash-preview`); glm-5.2 is the exact requested model.
- **Scope = per-project override, not the global config.** last30days merges config
  as `env > .claude/last30days.env (per-project, walking up from cwd) > ~/.config/last30days/.env (global)`.
  The cron does `cd ~/projects/reddit-music-monitor` before `./run.sh`, so a repo-local
  `.claude/last30days.env` overrides *only* this repo's runs. Editing the global file
  would change the model for **every** last30days consumer — rejected (blast radius).
- **No provider change.** Scoring runs through the `openrouter`→Ollama-Cloud
  passthrough (`OpenRouterClient`, OpenAI-compatible). The model id is just a string
  passed to Ollama. The `_require_gemini_31` guard in last30days applies **only** to
  the native `gemini` provider, so it does not constrain this change.
- **Only `rerank_model` matters here.** `score_with_llm` uses `runtime.rerank_model`.
  The override sets `LAST30DAYS_RERANK_MODEL`; leave planner untouched (the monitor
  doesn't use the planner).

## Implementation Units

### U1. Add the per-project model override
**Goal:** Pin `glm-5.2` for this repo only.
**Files:** `.claude/last30days.env` (new), `.gitignore` (verify/append).
**Approach:** Create `.claude/last30days.env` containing
`LAST30DAYS_RERANK_MODEL=glm-5.2`. Do NOT duplicate the provider/URL/API-key lines —
those still come from the global config; the per-project file only *overrides* the
one key. Decide whether the file is committed (shareable, no secret) or gitignored;
recommend **committing** it since it carries no credential and documents the choice
(add a comment line explaining it overrides the global deepseek pin).
**Verification:** From the repo dir, `resolve_runtime(get_config(), "quick").rerank_model`
returns `glm-5.2`; running the same resolver from `~` (or another dir) still returns
`deepseek-v4-flash` (proves scoping).
**Test expectation:** none — config-only; behavior verified by U2.

### U2. Verify glm-5.2 returns the parseable scoring JSON
**Goal:** Confirm the new model honors the scorer's output contract before relying on it.
**Files:** none (verification step; optionally a throwaway script under the scratchpad).
**Approach:** Run a real `score_with_llm`-shaped call against `glm-5.2` with ~10 sample
gated posts using the exact prompt (`"Return ONLY a JSON object mapping the index (as a
string) to an integer 0-100"`). Confirm `client.generate_json(...)` returns a `dict`
whose keys are the string indices and values are 0–100 ints. GLM models can wrap JSON
in prose or ```` ```json ```` fences — confirm last30days' `extract` handles it (the
`_extract_json`/first-JSON-object logic in `providers.py`). If GLM reasons aloud and
breaks extraction, that's the gate to fall back to `gemini-3-flash-preview`.
**Test scenarios:**
- Happy path: 10 posts in, dict of 10 `{str(i): int}` out, all 0–100.
- Coverage: assert `len(data) == len(posts)` (full coverage on a healthy call).
- Robustness: a post whose title contains prose/quotes does not break extraction.
**Verification:** A live run end-to-end (`./run.sh` or `l30d_monitor.py`) produces
non-null `relevance_score` values and a dashboard whose highlights look on-topic.

### U3. (Recommended) LLM-coverage canary
**Goal:** Make a model that scores too few posts *visible* instead of silently
falling back to keyword-only ranking (carries the earlier code-review finding that an
LLM-down run silently inverts the ranking).
**Files:** `l30d_monitor.py` (`score_with_llm`).
**Approach:** After parsing `data`, compute coverage `len(data)/len(posts)`. The
partial-response warning already exists at <50%; add a grep-able
`CANARY low-llm-coverage` WARNING (so `run.sh`'s notifier can alert) and record the
model id + coverage in the log line for observability across the model switch.
**Test scenarios:**
- 6/10 scored → emits `CANARY low-llm-coverage` once.
- 10/10 scored → no canary; logs model id + 100%.
**Verification:** Forcing a partial/empty response logs the canary; a healthy run logs
`glm-5.2 100%`.

## Rollback
Single-line revert: delete `.claude/last30days.env` (or change the value back to
`deepseek-v4-flash`). No code rollback needed if U3 is committed separately.

## Risks & Notes
- **GLM JSON discipline:** reasoning-heavy models sometimes emit chain-of-thought
  before JSON (cf. the project's known "Gemini reasoning leak" and "MiniMax phantom
  tool call" gotchas). U2 is the gate; `gemini-3-flash-preview` is the verified fallback.
- **Latency/cost:** glm-5.2 may be slower than deepseek-flash; the monitor scores one
  batch per run (6h cadence) so latency is non-critical, but watch the `run.sh`
  publish-timeout headroom.
- **Global config unchanged:** other last30days consumers keep deepseek — intended.

## Verification Strategy (whole change)
1. U1 resolver check (scoped override works).
2. U2 live JSON-contract check on glm-5.2.
3. One full `./run.sh` → dashboard highlights remain on-topic, `relevance_score`
   populated, cron.log shows `glm-5.2` + coverage.
