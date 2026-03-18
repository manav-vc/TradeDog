"""
Config Manager for TradeDog.

Reads/writes personal runtime parameters from config/user_config.json.
All risk settings are managed here so the engine, dashboard, and guards
can share a single source of truth — editable from the UI without code.

No tradingagents/ modifications.
"""

import json
import logging
import os
from copy import deepcopy
from threading import Lock
from typing import Any, Optional

logger = logging.getLogger(__name__)

_CONFIG_PATH = os.path.join(os.path.dirname(__file__), "user_config.json")

# ── Default values (used for "Reset to defaults") ──────────────────
DEFAULT_CONFIG: dict[str, Any] = {
    "capital_deployed_pct": 100,        # % of account balance to use (1-100)
    "max_positions": 10,                # max open positions (3-15)
    "risk_per_trade_pct": 5.0,          # max % of portfolio per position (1-10)
    "conviction_threshold": 65,         # min score to trigger buy (50-90)
    "daily_loss_limit_pct": 3.0,        # halt buys if down X% (1-10)
    "stop_loss_pct": 8.0,              # exit if down X% from entry (3-15)
    "profit_target_pct": 15.0,          # exit if up X% from entry (5-30)
    "trailing_stop_pct": 7.0,           # exit if X% drop from peak (3-15)
    "max_hold_days": 30,                # max days to hold (7-90)
    "max_sector_exposure_pct": 30.0,    # max % in one sector (10-50)
    "cash_reserve_pct": 10.0,           # always keep X% in cash (5-30)
    "min_agents_agree": 3,              # min agents for BUY (2-4)
    "cooldown_hours": 24,               # hours between same-ticker trades (0-72)
    "notification_toggles": {           # which Telegram alerts to send
        "trade_opened": True,
        "trade_closed": True,
        "guard_blocked": True,
        "daily_loss": True,
        "engine_error": True,
        "weekly_digest": True,
    },
}

# ── Slider ranges for the dashboard UI ──────────────────────────────
SLIDER_RANGES: dict[str, tuple] = {
    "capital_deployed_pct": (10, 100, 5),       # min, max, step
    "max_positions":        (3, 15, 1),
    "risk_per_trade_pct":   (1.0, 10.0, 0.5),
    "conviction_threshold": (50, 90, 1),
    "daily_loss_limit_pct": (1.0, 10.0, 0.5),
    "stop_loss_pct":        (3.0, 15.0, 0.5),
    "profit_target_pct":    (5.0, 30.0, 1.0),
    "trailing_stop_pct":    (3.0, 15.0, 0.5),
    "max_hold_days":        (7, 90, 1),
    "max_sector_exposure_pct": (10.0, 50.0, 5.0),
    "cash_reserve_pct":     (5.0, 30.0, 1.0),
    "min_agents_agree":     (2, 4, 1),
    "cooldown_hours":       (0, 72, 4),
}

# ── Human-readable labels ───────────────────────────────────────────
LABELS: dict[str, str] = {
    "capital_deployed_pct": "Capital Deployed (%)",
    "max_positions":        "Max Positions",
    "risk_per_trade_pct":   "Risk per Trade (%)",
    "conviction_threshold": "Conviction Threshold",
    "daily_loss_limit_pct": "Daily Loss Limit (%)",
    "stop_loss_pct":        "Stop Loss (%)",
    "profit_target_pct":    "Profit Target (%)",
    "trailing_stop_pct":    "Trailing Stop (%)",
    "max_hold_days":        "Max Hold Days",
    "max_sector_exposure_pct": "Max Sector Exposure (%)",
    "cash_reserve_pct":     "Cash Reserve (%)",
    "min_agents_agree":     "Min Agents Agree (of 4)",
    "cooldown_hours":       "Cooldown (hours)",
}

DESCRIPTIONS: dict[str, str] = {
    "capital_deployed_pct": "Percentage of your account balance the engine is allowed to use.",
    "max_positions":        "Maximum number of stocks to hold at once.",
    "risk_per_trade_pct":   "Maximum percentage of your portfolio per single position.",
    "conviction_threshold": "Minimum conviction score (0-100) to trigger a buy.",
    "daily_loss_limit_pct": "Halt all buys if portfolio drops this much in one day.",
    "stop_loss_pct":        "Hard floor — exit if loss exceeds this % from entry.",
    "profit_target_pct":    "Take profits — exit if gain exceeds this % from entry.",
    "trailing_stop_pct":    "Protect gains — exit if price drops this % from peak.",
    "max_hold_days":        "Maximum days to hold any position before time-based exit.",
    "max_sector_exposure_pct": "No single sector can exceed this % of total portfolio.",
    "cash_reserve_pct":     "Always keep this % of portfolio in cash.",
    "min_agents_agree":     "Minimum number of agents (out of 4) that must agree BUY.",
    "cooldown_hours":       "Hours to wait before buying the same ticker again.",
}


class ConfigManager:
    """Thread-safe read/write for user_config.json."""

    def __init__(self, config_path: Optional[str] = None):
        self.config_path = config_path or _CONFIG_PATH
        self._lock = Lock()
        self._cache: Optional[dict] = None

    def load(self) -> dict[str, Any]:
        """Load config from disk. Returns defaults if file is missing/corrupt."""
        with self._lock:
            try:
                with open(self.config_path, "r") as f:
                    data = json.load(f)
                # Merge with defaults so new keys are always present
                merged = deepcopy(DEFAULT_CONFIG)
                merged.update(data)
                self._cache = merged
                return deepcopy(merged)
            except (FileNotFoundError, json.JSONDecodeError) as e:
                logger.warning(f"Config load failed ({e}), using defaults")
                self._cache = deepcopy(DEFAULT_CONFIG)
                return deepcopy(DEFAULT_CONFIG)

    def save(self, config: dict[str, Any]) -> None:
        """Write config to disk."""
        with self._lock:
            with open(self.config_path, "w") as f:
                json.dump(config, f, indent=2)
                f.write("\n")
            self._cache = deepcopy(config)
            logger.info("Config saved to %s", self.config_path)

    def get(self, key: str, default: Any = None) -> Any:
        """Get a single config value (lazy-loads if needed)."""
        if self._cache is None:
            self.load()
        return self._cache.get(key, DEFAULT_CONFIG.get(key, default))

    def reset_to_defaults(self) -> dict[str, Any]:
        """Reset config to factory defaults and save."""
        defaults = deepcopy(DEFAULT_CONFIG)
        self.save(defaults)
        logger.info("Config reset to defaults")
        return defaults

    def validate(self, config: dict[str, Any]) -> list[str]:
        """
        Validate config values against SLIDER_RANGES.
        Returns a list of error messages (empty = valid).
        """
        errors = []
        for key, (lo, hi, _step) in SLIDER_RANGES.items():
            val = config.get(key)
            if val is None:
                continue
            if not (lo <= val <= hi):
                errors.append(
                    f"{LABELS.get(key, key)}: {val} is out of range [{lo}, {hi}]"
                )
        return errors
