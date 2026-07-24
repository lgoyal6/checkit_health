"""Run retrieval evaluation against the checked-in benchmark."""

import argparse
import json
from pathlib import Path

from evaluation import retrieval_metrics
from evidence import retrieve_evidence


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dataset", default="data/evaluation_claims.json")
    parser.add_argument("--limit", type=int, default=8)
    args = parser.parse_args()
    cases = json.loads(Path(args.dataset).read_text(encoding="utf-8"))
    results = [
        retrieve_evidence(case["claim"], limit=args.limit)["evidence"]
        for case in cases
    ]
    print(json.dumps(retrieval_metrics(cases, results), indent=2))


if __name__ == "__main__":
    main()
