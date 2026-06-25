"""
Catalyst classifier eval.

This measures whether the LLM step is actually doing its job: classifying WHY a
stock is down and, above all, catching genuine value traps. It runs a labeled
set of headlines (catalyst_cases.jsonl) through a classifier and reports how
often it's right.

The headline metric is TRAP RECALL: of all the real traps in the set, how many
did we correctly veto? This is the metric that protects your money, because the
errors are asymmetric:

    Missed trap   (we said "buy", it was a trap)   -> you lose money.   EXPENSIVE.
    False alarm   (we vetoed a fine stock)         -> you miss a gain.  cheap.

So a missed trap is treated as the serious failure, and the eval can fail the
build (exit non-zero) when trap recall drops below a threshold.

Run:
    python evals/eval_catalyst.py                 # mock classifier (free, deterministic baseline)
    python evals/eval_catalyst.py --groq          # the real model (needs GROQ_API_KEY)
    python evals/eval_catalyst.py --min-trap-recall 0.9
"""
import argparse
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import value_screener as vs   # noqa: E402

CASES = Path(__file__).resolve().parent / "catalyst_cases.jsonl"


def load_cases():
    with open(CASES) as fh:
        return [json.loads(line) for line in fh if line.strip()]


def run(classifier, cases):
    rows = []
    for c in cases:
        v = classifier.classify(c["ticker"], c["headlines"])
        rows.append({
            "id": c["id"],
            "exp_cat": c["expected_category"], "got_cat": v.category,
            "exp_veto": c["expected_veto"], "got_veto": v.veto,
            "cat_ok": v.category == c["expected_category"],
            "veto_ok": v.veto == c["expected_veto"],
            "note": c["note"],
        })
    return rows


def report(rows, min_trap_recall):
    n = len(rows)
    cat_acc = sum(r["cat_ok"] for r in rows) / n
    veto_acc = sum(r["veto_ok"] for r in rows) / n

    traps = [r for r in rows if r["exp_veto"]]                 # true value traps
    safe = [r for r in rows if not r["exp_veto"]]              # legitimately buyable
    caught = [r for r in traps if r["got_veto"]]
    missed = [r for r in traps if not r["got_veto"]]           # DANGEROUS errors
    false_alarms = [r for r in safe if r["got_veto"]]          # over-cautious

    trap_recall = len(caught) / len(traps) if traps else 1.0
    false_alarm_rate = len(false_alarms) / len(safe) if safe else 0.0

    print("=" * 64)
    print("CATALYST CLASSIFIER EVAL")
    print("=" * 64)
    print(f"Cases:                  {n}")
    print(f"Category accuracy:      {cat_acc:5.1%}")
    print(f"Veto accuracy:          {veto_acc:5.1%}")
    print("-" * 64)
    print(f"TRAP RECALL (caught):   {trap_recall:5.1%}   ({len(caught)}/{len(traps)} traps vetoed)")
    print(f"False-alarm rate:       {false_alarm_rate:5.1%}   ({len(false_alarms)}/{len(safe)} safe names over-vetoed)")
    print("-" * 64)

    if missed:
        print("MISSED TRAPS (let through as buyable -- the expensive errors):")
        for r in missed:
            print(f"  {r['id']}: expected {r['exp_cat']}, got {r['got_cat']}  <- {r['note']}")
    else:
        print("No missed traps. Every labeled trap was vetoed.")

    if false_alarms:
        print("\nFalse alarms (vetoed a name that was fine -- missed opportunities):")
        for r in false_alarms:
            print(f"  {r['id']}: expected {r['exp_cat']}, got {r['got_cat']}  <- {r['note']}")

    print("=" * 64)
    passed = trap_recall >= min_trap_recall
    print(f"RESULT: {'PASS' if passed else 'FAIL'}  "
          f"(trap recall {trap_recall:.1%} vs threshold {min_trap_recall:.1%})")
    return passed


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--groq", action="store_true",
                    help="evaluate the real Groq model instead of the mock baseline")
    ap.add_argument("--min-trap-recall", type=float, default=0.70,
                    help="fail the build below this trap-recall (default 0.70; raise for the real model)")
    args = ap.parse_args()

    if args.groq:
        if not os.getenv("GROQ_API_KEY"):
            sys.exit("GROQ_API_KEY not set; cannot run the live-model eval.")
        classifier = vs.GroqClassifier()
        print("Evaluating: Groq (live model)\n")
    else:
        classifier = vs.MockClassifier()
        print("Evaluating: mock keyword classifier (free baseline)\n")

    rows = run(classifier, load_cases())
    passed = report(rows, args.min_trap_recall)
    sys.exit(0 if passed else 1)


if __name__ == "__main__":
    main()
