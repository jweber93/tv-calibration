# Fix: file watcher survives transient directory disappearance

## Problem

When the watched directory is temporarily removed (e.g., USB drive ejected, network share disconnect), the polling observer's `_poll_loop` catches `FileNotFoundError` and calls `break`, permanently killing the polling thread. The watcher never resumes even after the directory reappears.

## Solution

Changed the `FileNotFoundError` handler to `continue` instead of `break`, keeping the poll loop alive. Added an `OSError` catch block that clears `_watcher_error` when the directory reappears (since `os.scandir` succeeds).

## Changes

- **`calibrator/file_watcher.py`** — Moved `global _watcher_error` to function scope in `_poll_loop`, replaced `break` with `continue` on `FileNotFoundError`, added `OSError` handler to clear error on recovery.
- **`tests/test_file_watcher.py`** — Updated error message assertion for the new wording. Added `test_polling_survives_transient_missing_dir` integration test that removes a directory mid-watch, drops a new CSV after recreation, and verifies the watcher resumes importing and clears the error.

## Testing

All 504 tests pass (2 skipped — polling-specific tests when watchdog is installed).
