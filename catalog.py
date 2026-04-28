"""
Catalog of known measurement templates.

Each entry describes a measurement type that the UI can offer plug-and-play:
default data type, scale, unit, range, update rate and a generator hint.

Adding a new template here makes it immediately available in the web UI and
in YAML/Python configs alike.
"""
from typing import Dict, List, Optional

from pydantic import BaseModel

from models import DataType, RegisterType


class MeasurementTemplate(BaseModel):
    """Default values for a known measurement type."""
    name: str
    label: str
    unit: str
    register_type: RegisterType = RegisterType.HOLDING_REGISTER
    data_type: DataType = DataType.UINT16
    scale: float = 1.0
    min_value: float = 0.0
    max_value: float = 65535.0
    update_rate: float = 1.0
    description: str = ""
    # Generator family: one of the known patterns or "generic".
    generator: str = "generic"


CATALOG: List[MeasurementTemplate] = [
    MeasurementTemplate(
        name="temperature", label="Temperatura", unit="°C",
        data_type=DataType.INT16, scale=10.0,
        min_value=-40.0, max_value=80.0, update_rate=1.0,
        description="Sinusoide ~24°C ±2°C, rumore ±0.1°C",
        generator="temperature",
    ),
    MeasurementTemplate(
        name="humidity", label="Umidità relativa", unit="%",
        data_type=DataType.UINT16, scale=10.0,
        min_value=0.0, max_value=100.0, update_rate=1.0,
        description="Sinusoide ~55% ±7%, rumore ±0.3%",
        generator="humidity",
    ),
    MeasurementTemplate(
        name="co2", label="CO2", unit="ppm",
        data_type=DataType.UINT16, scale=1.0,
        min_value=400.0, max_value=5000.0, update_rate=5.0,
        description="Base 520ppm con picchi sinusoidali fino a +300ppm",
        generator="co2",
    ),
    MeasurementTemplate(
        name="tvoc", label="TVOC", unit="ppb",
        data_type=DataType.UINT16, scale=1.0,
        min_value=0.0, max_value=5000.0, update_rate=5.0,
        description="Correlato al CO2 (segue il pattern)",
        generator="tvoc",
    ),
    MeasurementTemplate(
        name="pm25", label="PM 2.5", unit="µg/m³",
        data_type=DataType.UINT16, scale=1.0,
        min_value=0.0, max_value=500.0, update_rate=10.0,
        description="Base ~12, picchi casuali ogni 60-120s",
        generator="pm25",
    ),
    MeasurementTemplate(
        name="pm10", label="PM 10", unit="µg/m³",
        data_type=DataType.UINT16, scale=1.0,
        min_value=0.0, max_value=500.0, update_rate=10.0,
        description="Correlato al PM2.5 (offset 3-20 µg/m³)",
        generator="pm10",
    ),
    MeasurementTemplate(
        name="lux", label="Illuminamento", unit="lux",
        data_type=DataType.UINT16, scale=1.0,
        min_value=0.0, max_value=65535.0, update_rate=1.0,
        description="Pattern giorno/notte 50..850 lux",
        generator="lux",
    ),
    MeasurementTemplate(
        name="noise", label="Rumore", unit="dB",
        data_type=DataType.UINT16, scale=10.0,
        min_value=0.0, max_value=130.0, update_rate=1.0,
        description="Base ~38dB ±3dB, rumore ±1dB",
        generator="noise",
    ),
    MeasurementTemplate(
        name="pressure", label="Pressione", unit="hPa",
        data_type=DataType.UINT16, scale=1.0,
        min_value=900.0, max_value=1100.0, update_rate=2.0,
        description="Pressione atmosferica generica",
        generator="generic",
    ),
    MeasurementTemplate(
        name="voltage", label="Tensione", unit="V",
        data_type=DataType.FLOAT32, scale=1.0,
        min_value=200.0, max_value=250.0, update_rate=1.0,
        description="Tensione di rete ~230V",
        generator="generic",
    ),
    MeasurementTemplate(
        name="current", label="Corrente", unit="A",
        data_type=DataType.FLOAT32, scale=1.0,
        min_value=0.0, max_value=100.0, update_rate=1.0,
        description="Corrente assorbita",
        generator="generic",
    ),
    MeasurementTemplate(
        name="power", label="Potenza attiva", unit="W",
        data_type=DataType.FLOAT32, scale=1.0,
        min_value=0.0, max_value=50000.0, update_rate=1.0,
        description="Potenza istantanea",
        generator="generic",
    ),
    MeasurementTemplate(
        name="energy", label="Energia attiva", unit="kWh",
        data_type=DataType.FLOAT64, scale=1.0,
        min_value=0.0, max_value=1.0e9, update_rate=5.0,
        description="Contatore energia (alta precisione)",
        generator="generic",
    ),
    MeasurementTemplate(
        name="frequency", label="Frequenza", unit="Hz",
        data_type=DataType.FLOAT32, scale=1.0,
        min_value=49.0, max_value=51.0, update_rate=1.0,
        description="Frequenza di rete ~50Hz",
        generator="generic",
    ),
    MeasurementTemplate(
        name="custom", label="Misura personalizzata", unit="",
        data_type=DataType.UINT16, scale=1.0,
        min_value=0.0, max_value=65535.0, update_rate=1.0,
        description="Misura libera con pattern sinusoidale generico",
        generator="generic",
    ),
    # ---------------------------------------------------------------
    # 1-bit measurements (coil / discrete input)
    # ---------------------------------------------------------------
    MeasurementTemplate(
        name="alarm_active", label="Allarme attivo", unit="",
        register_type=RegisterType.COIL, data_type=DataType.BOOL,
        scale=1.0, min_value=0.0, max_value=1.0, update_rate=1.0,
        description="Stato allarme R/W (coil) — 0=inattivo, 1=attivo",
        generator="boolean_rare",
    ),
    MeasurementTemplate(
        name="motor_run", label="Motore in marcia", unit="",
        register_type=RegisterType.COIL, data_type=DataType.BOOL,
        scale=1.0, min_value=0.0, max_value=1.0, update_rate=2.0,
        description="Comando run/stop motore (coil R/W)",
        generator="boolean_periodic",
    ),
    MeasurementTemplate(
        name="presence", label="Presenza", unit="",
        register_type=RegisterType.DISCRETE_INPUT, data_type=DataType.BOOL,
        scale=1.0, min_value=0.0, max_value=1.0, update_rate=0.5,
        description="Sensore di presenza (discrete input R/O)",
        generator="boolean_periodic",
    ),
    MeasurementTemplate(
        name="limit_switch", label="Finecorsa", unit="",
        register_type=RegisterType.DISCRETE_INPUT, data_type=DataType.BOOL,
        scale=1.0, min_value=0.0, max_value=1.0, update_rate=0.5,
        description="Stato finecorsa (discrete input R/O)",
        generator="boolean_periodic",
    ),
    # ---------------------------------------------------------------
    # Read-only register: contatori / valori firmware (input register)
    # ---------------------------------------------------------------
    MeasurementTemplate(
        name="uptime_seconds", label="Uptime", unit="s",
        register_type=RegisterType.INPUT_REGISTER, data_type=DataType.UINT32,
        scale=1.0, min_value=0.0, max_value=4_294_967_295.0, update_rate=1.0,
        description="Contatore uptime device (input register, R/O)",
        generator="generic",
    ),
]


_BY_NAME: Dict[str, MeasurementTemplate] = {t.name: t for t in CATALOG}


def get_template(name: str) -> Optional[MeasurementTemplate]:
    return _BY_NAME.get(name)


def list_templates() -> List[MeasurementTemplate]:
    return list(CATALOG)
