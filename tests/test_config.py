"""
Tests for Phase 6B — Config Manager and Risk Settings.

Tests:
  - ConfigManager load / save / get / reset / validate
  - DEFAULT_CONFIG has all expected keys
  - SLIDER_RANGES has matching keys
  - Config wiring into OrderManager (live reload)
  - Config wiring into ConvictionGate
  - Config wiring into PortfolioGuard
"""

import json
import os
import tempfile
import unittest
from copy import deepcopy
from unittest.mock import MagicMock, patch

from config.config_manager import (
    ConfigManager,
    DEFAULT_CONFIG,
    SLIDER_RANGES,
    LABELS,
    DESCRIPTIONS,
)


class TestConfigManagerBasics(unittest.TestCase):
    """Test ConfigManager read/write/reset operations."""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self.config_path = os.path.join(self.tmpdir, "test_config.json")
        self.cm = ConfigManager(config_path=self.config_path)

    def test_load_returns_defaults_when_no_file(self):
        """If config file doesn't exist, load() returns defaults."""
        cfg = self.cm.load()
        self.assertEqual(cfg["max_positions"], DEFAULT_CONFIG["max_positions"])
        self.assertEqual(cfg["conviction_threshold"], DEFAULT_CONFIG["conviction_threshold"])

    def test_save_and_load_roundtrip(self):
        """Save a config, reload it, verify values match."""
        custom = deepcopy(DEFAULT_CONFIG)
        custom["max_positions"] = 7
        custom["conviction_threshold"] = 80
        self.cm.save(custom)

        loaded = self.cm.load()
        self.assertEqual(loaded["max_positions"], 7)
        self.assertEqual(loaded["conviction_threshold"], 80)

    def test_save_creates_file(self):
        """save() creates the JSON file on disk."""
        self.assertFalse(os.path.exists(self.config_path))
        self.cm.save(DEFAULT_CONFIG)
        self.assertTrue(os.path.exists(self.config_path))

        with open(self.config_path) as f:
            data = json.load(f)
        self.assertEqual(data["max_positions"], 10)

    def test_get_lazy_loads(self):
        """get() loads config lazily if not yet loaded."""
        # Write a custom config first
        with open(self.config_path, "w") as f:
            cfg = deepcopy(DEFAULT_CONFIG)
            cfg["max_positions"] = 12
            json.dump(cfg, f)

        val = self.cm.get("max_positions")
        self.assertEqual(val, 12)

    def test_get_returns_default_for_missing_key(self):
        """get() returns default if key missing from file."""
        self.cm.save({"max_positions": 5})  # partial config
        val = self.cm.get("conviction_threshold")
        self.assertEqual(val, DEFAULT_CONFIG["conviction_threshold"])

    def test_reset_to_defaults(self):
        """reset_to_defaults() overwrites file with factory defaults."""
        custom = deepcopy(DEFAULT_CONFIG)
        custom["max_positions"] = 3
        self.cm.save(custom)

        result = self.cm.reset_to_defaults()
        self.assertEqual(result["max_positions"], DEFAULT_CONFIG["max_positions"])

        # Verify file was overwritten
        loaded = self.cm.load()
        self.assertEqual(loaded["max_positions"], DEFAULT_CONFIG["max_positions"])

    def test_load_merges_new_keys(self):
        """If file is missing a key added in a newer version, load() fills it in."""
        partial = {"max_positions": 8}
        with open(self.config_path, "w") as f:
            json.dump(partial, f)

        loaded = self.cm.load()
        # Should have ALL default keys plus the override
        self.assertEqual(loaded["max_positions"], 8)
        self.assertIn("conviction_threshold", loaded)
        self.assertEqual(loaded["conviction_threshold"], DEFAULT_CONFIG["conviction_threshold"])

    def test_load_handles_corrupt_json(self):
        """If file contains invalid JSON, load() returns defaults."""
        with open(self.config_path, "w") as f:
            f.write("{broken json!!")

        cfg = self.cm.load()
        self.assertEqual(cfg["max_positions"], DEFAULT_CONFIG["max_positions"])


class TestValidation(unittest.TestCase):
    """Test config validation against slider ranges."""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self.cm = ConfigManager(config_path=os.path.join(self.tmpdir, "cfg.json"))

    def test_defaults_are_valid(self):
        """Default config should pass validation."""
        errors = self.cm.validate(DEFAULT_CONFIG)
        self.assertEqual(errors, [])

    def test_out_of_range_caught(self):
        """Values outside slider range produce errors."""
        cfg = deepcopy(DEFAULT_CONFIG)
        cfg["max_positions"] = 99  # max is 15
        cfg["stop_loss_pct"] = 0.5  # min is 3.0
        errors = self.cm.validate(cfg)
        self.assertEqual(len(errors), 2)
        self.assertIn("Max Positions", errors[0])

    def test_valid_custom_passes(self):
        """Custom values within range pass validation."""
        cfg = deepcopy(DEFAULT_CONFIG)
        cfg["max_positions"] = 5
        cfg["conviction_threshold"] = 75
        cfg["stop_loss_pct"] = 10.0
        errors = self.cm.validate(cfg)
        self.assertEqual(errors, [])

    def test_boundary_values_pass(self):
        """Min and max boundary values should pass."""
        cfg = deepcopy(DEFAULT_CONFIG)
        for key, (lo, hi, _step) in SLIDER_RANGES.items():
            cfg[key] = lo  # test min
        errors = self.cm.validate(cfg)
        self.assertEqual(errors, [])

        for key, (lo, hi, _step) in SLIDER_RANGES.items():
            cfg[key] = hi  # test max
        errors = self.cm.validate(cfg)
        self.assertEqual(errors, [])


