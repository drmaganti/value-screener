"""One-time/idempotent historical outcome correction for the official pick log."""
from datetime import date

import reporting_integrity as integrity
import value_screener as core


def main():
    provider = core.build_provider()
    log = core.load_log()
    snap = integrity.snapshot_legacy_outcomes(log)
    filled = integrity.backfill_legacy_exact_horizons(provider, log, date.today())
    integrity.save_json_strict(core._p(core.PICKS_LOG), log)
    print(f"Historical audit snapshots/repairs: {snap}; corrected outcome slots: {filled}")


if __name__ == "__main__":
    main()
