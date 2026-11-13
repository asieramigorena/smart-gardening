"""
test_garden.py
Domain model: soils, plants, plantings, probes, zones and the config loader.

    .venv/bin/python -m unittest discover -s ML/tests

Written by Diego Revilla on 12-11-2026
Copyright University of Deusto
"""

from __future__ import annotations

import sys
import tempfile
import unittest
from datetime import date
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from garden import (PLANTS, SOILS, CatalogueError, ConfigError, Garden, GeoPoint, IrrigationEvent,  # noqa: E402
                    IrrigationLog, IrrigationMethod, IrrigationSystem, Planting, ProbeArray, Soil, SoilProbe,
                    ValidationError, Zone, ZoneRole)


def make_zone(**overrides) -> Zone:
    spec = dict(key="bed", name="Bed", role=ZoneRole.DEPLOYMENT, area_m2=2.0,
                location=GeoPoint(latitude=43.26, longitude=-2.93), timezone="Europe/Madrid",
                soil=SOILS["loam"], planting=Planting(plant=PLANTS["tomato"], sown_on=date(2026, 4, 1)),
                probes=ProbeArray([SoilProbe(depth_cm=10, channel="a"), SoilProbe(depth_cm=30, channel="b")]),
                irrigation=IrrigationSystem(method=IrrigationMethod.DRIP, flow_l_h=20.0))
    spec.update(overrides)
    return Zone(**spec)


class SoilTest(unittest.TestCase):
    def test_rejects_impossible_limits(self):
        with self.assertRaises(ValidationError):
            Soil(key="x", name="x", theta_sat=0.4, theta_fc=0.1, theta_wp=0.2, ksat_mm_h=1, intake_rate_mm_h=1)

    def test_water_holding(self):
        loam = SOILS["loam"]
        self.assertAlmostEqual(loam.taw_mm(0.5), 1000 * (0.25 - 0.12) * 0.5)
        self.assertAlmostEqual(loam.theta_refill(0.4), 0.25 - 0.4 * 0.13)
        self.assertAlmostEqual(float(loam.relative_depletion(loam.theta_wp)), 1.0)

    def test_unknown_key_lists_the_catalogue(self):
        with self.assertRaises(CatalogueError) as ctx:
            SOILS["peat"]
        self.assertIn("sandy_loam", str(ctx.exception))


class PlantTest(unittest.TestCase):
    def setUp(self):
        self.tomato = PLANTS["tomato"]  # stages 30/40/40/25, Kc 0.60/1.15/0.80

    def test_kc_curve_follows_the_four_stages(self):
        kc = self.tomato.crop_coefficient(np.array([0, 30, 50, 70, 110, 135, 400]))
        np.testing.assert_allclose(kc, [0.60, 0.60, 0.875, 1.15, 1.15, 0.80, 0.80])

    def test_roots_grow_until_mid_season(self):
        np.testing.assert_allclose(self.tomato.root_depth_m([0, 35, 70, 200]), [0.15, 0.575, 1.0, 1.0])

    def test_stage_labels(self):
        self.assertEqual(list(self.tomato.stage([0, 30, 75, 120, 140])),
                         ["initial", "development", "mid", "late", "finished"])

    def test_depletion_fraction_adapts_to_demand(self):
        np.testing.assert_allclose(self.tomato.adjusted_depletion_fraction([5.0, 10.0, 0.0]), [0.40, 0.20, 0.60])

    def test_perennial_restarts_every_season(self):
        planting = Planting(plant=PLANTS["turfgrass_cool"], season_start=(3, 1))
        times = pd.DatetimeIndex(["2026-03-01 00:00", "2026-02-28 00:00", "2026-03-11 12:00"], tz="UTC")
        np.testing.assert_allclose(planting.days_since_planting(times), [0, 364, 10.5])

    def test_annual_needs_a_sowing_date(self):
        with self.assertRaises(ValidationError):
            Planting(plant=PLANTS["tomato"])


