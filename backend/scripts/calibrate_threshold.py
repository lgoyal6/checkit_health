"""Calibrate the narrative clustering cosine threshold against labeled pairs.

The threshold that decides whether two claims are the same rumor is a property
of the embedding, not a universal constant: the value that works for a managed
sentence embedding is wrong for the offline hashed fallback and vice versa.
Guessing it is how a clustering feature ends up silently doing nothing.

Run this whenever the embedding model or its dimensionality changes:

    cd backend
    PYTHONPATH=app python scripts/calibrate_threshold.py
    PYTHONPATH=app python scripts/calibrate_threshold.py --write

``--write`` prints the environment variable to set. The result is reported per
embedding mode, and the mode is named in the output so a number is never
carried across a model change by accident.
"""

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "app"))

import narratives as nar  # noqa: E402

PAIRS_PATH = Path(__file__).resolve().parents[1] / "data" / "claim_pairs.json"


def load_pairs(path: Path):
    payload = json.loads(path.read_text(encoding="utf-8"))
    return payload["pairs"]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--pairs", default=str(PAIRS_PATH))
    parser.add_argument("--start", type=float, default=0.20)
    parser.add_argument("--stop", type=float, default=0.95)
    parser.add_argument("--step", type=float, default=0.01)
    parser.add_argument("--write", action="store_true",
                        help="Print the env var line to add to your deployment")
    args = parser.parse_args()

    labeled = load_pairs(Path(args.pairs))
    texts = [p["a"] for p in labeled] + [p["b"] for p in labeled]
    vectors = nar.embed(texts)
    half = len(labeled)
    triples = [
        (vectors[i], vectors[half + i], bool(labeled[i]["same"]))
        for i in range(half)
    ]

    mode = nar.embedding_mode()
    positives = [nar.cosine(a, b) for a, b, same in triples if same]
    negatives = [nar.cosine(a, b) for a, b, same in triples if not same]

    print(f"embedding mode : {mode}")
    print(f"dimensions     : {len(vectors[0]) if vectors else 0}")
    print(f"labeled pairs  : {half} ({len(positives)} same, {len(negatives)} different)")
    print()
    print(f"same-rumor     min={min(positives):.3f} "
          f"median={sorted(positives)[len(positives)//2]:.3f} max={max(positives):.3f}")
    print(f"different      min={min(negatives):.3f} "
          f"median={sorted(negatives)[len(negatives)//2]:.3f} max={max(negatives):.3f}")
    print()

    sweep = nar.sweep_threshold(triples, args.start, args.stop, args.step)
    best = sweep["best"]
    sep = sweep["separation"]

    print(f"separation AUC : {sep['auc']}  ({sep['verdict']})")
    print(f"overlap range  : {sep['overlap_range'][0]:.3f} to "
          f"{sep['overlap_range'][1]:.3f}")
    if sep["auc"] is not None and sep["auc"] < 0.75:
        print()
        print("  WARNING: this embedding cannot separate same-rumor pairs from")
        print("  different-rumor pairs. No threshold fixes that. Configure")
        print("  GOOGLE_API_KEY so managed embeddings are used, then re-run.")
    print()

    print("threshold  precision  recall     f1      f0.5")
    for row in sweep["results"]:
        marker = "  <- best" if row["threshold"] == best["threshold"] else ""
        if row["f1"] > 0 and round(row["threshold"] * 100) % 5 == 0:
            print(f"  {row['threshold']:.2f}      {row['precision']:.3f}      "
                  f"{row['recall']:.3f}   {row['f1']:.3f}   {row['fbeta']:.3f}{marker}")
    if round(best["threshold"] * 100) % 5 != 0:
        print(f"  {best['threshold']:.2f}      {best['precision']:.3f}      "
              f"{best['recall']:.3f}   {best['f1']:.3f}   {best['fbeta']:.3f}  <- best")

    print()
    print(f"BEST threshold={best['threshold']:.2f}  f0.5={best['fbeta']:.3f}  "
          f"precision={best['precision']:.3f}  recall={best['recall']:.3f}")
    print("  (selected on F0.5: a wrong merge corrupts a narrative, a missed")
    print("   merge only duplicates a row, so precision is weighted higher)")
    print(f"  merges correctly: {best['true_positives']}/{len(positives)}")
    print(f"  wrong merges    : {best['false_positives']}")
    print(f"  missed merges   : {best['false_negatives']}")

    if args.write:
        var = (
            "NARRATIVE_THRESHOLD_MANAGED" if mode == "managed"
            else "NARRATIVE_THRESHOLD_HASHED"
        )
        print()
        print(f"export {var}={best['threshold']:.2f}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
