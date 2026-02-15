"""
Sensor Simulator - CLI Entrypoint

Simulates Siemens QNA2..D multi-sensors and writes values to Modbus TCP.
"""
import argparse
import sys

from config import load_config
from simulator.scheduler import run_simulation
from utils.logging import setup_logging, get_logger


def parse_args() -> argparse.Namespace:
    """Parse command line arguments."""
    parser = argparse.ArgumentParser(
        prog="sensor-simulator",
        description="Simulate Siemens QNA2..D multi-sensors with Modbus TCP output",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )

    parser.add_argument(
        "--config", "-c",
        type=str,
        default=None,
        help="Path to YAML configuration file",
    )

    parser.add_argument(
        "--host",
        type=str,
        default=None,
        help="Modbus TCP server host address",
    )

    parser.add_argument(
        "--port", "-p",
        type=int,
        default=None,
        help="Modbus TCP server port",
    )

    parser.add_argument(
        "--unit-id", "-u",
        type=int,
        default=None,
        help="Modbus slave unit ID",
    )

    parser.add_argument(
        "--tick", "-t",
        type=float,
        default=None,
        help="Main loop tick interval in seconds",
    )

    parser.add_argument(
        "--sensor-count", "-n",
        type=int,
        default=None,
        help="Number of sensors to simulate (for autogeneration)",
    )

    parser.add_argument(
        "--base-address", "-b",
        type=int,
        default=None,
        help="Base register address for first sensor (for autogeneration)",
    )

    parser.add_argument(
        "--stride", "-s",
        type=int,
        default=None,
        help="Register address stride between sensors (for autogeneration)",
    )

    parser.add_argument(
        "--log-level", "-l",
        type=str,
        default=None,
        choices=["DEBUG", "INFO", "WARNING", "ERROR"],
        help="Logging level",
    )

    return parser.parse_args()


def main():
    """Main entry point."""
    args = parse_args()

    # Load configuration with all overrides
    config = load_config(
        config_path=args.config,
        cli_host=args.host,
        cli_port=args.port,
        cli_unit_id=args.unit_id,
        cli_tick=args.tick,
        cli_sensor_count=args.sensor_count,
        cli_base_address=args.base_address,
        cli_stride=args.stride,
        cli_log_level=args.log_level,
    )

    # Setup logging
    setup_logging(level=config.log_level)
    logger = get_logger(__name__)

    # Log configuration summary
    logger.info("=" * 60)
    logger.info("Siemens QNA2..D Sensor Simulator")
    logger.info("=" * 60)
    logger.info(f"Modbus server: {config.modbus.host}:{config.modbus.port}")
    logger.info(f"Unit ID: {config.modbus.unit_id}")
    logger.info(f"Tick interval: {config.update.tick_seconds}s")
    logger.info(f"Sensors: {len(config.sensors)}")

    for sensor in config.sensors:
        logger.info(
            f"  - {sensor.id}: base_address={sensor.base_address}, "
            f"absolute={sensor.absolute}"
        )

    logger.info("Update rates:")
    rates = config.update.rates
    logger.info(f"  temperature: {rates.temperature}s, humidity: {rates.humidity}s")
    logger.info(f"  co2: {rates.co2}s, tvoc: {rates.tvoc}s")
    logger.info(f"  pm25: {rates.pm25}s, pm10: {rates.pm10}s")
    logger.info(f"  lux: {rates.lux}s, noise: {rates.noise}s")
    logger.info("=" * 60)
    logger.info("Press Ctrl+C to stop")
    logger.info("=" * 60)

    # Run the simulation
    try:
        run_simulation(config)
    except Exception as e:
        logger.error(f"Simulation error: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()

