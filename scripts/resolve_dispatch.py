"""Record a human-verified dispatch outcome while the backend is stopped."""

import argparse
import fcntl
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

from dreamer.config import Settings
from dreamer.store import Store


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("job_id")
    parser.add_argument("--outcome", choices=["submitted", "not-sent"], required=True)
    args = parser.parse_args()
    settings = Settings()
    store = Store(settings.data_dir)
    with (store.root / "worker.lock").open("w") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            parser.error("Stop the backend before resolving a dispatch")
        store.transition(
            args.job_id,
            {"send_unknown"},
            "submitted" if args.outcome == "submitted" else "sliced",
            f"Operator verified dispatch outcome: {args.outcome}",
            error=None,
        )
    print("Resolution recorded. No printer command was sent.")


if __name__ == "__main__":
    main()
