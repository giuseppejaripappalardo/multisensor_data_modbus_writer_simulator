"""
Sensor Simulator - CLI Entrypoint

Simulates configurable sensors and writes values to Modbus TCP holding registers.
"""
import argparse
import sys

from config import load_config
from simulator.scheduler import run_simulation
from utils.logging import setup_logging, get_logger


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="sensor-simulator",
        description="Simulate sensors with Modbus TCP output",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument("--config", "-c", type=str, default=None,
                        help="Path to YAML configuration file")
    parser.add_argument("--host", type=str, default=None,
                        help="Modbus TCP server host")
    parser.add_argument("--port", "-p", type=int, default=None,
                        help="Modbus TCP server port")
    parser.add_argument("--unit-id", "-u", type=int, default=None,
                        help="Modbus slave unit ID")
    parser.add_argument("--tick", "-t", type=float, default=None,
                        help="Main loop tick interval (seconds)")
    parser.add_argument("--log-level", "-l", type=str, default=None,
                        choices=["DEBUG", "INFO", "WARNING", "ERROR"],
                        help="Logging level")
    return parser.parse_args()


def main():
    args = parse_args()

    config = load_config(
        config_path=args.config,
        cli_host=args.host,
        cli_port=args.port,
        cli_unit_id=args.unit_id,
        cli_tick=args.tick,
        cli_log_level=args.log_level,
    )

    setup_logging(level=config.log_level)
    logger = get_logger(__name__)

    logger.info("=" * 60)
    logger.info("Sensor Simulator")
    logger.info("=" * 60)
    logger.info(f"Modbus: {config.modbus.host}:{config.modbus.port} (unit {config.modbus.unit_id})")
    logger.info(f"Tick: {config.tick_seconds}s")
    logger.info(f"Sensors: {len(config.sensors)}")

    for sensor in config.sensors:
        logger.info(
            f"  [{sensor.id}] base={sensor.base_address}, "
            f"endian={sensor.byte_order}/{sensor.word_order}, "
            f"measurements={len(sensor.measurements)}"
        )
        for m in sensor.measurements:
            logger.info(
                f"    - {m.name}: offset={m.offset}, type={m.data_type.value}, "
                f"scale={m.scale}, range=[{m.min_value}, {m.max_value}], "
                f"rate={m.update_rate}s, regs={m.register_count}"
            )

    logger.info("=" * 60)
    logger.info("Press Ctrl+C to stop")
    logger.info("=" * 60)

    try:
        run_simulation(config)
    except Exception as e:
        logger.error(f"Simulation error: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()
