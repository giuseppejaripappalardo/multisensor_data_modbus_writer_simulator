"""
Configuration loading with YAML support and environment variable overrides.
"""
import os
from pathlib import Path
from typing import Optional

import yaml

from models import (
    AppConfig,
)
from utils.logging import get_logger

logger = get_logger(__name__)


def load_yaml_config(config_path: str) -> dict:
    """
    Load configuration from a YAML file.

    Args:
        config_path: Path to the YAML configuration file.

    Returns:
        Dictionary with configuration values.
    """
    path = Path(config_path)
    if not path.exists():
        logger.warning(f"Config file not found: {config_path}")
        return {}

    with open(path, "r", encoding="utf-8") as f:
        config = yaml.safe_load(f)

    return config or {}


def get_env_int(name: str, default: Optional[int] = None) -> Optional[int]:
    """Get integer from environment variable."""
    value = os.environ.get(name)
    if value is not None:
        try:
            return int(value)
        except ValueError:
            logger.warning(f"Invalid integer value for {name}: {value}")
    return default


def get_env_float(name: str, default: Optional[float] = None) -> Optional[float]:
    """Get float from environment variable."""
    value = os.environ.get(name)
    if value is not None:
        try:
            return float(value)
        except ValueError:
            logger.warning(f"Invalid float value for {name}: {value}")
    return default


def get_env_str(name: str, default: Optional[str] = None) -> Optional[str]:
    """Get string from environment variable."""
    return os.environ.get(name, default)


def apply_env_overrides(config: dict) -> dict:
    """
    Apply environment variable overrides to configuration.

    Environment variables:
        MODBUS_HOST, MODBUS_PORT, MODBUS_UNIT_ID
        SIM_TICK_SECONDS
        SIM_SENSOR_COUNT, SIM_BASE_ADDRESS, SIM_SENSOR_STRIDE
        LOG_LEVEL
    """
    # Modbus overrides
    if "modbus" not in config:
        config["modbus"] = {}

    modbus_host = get_env_str("MODBUS_HOST")
    if modbus_host:
        config["modbus"]["host"] = modbus_host

    modbus_port = get_env_int("MODBUS_PORT")
    if modbus_port:
        config["modbus"]["port"] = modbus_port

    modbus_unit_id = get_env_int("MODBUS_UNIT_ID")
    if modbus_unit_id:
        config["modbus"]["unit_id"] = modbus_unit_id

    # Update overrides
    if "update" not in config:
        config["update"] = {}

    tick_seconds = get_env_float("SIM_TICK_SECONDS")
    if tick_seconds:
        config["update"]["tick_seconds"] = tick_seconds

    # Log level override
    log_level = get_env_str("LOG_LEVEL")
    if log_level:
        config["log_level"] = log_level

    return config


def apply_cli_overrides(
    config: dict,
    host: Optional[str] = None,
    port: Optional[int] = None,
    unit_id: Optional[int] = None,
    tick: Optional[float] = None,
    sensor_count: Optional[int] = None,
    base_address: Optional[int] = None,
    stride: Optional[int] = None,
    log_level: Optional[str] = None,
) -> dict:
    """
    Apply CLI argument overrides to configuration.
    CLI flags take highest priority over config file and env vars.
    """
    if "modbus" not in config:
        config["modbus"] = {}

    if host is not None:
        config["modbus"]["host"] = host
    if port is not None:
        config["modbus"]["port"] = port
    if unit_id is not None:
        config["modbus"]["unit_id"] = unit_id

    if "update" not in config:
        config["update"] = {}

    if tick is not None:
        config["update"]["tick_seconds"] = tick

    if log_level is not None:
        config["log_level"] = log_level

    # Store CLI sensor generation params for later use
    if sensor_count is not None:
        config["_cli_sensor_count"] = sensor_count
    if base_address is not None:
        config["_cli_base_address"] = base_address
    if stride is not None:
        config["_cli_stride"] = stride

    return config


