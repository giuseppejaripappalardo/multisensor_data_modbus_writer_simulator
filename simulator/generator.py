"""
Realistic sensor data generator.

Provides specific patterns for known measurement types and a generic
sinusoidal pattern for custom/unknown measurement types.

Two device families are supported with coordinated, physically coherent
generators:

- Siemens SENTRON PAC2200 (power meter trifase): V_LN → V_LL → I → PF → P, Q,
  S → totali → energie integrate. Le grandezze tra fasi sono coordinate per
  emulare un sistema reale (squilibrio piccolo, carico comune).

- Siemens QNA2820D (sensore ambientale IAQ via gateway LoRaWAN→Modbus): T,
  RH, CO2, TVOC, PM2.5, PM10, sound, illuminance — pattern indoor verosimili,
  con correlazione tra CO2/TVOC e tra PM2.5/PM10.

- Eastron SDM120/SDM230 (energy meter monofase): V → I ← P ← curva oraria
  wall-clock (uffici italiano) → PF → Hz → kWh totale (counter monotonico
  Float32 integrato in tempo reale).
"""
import datetime
import hashlib
import math
import random
from typing import Dict, List, Optional

from models import MeasurementConfig
from utils.clamp import clamp
from utils.logging import get_logger

logger = get_logger(__name__)


