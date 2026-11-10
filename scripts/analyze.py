"""Run the read-only graph analyzer directly from a checkout."""

from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from papertrail.analysis_cli import main


if __name__ == "__main__":
    raise SystemExit(main())
