"""
FastAPI app exposing the simulator's plug-and-play web UI and REST API.

Run with:
    uvicorn webui.app:app --host 0.0.0.0 --port 8000
or via the convenience entry point:
    python -m webui

Topology: AppConfig -> servers[] -> sensors[] -> measurements[]. Endpoints
nest accordingly under /api/servers/{server_id}/sensors/{sensor_id}/...
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import List, Optional

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field, ValidationError

from catalog import list_templates, get_template
from models import (
    AppConfig,
    DataType,
    MeasurementConfig,
    MeasurementFault,
    RegisterType,
    SensorConfig,
    SensorFault,
    ServerConfig,
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
# Pydantic request models
# --------------------------------------------------------------------------
class ServerCreate(BaseModel):
    id: str
    label: Optional[str] = None
    description: Optional[str] = None
    host: str = "0.0.0.0"
    port: int = Field(default=502, ge=1, le=65535)
    default_unit_id: int = Field(default=1, ge=0, le=247)
    register_count_min: int = Field(default=16, ge=1)
    auto_start: bool = False


class ServerUpdate(BaseModel):
    label: Optional[str] = None
    description: Optional[str] = None
    host: Optional[str] = None
    port: Optional[int] = Field(default=None, ge=1, le=65535)
    default_unit_id: Optional[int] = Field(default=None, ge=0, le=247)
    register_count_min: Optional[int] = Field(default=None, ge=1)
    auto_start: Optional[bool] = None


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
    template_name: Optional[str] = None
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


class SpikeRequest(BaseModel):
    value: float
    duration_seconds: float = Field(gt=0)


# --------------------------------------------------------------------------
# Helpers
# --------------------------------------------------------------------------

# Fields that require the server to be stopped before they can be edited.
# Live-reloadable fields (faults, scale, ranges, update_rate) are not listed
# and remain editable while the server is running.
_SERVER_STRUCTURAL = {"host", "port", "register_count_min", "default_unit_id"}
_SENSOR_STRUCTURAL = {"unit_id", "base_address", "byte_order", "word_order"}
_MEASUREMENT_STRUCTURAL = {"offset", "register_type", "data_type"}


def _validation_errors_to_json(e: ValidationError) -> List[dict]:
    """
    pydantic v2 ValidationError carries the original Python exception in
    `ctx['error']` for model_validator failures, which is not JSON-serializable.
    Return only the JSON-safe fields.
    """
    out: List[dict] = []
    for err in e.errors():
        out.append({
            "type": err.get("type"),
            "loc": list(err.get("loc", ())),
            "msg": err.get("msg"),
        })
    return out


def _replace(config: AppConfig) -> AppConfig:
    try:
        runtime.replace_config(config)
    except ValidationError as e:
        raise HTTPException(status_code=422, detail=_validation_errors_to_json(e))
    except ValueError as e:
        raise HTTPException(status_code=409, detail=str(e))
    return runtime.config


def _find_server(config: AppConfig, server_id: str) -> ServerConfig:
    s = config.find_server(server_id)
    if s is None:
        raise HTTPException(status_code=404, detail=f"Server not found: {server_id}")
    return s


def _find_sensor(server: ServerConfig, sensor_id: str) -> SensorConfig:
    for s in server.sensors:
        if s.id == sensor_id:
            return s
    raise HTTPException(status_code=404, detail=f"Sensor not found: {sensor_id}")


def _find_measurement(sensor: SensorConfig, name: str) -> MeasurementConfig:
    for m in sensor.measurements:
        if m.name == name:
            return m
    raise HTTPException(status_code=404, detail=f"Measurement not found: {name}")


def _assert_editable(server_id: str, patch_keys: set, structural: set, what: str) -> None:
    """
    Reject structural edits when the server is running. Live-reloadable
    fields (anything not in `structural`) are always allowed.
    """
    if not runtime.is_server_running(server_id):
        return
    blocked = patch_keys & structural
    if blocked:
        raise HTTPException(
            status_code=409,
            detail=(
                f"Server '{server_id}' is running: {what} field(s) "
                f"{sorted(blocked)} cannot be edited live. Stop the server first."
            ),
        )


def _new_config_with(*, mutate) -> AppConfig:
    """
    Deep-copy the live config, apply `mutate(cfg)`, and validate-then-replace.
    `mutate` may raise HTTPException to abort. Pydantic ValidationError raised
    during model construction inside `mutate` (e.g. invalid server id, sensor
    field constraint) is caught here and surfaced as 422.
    """
    cfg = runtime.config.model_copy(deep=True)
    try:
        mutate(cfg)
        validated = AppConfig(**cfg.model_dump())
    except ValidationError as e:
        raise HTTPException(status_code=422, detail=_validation_errors_to_json(e))
    except ValueError as e:
        raise HTTPException(status_code=409, detail=str(e))
    _replace(validated)
    return runtime.config


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


# ---- Servers (CRUD) ------------------------------------------------------

@app.get("/api/servers")
def list_servers():
    return {"servers": [sc.model_dump(mode="json") for sc in runtime.config.servers]}


@app.get("/api/servers/{server_id}")
def get_server(server_id: str):
    sc = _find_server(runtime.config, server_id)
    return sc.model_dump(mode="json")


@app.post("/api/servers")
def create_server(payload: ServerCreate):
    if runtime.config.find_server(payload.id) is not None:
        raise HTTPException(status_code=409, detail=f"Server id already exists: {payload.id}")

    def _mutate(cfg: AppConfig) -> None:
        cfg.servers.append(ServerConfig(
            id=payload.id,
            label=payload.label,
            description=payload.description,
            host=payload.host,
            port=payload.port,
            default_unit_id=payload.default_unit_id,
            register_count_min=payload.register_count_min,
            auto_start=payload.auto_start,
            sensors=[],
        ))

    _new_config_with(mutate=_mutate)
    return runtime.server_status(payload.id)


@app.put("/api/servers/{server_id}")
def update_server(server_id: str, patch: ServerUpdate):
    updates = patch.model_dump(exclude_unset=True, exclude_none=True)
    _assert_editable(server_id, set(updates.keys()), _SERVER_STRUCTURAL, "server")

    def _mutate(cfg: AppConfig) -> None:
        sc = _find_server(cfg, server_id)
        for k, v in updates.items():
            setattr(sc, k, v)

    _new_config_with(mutate=_mutate)
    return runtime.server_status(server_id)


@app.delete("/api/servers/{server_id}")
def delete_server(server_id: str):
    _find_server(runtime.config, server_id)  # 404 if missing
    if runtime.is_server_running(server_id):
        raise HTTPException(
            status_code=409,
            detail=f"Server '{server_id}' is running: stop it before deleting.",
        )

    def _mutate(cfg: AppConfig) -> None:
        cfg.servers = [s for s in cfg.servers if s.id != server_id]

    _new_config_with(mutate=_mutate)
    return {"deleted": server_id}


# ---- Server lifecycle ----------------------------------------------------

@app.post("/api/servers/{server_id}/start")
def start_server(server_id: str):
    _find_server(runtime.config, server_id)
    try:
        runtime.start_server(server_id)
    except RuntimeError as e:
        raise HTTPException(status_code=500, detail=str(e))
    return runtime.server_status(server_id)


@app.post("/api/servers/{server_id}/stop")
def stop_server(server_id: str):
    _find_server(runtime.config, server_id)
    runtime.stop_server(server_id)
    return runtime.server_status(server_id)


@app.post("/api/servers/{server_id}/kick")
def kick_server(server_id: str):
    _find_server(runtime.config, server_id)
    try:
        runtime.kick_server(server_id)
    except RuntimeError as e:
        raise HTTPException(status_code=409, detail=str(e))
    return runtime.server_status(server_id)


@app.post("/api/servers/{server_id}/simulator/start")
def start_simulator(server_id: str):
    _find_server(runtime.config, server_id)
    ok = runtime.start_simulator(server_id)
    if not ok:
        raise HTTPException(status_code=500, detail="Failed to start simulator")
    return runtime.server_status(server_id)


@app.post("/api/servers/{server_id}/simulator/stop")
def stop_simulator(server_id: str):
    _find_server(runtime.config, server_id)
    runtime.stop_simulator(server_id)
    return runtime.server_status(server_id)


# ---- Bulk lifecycle ------------------------------------------------------

@app.post("/api/servers/start-all")
def start_all_servers():
    runtime.start_all_servers()
    return {"servers": runtime.all_servers_status()}


@app.post("/api/servers/stop-all")
def stop_all_servers():
    runtime.stop_all_servers()
    return {"servers": runtime.all_servers_status()}


@app.post("/api/simulator/start-all")
def start_all_simulators():
    runtime.start_all_simulators()
    return {"servers": runtime.all_servers_status()}


@app.post("/api/simulator/stop-all")
def stop_all_simulators():
    runtime.stop_all_simulators()
    return {"servers": runtime.all_servers_status()}


# ---- Sensors (nested under server) ---------------------------------------

@app.post("/api/servers/{server_id}/sensors")
def create_sensor(server_id: str, payload: SensorCreate):
    if runtime.is_server_running(server_id):
        raise HTTPException(
            status_code=409,
            detail=f"Server '{server_id}' is running: stop it before adding sensors.",
        )
    sc = _find_server(runtime.config, server_id)
    if any(s.id == payload.id for s in sc.sensors):
        raise HTTPException(status_code=409, detail="Sensor id already exists in this server")

    def _mutate(cfg: AppConfig) -> None:
        srv = _find_server(cfg, server_id)
        srv.sensors.append(SensorConfig(
            id=payload.id,
            unit_id=payload.unit_id,
            base_address=payload.base_address,
            byte_order=payload.byte_order,
            word_order=payload.word_order,
            write_rate_seconds=payload.write_rate_seconds,
            measurements=[],
        ))

    _new_config_with(mutate=_mutate)
    sc = _find_server(runtime.config, server_id)
    return _find_sensor(sc, payload.id).model_dump(mode="json")


@app.put("/api/servers/{server_id}/sensors/{sensor_id}")
def update_sensor(server_id: str, sensor_id: str, patch: SensorUpdate):
    updates = patch.model_dump(exclude_unset=True, exclude_none=True)
    _assert_editable(server_id, set(updates.keys()), _SENSOR_STRUCTURAL, "sensor")

    def _mutate(cfg: AppConfig) -> None:
        srv = _find_server(cfg, server_id)
        sensor = _find_sensor(srv, sensor_id)
        merged = {**sensor.model_dump(), **updates}
        new_sensor = SensorConfig(**merged)
        srv.sensors = [new_sensor if s.id == sensor_id else s for s in srv.sensors]

    _new_config_with(mutate=_mutate)
    sc = _find_server(runtime.config, server_id)
    return _find_sensor(sc, sensor_id).model_dump(mode="json")


@app.delete("/api/servers/{server_id}/sensors/{sensor_id}")
def delete_sensor(server_id: str, sensor_id: str):
    if runtime.is_server_running(server_id):
        raise HTTPException(
            status_code=409,
            detail=f"Server '{server_id}' is running: stop it before deleting sensors.",
        )

    def _mutate(cfg: AppConfig) -> None:
        srv = _find_server(cfg, server_id)
        srv.sensors = [s for s in srv.sensors if s.id != sensor_id]

    _new_config_with(mutate=_mutate)
    return {"deleted": sensor_id}


# ---- Measurements --------------------------------------------------------

@app.post("/api/servers/{server_id}/sensors/{sensor_id}/measurements")
def create_measurement(server_id: str, sensor_id: str, payload: MeasurementCreate):
    if runtime.is_server_running(server_id):
        raise HTTPException(
            status_code=409,
            detail=f"Server '{server_id}' is running: stop it before adding measurements.",
        )
    sc = _find_server(runtime.config, server_id)
    sensor = _find_sensor(sc, sensor_id)

    template = get_template(payload.template_name) if payload.template_name else None
    name = payload.name or (template.name if template else None)
    if not name:
        raise HTTPException(status_code=400, detail="Measurement name required")
    if any(m.name == name for m in sensor.measurements):
        raise HTTPException(status_code=409, detail="Measurement name already exists for this sensor")

    base = template.model_dump() if template else {}
    for k in ("label", "description", "generator"):
        base.pop(k, None)
    base.update({
        "name": name,
        "offset": payload.offset,
        **{k: v for k, v in payload.model_dump(exclude_unset=True).items()
           if k not in ("template_name", "name") and v is not None},
    })

    def _mutate(cfg: AppConfig) -> None:
        srv = _find_server(cfg, server_id)
        s = _find_sensor(srv, sensor_id)
        s.measurements.append(MeasurementConfig(**base))

    _new_config_with(mutate=_mutate)
    return _find_measurement(
        _find_sensor(_find_server(runtime.config, server_id), sensor_id), name,
    ).model_dump(mode="json")


@app.put("/api/servers/{server_id}/sensors/{sensor_id}/measurements/{name}")
def update_measurement(
    server_id: str, sensor_id: str, name: str, patch: MeasurementUpdate,
):
    updates = patch.model_dump(exclude_unset=True, exclude_none=True)
    _assert_editable(server_id, set(updates.keys()), _MEASUREMENT_STRUCTURAL, "measurement")

    def _mutate(cfg: AppConfig) -> None:
        srv = _find_server(cfg, server_id)
        sensor = _find_sensor(srv, sensor_id)
        m = _find_measurement(sensor, name)
        merged = {**m.model_dump(), **updates}
        new_m = MeasurementConfig(**merged)
        sensor.measurements = [new_m if x.name == name else x for x in sensor.measurements]

    _new_config_with(mutate=_mutate)
    sc = _find_server(runtime.config, server_id)
    sensor = _find_sensor(sc, sensor_id)
    return _find_measurement(sensor, name).model_dump(mode="json")


@app.delete("/api/servers/{server_id}/sensors/{sensor_id}/measurements/{name}")
def delete_measurement(server_id: str, sensor_id: str, name: str):
    if runtime.is_server_running(server_id):
        raise HTTPException(
            status_code=409,
            detail=f"Server '{server_id}' is running: stop it before deleting measurements.",
        )

    def _mutate(cfg: AppConfig) -> None:
        srv = _find_server(cfg, server_id)
        sensor = _find_sensor(srv, sensor_id)
        sensor.measurements = [m for m in sensor.measurements if m.name != name]

    _new_config_with(mutate=_mutate)
    return {"deleted": name}


# ---- Spikes --------------------------------------------------------------

@app.post("/api/servers/{server_id}/sensors/{sensor_id}/measurements/{name}/spike")
def inject_spike(
    server_id: str, sensor_id: str, name: str, payload: SpikeRequest,
):
    sc = _find_server(runtime.config, server_id)
    sensor = _find_sensor(sc, sensor_id)
    _find_measurement(sensor, name)
    try:
        runtime.inject_spike(server_id, sensor_id, name, payload.value, payload.duration_seconds)
    except RuntimeError as e:
        raise HTTPException(status_code=409, detail=str(e))
    return {
        "server_id": server_id,
        "sensor_id": sensor_id,
        "name": name,
        "value": payload.value,
        "duration_seconds": payload.duration_seconds,
    }


@app.delete("/api/servers/{server_id}/sensors/{sensor_id}/measurements/{name}/spike")
def clear_spike(server_id: str, sensor_id: str, name: str):
    cleared = runtime.clear_spike(server_id, sensor_id, name)
    return {"cleared": cleared}


# ---- Live status ---------------------------------------------------------

@app.get("/api/status")
def status():
    return {
        "servers": runtime.all_servers_status(),
        "values": runtime.latest_values(),
    }


@app.get("/api/events")
def events(limit: int = 50):
    return {"events": runtime.recent_events(limit=limit)}


@app.get("/api/slaves")
def slaves():
    """Per-server, per-slave register dump for debugging."""
    return {"servers": runtime.slave_dump()}