class ProbeArrayTest(unittest.TestCase):
    def setUp(self):
        self.probes = ProbeArray([SoilProbe(depth_cm=d, channel=f"p{d}") for d in (5, 10, 20, 50, 100)])

    def test_layer_weights(self):
        # layers: 0-7.5, 7.5-15, 15-35, 35-75, 75-∞ cm; a 50 cm root zone
        np.testing.assert_allclose(self.probes.layer_weights(0.5)[0], [0.15, 0.15, 0.40, 0.30, 0.0])
        self.assertAlmostEqual(self.probes.layer_weights([0.3, 1.2]).sum(axis=1).tolist()[1], 1.0)

    def test_missing_probe_weight_is_redistributed(self):
        readings = pd.DataFrame({"p5": [0.1, 0.1], "p10": [0.2, np.nan], "p20": [0.3, 0.3],
                                 "p50": [0.4, 0.4], "p100": [0.9, 0.9]})
        theta = self.probes.root_zone_theta(readings, 0.5)
        self.assertAlmostEqual(theta[0], 0.15 * 0.1 + 0.15 * 0.2 + 0.4 * 0.3 + 0.3 * 0.4)
        self.assertAlmostEqual(theta[1], (0.15 * 0.1 + 0.4 * 0.3 + 0.3 * 0.4) / 0.85)

    def test_too_little_coverage_gives_nan(self):
        readings = pd.DataFrame({c: [np.nan] for c in self.probes.channels} | {"p5": [0.2]})
        self.assertTrue(np.isnan(self.probes.root_zone_theta(readings, 0.5)[0]))


class ZoneTest(unittest.TestCase):
    def test_valve_time_matches_the_readme_formula(self):
        zone = make_zone()
        # λ·1000·(θ_FC − θ)·Z_r·A / (E_a·Q) = 1·1000·0.05·0.4·2 / (0.9·20) h
        expected_h = 1000 * 0.05 * 0.4 * 2.0 / (0.9 * 20.0)
        self.assertAlmostEqual(float(zone.valve_open_seconds(0.20, 0.4)), expected_h * 3600)
        self.assertEqual(float(zone.valve_open_seconds(0.30, 0.4)), 0.0)

    def test_bed_depth_caps_the_root_zone(self):
        times = pd.DatetimeIndex(["2026-07-01"], tz="UTC")  # tomato at full rooting depth (1.0 m)
        self.assertAlmostEqual(float(make_zone().root_depth_m(times)[0]), 1.0)
        self.assertAlmostEqual(float(make_zone(soil_depth_m=0.4).root_depth_m(times)[0]), 0.4)

    def test_deployment_zone_needs_a_valve(self):
        with self.assertRaises(ValidationError):
            make_zone(irrigation=None)

    def test_pulsing_when_emitters_outrun_the_soil(self):
        self.assertFalse(make_zone().needs_pulsing())  # 10 mm/h on loam (12 mm/h)
        fast = IrrigationSystem(method=IrrigationMethod.SPRINKLER, flow_l_h=60.0)
        self.assertTrue(make_zone(irrigation=fast).needs_pulsing())

    def test_irrigation_log_splits_events_across_hours(self):
        index = pd.date_range("2026-06-01 06:00", periods=4, freq="h", tz="UTC")
        log = IrrigationLog([IrrigationEvent(pd.Timestamp("2026-06-01 06:45", tz="UTC"), 1800)])
        np.testing.assert_allclose(log.seconds_per_hour(index).to_numpy(), [0, 900, 900, 0])


class ConfigTest(unittest.TestCase):
    def test_project_config_loads(self):
        garden = Garden.load()
        self.assertIn("darrington", garden.with_data())
        self.assertIs(garden["bilbao_tomatoes"].plant, PLANTS["tomato"])

    def test_errors_name_the_zone(self):
        with tempfile.NamedTemporaryFile("w", suffix=".toml", delete=False) as f:
            f.write('schema_version = 1\n[zones.bad]\narea_m2 = 1\ntimezone = "UTC"\n'
                    'location = { lat = 0, lon = 0 }\nsoil = { texture = "moon_dust" }\n'
                    'planting = { plant = "tomato", sown_on = "2026-01-01" }\n'
                    'probes = [{ depth_cm = 10, channel = "a" }]\n')
        with self.assertRaises(ConfigError) as ctx:
            Garden.load(f.name)
        self.assertIn("zones.bad", str(ctx.exception))
        self.assertIn("moon_dust", str(ctx.exception))


if __name__ == "__main__":
    unittest.main()
