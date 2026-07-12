# Changelog

All notable changes to this project are documented here. Format loosely follows
[Keep a Changelog](https://keepachangelog.com/en/1.0.0/). This project doesn't
yet cut versioned releases, so entries live under `[Unreleased]` until that
changes.

## [Unreleased]

### Fixed

- **`.prefs.json` mistakenly created as a directory.** Docker/Unraid bind
  mounts of a single host file that doesn't exist yet get created as a
  directory instead, which previously made every preferences load and save
  fail (`IsADirectoryError`) for the life of the container. Startup now
  detects an empty directory at the prefs path, removes it, and lets prefs
  load/save normally again; a non-empty directory (or one that can't be
  removed) now logs a clear, actionable warning instead of a bare traceback.

- **Prefs never persisted when `.prefs.json` was bind-mounted directly.**
  Docker/Unraid single-file bind mounts turn `.prefs.json` into a mount
  point, and mount points can't be replaced via `rename()` — so the atomic
  `.prefs.tmp` → `.prefs.json` swap failed every single save with
  `[Errno 16] Device or resource busy`, silently discarding every
  preference change. `.prefs.json` now lives inside its own `.prefs/`
  directory (`server.py:_PREFS_DIR`), and the Unraid template's "Prefs dir"
  config maps that directory instead of the file, so the mount point sits
  above the file Docker/Unraid never needs to swap. `_save_prefs()` also
  falls back to a direct (non-atomic) write if it ever hits `EBUSY` again,
  for deployments still bind-mounting the file itself.
  **Migration:** existing Unraid installs should update the template (path
  mapping changes from `/app/.prefs.json` to `/app/.prefs`) and move their
  host-side `.prefs.json` into the new `.prefs/` folder, e.g.
  `mkdir -p /mnt/user/appdata/tv-calibration/.prefs && mv /mnt/user/appdata/tv-calibration/.prefs.json /mnt/user/appdata/tv-calibration/.prefs/.prefs.json`.
  Skipping this just means preferences reset to defaults once — nothing
  else breaks.

- **Unraid template icon not displaying.** The template's `<Icon>` pointed at
  `frontend/public/favicon.svg`; Unraid's Docker Manager doesn't render SVG
  icons, so the app showed no icon at all. Added `frontend/public/icon.png` —
  a 256×256 raster rendering of the same mark — and pointed the template at
  it instead.

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
