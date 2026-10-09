"""Write synthetic judge-trial fixtures for the browser check. Offline.

    .venv/bin/python scripts/make_trial_fixtures.py /tmp/trial-fixtures

Generated images only: never real photographs, so nothing private can end up
in a screenshot or in the repository.
"""

import pathlib
import sys

REPO = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "tests"))
sys.path.insert(0, str(REPO / "backend"))

from trial_support import DUMP, KOLKATA, drive, jpeg, minimal_pdf, trace_json  # noqa: E402


def main(target):
    out = pathlib.Path(target)
    out.mkdir(parents=True, exist_ok=True)
    (out / "IMG_before.jpg").write_bytes(
        jpeg(51, lat=22.58012, lon=88.47118, stamp="2026:10:09 09:53:12", size=(640, 480), noise=True))
    (out / "IMG_after.jpg").write_bytes(
        jpeg(52, lat=22.58020, lon=88.47131, stamp="2026:10:09 09:58:40", size=(640, 480), noise=True))
    (out / "slip.jpg").write_bytes(jpeg(53, stamp=None, size=(400, 300)))
    (out / "bill.pdf").write_bytes(minimal_pdf(1))
    (out / "trace.json").write_bytes(
        trace_json(drive(KOLKATA, DUMP, "2026-10-09T07:00:00+05:30", 25)))
    (out / "notes.txt").write_text("not evidence")
    print(f"fixtures written to {out}")


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "/tmp/trial-fixtures")
