# tv-calibration

`tv-calibration` is a monorepo for two related pieces of TV-calibration tooling:

- `calcore/`: a stdlib-only Python package that holds the deterministic calibration math and CSV parsing logic.
- `calibrator/` + `server.py`: a FastAPI backend for the guided web workflow, imported from the `u8g-calibrator` project and decomposed into modules.

At the moment, this repo includes the backend and built static web assets under `static/`. The original React source tree is not in this repo yet.

## Repo Layout

```text
calcore/        Shared math engine and CLI-oriented analysis helpers
calibrator/     Web-workflow backend modules
server.py       FastAPI entry point for the calibrator app
cli.py          Command-line entry point for the calcore workflow
static/         Built frontend assets served by FastAPI
tests/          End-to-end, API, and package-level test coverage
tools/          Reference CSVs and ZRO bridge helper files
```

## Requirements

- Python 3.11+
- Pip-installable dependencies from `requirements.txt`

## Getting Started

Create a virtual environment, install dependencies, and run either the CLI or the web backend:

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
```

### Run the CLI

```bash
python cli.py --csv /path/to/measurements.csv
```

Common options:

- `--mode sdr|hdr`
- `--eotf pq|gamma22|bt1886|<numeric gamma>`
- `--target-space bt709|p3d65|bt2020`
- `--watch`

### Run the Web Backend

```bash
uvicorn server:app --host 0.0.0.0 --port 8000
```

Then open [http://localhost:8000](http://localhost:8000).

## Testing

Run the full suite with:

```bash
pytest -q
```

Useful spot checks:

```bash
pytest -q tests/test_calcore
pytest -q tests/test_server_api.py
python3 -m compileall calcore calibrator server.py cli.py
```

## Current State

- `calcore` has been extracted from the original `tv_calibration_coach` script.
- The calibrator backend has been decomposed into `calibrator/guidance.py`, `calibrator/session.py`, `calibrator/quality.py`, and `calibrator/reports.py`.
- GitHub Actions now runs the Python test suite on pushes and pull requests.

## Notes

- Session persistence is stored under `.sessions/` at runtime and ignored by git.
- The backend currently serves built static assets from `static/`.
- Future phases are expected to wire `calcore` directly into `calibrator` and bring over richer web-app source/UI workflows.