def autogenerate_sensors(
    count: int,
    base_address: int = 0,
    stride: int = 10,
) -> list:
    """
    Autogenerate sensor configurations.

    Args:
        count: Number of sensors to generate.
        base_address: Starting base address.
        stride: Address increment between sensors.

    Returns:
        List of sensor configuration dictionaries.
    """
    sensors = []
    for i in range(count):
        sensor = {
            "id": f"sensor_{i + 1}",
            "base_address": base_address + (i * stride),
            "register_map": {
                "temperature": 0,
                "humidity": 1,
                "co2": 2,
                "tvoc": 3,
                "pm25": 4,
                "pm10": 5,
                "lux": 6,
                "noise": 7,
            }
        }
        sensors.append(sensor)
    return sensors


def load_config(
    config_path: Optional[str] = None,
    cli_host: Optional[str] = None,
    cli_port: Optional[int] = None,
    cli_unit_id: Optional[int] = None,
    cli_tick: Optional[float] = None,
    cli_sensor_count: Optional[int] = None,
    cli_base_address: Optional[int] = None,
    cli_stride: Optional[int] = None,
    cli_log_level: Optional[str] = None,
) -> AppConfig:
    """
    Load complete configuration with YAML, env overrides, and CLI overrides.

    Priority (highest to lowest):
        1. CLI arguments
        2. Environment variables
        3. Config file
        4. Default values
    """
    # Start with empty config or load from file
    if config_path is None:
        config_path = get_env_str("SIM_CONFIG_PATH")

    if config_path:
        config = load_yaml_config(config_path)
        logger.info(f"Loaded configuration from: {config_path}")
    else:
        config = {}
        logger.info("No config file specified, using defaults")

    # Apply environment overrides
    config = apply_env_overrides(config)

    # Apply CLI overrides
    config = apply_cli_overrides(
        config,
        host=cli_host,
        port=cli_port,
        unit_id=cli_unit_id,
        tick=cli_tick,
        sensor_count=cli_sensor_count,
        base_address=cli_base_address,
        stride=cli_stride,
        log_level=cli_log_level,
    )

    # Handle sensor autogeneration
    sensors_in_config = config.get("sensors", [])

    if not sensors_in_config:
        # Try to get autogeneration params from CLI, env, or defaults
        sensor_count = config.pop("_cli_sensor_count", None)
        base_address = config.pop("_cli_base_address", None)
        stride = config.pop("_cli_stride", None)

        if sensor_count is None:
            sensor_count = get_env_int("SIM_SENSOR_COUNT", 1)
        if base_address is None:
            base_address = get_env_int("SIM_BASE_ADDRESS", 0)
        if stride is None:
            stride = get_env_int("SIM_SENSOR_STRIDE", 10)

        config["sensors"] = autogenerate_sensors(
            count=sensor_count,
            base_address=base_address,
            stride=stride,
        )
        logger.info(f"Autogenerated {sensor_count} sensor(s)")
    else:
        # Remove CLI params if they exist but weren't used
        config.pop("_cli_sensor_count", None)
        config.pop("_cli_base_address", None)
        config.pop("_cli_stride", None)

    # Convert nested dicts for modbus config
    if "modbus" in config:
        modbus_dict = config["modbus"]
        # Handle timeout and retry config from YAML
        if "timeouts" in modbus_dict:
            timeouts = modbus_dict.pop("timeouts")
            if "connect_ms" in timeouts:
                modbus_dict["connect_timeout_ms"] = timeouts["connect_ms"]
            if "write_ms" in timeouts:
                modbus_dict["write_timeout_ms"] = timeouts["write_ms"]

        if "retry" in modbus_dict:
            retry = modbus_dict.pop("retry")
            if "max_attempts" in retry:
                modbus_dict["max_retry_attempts"] = retry["max_attempts"]
            if "backoff_seconds" in retry:
                modbus_dict["backoff_seconds"] = retry["backoff_seconds"]

    # Convert nested dicts for update config
    if "update" in config:
        update_dict = config["update"]
        if "rates_seconds" in update_dict:
            update_dict["rates"] = update_dict.pop("rates_seconds")

    # Validate and create the config object
    app_config = AppConfig(**config)

    return app_config