class TestMetadata(unittest.TestCase):
    """Test that all scalar config keys have labels, descriptions, and ranges."""

    # notification_toggles is a nested dict, not a slider param — skip it
    _SCALAR_KEYS = [k for k in DEFAULT_CONFIG if not isinstance(DEFAULT_CONFIG[k], dict)]

    def test_all_keys_have_labels(self):
        for key in self._SCALAR_KEYS:
            self.assertIn(key, LABELS, f"Missing label for {key}")

    def test_all_keys_have_descriptions(self):
        for key in self._SCALAR_KEYS:
            self.assertIn(key, DESCRIPTIONS, f"Missing description for {key}")

    def test_all_keys_have_slider_ranges(self):
        for key in self._SCALAR_KEYS:
            self.assertIn(key, SLIDER_RANGES, f"Missing slider range for {key}")

    def test_slider_ranges_are_tuples_of_3(self):
        for key, val in SLIDER_RANGES.items():
            self.assertEqual(len(val), 3, f"{key} range should be (min, max, step)")


class TestConfigWiringOrderManager(unittest.TestCase):
    """Test that OrderManager reads live config via _apply_live_config()."""

    def test_apply_live_config_updates_max_position_pct(self):
        from execution.order_manager import OrderManager
        from execution.broker_interface import BrokerInterface

        broker = MagicMock(spec=BrokerInterface)
        db = MagicMock()

        # Create a ConfigManager with custom config
        tmpdir = tempfile.mkdtemp()
        cm = ConfigManager(config_path=os.path.join(tmpdir, "cfg.json"))
        custom = deepcopy(DEFAULT_CONFIG)
        custom["risk_per_trade_pct"] = 8.0  # 8% instead of 5%
        cm.save(custom)

        om = OrderManager(broker=broker, db=db, config_manager=cm)
        om._apply_live_config()

        self.assertAlmostEqual(om.max_position_pct, 0.08)

    def test_apply_live_config_updates_conviction_gate(self):
        from execution.order_manager import OrderManager
        from execution.broker_interface import BrokerInterface
        from portfolio.conviction_gate import ConvictionGate

        broker = MagicMock(spec=BrokerInterface)
        db = MagicMock()
        gate = ConvictionGate(db=None)

        tmpdir = tempfile.mkdtemp()
        cm = ConfigManager(config_path=os.path.join(tmpdir, "cfg.json"))
        custom = deepcopy(DEFAULT_CONFIG)
        custom["conviction_threshold"] = 80
        custom["min_agents_agree"] = 4
        custom["cooldown_hours"] = 48
        cm.save(custom)

        om = OrderManager(broker=broker, db=db, conviction_gate=gate, config_manager=cm)
        om._apply_live_config()

        self.assertEqual(gate.buy_threshold, 80.0)
        self.assertEqual(gate.min_agents_agree, 4)
        self.assertEqual(gate.cooldown_hours, 48)

    def test_apply_live_config_updates_portfolio_guard(self):
        from execution.order_manager import OrderManager
        from execution.broker_interface import BrokerInterface
        from portfolio.portfolio_guard import PortfolioGuard

        broker = MagicMock(spec=BrokerInterface)
        db_mock = MagicMock()

        guard = PortfolioGuard(db=db_mock, sector_map={})

        tmpdir = tempfile.mkdtemp()
        cm = ConfigManager(config_path=os.path.join(tmpdir, "cfg.json"))
        custom = deepcopy(DEFAULT_CONFIG)
        custom["max_positions"] = 5
        custom["max_sector_exposure_pct"] = 20.0
        custom["daily_loss_limit_pct"] = 5.0
        custom["cash_reserve_pct"] = 15.0
        cm.save(custom)

        om = OrderManager(broker=broker, db=db_mock, portfolio_guard=guard, config_manager=cm)
        om._apply_live_config()

        self.assertEqual(guard.max_positions, 5)
        self.assertAlmostEqual(guard.max_sector_exposure, 0.20)
        self.assertAlmostEqual(guard.daily_loss_limit, -0.05)
        self.assertAlmostEqual(guard.cash_reserve, 0.15)

    def test_no_config_manager_is_noop(self):
        """If no ConfigManager is passed, _apply_live_config() does nothing."""
        from execution.order_manager import OrderManager
        from execution.broker_interface import BrokerInterface

        broker = MagicMock(spec=BrokerInterface)
        db = MagicMock()

        om = OrderManager(broker=broker, db=db, max_position_pct=0.05)
        om._apply_live_config()  # Should not raise

        self.assertAlmostEqual(om.max_position_pct, 0.05)

    def test_process_decision_calls_apply_config(self):
        """process_decision() hot-reloads config before processing."""
        from execution.order_manager import OrderManager
        from execution.broker_interface import BrokerInterface

        broker = MagicMock(spec=BrokerInterface)
        db_mock = MagicMock()
        db_mock.insert_signal = MagicMock()

        tmpdir = tempfile.mkdtemp()
        cm = ConfigManager(config_path=os.path.join(tmpdir, "cfg.json"))
        custom = deepcopy(DEFAULT_CONFIG)
        custom["risk_per_trade_pct"] = 9.0
        cm.save(custom)

        om = OrderManager(broker=broker, db=db_mock, config_manager=cm)
        # Process a HOLD to trigger _apply_live_config
        om.process_decision("AAPL", "2025-01-01", "HOLD")

        # Verify config was applied
        self.assertAlmostEqual(om.max_position_pct, 0.09)


if __name__ == "__main__":
    unittest.main()