class SensorGenerator:
    """
    Generates realistic sensor values for a single sensor.

    Each instance has its own RNG seed for reproducibility.
    Known measurement types use specific patterns with correlations.
    Unknown types use a generic sinusoidal + noise pattern.
    """

    def __init__(
        self,
        sensor_id: str,
        measurements: List[MeasurementConfig],
        seed: Optional[int] = None,
    ):
        self.sensor_id = sensor_id
        self._measurements = {m.name: m for m in measurements}

        if seed is None:
            hash_value = hashlib.md5(sensor_id.encode()).hexdigest()
            seed = int(hash_value[:8], 16)

        self.rng = random.Random(seed)

        # State for correlated values (cache for current tick + cumulative counters)
        self._state: Dict[str, float] = {}

        # PM peak event tracking
        self._pm_peak_active = False
        self._pm_peak_start_time = 0.0
        self._pm_peak_duration = 0.0
        self._next_pm_peak_time = self.rng.uniform(60, 120)

        logger.debug(f"Initialized generator for '{sensor_id}' (seed={seed})")

    def generate(self, name: str, time_seconds: float) -> float:
        """Generate a value for the named measurement."""
        config = self._measurements.get(name)
        if config is None:
            logger.warning(f"Unknown measurement '{name}' for sensor '{self.sensor_id}'")
            return 0.0

        gen_fn = _GENERATORS.get(name)
        if gen_fn:
            value = gen_fn(self, time_seconds)
        else:
            value = self._gen_generic(config, time_seconds)

        value = clamp(value, config.min_value, config.max_value)
        self._state[name] = value
        return value

    def generate_all(self, time_seconds: float) -> Dict[str, float]:
        """Generate all measurement values (dependency-ordered)."""
        names = self._dependency_order()
        return {name: self.generate(name, time_seconds) for name in names}

    def _dependency_order(self) -> List[str]:
        """
        Order measurements so dependencies come first. Within a sensor:

        QNA: co2 before tvoc; pm25 before pm10.

        PAC2200: tariff → V_LN per phase → I per phase → PF per phase →
        S/P/Q per phase → averages and totals → energies (which integrate
        the totals in time).
        """
        priorities = {
            # QNA correlations
            "qna_co2": 0, "qna_tvoc": 1,
            "qna_pm25": 0, "qna_pm10": 1,
            # Legacy alias (example.yaml): stesse correlazioni dei qna_*.
            "co2": 0, "tvoc": 1,
            "pm25": 0, "pm10": 1,
            # ACS580: lo stato condiviso (running/speed/fault) deve essere
            # costruito prima di status/fault word e prima dei contatori.
            "acs580_speed": 0, "acs580_frequency": 0,
            "acs580_current": 0, "acs580_torque": 0, "acs580_power": 0,
            "acs580_dc_voltage": 0, "acs580_motor_temp": 0,
            "acs580_status_word": 1, "acs580_fault_word": 1,
            "acs580_run_time": 2, "acs580_kwh_counter": 2,
            "acs580_run_cmd": 1, "acs580_reset_cmd": 1,
            "acs580_ready_status": 1, "acs580_running_status": 1,
            "acs580_fault_status": 1, "acs580_at_setpoint": 1,
            # PAC2200 ordering
            "pac2200_active_tariff": 0,
            "pac2200_v_l1_n": 1, "pac2200_v_l2_n": 1, "pac2200_v_l3_n": 1,
            "pac2200_i_l1": 2, "pac2200_i_l2": 2, "pac2200_i_l3": 2,
            "pac2200_pf_l1": 3, "pac2200_pf_l2": 3, "pac2200_pf_l3": 3,
            "pac2200_s_l1": 4, "pac2200_s_l2": 4, "pac2200_s_l3": 4,
            "pac2200_p_l1": 4, "pac2200_p_l2": 4, "pac2200_p_l3": 4,
            "pac2200_q_l1": 4, "pac2200_q_l2": 4, "pac2200_q_l3": 4,
            "pac2200_v_l1_l2": 5, "pac2200_v_l2_l3": 5, "pac2200_v_l3_l1": 5,
            "pac2200_thd_v_l1": 5, "pac2200_thd_v_l2": 5, "pac2200_thd_v_l3": 5,
            "pac2200_thd_i_l1": 5, "pac2200_thd_i_l2": 5, "pac2200_thd_i_l3": 5,
            "pac2200_frequency": 5,
            "pac2200_v_ln_avg": 6, "pac2200_v_ll_avg": 6, "pac2200_i_avg": 6,
            "pac2200_s_total": 6, "pac2200_p_total": 6, "pac2200_q_total": 6,
            "pac2200_pf_total": 6,
            # Energies last: they read p_total / q_total / s_total
            "pac2200_eact_imp_t1": 7, "pac2200_eact_imp_t2": 7,
            "pac2200_eact_exp_t1": 7, "pac2200_eact_exp_t2": 7,
            "pac2200_ereact_imp_t1": 7, "pac2200_ereact_imp_t2": 7,
            "pac2200_ereact_exp_t1": 7, "pac2200_ereact_exp_t2": 7,
            "pac2200_eapp_t1": 7, "pac2200_eapp_t2": 7,
            # SDM120/SDM230: V/PF/Hz indipendenti, P deriva dalla curva oraria,
            # I deriva da P/V/PF, kWh integra P nel tempo (deve venire dopo).
            "sdm_voltage": 0, "sdm_pf_total": 0, "sdm_frequency": 0,
            "sdm_active_power": 1,
            "sdm_current": 2,
            "sdm_total_active_energy": 3,
        }
        names = list(self._measurements.keys())
        names.sort(key=lambda n: priorities.get(n, 0))
        return names

    # =========================================================================
    # PAC2200: shared state (computed once per tick) for cross-phase coherence
    # =========================================================================

    def _pac2200_ensure_state(self, t: float) -> None:
        """
        Compute and cache the shared per-tick state used by all PAC2200
        generators (load factor, V_LN/I/PF per phase, frequency).

        Ensures that V_L1, V_L2, V_L3 in the same tick agree on their average,
        I follows the load factor with small per-phase imbalance, PF varies
        slowly. P/Q/S are then derived deterministically from these.
        """
        if self._state.get("_pac2200_t") == t:
            return
        self._state["_pac2200_t"] = t

        # Load factor 0..1: long oscillation (10 min) + medium (90 s) + jitter.
        load = 0.45 + 0.25 * math.sin(t / 600.0) + 0.10 * math.sin(t / 90.0)
        load = clamp(load + self.rng.gauss(0, 0.02), 0.05, 0.98)
        self._state["_pac2200_load"] = load

        # V_LN base ~230V with slow ±1.5V flutter and per-phase offset.
        v_base = 230.0 + math.sin(t / 45.0) * 1.5
        self._state["_pac2200_v_ln_1"] = v_base + 0.4 + self.rng.gauss(0, 0.25)
        self._state["_pac2200_v_ln_2"] = v_base - 0.2 + self.rng.gauss(0, 0.25)
        self._state["_pac2200_v_ln_3"] = v_base - 0.3 + self.rng.gauss(0, 0.25)

        # I = load × full-scale (80 A) with small per-phase imbalance.
        full_scale_i = 80.0
        i_base = load * full_scale_i
        self._state["_pac2200_i_1"] = max(0.0, i_base * (1.0 + self.rng.gauss(0, 0.02)) + 0.4)
        self._state["_pac2200_i_2"] = max(0.0, i_base * (1.0 + self.rng.gauss(0, 0.02)))
        self._state["_pac2200_i_3"] = max(0.0, i_base * (1.0 + self.rng.gauss(0, 0.02)) - 0.2)

        # PF: inductive 0.88..0.95, slightly worse at higher load.
        pf_base = 0.94 - load * 0.06
        self._state["_pac2200_pf_1"] = clamp(pf_base + self.rng.gauss(0, 0.005), 0.70, 0.99)
        self._state["_pac2200_pf_2"] = clamp(pf_base + self.rng.gauss(0, 0.005), 0.70, 0.99)
        self._state["_pac2200_pf_3"] = clamp(pf_base + self.rng.gauss(0, 0.005), 0.70, 0.99)

        # P sign: import (positivo) di base, finestre di export (negativo) ~25%
        # del tempo per emulare un impianto con generazione locale (PV/CHP).
        # Periodo 20 min. La corrente resta positiva (modulo); il segno è su P.
        self._state["_pac2200_p_sign"] = 1.0 if math.sin(t / 1200.0) > -0.5 else -1.0

        # Q sign: prevalentemente induttivo (positivo), capacitivo (negativo)
        # in finestre più lunghe (es. rifasamento eccessivo notturno). ~20%
        # del tempo capacitivo. Indipendente dal segno di P.
        self._state["_pac2200_q_sign"] = 1.0 if math.sin(t / 720.0) > -0.6 else -1.0

        # Grid frequency 50 Hz ±0.05 Hz (Europe ENTSO-E).
        self._state["_pac2200_f"] = 50.0 + math.sin(t / 30.0) * 0.03 + self.rng.gauss(0, 0.01)

    def _pac2200_phase_p(self, phase: int) -> float:
        v = self._state[f"_pac2200_v_ln_{phase}"]
        i = self._state[f"_pac2200_i_{phase}"]
        pf = self._state[f"_pac2200_pf_{phase}"]
        sign = self._state.get("_pac2200_p_sign", 1.0)
        return v * i * pf * sign

    def _pac2200_phase_q(self, phase: int) -> float:
        v = self._state[f"_pac2200_v_ln_{phase}"]
        i = self._state[f"_pac2200_i_{phase}"]
        pf = self._state[f"_pac2200_pf_{phase}"]
        # |Q| = sqrt(1-pf²) × |S|. Segno: induttivo (+) o capacitivo (-).
        sin_phi = math.sqrt(max(0.0, 1.0 - pf * pf))
        sign = self._state.get("_pac2200_q_sign", 1.0)
        return v * i * sin_phi * sign

    def _pac2200_phase_s(self, phase: int) -> float:
        v = self._state[f"_pac2200_v_ln_{phase}"]
        i = self._state[f"_pac2200_i_{phase}"]
        return v * i

    def _pac2200_p_total(self) -> float:
        return sum(self._pac2200_phase_p(p) for p in (1, 2, 3))

    def _pac2200_q_total(self) -> float:
        return sum(self._pac2200_phase_q(p) for p in (1, 2, 3))

    def _pac2200_s_total(self) -> float:
        return sum(self._pac2200_phase_s(p) for p in (1, 2, 3))

    def _pac2200_integrate_energy(self, t: float, key: str, rate_per_hour: float) -> float:
        """
        Generic energy counter accumulator.

        Adds rate_per_hour × dt/3600 to the cumulative counter stored under
        key. Implements the PAC2200 overflow at 1.0e+12.
        """
        last_t_key = f"{key}_last_t"
        last_t = self._state.get(last_t_key)
        counter = self._state.get(key, 0.0)

        if last_t is not None:
            dt = t - last_t
            if dt > 0 and rate_per_hour > 0:
                counter += rate_per_hour * dt / 3600.0
                if counter >= 1.0e12:
                    counter -= 1.0e12

        self._state[key] = counter
        self._state[last_t_key] = t
        return counter

    # =========================================================================
    # PAC2200 generators
    # =========================================================================

    def _gen_pac2200_v_ln_1(self, t: float) -> float:
        self._pac2200_ensure_state(t)
        return self._state["_pac2200_v_ln_1"]

    def _gen_pac2200_v_ln_2(self, t: float) -> float:
        self._pac2200_ensure_state(t)
        return self._state["_pac2200_v_ln_2"]

    def _gen_pac2200_v_ln_3(self, t: float) -> float:
        self._pac2200_ensure_state(t)
        return self._state["_pac2200_v_ln_3"]

    def _gen_pac2200_v_ll_12(self, t: float) -> float:
        self._pac2200_ensure_state(t)
        # |V_L1 - V_L2| in a balanced 3φ system is approximately √3 × V_LN avg,
        # with a small stochastic offset from the imbalance.
        avg = (self._state["_pac2200_v_ln_1"] + self._state["_pac2200_v_ln_2"]) / 2.0
        return avg * math.sqrt(3.0) + self.rng.gauss(0, 0.3)

    def _gen_pac2200_v_ll_23(self, t: float) -> float:
        self._pac2200_ensure_state(t)
        avg = (self._state["_pac2200_v_ln_2"] + self._state["_pac2200_v_ln_3"]) / 2.0
        return avg * math.sqrt(3.0) + self.rng.gauss(0, 0.3)

    def _gen_pac2200_v_ll_31(self, t: float) -> float:
        self._pac2200_ensure_state(t)
        avg = (self._state["_pac2200_v_ln_3"] + self._state["_pac2200_v_ln_1"]) / 2.0
        return avg * math.sqrt(3.0) + self.rng.gauss(0, 0.3)

    def _gen_pac2200_i_1(self, t: float) -> float:
        self._pac2200_ensure_state(t)
        return self._state["_pac2200_i_1"]

    def _gen_pac2200_i_2(self, t: float) -> float:
        self._pac2200_ensure_state(t)
        return self._state["_pac2200_i_2"]

    def _gen_pac2200_i_3(self, t: float) -> float:
        self._pac2200_ensure_state(t)
        return self._state["_pac2200_i_3"]

    def _gen_pac2200_s_1(self, t: float) -> float:
        self._pac2200_ensure_state(t)
        return self._pac2200_phase_s(1)

    def _gen_pac2200_s_2(self, t: float) -> float:
        self._pac2200_ensure_state(t)
        return self._pac2200_phase_s(2)

    def _gen_pac2200_s_3(self, t: float) -> float:
        self._pac2200_ensure_state(t)
        return self._pac2200_phase_s(3)

    def _gen_pac2200_p_1(self, t: float) -> float:
        self._pac2200_ensure_state(t)
        return self._pac2200_phase_p(1)

    def _gen_pac2200_p_2(self, t: float) -> float:
        self._pac2200_ensure_state(t)
        return self._pac2200_phase_p(2)

    def _gen_pac2200_p_3(self, t: float) -> float:
        self._pac2200_ensure_state(t)
        return self._pac2200_phase_p(3)

    def _gen_pac2200_q_1(self, t: float) -> float:
        self._pac2200_ensure_state(t)
        return self._pac2200_phase_q(1)

    def _gen_pac2200_q_2(self, t: float) -> float:
        self._pac2200_ensure_state(t)
        return self._pac2200_phase_q(2)

    def _gen_pac2200_q_3(self, t: float) -> float:
        self._pac2200_ensure_state(t)
        return self._pac2200_phase_q(3)

    def _gen_pac2200_pf_1(self, t: float) -> float:
        self._pac2200_ensure_state(t)
        return self._state["_pac2200_pf_1"]

    def _gen_pac2200_pf_2(self, t: float) -> float:
        self._pac2200_ensure_state(t)
        return self._state["_pac2200_pf_2"]

    def _gen_pac2200_pf_3(self, t: float) -> float:
        self._pac2200_ensure_state(t)
        return self._state["_pac2200_pf_3"]

    def _gen_pac2200_thd_v_1(self, t: float) -> float:
        # Tipico rete pulita: 1.5-2.5 %, lente fluttuazioni.
        return 1.8 + math.sin(t / 240.0) * 0.4 + self.rng.gauss(0, 0.05)

    def _gen_pac2200_thd_v_2(self, t: float) -> float:
        return 1.7 + math.sin(t / 240.0 + 0.5) * 0.4 + self.rng.gauss(0, 0.05)

    def _gen_pac2200_thd_v_3(self, t: float) -> float:
        return 1.9 + math.sin(t / 240.0 + 1.0) * 0.4 + self.rng.gauss(0, 0.05)

    def _gen_pac2200_thd_i_1(self, t: float) -> float:
        # THD-I cresce con il carico: a vuoto è alto in % (ma poco rilevante);
        # a pieno carico si stabilizza a 8-15% per carichi misti.
        self._pac2200_ensure_state(t)
        load = self._state["_pac2200_load"]
        # Quando load≈0 il valore in % è ad alta varianza; lo cappiamo.
        base = 12.0 if load > 0.1 else 25.0
        return clamp(base + math.sin(t / 60.0) * 2.0 + self.rng.gauss(0, 0.5), 0.0, 80.0)

    def _gen_pac2200_thd_i_2(self, t: float) -> float:
        self._pac2200_ensure_state(t)
        load = self._state["_pac2200_load"]
        base = 11.5 if load > 0.1 else 24.0
        return clamp(base + math.sin(t / 60.0 + 0.7) * 2.0 + self.rng.gauss(0, 0.5), 0.0, 80.0)

    def _gen_pac2200_thd_i_3(self, t: float) -> float:
        self._pac2200_ensure_state(t)
        load = self._state["_pac2200_load"]
        base = 12.5 if load > 0.1 else 26.0
        return clamp(base + math.sin(t / 60.0 + 1.4) * 2.0 + self.rng.gauss(0, 0.5), 0.0, 80.0)

    def _gen_pac2200_frequency(self, t: float) -> float:
        self._pac2200_ensure_state(t)
        return self._state["_pac2200_f"]

    def _gen_pac2200_v_ln_avg(self, t: float) -> float:
        self._pac2200_ensure_state(t)
        return sum(self._state[f"_pac2200_v_ln_{p}"] for p in (1, 2, 3)) / 3.0

    def _gen_pac2200_v_ll_avg(self, t: float) -> float:
        return self._gen_pac2200_v_ln_avg(t) * math.sqrt(3.0)

    def _gen_pac2200_i_avg(self, t: float) -> float:
        self._pac2200_ensure_state(t)
        return sum(self._state[f"_pac2200_i_{p}"] for p in (1, 2, 3)) / 3.0

    def _gen_pac2200_s_total(self, t: float) -> float:
        self._pac2200_ensure_state(t)
        return self._pac2200_s_total()

    def _gen_pac2200_p_total(self, t: float) -> float:
        self._pac2200_ensure_state(t)
        return self._pac2200_p_total()

    def _gen_pac2200_q_total(self, t: float) -> float:
        self._pac2200_ensure_state(t)
        return self._pac2200_q_total()

    def _gen_pac2200_pf_total(self, t: float) -> float:
        self._pac2200_ensure_state(t)
        s = self._pac2200_s_total()
        if s == 0:
            return 1.0
        return self._pac2200_p_total() / s

    # ----- Stato e tariffa ----------------------------------------------------

    def _gen_pac2200_diagnostics(self, t: float) -> float:
        # Raramente accende il bit 0 (esempio "warning"); altrimenti 0.
        return 1.0 if self.rng.random() < 0.005 else 0.0

    def _gen_pac2200_digital_input(self, t: float) -> float:
        # Bit 0 = stato DI1 (es. tariff switch), commuta ogni 5 min.
        return 1.0 if (int(t / 300.0) % 2 == 0) else 0.0

    def _gen_pac2200_digital_output(self, t: float) -> float:
        return 0.0  # Output di default a 0.

    def _gen_pac2200_active_tariff(self, t: float) -> float:
        # T1 di "giorno" (06:00-22:00), T2 di "notte". Periodo simulato 24 min
        # per facilitare il test (1 sim-min ≈ 1 ora reale).
        sim_hour = (t / 60.0) % 24.0
        return 1.0 if 6.0 <= sim_hour < 22.0 else 2.0

    # ----- Energie integrate --------------------------------------------------

    def _gen_pac2200_e_act_imp_t1(self, t: float) -> float:
        self._pac2200_ensure_state(t)
        tariff = 1 if 6.0 <= ((t / 60.0) % 24.0) < 22.0 else 2
        rate = self._pac2200_p_total() if (tariff == 1 and self._pac2200_p_total() > 0) else 0.0
        return self._pac2200_integrate_energy(t, "_pac2200_e_act_imp_t1", rate)

    def _gen_pac2200_e_act_imp_t2(self, t: float) -> float:
        self._pac2200_ensure_state(t)
        tariff = 1 if 6.0 <= ((t / 60.0) % 24.0) < 22.0 else 2
        rate = self._pac2200_p_total() if (tariff == 2 and self._pac2200_p_total() > 0) else 0.0
        return self._pac2200_integrate_energy(t, "_pac2200_e_act_imp_t2", rate)

    def _gen_pac2200_e_act_exp_t1(self, t: float) -> float:
        self._pac2200_ensure_state(t)
        tariff = 1 if 6.0 <= ((t / 60.0) % 24.0) < 22.0 else 2
        p = self._pac2200_p_total()
        rate = -p if (tariff == 1 and p < 0) else 0.0
        return self._pac2200_integrate_energy(t, "_pac2200_e_act_exp_t1", rate)

    def _gen_pac2200_e_act_exp_t2(self, t: float) -> float:
        self._pac2200_ensure_state(t)
        tariff = 1 if 6.0 <= ((t / 60.0) % 24.0) < 22.0 else 2
        p = self._pac2200_p_total()
        rate = -p if (tariff == 2 and p < 0) else 0.0
        return self._pac2200_integrate_energy(t, "_pac2200_e_act_exp_t2", rate)

    def _gen_pac2200_e_react_imp_t1(self, t: float) -> float:
        self._pac2200_ensure_state(t)
        tariff = 1 if 6.0 <= ((t / 60.0) % 24.0) < 22.0 else 2
        rate = self._pac2200_q_total() if (tariff == 1 and self._pac2200_q_total() > 0) else 0.0
        return self._pac2200_integrate_energy(t, "_pac2200_e_react_imp_t1", rate)

    def _gen_pac2200_e_react_imp_t2(self, t: float) -> float:
        self._pac2200_ensure_state(t)
        tariff = 1 if 6.0 <= ((t / 60.0) % 24.0) < 22.0 else 2
        rate = self._pac2200_q_total() if (tariff == 2 and self._pac2200_q_total() > 0) else 0.0
        return self._pac2200_integrate_energy(t, "_pac2200_e_react_imp_t2", rate)

    def _gen_pac2200_e_react_exp_t1(self, t: float) -> float:
        self._pac2200_ensure_state(t)
        tariff = 1 if 6.0 <= ((t / 60.0) % 24.0) < 22.0 else 2
        q = self._pac2200_q_total()
        rate = -q if (tariff == 1 and q < 0) else 0.0
        return self._pac2200_integrate_energy(t, "_pac2200_e_react_exp_t1", rate)

    def _gen_pac2200_e_react_exp_t2(self, t: float) -> float:
        self._pac2200_ensure_state(t)
        tariff = 1 if 6.0 <= ((t / 60.0) % 24.0) < 22.0 else 2
        q = self._pac2200_q_total()
        rate = -q if (tariff == 2 and q < 0) else 0.0
        return self._pac2200_integrate_energy(t, "_pac2200_e_react_exp_t2", rate)

    def _gen_pac2200_e_app_t1(self, t: float) -> float:
        self._pac2200_ensure_state(t)
        tariff = 1 if 6.0 <= ((t / 60.0) % 24.0) < 22.0 else 2
        rate = self._pac2200_s_total() if tariff == 1 else 0.0
        return self._pac2200_integrate_energy(t, "_pac2200_e_app_t1", rate)

    def _gen_pac2200_e_app_t2(self, t: float) -> float:
        self._pac2200_ensure_state(t)
        tariff = 1 if 6.0 <= ((t / 60.0) % 24.0) < 22.0 else 2
        rate = self._pac2200_s_total() if tariff == 2 else 0.0
        return self._pac2200_integrate_energy(t, "_pac2200_e_app_t2", rate)

    # =========================================================================
    # QNA2820D generators (indoor IAQ)
    # =========================================================================

    def _gen_qna_temperature(self, t: float) -> float:
        # Indoor controllata: ~22-24 °C, lieve rumore.
        return 23.0 + math.sin(t / 1800.0) * 1.2 + self.rng.gauss(0, 0.1)

    def _gen_qna_humidity(self, t: float) -> float:
        # Indoor: 45-60 %RH tipico.
        return 52.0 + math.sin(t / 2400.0) * 5.0 + self.rng.gauss(0, 0.5)

    def _gen_qna_co2(self, t: float) -> float:
        # Indoor occupato: base 450 ppm, picchi a 800-1100 in occupazione.
        # Pattern di occupazione lento (period 30 min) per simulare presenza.
        occupancy = max(0.0, math.sin(t / 1800.0))
        return 450.0 + occupancy * 600.0 + self.rng.gauss(0, 15)

    def _gen_qna_tvoc(self, t: float) -> float:
        # Correlato al CO2 (entrambi cresceranno con presenza umana). Tollera
        # sia la chiave nuova ("qna_co2") sia quella legacy ("co2"): un sensore
        # configurato con i nomi generici di example.yaml si correla comunque.
        co2 = self._state.get("qna_co2", self._state.get("co2", 450.0))
        return 80.0 + (co2 - 400.0) * 0.5 + self.rng.gauss(0, 20)

    def _gen_qna_pm25(self, t: float) -> float:
        base = 8.0 + self.rng.gauss(0, 1.5)
        peak = 0.0
        if self._pm_peak_active:
            elapsed = t - self._pm_peak_start_time
            if elapsed > self._pm_peak_duration:
                self._pm_peak_active = False
                self._next_pm_peak_time = t + self.rng.uniform(900, 1800)  # 15-30 min
            else:
                center = self._pm_peak_duration / 2
                intensity = 25 + self.rng.uniform(0, 15)
                sigma = self._pm_peak_duration / 4
                peak = intensity * math.exp(-((elapsed - center) ** 2) / (2 * sigma ** 2))
        elif t >= self._next_pm_peak_time:
            self._pm_peak_active = True
            self._pm_peak_start_time = t
            self._pm_peak_duration = self.rng.uniform(180, 360)  # 3-6 min
        return max(0.0, base + peak)

    def _gen_qna_pm10(self, t: float) -> float:
        # Stessa correlazione del TVOC: accetta anche la chiave legacy "pm25".
        pm25 = self._state.get("qna_pm25", self._state.get("pm25", 8.0))
        return pm25 + self.rng.uniform(2, 8)

    def _gen_qna_sound(self, t: float) -> float:
        # Indoor ufficio: 40-55 dB. Picchi corti per attività.
        base = 42.0 + math.sin(t / 600.0) * 4.0
        burst = 8.0 if self.rng.random() < 0.05 else 0.0
        return clamp(base + burst + self.rng.gauss(0, 1.0), 35.0, 100.0)

    # =========================================================================
    # ABB ACS580 generators (variatore di frequenza)
    # =========================================================================

    def _acs580_ensure_state(self, t: float) -> None:
        """
        Cache shared state for the drive (running flag, speed, derived
        electrical quantities). Modello semplificato: il drive lavora a cicli
        di 10 minuti — 8 minuti di run con rampa + plateau + rampa giù, poi
        2 minuti di stop. Una volta su 50 cicli inietta un fault breve.
        """
        if self._state.get("_acs580_t") == t:
            return
        self._state["_acs580_t"] = t

        cycle_period = 600.0  # 10 minuti
        run_fraction = 0.85   # 85% del ciclo in run
        in_cycle = t % cycle_period
        run_window = cycle_period * run_fraction
        ramp = 30.0  # 30s rampa di salita e discesa

        running = in_cycle < run_window
        if running:
            if in_cycle < ramp:
                speed_pu = in_cycle / ramp
            elif in_cycle > run_window - ramp:
                speed_pu = max(0.0, (run_window - in_cycle) / ramp)
            else:
                speed_pu = 1.0
        else:
            speed_pu = 0.0

        # Direzione: positiva di base, negativa raramente (2 cicli su 10).
        direction = -1.0 if (int(t / cycle_period) % 5 == 4) else 1.0
        # Velocità nominale 1450 RPM (motore 4 poli 50 Hz con scivolamento).
        speed = direction * speed_pu * 1450.0
        if running:
            speed += self.rng.gauss(0, 4.0)

        self._state["_acs580_running"] = running
        self._state["_acs580_speed"] = speed

        # Frequenza segue la velocità (50 Hz a 1500 RPM ideali).
        self._state["_acs580_frequency"] = speed / 30.0  # rpm/30 = Hz approx

        # Corrente: a vuoto ~5 A, a pieno carico ~22 A. Sempre positiva.
        load = abs(speed_pu)
        i_base = 4.5 + load * 18.0
        self._state["_acs580_current"] = max(0.2, i_base + self.rng.gauss(0, 0.3))

        # Coppia in % (signed): segue load × direction.
        torque = load * 78.0 * direction + self.rng.gauss(0, 1.5) if running else 0.0
        self._state["_acs580_torque"] = torque

        # Potenza meccanica: P = T × ω = (T% × T_nom) × (rpm × 2π/60). Qui
        # semplifichiamo: P_kW ≈ (torque% / 100) × (|speed| / 1500) × P_nom.
        p_nom = 11.0  # kW motore tipico ACS580 piccolo
        power = (torque / 100.0) * (abs(speed) / 1500.0) * p_nom
        self._state["_acs580_power"] = power

        # DC bus: 565 V tipico, sale a 700+ in frenata (regen).
        regen = power < -0.5
        dc = 760.0 if regen else 565.0
        self._state["_acs580_dc_voltage"] = dc + self.rng.gauss(0, 1.5)

        # Temperatura motore: parte da 35°C, sale con load, scende a stop.
        prev_temp = self._state.get("_acs580_motor_temp_last", 35.0)
        target = 35.0 + load * 55.0  # 35°C a vuoto, 90°C a pieno carico
        # Tau ≈ 5 minuti
        prev_t = self._state.get("_acs580_temp_last_t", t)
        dt = max(0.0, t - prev_t)
        tau = 300.0
        new_temp = prev_temp + (target - prev_temp) * (1.0 - math.exp(-dt / tau)) \
            + self.rng.gauss(0, 0.2)
        self._state["_acs580_motor_temp"] = new_temp
        self._state["_acs580_motor_temp_last"] = new_temp
        self._state["_acs580_temp_last_t"] = t

        # Fault injection raro: ~0.5% probabilità per tick durante run.
        prev_fault = self._state.get("_acs580_fault_active", False)
        fault_until = self._state.get("_acs580_fault_until", 0.0)
        if prev_fault and t < fault_until:
            fault_active = True
        elif running and self.rng.random() < 0.001:
            fault_active = True
            fault_until = t + self.rng.uniform(8.0, 20.0)
            self._state["_acs580_fault_until"] = fault_until
            # Sceglie un bit di fault casuale (b0..b7).
            self._state["_acs580_fault_bit"] = self.rng.randrange(0, 8)
        else:
            fault_active = False
            self._state.pop("_acs580_fault_bit", None)
        self._state["_acs580_fault_active"] = fault_active

    def _gen_acs580_speed(self, t: float) -> float:
        self._acs580_ensure_state(t)
        return self._state["_acs580_speed"]

    def _gen_acs580_frequency(self, t: float) -> float:
        self._acs580_ensure_state(t)
        return self._state["_acs580_frequency"]

    def _gen_acs580_current(self, t: float) -> float:
        self._acs580_ensure_state(t)
        return self._state["_acs580_current"]

    def _gen_acs580_dc_voltage(self, t: float) -> float:
        self._acs580_ensure_state(t)
        return self._state["_acs580_dc_voltage"]

    def _gen_acs580_torque(self, t: float) -> float:
        self._acs580_ensure_state(t)
        return self._state["_acs580_torque"]

    def _gen_acs580_power(self, t: float) -> float:
        self._acs580_ensure_state(t)
        return self._state["_acs580_power"]

    def _gen_acs580_motor_temp(self, t: float) -> float:
        self._acs580_ensure_state(t)
        return self._state["_acs580_motor_temp"]

    def _gen_acs580_run_time(self, t: float) -> float:
        """Run time cumulativo in ore (incrementa solo quando il drive è in run)."""
        self._acs580_ensure_state(t)
        last_t = self._state.get("_acs580_run_time_last_t")
        counter = self._state.get("_acs580_run_time", 12345.0)  # parte da un valore plausibile
        if last_t is not None:
            dt = t - last_t
            if dt > 0 and self._state.get("_acs580_running", False):
                counter += dt / 3600.0
        self._state["_acs580_run_time"] = counter
        self._state["_acs580_run_time_last_t"] = t
        return counter

    def _gen_acs580_kwh_counter(self, t: float) -> float:
        """Energia totale cumulativa in kWh (integra |power|)."""
        self._acs580_ensure_state(t)
        last_t = self._state.get("_acs580_kwh_last_t")
        counter = self._state.get("_acs580_kwh_counter", 5678.0)
        if last_t is not None:
            dt = t - last_t
            power = self._state.get("_acs580_power", 0.0)
            if dt > 0 and power > 0:
                counter += power * dt / 3600.0
        self._state["_acs580_kwh_counter"] = counter
        self._state["_acs580_kwh_last_t"] = t
        return counter

    def _gen_acs580_status_word(self, t: float) -> float:
        """Bitmask: b0=Ready, b1=Enabled, b2=Running, b3=Faulted, b4=AtSetpoint, b5=Reverse."""
        self._acs580_ensure_state(t)
        running = self._state.get("_acs580_running", False)
        fault = self._state.get("_acs580_fault_active", False)
        speed = self._state.get("_acs580_speed", 0.0)
        bits = 0
        if not fault:
            bits |= 1 << 0  # Ready
            bits |= 1 << 1  # Enabled
        if running and not fault:
            bits |= 1 << 2  # Running
        if fault:
            bits |= 1 << 3  # Faulted
        if running and not fault and abs(speed) > 50:
            bits |= 1 << 4  # At setpoint (semplificazione)
        if speed < 0:
            bits |= 1 << 5  # Reverse
        return float(bits)

    def _gen_acs580_fault_word(self, t: float) -> float:
        self._acs580_ensure_state(t)
        if not self._state.get("_acs580_fault_active", False):
            return 0.0
        bit = self._state.get("_acs580_fault_bit", 0)
        return float(1 << bit)

    def _gen_acs580_run_cmd(self, t: float) -> float:
        """COIL R/W: emula un comando run che il consumer scrive. Qui rispecchia
        lo stato running interno per dare un valore "vivo" sulla coil."""
        self._acs580_ensure_state(t)
        return 1.0 if self._state.get("_acs580_running", False) else 0.0

    def _gen_acs580_reset_cmd(self, t: float) -> float:
        """COIL R/W (pulse): valore di default 0; lo SCADA lo alza per resettare."""
        return 0.0

    def _gen_acs580_ready_status(self, t: float) -> float:
        self._acs580_ensure_state(t)
        return 0.0 if self._state.get("_acs580_fault_active", False) else 1.0

    def _gen_acs580_running_status(self, t: float) -> float:
        self._acs580_ensure_state(t)
        running = self._state.get("_acs580_running", False)
        fault = self._state.get("_acs580_fault_active", False)
        return 1.0 if (running and not fault) else 0.0

    def _gen_acs580_fault_status(self, t: float) -> float:
        self._acs580_ensure_state(t)
        return 1.0 if self._state.get("_acs580_fault_active", False) else 0.0

    def _gen_acs580_at_setpoint(self, t: float) -> float:
        self._acs580_ensure_state(t)
        running = self._state.get("_acs580_running", False)
        fault = self._state.get("_acs580_fault_active", False)
        speed = self._state.get("_acs580_speed", 0.0)
        return 1.0 if (running and not fault and abs(speed) > 50) else 0.0

    def _gen_qna_illuminance(self, t: float) -> float:
        # Pattern giorno/notte 24 sim-min: 0-1500 lx con luce diurna,
        # 0-100 lx con illuminazione artificiale.
        sim_hour = (t / 60.0) % 24.0
        if 7.0 <= sim_hour < 19.0:
            # Day: smooth bell from 7:00 to 19:00 peaking around 13:00.
            phase = (sim_hour - 7.0) / 12.0  # 0..1
            day_factor = math.sin(phase * math.pi)
            return 50.0 + day_factor * 1300.0 + self.rng.gauss(0, 30)
        else:
            return 30.0 + self.rng.gauss(0, 10)

    # =========================================================================
    # Eastron SDM120 / SDM230 — Energy Meter monofase (wall-clock office curve)
    # =========================================================================

    # Curva di carico tipica edificio uffici italiano. Tuple (ora_inizio, kW_base).
    # L'ora finale si chiude implicitamente con il primo elemento (24h cyclic).
    _SDM_OFFICE_LOAD_CURVE = (
        (0.0, 1.0),    # 00-06: minimi notturni (server, frigo, luci emergenza)
        (6.0, 1.5),    # 06-08: rampa di accensione progressiva
        (8.0, 8.5),    # 08-13: pieno regime mattina
        (13.0, 5.0),   # 13-14: pausa pranzo (carichi parziali)
        (14.0, 9.0),   # 14-18: pieno regime pomeriggio
        (18.0, 3.0),   # 18-22: spegnimento progressivo
        (22.0, 1.0),   # 22-24: ritorno a base notturna
    )

    @classmethod
    def _sdm_load_kw_for_hour(cls, hour: float) -> float:
        """Interpola linearmente la curva oraria per una data ora del giorno (0..24)."""
        curve = cls._SDM_OFFICE_LOAD_CURVE
        for i, (h0, kw0) in enumerate(curve):
            h1 = curve[(i + 1) % len(curve)][0]
            kw1 = curve[(i + 1) % len(curve)][1]
            if h1 <= h0:  # wrap mezzanotte
                h1 += 24.0
            h_check = hour if hour >= h0 else hour + 24.0
            if h0 <= h_check < h1:
                frac = (h_check - h0) / (h1 - h0)
                return kw0 + (kw1 - kw0) * frac
        return curve[0][1]  # fallback

    def _sdm_ensure_state(self, t: float) -> None:
        """
        Cache shared SDM state per tick: P (W), V, PF, Hz.

        La curva di carico è basata sull'ora del giorno wall-clock (datetime.now).
        Il counter kWh viene poi integrato usando dt dal `t` simulato.
        """
        if self._state.get("_sdm_t") == t:
            return
        self._state["_sdm_t"] = t

        now = datetime.datetime.now()
        hour_of_day = now.hour + now.minute / 60.0 + now.second / 3600.0
        base_kw = self._sdm_load_kw_for_hour(hour_of_day)
        # Jitter ±10% (gauss) + rumore sub-secondo per realismo.
        jitter = 1.0 + self.rng.gauss(0, 0.10)
        sub_minute = math.sin(t / 23.0) * 0.04
        load_kw = max(0.0, base_kw * jitter * (1.0 + sub_minute))

        self._state["_sdm_load_kw"] = load_kw
        self._state["_sdm_active_power_w"] = load_kw * 1000.0
        self._state["_sdm_v"] = 230.0 + math.sin(t / 60.0) * 1.2 + self.rng.gauss(0, 0.3)
        # Uffici: PF leggermente induttivo, peggiora un po' a basso carico
        # (effetto dei carichi capacitivi/UPS senza compensazione).
        pf_base = 0.95 - max(0.0, (2.0 - load_kw)) * 0.02
        self._state["_sdm_pf"] = clamp(pf_base + self.rng.gauss(0, 0.005), 0.80, 0.99)
        self._state["_sdm_f"] = 50.0 + math.sin(t / 30.0) * 0.03 + self.rng.gauss(0, 0.01)

    def _gen_sdm_voltage(self, t: float) -> float:
        self._sdm_ensure_state(t)
        return self._state["_sdm_v"]

    def _gen_sdm_pf_total(self, t: float) -> float:
        self._sdm_ensure_state(t)
        return self._state["_sdm_pf"]

    def _gen_sdm_frequency(self, t: float) -> float:
        self._sdm_ensure_state(t)
        return self._state["_sdm_f"]

    def _gen_sdm_active_power(self, t: float) -> float:
        self._sdm_ensure_state(t)
        return self._state["_sdm_active_power_w"]

    def _gen_sdm_current(self, t: float) -> float:
        self._sdm_ensure_state(t)
        v = self._state["_sdm_v"]
        pf = self._state["_sdm_pf"]
        p = self._state["_sdm_active_power_w"]
        denom = v * pf
        return p / denom if denom > 1e-3 else 0.0

    def _gen_sdm_total_active_energy(self, t: float) -> float:
        """
        Counter Float32 monotonico crescente (kWh). Inizializzato a 12000.0
        per simulare un contatore in uso da circa un anno. Integra la
        potenza attiva istantanea: ΔE [kWh] = P [kW] × Δt [h].
        """
        self._sdm_ensure_state(t)
        last_t = self._state.get("_sdm_kwh_last_t")
        counter = self._state.get("_sdm_kwh_counter", 12000.0)
        if last_t is not None:
            dt = t - last_t
            load_kw = self._state.get("_sdm_load_kw", 0.0)
            if dt > 0 and load_kw > 0:
                counter += load_kw * dt / 3600.0
        self._state["_sdm_kwh_counter"] = counter
        self._state["_sdm_kwh_last_t"] = t
        return counter

    # =========================================================================
    # Generic fallback
    # =========================================================================

    def _gen_generic(self, config: MeasurementConfig, t: float) -> float:
        """Sinusoidal + noise pattern based on configured range."""
        mid = (config.min_value + config.max_value) / 2
        amplitude = (config.max_value - config.min_value) * 0.15
        noise_std = amplitude * 0.05
        period = 120.0 + (hash(config.name) % 180)
        return mid + math.sin(t / period) * amplitude + self.rng.gauss(0, noise_std)


