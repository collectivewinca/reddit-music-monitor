# Silent AttributeError Swallowed by Broad `except` — Proxy Rotation Failure

**Date:** 2026-06-15
**Commit:** 66be62b
**Files:** `reddit_monitor.py`, `gen_dashboard.py`

## Problem

On every HTTP 403 response, the monitor called `self.proxy_manager.mark_proxy_failed(proxy)` — a method that **did not exist**. The real method was `mark_failed()`. This raised an `AttributeError` every time, but a broad `except Exception` block around the request logic silently swallowed it, so:

- The failed proxy was never removed from the pool.
- The same blocked proxy kept being reused on retries.
- Proxy rotation was effectively broken on every 403.
- No log line, no traceback — completely silent failure.

This went undetected for a long time because the code *appeared* to work (it retried, just with the same bad proxy).

## Root Cause

A method-name mismatch: `mark_proxy_failed()` vs the actual `mark_failed()`. Likely a rename that wasn't propagated to all call sites, or a method name written from memory without verifying the class interface.

## Fix

```python
# Before (broken — AttributeError on every call)
self.proxy_manager.mark_proxy_failed(proxy)

# After (correct method name)
self.proxy_manager.mark_failed(proxy)
```

## Lessons

### 1. Broad `except Exception` hides real bugs

A bare or broad except that logs nothing (or logs a generic message and continues) is the #1 way bugs like this survive in production. The AttributeError was a programming error, not a transient network issue — it should have crashed loudly.

**Rule of thumb:** Catch the *narrowest* exception you expect (`requests.RequestException`, `json.JSONDecodeError`, etc.). Let unexpected exceptions (`AttributeError`, `TypeError`, `KeyError`) propagate so they surface immediately.

### 2. Method-name mismatches are easy to introduce, hard to spot

When you rename a method or write a call from memory, the mismatch only shows up at runtime — and only on the code path that triggers it. In a dynamically-typed language like Python, the compiler won't catch it.

**Mitigations:**
- Use `grep -r` or your IDE's "find usages" after any method rename.
- Add type hints and run `mypy` — it catches `AttributeError` at static-analysis time.
- Write a test that exercises the error-handling path (e.g., mock a 403 response and assert the proxy gets removed).

### 3. Test error paths, not just happy paths

This bug lived exclusively in the 403-handling branch. Happy-path requests (200) never touched it. If there had been a single test that simulated a 403 and asserted that `mark_failed()` was called, the mismatch would have been caught immediately.

---

## Bonus: Security Issues Found in the Same Review

### eval() on DB column → Remote Code Execution

`gen_dashboard.py` used `eval()` to deserialize a JSON string from a SQLite column:

```python
# Before (RCE vector — attacker-controlled post data stored in DB)
keywords = eval(row['matched_keywords'])

# After
keywords = json.loads(row['matched_keywords'])
```

**Rule:** Never use `eval()` on data that has passed through an external boundary (network, DB, file). Use `json.loads()`, `ast.literal_eval()`, or a proper deserializer.

### Unescaped user content in HTML → XSS

Post titles, authors, and URLs from Reddit were interpolated directly into the HTML dashboard without escaping. A malicious post title like `<script>alert(1)</script>` would execute in the viewer's browser.

**Fix:** Apply `html.escape()` to all user-sourced strings, and validate URL schemes (`http://`, `https://`) before using them in `href` attributes — a `javascript:` URL bypasses HTML escaping.

```python
safe_title = html_mod.escape(str(title))
safe_url = html_mod.escape(str(url)) if str(url).startswith(("http://", "https://")) else "#"
```
