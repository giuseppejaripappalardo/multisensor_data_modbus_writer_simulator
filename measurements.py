"""
Factory functions for known measurement types.

Each function returns a MeasurementConfig with sensible defaults
for that measurement type. All parameters are overridable.

Usage:
    from measurements import temperature, humidity, co2, measurement

    temperature(offset=0)                              # int16, scale=10, -40..80
    temperature(offset=0, data_type=DataType.FLOAT32)  # override type
    humidity(offset=2, scale=1)                        # override scale
    measurement("pressure", offset=4, ...)             # custom measurement
"""
from models import DataType, MeasurementConfig


def temperature(
    offset: int,
    data_type: DataType = DataType.INT16,
    scale: float = 10.0,
    min_value: float = -40.0,
    max_value: float = 80.0,
    update_rate: float = 1.0,
) -> MeasurementConfig:
    """Temperature in C. Default: int16, scale=10 (25.3C -> 253)."""
    return MeasurementConfig(
        name="temperature",
        offset=offset,
        data_type=data_type,
        scale=scale,
        min_value=min_value,
        max_value=max_value,
        update_rate=update_rate,
    )


def humidity(
    offset: int,
    data_type: DataType = DataType.UINT16,
    scale: float = 10.0,
    min_value: float = 0.0,
    max_value: float = 100.0,
    update_rate: float = 1.0,
) -> MeasurementConfig:
    """Relative humidity in %. Default: uint16, scale=10 (55.2% -> 552)."""
    return MeasurementConfig(
        name="humidity",
        offset=offset,
        data_type=data_type,
        scale=scale,
        min_value=min_value,
        max_value=max_value,
        update_rate=update_rate,
    )


def co2(
    offset: int,
    data_type: DataType = DataType.UINT16,
    scale: float = 1.0,
    min_value: float = 400.0,
    max_value: float = 5000.0,
    update_rate: float = 5.0,
) -> MeasurementConfig:
    """CO2 in ppm. Default: uint16, scale=1."""
    return MeasurementConfig(
        name="co2",
        offset=offset,
        data_type=data_type,
        scale=scale,
        min_value=min_value,
        max_value=max_value,
        update_rate=update_rate,
    )


def tvoc(
    offset: int,
    data_type: DataType = DataType.UINT16,
    scale: float = 1.0,
    min_value: float = 0.0,
    max_value: float = 5000.0,
    update_rate: float = 5.0,
) -> MeasurementConfig:
    """TVOC in ppb. Default: uint16, scale=1."""
    return MeasurementConfig(
        name="tvoc",
        offset=offset,
        data_type=data_type,
        scale=scale,
        min_value=min_value,
        max_value=max_value,
        update_rate=update_rate,
    )


def pm25(
    offset: int,
    data_type: DataType = DataType.UINT16,
    scale: float = 1.0,
    min_value: float = 0.0,
    max_value: float = 500.0,
    update_rate: float = 10.0,
) -> MeasurementConfig:
    """PM2.5 in ug/m3. Default: uint16, scale=1."""
    return MeasurementConfig(
        name="pm25",
        offset=offset,
        data_type=data_type,
        scale=scale,
        min_value=min_value,
        max_value=max_value,
        update_rate=update_rate,
    )


def pm10(
    offset: int,
    data_type: DataType = DataType.UINT16,
    scale: float = 1.0,
    min_value: float = 0.0,
    max_value: float = 500.0,
    update_rate: float = 10.0,
) -> MeasurementConfig:
    """PM10 in ug/m3. Default: uint16, scale=1."""
    return MeasurementConfig(
        name="pm10",
        offset=offset,
        data_type=data_type,
        scale=scale,
        min_value=min_value,
        max_value=max_value,
        update_rate=update_rate,
    )


def lux(
    offset: int,
    data_type: DataType = DataType.UINT16,
    scale: float = 1.0,
    min_value: float = 0.0,
    max_value: float = 65535.0,
    update_rate: float = 1.0,
) -> MeasurementConfig:
    """Illuminance in lux. Default: uint16, scale=1."""
    return MeasurementConfig(
        name="lux",
        offset=offset,
        data_type=data_type,
        scale=scale,
        min_value=min_value,
        max_value=max_value,
        update_rate=update_rate,
    )


def noise(
    offset: int,
    data_type: DataType = DataType.UINT16,
    scale: float = 10.0,
    min_value: float = 0.0,
    max_value: float = 130.0,
    update_rate: float = 1.0,
) -> MeasurementConfig:
    """Noise level in dB. Default: uint16, scale=10 (38.5dB -> 385)."""
    return MeasurementConfig(
        name="noise",
        offset=offset,
        data_type=data_type,
        scale=scale,
        min_value=min_value,
        max_value=max_value,
        update_rate=update_rate,
    )


def measurement(
    name: str,
    offset: int,
    data_type: DataType = DataType.UINT16,
    scale: float = 1.0,
    min_value: float = 0.0,
    max_value: float = 65535.0,
    update_rate: float = 1.0,
) -> MeasurementConfig:
    """Generic measurement for custom types (uses sinusoidal generator)."""
    return MeasurementConfig(
        name=name,
        offset=offset,
        data_type=data_type,
        scale=scale,
        min_value=min_value,
        max_value=max_value,
        update_rate=update_rate,
    )
