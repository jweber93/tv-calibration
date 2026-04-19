#!/usr/bin/env python3
"""Generate deterministic report artifacts for CI visual regression checks."""

from __future__ import annotations

import argparse
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from calibrator.report_samples import write_sample_report_artifacts


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Generate sample calibration report artifacts."
    )
    parser.add_argument(
        "--output-dir",
        default="test-artifacts/report-layout",
        help="Directory where the sample HTML/PDF/JSON files will be written.",
    )
    parser.add_argument(
        "--skip-pdf",
        action="store_true",
        help="Write HTML and JSON only. Useful on machines without WeasyPrint system libraries.",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    artifacts = write_sample_report_artifacts(
        Path(args.output_dir),
        include_pdf=not args.skip_pdf,
    )
    for label, path in artifacts.items():
        print(f"{label}: {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
