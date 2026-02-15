"""
Modbus TCP client wrapper with retry and backoff support.
"""
import time
from typing import Dict, List, Optional, Tuple

from pymodbus.client import ModbusTcpClient
from pymodbus.exceptions import ModbusException

from models import ModbusConfig
from utils.logging import get_logger

logger = get_logger(__name__)


class ModbusClient:
    """
    Wrapper for pymodbus ModbusTcpClient with retry/backoff support.
    """

    def __init__(self, config: ModbusConfig):
        """
        Initialize the Modbus client.

        Args:
            config: Modbus configuration object.
        """
        self.config = config
        self.client: Optional[ModbusTcpClient] = None
        self._connected = False

    def connect(self) -> bool:
        """
        Connect to the Modbus TCP server with retry.

        Returns:
            True if connection successful, False otherwise.
        """
        if self._connected and self.client:
            return True

        backoff_list = self.config.backoff_seconds

        for attempt in range(self.config.max_retry_attempts):
            try:
                logger.info(
                    f"Connecting to Modbus server at "
                    f"{self.config.host}:{self.config.port} "
                    f"(attempt {attempt + 1}/{self.config.max_retry_attempts})"
                )

                self.client = ModbusTcpClient(
                    host=self.config.host,
                    port=self.config.port,
                    timeout=self.config.connect_timeout_ms / 1000.0,
                )

                if self.client.connect():
                    self._connected = True
                    logger.info("Connected to Modbus server")
                    return True
                else:
                    logger.warning("Connection failed")

            except Exception as e:
                logger.error(f"Connection error: {e}")

            # Apply backoff before retry
            if attempt < self.config.max_retry_attempts - 1:
                backoff_idx = min(attempt, len(backoff_list) - 1)
                backoff_time = backoff_list[backoff_idx]
                logger.info(f"Waiting {backoff_time}s before retry...")
                time.sleep(backoff_time)

        logger.error("Failed to connect after all retry attempts")
        return False

    def disconnect(self):
        """Disconnect from the Modbus server."""
        if self.client:
            self.client.close()
            self._connected = False
            logger.info("Disconnected from Modbus server")

    def is_connected(self) -> bool:
        """Check if client is connected."""
        return self._connected and self.client is not None

    def write_register(
        self,
        address: int,
        value: int,
        unit_id: Optional[int] = None,
    ) -> bool:
        """
        Write a single holding register with retry.

        Args:
            address: Register address (0-based).
            value: Value to write (uint16).
            unit_id: Slave unit ID (uses config default if not specified).

        Returns:
            True if write successful, False otherwise.
        """
        if unit_id is None:
            unit_id = self.config.unit_id

        if not self.is_connected():
            if not self.connect():
                return False

        backoff_list = self.config.backoff_seconds

        for attempt in range(self.config.max_retry_attempts):
            try:
                result = self.client.write_register(
                    address=address,
                    value=value & 0xFFFF,  # Ensure uint16
                    device_id=unit_id,
                )

                if result.isError():
                    logger.warning(f"Write error at address {address}: {result}")
                else:
                    return True

            except ModbusException as e:
                logger.error(f"Modbus exception writing to {address}: {e}")
                self._connected = False
            except Exception as e:
                logger.error(f"Error writing to {address}: {e}")
                self._connected = False

            # Apply backoff and reconnect
            if attempt < self.config.max_retry_attempts - 1:
                backoff_idx = min(attempt, len(backoff_list) - 1)
                backoff_time = backoff_list[backoff_idx]
                logger.debug(f"Retry write in {backoff_time}s...")
                time.sleep(backoff_time)
                self.connect()

        return False

    def write_registers(
        self,
        address: int,
        values: List[int],
        unit_id: Optional[int] = None,
    ) -> bool:
        """
        Write multiple contiguous holding registers with retry.

        Args:
            address: Starting register address (0-based).
            values: List of values to write (uint16).
            unit_id: Slave unit ID (uses config default if not specified).

        Returns:
            True if write successful, False otherwise.
        """
        if unit_id is None:
            unit_id = self.config.unit_id

        if not values:
            return True

        if len(values) == 1:
            return self.write_register(address, values[0], unit_id)

        if not self.is_connected():
            if not self.connect():
                return False

        backoff_list = self.config.backoff_seconds

        # Ensure all values are uint16
        values = [v & 0xFFFF for v in values]

        for attempt in range(self.config.max_retry_attempts):
            try:
                result = self.client.write_registers(
                    address=address,
                    values=values,
                    device_id=unit_id,
                )

                if result.isError():
                    logger.warning(
                        f"Write error at address {address} "
                        f"(count={len(values)}): {result}"
                    )
                else:
                    return True

            except ModbusException as e:
                logger.error(
                    f"Modbus exception writing to {address} "
                    f"(count={len(values)}): {e}"
                )
                self._connected = False
            except Exception as e:
                logger.error(
                    f"Error writing to {address} (count={len(values)}): {e}"
                )
                self._connected = False

            # Apply backoff and reconnect
            if attempt < self.config.max_retry_attempts - 1:
                backoff_idx = min(attempt, len(backoff_list) - 1)
                backoff_time = backoff_list[backoff_idx]
                logger.debug(f"Retry write in {backoff_time}s...")
                time.sleep(backoff_time)
                self.connect()

        return False

    def write_register_blocks(
        self,
        registers: Dict[int, int],
        unit_id: Optional[int] = None,
    ) -> bool:
        """
        Write registers in contiguous blocks for efficiency.

        This method groups registers by contiguity and writes each block
        with a single Modbus transaction.

        Args:
            registers: Dictionary mapping addresses to values.
            unit_id: Slave unit ID (uses config default if not specified).

        Returns:
            True if all writes successful, False otherwise.
        """
        if not registers:
            return True

        # Group registers into contiguous blocks
        blocks = self._group_contiguous_registers(registers)

        all_success = True
        for start_address, values in blocks:
            success = self.write_registers(start_address, values, unit_id)
            if not success:
                all_success = False
                logger.error(
                    f"Failed to write block at address {start_address} "
                    f"(count={len(values)})"
                )

        return all_success

    def _group_contiguous_registers(
        self,
        registers: Dict[int, int],
    ) -> List[Tuple[int, List[int]]]:
        """
        Group registers into contiguous blocks.

        Args:
            registers: Dictionary mapping addresses to values.

        Returns:
            List of (start_address, values) tuples.
        """
        if not registers:
            return []

        sorted_addrs = sorted(registers.keys())
        blocks = []

        current_start = sorted_addrs[0]
        current_values = [registers[current_start]]
        expected_next = current_start + 1

        for addr in sorted_addrs[1:]:
            if addr == expected_next:
                # Contiguous, add to current block
                current_values.append(registers[addr])
                expected_next = addr + 1
            else:
                # Gap found, start new block
                blocks.append((current_start, current_values))
                current_start = addr
                current_values = [registers[addr]]
                expected_next = addr + 1

        # Don't forget the last block
        blocks.append((current_start, current_values))

        return blocks

