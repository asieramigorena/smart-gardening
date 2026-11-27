"""
evaluate_baselines.py
Scores the reference forecasters (persistence, FAO-56 bucket, linear
quantile regression) on a built training set: pinball loss and coverage of
the 10th percentile and median, and MAE of the median, per horizon.

    python ML/src/evaluate_baselines.py                       # darrington, validation split
    python ML/src/evaluate_baselines.py --split test --stride 1

Keep the test split for the final comparison against the forest; tune on
validation. Writes data/training/<zone>/baselines_<split>.csv.

Written by Diego Revilla on 27-11-2026
Copyright University of Deusto
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import pandas as pd

from garden import GardenError
from garden.config import REPO_ROOT
from training import Bucket, LinearQuantile, Persistence, TrainingSet, evaluate


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--zone", default="darrington")
    parser.add_argument("--split", default="validation", choices=["train", "validation", "test"])
    parser.add_argument("--stride", type=int, default=3, help="fit the linear model on every N-th train row")
    parser.add_argument("--data", type=Path, default=REPO_ROOT / "data" / "training")
    args = parser.parse_args()

    directory = args.data / args.zone
    try:
        training_set = TrainingSet.load(directory)
        models = [Persistence(), Bucket(), LinearQuantile(stride=args.stride)]
        for model in models:
            print(f"fitting {model!r}", flush=True)
            model.fit(training_set)
        scores = evaluate(models, training_set, args.split)
    except FileNotFoundError:
        print(f"error: no training set in {directory}; run build_training_set.py first", file=sys.stderr)
        return 1
    except GardenError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    with pd.option_context("display.float_format", "{:.4f}".format, "display.width", 160,
                           "display.max_columns", None):
        print(f"\n{args.zone}, {args.split} split\n{scores}")
    out = directory / f"baselines_{args.split}.csv"
    scores.to_csv(out)
    print(f"\n  {out.relative_to(REPO_ROOT)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
