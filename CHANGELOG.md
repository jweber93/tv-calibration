# Changelog

All notable changes to this project are documented here. Format loosely follows
[Keep a Changelog](https://keepachangelog.com/en/1.0.0/). This project doesn't
yet cut versioned releases, so entries live under `[Unreleased]` until that
changes.

## [Unreleased]

### Added

- **Split-host Dogegen support.** If you're moving the backend to Docker/Unraid
  while keeping Dogegen on a Windows PC: install the
  [Dogegen Companion Agent](tools/dogegen-agent/README.md) on that PC and set
  `DOGEGEN_AGENT_URL` (env var, `.prefs.json`, or the Dogegen card's new
  "Agent URL" field, which includes a "Test" button to check reachability
  before saving) to point at it. The backend then proxies Dogegen
  start/stop/status over HTTP instead of spawning a local process. Same-host
  installs need no changes — leave `DOGEGEN_AGENT_URL` unset to keep today's
  local-`Popen` behavior. See
  [README: Dogegen — same-host vs. split-host](README.md#dogegen-same-host-vs-split-host).
  (#585)
