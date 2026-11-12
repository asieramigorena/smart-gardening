"""
build_training_set.py
Builds the soil-moisture forest's training set for one configured zone.

    python ML/src/build_training_set.py                    # darrington
    python ML/src/build_training_set.py --zone darrington --lag 6
    python ML/src/build_training_set.py --list

Writes data/training/<zone>/dataset.parquet and manifest.json.

Written by Diego Revilla on 12-11-2026
Copyright University of Deusto
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from garden import DEFAULT_CONFIG, Garden, GardenError
from garden.config import REPO_ROOT
from training import BuildSettings, TrainingSetBuilder


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    parser.add_argument("--zone", default="darrington")
    parser.add_argument("--lag", type=int, default=0, help="hours between a forecast run starting and being usable")
    parser.add_argument("--out", type=Path, default=REPO_ROOT / "data" / "training")
    parser.add_argument("--list", action="store_true", help="list the configured zones and exit")
    args = parser.parse_args()

    try:
        garden = Garden.load(args.config)
        if args.list:
            for key, zone in garden.items():
                data = "data" if key in garden.with_data() else "no data"
                print(f"{key:20s} {zone.role.value:10s} {zone.plant.name:20s} {zone.soil.name:30s} {data}")
            return 0
        builder = TrainingSetBuilder(garden[args.zone], garden.binding(args.zone),
                                     BuildSettings(availability_lag_h=args.lag))
        training_set = builder.build()
    except GardenError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    data_path, manifest_path = training_set.save(args.out / args.zone)
    m = training_set.manifest
    print(training_set)
    print(f"  {data_path.relative_to(REPO_ROOT)}\n  {manifest_path.relative_to(REPO_ROOT)}")
    print(f"  period {m['period'][0]} to {m['period'][1]}; {m['rows']['usable']:,} usable of "
          f"{m['rows']['hours_in_table']:,} hours")
    if m["soil_calibration"]:
        c = m["soil_calibration"]
        print(f"  soil calibrated from {c['events']} drainage events: θ_FC {c['theta_fc']:.3f}, θ_WP {c['theta_wp']:.3f}")
    sparse = {k: v for k, v in m["feature_coverage"].items() if v < 0.95}
    if sparse:
        print("  features present in under 95 % of rows: " + ", ".join(f"{k} {v:.0%}" for k, v in sparse.items()))
    return 0


if __name__ == "__main__":
    sys.exit(main())
