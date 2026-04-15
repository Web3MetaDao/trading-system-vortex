from __future__ import annotations

import argparse
import json
import time
import traceback
from datetime import datetime
from pathlib import Path

from main import run_cycle

ROOT = Path(__file__).resolve().parents[1]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run trading-system on a fixed interval")
    parser.add_argument("--interval-seconds", type=int, default=900, help="Seconds between cycles")
    parser.add_argument("--max-cycles", type=int, default=0, help="0 means run forever")
    parser.add_argument(
        "--error-backoff-seconds",
        type=int,
        default=30,
        help="Sleep time after an unexpected cycle error",
    )
    parser.add_argument(
        "--alert-threshold",
        type=int,
        default=3,
        help="Consecutive error threshold for alert marker",
    )
    return parser.parse_args()


def _write_runner_alert(consecutive_errors: int, last_error: str) -> None:
    path = ROOT / "logs" / "runner_alert.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "ts": datetime.now().isoformat(),
        "consecutive_errors": consecutive_errors,
        "last_error": last_error,
    }
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def main() -> None:
    args = parse_args()
    cycle = 0
    consecutive_errors = 0
    while True:
        cycle += 1
        print(f"\n===== trading cycle {cycle} @ {datetime.now().isoformat()} =====")
        try:
            run_cycle()
            consecutive_errors = 0
            if args.max_cycles and cycle >= args.max_cycles:
                print("Reached max cycles, stopping runner.")
                break
            print(f"Sleeping {args.interval_seconds} seconds...")
            time.sleep(args.interval_seconds)
        except KeyboardInterrupt:
            print("Runner interrupted by user, stopping.")
            break
        except Exception as exc:
            consecutive_errors += 1
            print(f"[runner] cycle failed: {exc}")
            traceback.print_exc()
            if consecutive_errors >= args.alert_threshold:
                _write_runner_alert(consecutive_errors, str(exc))
            if args.max_cycles and cycle >= args.max_cycles:
                print("Reached max cycles after error, stopping runner.")
                break
            backoff = max(
                args.error_backoff_seconds,
                min(args.interval_seconds, args.error_backoff_seconds * consecutive_errors),
            )
            print(
                f"[runner] sleeping {backoff} seconds before retry (consecutive_errors={consecutive_errors})..."
            )
            time.sleep(backoff)


if __name__ == "__main__":
    main()
