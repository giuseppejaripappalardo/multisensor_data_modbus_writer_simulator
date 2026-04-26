"""
Configuration loading from Python modules or YAML files.

Supports:
  - Python config: file exports a `config` variable of type AppConfig
  - YAML config: parsed and converted to AppConfig
  - Environment variable and CLI overrides on top of both
"""
import importlib.util
import os
import sys
from pathlib import Path
from typing import Optional

import yaml

from models import AppConfig
from utils.logging import get_logger

logger = get_logger(__name__)


def load_python_config(path: str) -> AppConfig:
    """
    Load an AppConfig from a Python module.

    The module must define a `config` variable of type AppConfig.
    """
    p = Path(path).resolve()
    if not p.exists():
        raise FileNotFoundError(f"Config file not found: {path}")

    # Add parent dir to sys.path so the module can import project packages
    parent = str(p.parent)
    if parent not in sys.path:
        sys.path.insert(0, parent)

    spec = importlib.util.spec_from_file_location("_sensor_config", str(p))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    if not hasattr(module, "config"):
        raise AttributeError(
            f"Config file '{path}' must define a 'config' variable of type AppConfig"
        )

    config = module.config
    if not isinstance(config, AppConfig):
        raise TypeError(
            f"'config' in '{path}' must be an AppConfig instance, got {type(config).__name__}"
        )

    return config


def load_yaml_config(path: str) -> AppConfig:
    """Load an AppConfig from a YAML file."""
    p = Path(path)
    if not p.exists():
        raise FileNotFoundError(f"Config file not found: {path}")

    with open(p, "r", encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}

    return AppConfig(**data)


def apply_overrides(
    config: AppConfig,
    host: Optional[str] = None,
    port: Optional[int] = None,
    unit_id: Optional[int] = None,
    tick: Optional[float] = None,
    log_level: Optional[str] = None,
) -> AppConfig:
    """Apply env var and CLI overrides, returning a new AppConfig."""
    updates: dict = {}
    modbus_updates: dict = {}

    # Environment variables (lower priority)
    val = os.environ.get("MODBUS_HOST")
    if val:
        modbus_updates["host"] = val
    val = os.environ.get("MODBUS_PORT")
    if val:
        try:
            modbus_updates["port"] = int(val)
        except ValueError:
            pass
    val = os.environ.get("MODBUS_UNIT_ID")
    if val:
        try:
            modbus_updates["unit_id"] = int(val)
        except ValueError:
            pass
    val = os.environ.get("SIM_TICK_SECONDS")
    if val:
        try:
            updates["tick_seconds"] = float(val)
        except ValueError:
            pass
    val = os.environ.get("LOG_LEVEL")
    if val:
        updates["log_level"] = val

    # CLI overrides (highest priority)
    if host is not None:
        modbus_updates["host"] = host
    if port is not None:
        modbus_updates["port"] = port
    if unit_id is not None:
        modbus_updates["unit_id"] = unit_id
    if tick is not None:
        updates["tick_seconds"] = tick
    if log_level is not None:
        updates["log_level"] = log_level

    # If there are any overrides, create a new config
    if not updates and not modbus_updates:
        return config

    data = config.model_dump()
    data.update(updates)
    if modbus_updates:
        data["modbus"].update(modbus_updates)

    return AppConfig(**data)


def load_config(
    config_path: Optional[str] = None,
    cli_host: Optional[str] = None,
    cli_port: Optional[int] = None,
    cli_unit_id: Optional[int] = None,
    cli_tick: Optional[float] = None,
    cli_log_level: Optional[str] = None,
) -> AppConfig:
    """
    Load configuration with priority: CLI > env vars > config file > defaults.

    Detects file type by extension:
      .py   -> Python config (must export `config: AppConfig`)
      .yaml/.yml -> YAML config
    """
    if config_path is None:
        config_path = os.environ.get("SIM_CONFIG_PATH")

    if config_path:
        ext = Path(config_path).suffix.lower()
        if ext == ".py":
            config = load_python_config(config_path)
            logger.info(f"Loaded Python config from: {config_path}")
        elif ext in (".yaml", ".yml"):
            config = load_yaml_config(config_path)
            logger.info(f"Loaded YAML config from: {config_path}")
        else:
            raise ValueError(f"Unsupported config file type: {ext} (use .py or .yaml)")
    else:
        config = AppConfig()
        logger.info("No config file specified, using defaults")

    return apply_overrides(
        config,
        host=cli_host,
        port=cli_port,
        unit_id=cli_unit_id,
        tick=cli_tick,
        log_level=cli_log_level,
    )
