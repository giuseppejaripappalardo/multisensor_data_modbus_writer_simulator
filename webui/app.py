"""
FastAPI app exposing the simulator's plug-and-play web UI and REST API.

Run with:
    uvicorn webui.app:app --host 0.0.0.0 --port 8000
or via the convenience entry point:
    python -m webui
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import List, Optional

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from catalog import list_templates, get_template
from models import (
    AppConfig,
    DataType,
    MeasurementConfig,
    MeasurementFault,
    RegisterType,
    SensorConfig,
    SensorFault,
    REGISTERS_PER_TYPE,
)
from utils.logging import setup_logging
from webui.runtime import Runtime


# --------------------------------------------------------------------------
# Bootstrap
# --------------------------------------------------------------------------
HERE = Path(__file__).parent
PROJECT_ROOT = HERE.parent
STATIC_DIR = HERE / "static"
TEMPLATES_DIR = HERE / "templates"

CONFIG_PATH = Path(os.environ.get("SIM_CONFIG_PATH", PROJECT_ROOT / "configs" / "runtime.yaml"))
LOG_LEVEL = os.environ.get("LOG_LEVEL", "INFO")

setup_logging(level=LOG_LEVEL)


def _load_initial_config() -> AppConfig:
    if CONFIG_PATH.exists():
        try:
            import yaml
            with CONFIG_PATH.open("r", encoding="utf-8") as f:
                data = yaml.safe_load(f) or {}
            return AppConfig(**data)
        except Exception:
            pass
    return AppConfig()


runtime = Runtime(_load_initial_config(), persist_path=CONFIG_PATH)


# --------------------------------------------------------------------------
# Pydantic request/response models
# --------------------------------------------------------------------------
class SensorCreate(BaseModel):
    id: str
    unit_id: int = 1
    base_address: int = 0
    byte_order: str = "big"
    word_order: str = "big"
    write_rate_seconds: float = 1.0


class SensorUpdate(BaseModel):
    unit_id: Optional[int] = None
    base_address: Optional[int] = None
    byte_order: Optional[str] = None
    word_order: Optional[str] = None
    write_rate_seconds: Optional[float] = None
    fault: Optional[SensorFault] = None


class MeasurementCreate(BaseModel):
    template_name: Optional[str] = None        # if set, prefill from catalog
    name: Optional[str] = None
    offset: int
    register_type: Optional[RegisterType] = None
    data_type: Optional[DataType] = None
    scale: Optional[float] = None
    min_value: Optional[float] = None
    max_value: Optional[float] = None
    update_rate: Optional[float] = None
    unit: Optional[str] = None


class MeasurementUpdate(BaseModel):
    offset: Optional[int] = None
    register_type: Optional[RegisterType] = None
    data_type: Optional[DataType] = None
    scale: Optional[float] = None
    min_value: Optional[float] = None
    max_value: Optional[float] = None
    update_rate: Optional[float] = None
    unit: Optional[str] = None
    fault: Optional[MeasurementFault] = None


# --------------------------------------------------------------------------
# Helpers
# --------------------------------------------------------------------------
def _replace(config: AppConfig) -> AppConfig:
    runtime.replace_config(config)
    return runtime.config


def _next_offset(sensor: SensorConfig) -> int:
    """Return the smallest non-overlapping offset after existing measurements."""
    if not sensor.measurements:
        return 0
    last = max(m.offset + m.register_count for m in sensor.measurements)
    return last


def _find_sensor(config: AppConfig, sensor_id: str) -> SensorConfig:
    for s in config.sensors:
        if s.id == sensor_id:
            return s
    raise HTTPException(status_code=404, detail=f"Sensor not found: {sensor_id}")


def _find_measurement(sensor: SensorConfig, name: str) -> MeasurementConfig:
    for m in sensor.measurements:
        if m.name == name:
            return m
    raise HTTPException(status_code=404, detail=f"Measurement not found: {name}")


# --------------------------------------------------------------------------
# App
# --------------------------------------------------------------------------
app = FastAPI(title="Multisensor Modbus Simulator")
app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")


@app.on_event("shutdown")
def _shutdown_runtime() -> None:
    runtime.shutdown()


@app.get("/", include_in_schema=False)
def index():
    return FileResponse(TEMPLATES_DIR / "index.html")


# ---- Catalog -------------------------------------------------------------

@app.get("/api/catalog")
def catalog():
    items = []
    for t in list_templates():
        items.append({
            **t.model_dump(),
            "register_count": REGISTERS_PER_TYPE[t.data_type],
        })
    return {
        "templates": items,
        "data_types": [d.value for d in DataType],
        "register_types": [r.value for r in RegisterType],
    }


# ---- Config (full read/write) -------------------------------------------

@app.get("/api/config")
def get_config():
    return runtime.config.model_dump(mode="json")


@app.put("/api/config")
def put_config(config: AppConfig):
    return _replace(config).model_dump(mode="json")


# ---- Sensors -------------------------------------------------------------

@app.post("/api/sensors")
def create_sensor(sensor: SensorCreate):
    cfg = runtime.config
    if any(s.id == sensor.id for s in cfg.sensors):
        raise HTTPException(status_code=409, detail="Sensor id already exists")
    new_cfg = cfg.model_copy(deep=True)
    new_cfg.sensors.append(SensorConfig(
        id=sensor.id,
        unit_id=sensor.unit_id,
        base_address=sensor.base_address,
        byte_order=sensor.byte_order,
        word_order=sensor.word_order,
        write_rate_seconds=sensor.write_rate_seconds,
        measurements=[],
    ))
    _replace(new_cfg)
    return _find_sensor(runtime.config, sensor.id).model_dump(mode="json")


@app.put("/api/sensors/{sensor_id}")
def update_sensor(sensor_id: str, patch: SensorUpdate):
    cfg = runtime.config.model_copy(deep=True)
    sensor = _find_sensor(cfg, sensor_id)
    updates = patch.model_dump(exclude_unset=True, exclude_none=True)
    # Rebuild via the model so nested fields (fault) get re-validated as
    # SensorFault, not as a plain dict. model_copy(update=...) bypasses
    # validation on nested models in pydantic v2.
    merged = {**sensor.model_dump(), **updates}
    new_sensor = SensorConfig(**merged)
    cfg.sensors = [new_sensor if s.id == sensor_id else s for s in cfg.sensors]
    _replace(cfg)
    return new_sensor.model_dump(mode="json")


@app.delete("/api/sensors/{sensor_id}")
def delete_sensor(sensor_id: str):
    cfg = runtime.config.model_copy(deep=True)
    cfg.sensors = [s for s in cfg.sensors if s.id != sensor_id]
    _replace(cfg)
    return {"deleted": sensor_id}


# ---- Measurements --------------------------------------------------------

@app.post("/api/sensors/{sensor_id}/measurements")
def create_measurement(sensor_id: str, payload: MeasurementCreate):
    cfg = runtime.config.model_copy(deep=True)
    sensor = _find_sensor(cfg, sensor_id)

    template = get_template(payload.template_name) if payload.template_name else None
    name = payload.name or (template.name if template else None)
    if not name:
        raise HTTPException(status_code=400, detail="Measurement name required")
    if any(m.name == name for m in sensor.measurements):
        raise HTTPException(status_code=409, detail="Measurement name already exists for this sensor")

    base = template.model_dump() if template else {}
    # Drop fields that don't belong to MeasurementConfig.
    for k in ("label", "description", "generator"):
        base.pop(k, None)
    base.update({
        "name": name,
        "offset": payload.offset,
        **{k: v for k, v in payload.model_dump(exclude_unset=True).items()
           if k not in ("template_name", "name") and v is not None},
    })

    measurement = MeasurementConfig(**base)
    # Overlap sanity check — only against measurements in the *same*
    # register_type address space (coil 0 and holding 0 are independent).
    end = measurement.offset + measurement.register_count - 1
    for m in sensor.measurements:
        if m.register_type != measurement.register_type:
            continue
        m_end = m.offset + m.register_count - 1
        if m.offset <= end and measurement.offset <= m_end:
            raise HTTPException(
                status_code=409,
                detail=f"Address overlap with '{m.name}' "
                       f"({m.register_type.value} {m.offset}..{m_end})",
            )
    sensor.measurements.append(measurement)
    _replace(cfg)
    return measurement.model_dump(mode="json")


@app.put("/api/sensors/{sensor_id}/measurements/{name}")
def update_measurement(sensor_id: str, name: str, patch: MeasurementUpdate):
    cfg = runtime.config.model_copy(deep=True)
    sensor = _find_sensor(cfg, sensor_id)
    m = _find_measurement(sensor, name)
    updates = patch.model_dump(exclude_unset=True, exclude_none=True)
    # Rebuild via the model so the nested fault dict is re-validated as
    # MeasurementFault (model_copy(update=...) does NOT re-validate nested
    # models in pydantic v2 -> they would silently remain plain dicts).
    merged = {**m.model_dump(), **updates}
    new_m = MeasurementConfig(**merged)

    # Overlap check after the patch — only against measurements in the
    # same register_type address space (changing offset / register_type /
    # data_type can introduce overlaps).
    new_end = new_m.offset + new_m.register_count - 1
    for x in sensor.measurements:
        if x.name == name:
            continue
        if x.register_type != new_m.register_type:
            continue
        x_end = x.offset + x.register_count - 1
        if x.offset <= new_end and new_m.offset <= x_end:
            raise HTTPException(
                status_code=409,
                detail=f"Address overlap with '{x.name}' "
                       f"({x.register_type.value} {x.offset}..{x_end})",
            )

    sensor.measurements = [new_m if x.name == name else x for x in sensor.measurements]
    _replace(cfg)
    return new_m.model_dump(mode="json")


@app.delete("/api/sensors/{sensor_id}/measurements/{name}")
def delete_measurement(sensor_id: str, name: str):
    cfg = runtime.config.model_copy(deep=True)
    sensor = _find_sensor(cfg, sensor_id)
    sensor.measurements = [m for m in sensor.measurements if m.name != name]
    _replace(cfg)
    return {"deleted": name}


class SpikeRequest(BaseModel):
    value: float
    duration_seconds: float = Field(gt=0)


@app.post("/api/sensors/{sensor_id}/measurements/{name}/spike")
def inject_spike(sensor_id: str, name: str, payload: SpikeRequest):
    """One-shot value override: replace the generated value for N seconds."""
    sensor = _find_sensor(runtime.config, sensor_id)
    _find_measurement(sensor, name)  # 404 if missing
    try:
        runtime.inject_spike(sensor_id, name, payload.value, payload.duration_seconds)
    except RuntimeError as e:
        raise HTTPException(status_code=409, detail=str(e))
    return {
        "sensor_id": sensor_id,
        "name": name,
        "value": payload.value,
        "duration_seconds": payload.duration_seconds,
    }


@app.delete("/api/sensors/{sensor_id}/measurements/{name}/spike")
def clear_spike(sensor_id: str, name: str):
    cleared = runtime.clear_spike(sensor_id, name)
    return {"cleared": cleared}


# ---- Lifecycle controls --------------------------------------------------

@app.post("/api/server/start")
def server_start():
    try:
        runtime.start_server()
    except RuntimeError as e:
        raise HTTPException(status_code=500, detail=str(e))
    return runtime.server_status()


@app.post("/api/server/stop")
def server_stop():
    runtime.stop_server()
    return runtime.server_status()


@app.post("/api/server/kick")
def server_kick():
    """Drop all active TCP connections; clients will receive a close/RST."""
    try:
        runtime.kick_clients()
    except RuntimeError as e:
        raise HTTPException(status_code=409, detail=str(e))
    return runtime.server_status()


@app.post("/api/simulator/start")
def simulator_start():
    ok = runtime.start_simulator()
    if not ok:
        raise HTTPException(status_code=500, detail="Failed to start simulator")
    return runtime.simulator_status()


@app.post("/api/simulator/stop")
def simulator_stop():
    runtime.stop_simulator()
    return runtime.simulator_status()


# ---- Live status ---------------------------------------------------------

@app.get("/api/status")
def status():
    return {
        "server": runtime.server_status(),
        "simulator": runtime.simulator_status(),
        "values": runtime.latest_values(),
    }


@app.get("/api/events")
def events(limit: int = 50):
    return {"events": runtime.recent_events(limit=limit)}


@app.get("/api/slaves")
def slaves():
    """
    Per-slave register dump for debugging. Each slave reports its full
    register bank, plus the sensor/measurement layout that owns each address.
    """
    return {"slaves": runtime.slave_dump()}