# =============================================================================
# Generator name → method mapping (used by SensorGenerator.generate)
# =============================================================================
_GENERATORS = {
    # PAC2200
    "pac2200_v_l1_n":      lambda self, t: self._gen_pac2200_v_ln_1(t),
    "pac2200_v_l2_n":      lambda self, t: self._gen_pac2200_v_ln_2(t),
    "pac2200_v_l3_n":      lambda self, t: self._gen_pac2200_v_ln_3(t),
    "pac2200_v_l1_l2":     lambda self, t: self._gen_pac2200_v_ll_12(t),
    "pac2200_v_l2_l3":     lambda self, t: self._gen_pac2200_v_ll_23(t),
    "pac2200_v_l3_l1":     lambda self, t: self._gen_pac2200_v_ll_31(t),
    "pac2200_i_l1":        lambda self, t: self._gen_pac2200_i_1(t),
    "pac2200_i_l2":        lambda self, t: self._gen_pac2200_i_2(t),
    "pac2200_i_l3":        lambda self, t: self._gen_pac2200_i_3(t),
    "pac2200_s_l1":        lambda self, t: self._gen_pac2200_s_1(t),
    "pac2200_s_l2":        lambda self, t: self._gen_pac2200_s_2(t),
    "pac2200_s_l3":        lambda self, t: self._gen_pac2200_s_3(t),
    "pac2200_p_l1":        lambda self, t: self._gen_pac2200_p_1(t),
    "pac2200_p_l2":        lambda self, t: self._gen_pac2200_p_2(t),
    "pac2200_p_l3":        lambda self, t: self._gen_pac2200_p_3(t),
    "pac2200_q_l1":        lambda self, t: self._gen_pac2200_q_1(t),
    "pac2200_q_l2":        lambda self, t: self._gen_pac2200_q_2(t),
    "pac2200_q_l3":        lambda self, t: self._gen_pac2200_q_3(t),
    "pac2200_pf_l1":       lambda self, t: self._gen_pac2200_pf_1(t),
    "pac2200_pf_l2":       lambda self, t: self._gen_pac2200_pf_2(t),
    "pac2200_pf_l3":       lambda self, t: self._gen_pac2200_pf_3(t),
    "pac2200_thd_v_l1":    lambda self, t: self._gen_pac2200_thd_v_1(t),
    "pac2200_thd_v_l2":    lambda self, t: self._gen_pac2200_thd_v_2(t),
    "pac2200_thd_v_l3":    lambda self, t: self._gen_pac2200_thd_v_3(t),
    "pac2200_thd_i_l1":    lambda self, t: self._gen_pac2200_thd_i_1(t),
    "pac2200_thd_i_l2":    lambda self, t: self._gen_pac2200_thd_i_2(t),
    "pac2200_thd_i_l3":    lambda self, t: self._gen_pac2200_thd_i_3(t),
    "pac2200_frequency":   lambda self, t: self._gen_pac2200_frequency(t),
    "pac2200_v_ln_avg":    lambda self, t: self._gen_pac2200_v_ln_avg(t),
    "pac2200_v_ll_avg":    lambda self, t: self._gen_pac2200_v_ll_avg(t),
    "pac2200_i_avg":       lambda self, t: self._gen_pac2200_i_avg(t),
    "pac2200_s_total":     lambda self, t: self._gen_pac2200_s_total(t),
    "pac2200_p_total":     lambda self, t: self._gen_pac2200_p_total(t),
    "pac2200_q_total":     lambda self, t: self._gen_pac2200_q_total(t),
    "pac2200_pf_total":    lambda self, t: self._gen_pac2200_pf_total(t),
    "pac2200_diagnostics":     lambda self, t: self._gen_pac2200_diagnostics(t),
    "pac2200_digital_input":   lambda self, t: self._gen_pac2200_digital_input(t),
    "pac2200_digital_output":  lambda self, t: self._gen_pac2200_digital_output(t),
    "pac2200_active_tariff":   lambda self, t: self._gen_pac2200_active_tariff(t),
    "pac2200_eact_imp_t1":     lambda self, t: self._gen_pac2200_e_act_imp_t1(t),
    "pac2200_eact_imp_t2":     lambda self, t: self._gen_pac2200_e_act_imp_t2(t),
    "pac2200_eact_exp_t1":     lambda self, t: self._gen_pac2200_e_act_exp_t1(t),
    "pac2200_eact_exp_t2":     lambda self, t: self._gen_pac2200_e_act_exp_t2(t),
    "pac2200_ereact_imp_t1":   lambda self, t: self._gen_pac2200_e_react_imp_t1(t),
    "pac2200_ereact_imp_t2":   lambda self, t: self._gen_pac2200_e_react_imp_t2(t),
    "pac2200_ereact_exp_t1":   lambda self, t: self._gen_pac2200_e_react_exp_t1(t),
    "pac2200_ereact_exp_t2":   lambda self, t: self._gen_pac2200_e_react_exp_t2(t),
    "pac2200_eapp_t1":         lambda self, t: self._gen_pac2200_e_app_t1(t),
    "pac2200_eapp_t2":         lambda self, t: self._gen_pac2200_e_app_t2(t),
    # QNA2820D
    "qna_temperature":  lambda self, t: self._gen_qna_temperature(t),
    "qna_humidity":     lambda self, t: self._gen_qna_humidity(t),
    "qna_co2":          lambda self, t: self._gen_qna_co2(t),
    "qna_tvoc":         lambda self, t: self._gen_qna_tvoc(t),
    "qna_pm25":         lambda self, t: self._gen_qna_pm25(t),
    "qna_pm10":         lambda self, t: self._gen_qna_pm10(t),
    "qna_sound":        lambda self, t: self._gen_qna_sound(t),
    "qna_illuminance":  lambda self, t: self._gen_qna_illuminance(t),
    # Legacy aliases (mantengono il pattern realistico per i nomi generici
    # usati in configs/example.yaml e in eventuali runtime.yaml più vecchi).
    "temperature":      lambda self, t: self._gen_qna_temperature(t),
    "humidity":         lambda self, t: self._gen_qna_humidity(t),
    "co2":              lambda self, t: self._gen_qna_co2(t),
    "tvoc":             lambda self, t: self._gen_qna_tvoc(t),
    "pm25":             lambda self, t: self._gen_qna_pm25(t),
    "pm10":             lambda self, t: self._gen_qna_pm10(t),
    "sound":            lambda self, t: self._gen_qna_sound(t),
    "illuminance":      lambda self, t: self._gen_qna_illuminance(t),
    # ABB ACS580 (variatore di frequenza)
    "acs580_speed":          lambda self, t: self._gen_acs580_speed(t),
    "acs580_frequency":      lambda self, t: self._gen_acs580_frequency(t),
    "acs580_current":        lambda self, t: self._gen_acs580_current(t),
    "acs580_dc_voltage":     lambda self, t: self._gen_acs580_dc_voltage(t),
    "acs580_torque":         lambda self, t: self._gen_acs580_torque(t),
    "acs580_power":          lambda self, t: self._gen_acs580_power(t),
    "acs580_motor_temp":     lambda self, t: self._gen_acs580_motor_temp(t),
    "acs580_run_time":       lambda self, t: self._gen_acs580_run_time(t),
    "acs580_kwh_counter":    lambda self, t: self._gen_acs580_kwh_counter(t),
    "acs580_status_word":    lambda self, t: self._gen_acs580_status_word(t),
    "acs580_fault_word":     lambda self, t: self._gen_acs580_fault_word(t),
    "acs580_run_cmd":        lambda self, t: self._gen_acs580_run_cmd(t),
    "acs580_reset_cmd":      lambda self, t: self._gen_acs580_reset_cmd(t),
    "acs580_ready_status":   lambda self, t: self._gen_acs580_ready_status(t),
    "acs580_running_status": lambda self, t: self._gen_acs580_running_status(t),
    "acs580_fault_status":   lambda self, t: self._gen_acs580_fault_status(t),
    "acs580_at_setpoint":    lambda self, t: self._gen_acs580_at_setpoint(t),
    # Eastron SDM120/SDM230 (energy meter monofase)
    "sdm_voltage":              lambda self, t: self._gen_sdm_voltage(t),
    "sdm_current":              lambda self, t: self._gen_sdm_current(t),
    "sdm_active_power":         lambda self, t: self._gen_sdm_active_power(t),
    "sdm_pf_total":             lambda self, t: self._gen_sdm_pf_total(t),
    "sdm_frequency":            lambda self, t: self._gen_sdm_frequency(t),
    "sdm_total_active_energy":  lambda self, t: self._gen_sdm_total_active_energy(t),
}
