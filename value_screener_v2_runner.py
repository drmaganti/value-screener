"""Production runner for V2 with reporting-integrity safeguards."""
from __future__ import annotations

import os
from datetime import date

import email_report_v2
import reporting_integrity as integrity
import value_screener as core
import value_screener_v2 as v2


def _official_run():
    return os.getenv("OFFICIAL_RUN", "0").strip().lower() in {"1", "true", "yes"}


def _mark_new_records(records, start_index):
    for record in records[start_index:]:
        record["official"] = True
        record["run_type"] = "scheduled"


def main():
    today = date.today()
    official = _official_run()
    print(
        f"Weekly screen [provider={core.PROVIDER} classifier={core.CLASSIFIER} "
        f"strategy={v2.STRATEGY_VERSION} official={official}] {today}"
    )
    provider = core.build_provider()

    log = core.load_log()
    integrity.snapshot_legacy_outcomes(log)
    repaired = v2.update_outcomes_v2(provider, log, today)
    corrected = integrity.backfill_legacy_exact_horizons(provider, log, today)
    print(f"  updated/repaired {repaired} standard outcome slots")
    print(f"  backfilled {corrected} corrected legacy outcome slots")

    candidate_log = v2.load_candidate_log()
    candidate_filled = v2.update_candidate_outcomes(provider, candidate_log, today)
    print(f"  updated/repaired {candidate_filled} candidate outcome slots")

    universe = core.load_universe()
    all_tickers = [t for group in universe.values() for t in group]
    fund_cache = core.load_fund_cache()
    refreshed = core.refresh_fundamentals_cache(
        provider, fund_cache, all_tickers, today
    )
    sector_medians = core.compute_sector_medians(fund_cache)
    print(
        f"  refreshed {refreshed} fundamentals; sector medians for "
        f"{len(sector_medians)} sectors ({len(fund_cache)} names cached)"
    )

    exclude = core.open_tickers(log, today)
    core._analysis_inputs = v2.analysis_inputs_v2
    qualifiers, featured, rejected, all_candidates = v2.run_screen_v2(
        provider,
        core.build_classifier(),
        core.build_analyst(),
        exclude,
        sector_medians=sector_medians,
        fund_cache=fund_cache,
        today=today,
    )

    if official:
        pick_start = len(log.get("picks", []))
        candidate_start = len(candidate_log.get("candidates", []))
        v2.record_picks_v2(log, qualifiers, provider, today)
        v2.record_candidate_observations(
            provider, candidate_log, all_candidates, qualifiers, today
        )
        _mark_new_records(log["picks"], pick_start)
        _mark_new_records(candidate_log["candidates"], candidate_start)

        integrity.save_json_strict(core._p(core.PICKS_LOG), log)
        integrity.save_json_strict(core._p(v2.CANDIDATES_LOG), candidate_log)
        core.save_fund_cache(fund_cache)
    else:
        print("  manual/test run: official pick, candidate, and cache logs are not persisted")

    stats = integrity.performance_by_strategy(log)
    html, text = email_report_v2.build(
        today, featured, qualifiers, log.get("picks", []), stats
    )
    core.send_email(
        html,
        text,
        f"Weekly Value Screen V2 - {today.strftime('%b %d, %Y')}",
    )
    print(
        f"Done. {len(qualifiers)} qualifiers ({len(featured)} featured), "
        f"{len(rejected)} hard-screened out, {len(all_candidates)} candidates evaluated."
    )


if __name__ == "__main__":
    main()
