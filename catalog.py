"""
Catalog of measurement templates per device.

Ogni template descrive UNA misura di un device reale, con tutte le informazioni
che il consumer (gateway / SCADA / PLC) deve sapere per leggerla via Modbus:
indirizzo del registro, data type, byte/word order, scale factor, unità.

La label include indirizzo + data type + endianness + scale, così appare già
completa nel dropdown della UI del simulatore.

Convenzione di scale (lato consumer)
------------------------------------
Il simulatore scrive nel registro il valore "finito" così come fa un device
fisico reale: NON applica nessuna scalatura interna. Lo scale è solo metadata
documentale per il consumer.

Formula di lettura lato consumer:

    valore_ingegneristico = valore_registro / scale

Casi:
  - scale = 1.0  → il valore di registro è già in unità ingegneristiche
                   (V, A, W, Hz, Wh, °C, ppm, ...). Nessuna conversione da fare.
  - scale = 10   → divide per 10 (es. registro 253 = 25.3 °C su INT16 scale=10).
  - scale = 100  → divide per 100 (es. registro 5000 = 50.00 Hz).

I due device qui replicati (PAC2200 e QNA2820D in FLOAT32 verbose) usano tutti
**scale = 1.0**, quindi il consumer non deve applicare alcuna conversione: il
valore letto dal registro è già nelle unità documentate dal campo `unit`.

Device coperti:
  - Siemens SENTRON PAC2200 (7KM2200-2EA00)
  - Siemens QNA2820D (Symaro IAQ multi-sensor LoRaWAN, esposto via gateway in
    convenzione "FLOAT32 verbose": registro contiene il valore in unità
    ingegneristiche, scale=1.0)

Riferimenti:
  - Manuale 7KM PAC2200, doc. Siemens L1V30415167B-06 (12/2022)
  - Datasheet QNA2..D, doc. Siemens A6V13589454
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
    # NB: il dispatch del generator avviene in simulator.generator._GENERATORS
    # mappato per `name` della misura. Non c'è quindi un campo `generator` qui:
    # ogni template è auto-referenziale (la sua generazione segue il `name`).


# ============================================================================
# Siemens SENTRON PAC2200 (7KM2200-2EA00 e varianti)
# ============================================================================
# Power meter trifase 96x96 mm, Modbus TCP standard (porta 502).
# Slave ID Modbus default: 126 (configurabile 1-247).
# FC supportate: 0x03 (Read Holding) e 0x04 (Read Input) restituiscono lo
# stesso contenuto. 0x10 (Write Multiple) per parametri.
#
# Convenzioni dati (fondamentali per il consumer):
#   - Indirizzamento: gli offset sotto sono 1-based (es. 1 = registro 40001).
#     Sul filo (PDU) sono 0-based, quindi offset 1 → indirizzo wire 0x0000.
#   - FLOAT32 (2 reg): byte_order=big, word_order=big → ABCD network order.
#   - DOUBLE 64-bit (4 reg): big-endian completo (MSW prima).
#   - UINT32 (2 reg): big-endian, high-word prima.
#   - Energie: contatori cumulativi monotonici, R/W (resettabili dal client),
#     overflow a 1.0e+12.
#
# Lettura lato consumer (gateway/SCADA/PLC):
#   - scale = 1.0 ovunque sul PAC2200 → NESSUNA conversione lato consumer.
#     Il valore di registro è già in V, A, W, var, VA, Hz, %, Wh, varh, VAh.
#   - Configurazione tipica del data point sul gateway:
#       Address 4xxxx, Word count 2 (FLOAT32) o 4 (FLOAT64),
#       Byte order = Big endian, Word order = Big endian (ABCD),
#       Multiplier = 1, Divisor = 1, Offset = 0.
# ============================================================================

PAC2200_TEMPLATES: List[MeasurementTemplate] = [
    # ----- Tensioni Fase-Neutro (offset 1, 3, 5) ------------------------------
    MeasurementTemplate(
        name="pac2200_v_l1_n",
        label="PAC2200 · V L1-N · @40001 · FLOAT32 ABCD · scale=1 · V",
        unit="V",
        data_type=DataType.FLOAT32, scale=1.0,
        min_value=0.0, max_value=500.0, update_rate=1.0,
        description=(
            "Siemens SENTRON PAC2200 · Tensione Fase 1 - Neutro. "
            "Indirizzo: 40001 (offset 1), 2 registri FLOAT32 big-endian (ABCD). "
            "Scale: 1.0 (valore già in V). Read-Only via FC 0x03/0x04. "
            "Slave ID default 126. Range tipico rete LV: 220-240 V."
        ),
    ),
    MeasurementTemplate(
        name="pac2200_v_l2_n",
        label="PAC2200 · V L2-N · @40003 · FLOAT32 ABCD · scale=1 · V",
        unit="V",
        data_type=DataType.FLOAT32, scale=1.0,
        min_value=0.0, max_value=500.0, update_rate=1.0,
        description=(
            "Siemens SENTRON PAC2200 · Tensione Fase 2 - Neutro. "
            "Indirizzo: 40003 (offset 3), 2 registri FLOAT32 ABCD, scale 1.0. "
            "Read-Only. Range tipico: 220-240 V."
        ),
    ),
    MeasurementTemplate(
        name="pac2200_v_l3_n",
        label="PAC2200 · V L3-N · @40005 · FLOAT32 ABCD · scale=1 · V",
        unit="V",
        data_type=DataType.FLOAT32, scale=1.0,
        min_value=0.0, max_value=500.0, update_rate=1.0,
        description=(
            "Siemens SENTRON PAC2200 · Tensione Fase 3 - Neutro. "
            "Indirizzo: 40005 (offset 5), 2 registri FLOAT32 ABCD, scale 1.0. "
            "Read-Only. Range tipico: 220-240 V."
        ),
    ),

    # ----- Tensioni Fase-Fase concatenate (offset 7, 9, 11) -------------------
    MeasurementTemplate(
        name="pac2200_v_l1_l2",
        label="PAC2200 · V L1-L2 · @40007 · FLOAT32 ABCD · scale=1 · V",
        unit="V",
        data_type=DataType.FLOAT32, scale=1.0,
        min_value=0.0, max_value=900.0, update_rate=1.0,
        description=(
            "Siemens SENTRON PAC2200 · Tensione concatenata Fase 1 - Fase 2. "
            "Indirizzo: 40007 (offset 7), 2 registri FLOAT32 ABCD, scale 1.0. "
            "Read-Only. Range tipico: 380-415 V (= V_LN × √3)."
        ),
    ),
    MeasurementTemplate(
        name="pac2200_v_l2_l3",
        label="PAC2200 · V L2-L3 · @40009 · FLOAT32 ABCD · scale=1 · V",
        unit="V",
        data_type=DataType.FLOAT32, scale=1.0,
        min_value=0.0, max_value=900.0, update_rate=1.0,
        description=(
            "Siemens SENTRON PAC2200 · Tensione concatenata Fase 2 - Fase 3. "
            "Indirizzo: 40009 (offset 9), 2 registri FLOAT32 ABCD, scale 1.0."
        ),
    ),
    MeasurementTemplate(
        name="pac2200_v_l3_l1",
        label="PAC2200 · V L3-L1 · @40011 · FLOAT32 ABCD · scale=1 · V",
        unit="V",
        data_type=DataType.FLOAT32, scale=1.0,
        min_value=0.0, max_value=900.0, update_rate=1.0,
        description=(
            "Siemens SENTRON PAC2200 · Tensione concatenata Fase 3 - Fase 1. "
            "Indirizzo: 40011 (offset 11), 2 registri FLOAT32 ABCD, scale 1.0."
        ),
    ),

    # ----- Correnti di linea (offset 13, 15, 17) ------------------------------
    MeasurementTemplate(
        name="pac2200_i_l1",
        label="PAC2200 · I L1 · @40013 · FLOAT32 ABCD · scale=1 · A",
        unit="A",
        data_type=DataType.FLOAT32, scale=1.0,
        min_value=0.0, max_value=6500.0, update_rate=1.0,
        description=(
            "Siemens SENTRON PAC2200 · Corrente di linea Fase 1. "
            "Indirizzo: 40013 (offset 13), 2 registri FLOAT32 ABCD, scale 1.0. "
            "Read-Only. Dipende dal CT primario configurato (1-99999 A)."
        ),
    ),
    MeasurementTemplate(
        name="pac2200_i_l2",
        label="PAC2200 · I L2 · @40015 · FLOAT32 ABCD · scale=1 · A",
        unit="A",
        data_type=DataType.FLOAT32, scale=1.0,
        min_value=0.0, max_value=6500.0, update_rate=1.0,
        description=(
            "Siemens SENTRON PAC2200 · Corrente di linea Fase 2. "
            "Indirizzo: 40015 (offset 15), FLOAT32 ABCD, scale 1.0."
        ),
    ),
    MeasurementTemplate(
        name="pac2200_i_l3",
        label="PAC2200 · I L3 · @40017 · FLOAT32 ABCD · scale=1 · A",
        unit="A",
        data_type=DataType.FLOAT32, scale=1.0,
        min_value=0.0, max_value=6500.0, update_rate=1.0,
        description=(
            "Siemens SENTRON PAC2200 · Corrente di linea Fase 3. "
            "Indirizzo: 40017 (offset 17), FLOAT32 ABCD, scale 1.0."
        ),
    ),

    # ----- Potenze apparenti per fase (offset 19, 21, 23) ---------------------
    MeasurementTemplate(
        name="pac2200_s_l1",
        label="PAC2200 · S L1 · @40019 · FLOAT32 ABCD · scale=1 · VA",
        unit="VA",
        data_type=DataType.FLOAT32, scale=1.0,
        min_value=0.0, max_value=2_000_000.0, update_rate=1.0,
        description=(
            "Siemens SENTRON PAC2200 · Potenza apparente Fase 1 (S = V·I). "
            "Indirizzo: 40019 (offset 19), FLOAT32 ABCD, scale 1.0."
        ),
    ),
    MeasurementTemplate(
        name="pac2200_s_l2",
        label="PAC2200 · S L2 · @40021 · FLOAT32 ABCD · scale=1 · VA",
        unit="VA",
        data_type=DataType.FLOAT32, scale=1.0,
        min_value=0.0, max_value=2_000_000.0, update_rate=1.0,
        description=(
            "Siemens SENTRON PAC2200 · Potenza apparente Fase 2. "
            "Indirizzo: 40021 (offset 21), FLOAT32 ABCD, scale 1.0."
        ),
    ),
    MeasurementTemplate(
        name="pac2200_s_l3",
        label="PAC2200 · S L3 · @40023 · FLOAT32 ABCD · scale=1 · VA",
        unit="VA",
        data_type=DataType.FLOAT32, scale=1.0,
        min_value=0.0, max_value=2_000_000.0, update_rate=1.0,
        description=(
            "Siemens SENTRON PAC2200 · Potenza apparente Fase 3. "
            "Indirizzo: 40023 (offset 23), FLOAT32 ABCD, scale 1.0."
        ),
    ),

    # ----- Potenze attive per fase (offset 25, 27, 29) ------------------------
    MeasurementTemplate(
        name="pac2200_p_l1",
        label="PAC2200 · P L1 · @40025 · FLOAT32 ABCD · scale=1 · W",
        unit="W",
        data_type=DataType.FLOAT32, scale=1.0,
        min_value=-2_000_000.0, max_value=2_000_000.0, update_rate=1.0,
        description=(
            "Siemens SENTRON PAC2200 · Potenza attiva Fase 1 (P = V·I·cosφ). "
            "Indirizzo: 40025 (offset 25), FLOAT32 ABCD, scale 1.0. "
            "Segno: positivo = import (consumo), negativo = export (immissione)."
        ),
    ),
    MeasurementTemplate(
        name="pac2200_p_l2",
        label="PAC2200 · P L2 · @40027 · FLOAT32 ABCD · scale=1 · W",
        unit="W",
        data_type=DataType.FLOAT32, scale=1.0,
        min_value=-2_000_000.0, max_value=2_000_000.0, update_rate=1.0,
        description=(
            "Siemens SENTRON PAC2200 · Potenza attiva Fase 2. "
            "Indirizzo: 40027 (offset 27), FLOAT32 ABCD, scale 1.0."
        ),
    ),
    MeasurementTemplate(
        name="pac2200_p_l3",
        label="PAC2200 · P L3 · @40029 · FLOAT32 ABCD · scale=1 · W",
        unit="W",
        data_type=DataType.FLOAT32, scale=1.0,
        min_value=-2_000_000.0, max_value=2_000_000.0, update_rate=1.0,
        description=(
            "Siemens SENTRON PAC2200 · Potenza attiva Fase 3. "
            "Indirizzo: 40029 (offset 29), FLOAT32 ABCD, scale 1.0."
        ),
    ),

    # ----- Potenze reattive per fase (offset 31, 33, 35) ----------------------
    MeasurementTemplate(
        name="pac2200_q_l1",
        label="PAC2200 · Q L1 · @40031 · FLOAT32 ABCD · scale=1 · var",
        unit="var",
        data_type=DataType.FLOAT32, scale=1.0,
        min_value=-2_000_000.0, max_value=2_000_000.0, update_rate=1.0,
        description=(
            "Siemens SENTRON PAC2200 · Potenza reattiva Fase 1 (Q = V·I·sinφ). "
            "Indirizzo: 40031 (offset 31), FLOAT32 ABCD, scale 1.0. "
            "Segno: positivo = induttiva, negativo = capacitiva."
        ),
    ),
    MeasurementTemplate(
        name="pac2200_q_l2",
        label="PAC2200 · Q L2 · @40033 · FLOAT32 ABCD · scale=1 · var",
        unit="var",
        data_type=DataType.FLOAT32, scale=1.0,
        min_value=-2_000_000.0, max_value=2_000_000.0, update_rate=1.0,
        description=(
            "Siemens SENTRON PAC2200 · Potenza reattiva Fase 2. "
            "Indirizzo: 40033 (offset 33), FLOAT32 ABCD, scale 1.0."
        ),
    ),
    MeasurementTemplate(
        name="pac2200_q_l3",
        label="PAC2200 · Q L3 · @40035 · FLOAT32 ABCD · scale=1 · var",
        unit="var",
        data_type=DataType.FLOAT32, scale=1.0,
        min_value=-2_000_000.0, max_value=2_000_000.0, update_rate=1.0,
        description=(
            "Siemens SENTRON PAC2200 · Potenza reattiva Fase 3. "
            "Indirizzo: 40035 (offset 35), FLOAT32 ABCD, scale 1.0."
        ),
    ),

    # ----- Power Factor per fase (offset 37, 39, 41) --------------------------
    MeasurementTemplate(
        name="pac2200_pf_l1",
        label="PAC2200 · PF L1 · @40037 · FLOAT32 ABCD · scale=1 · —",
        unit="",
        data_type=DataType.FLOAT32, scale=1.0,
        min_value=-1.0, max_value=1.0, update_rate=1.0,
        description=(
            "Siemens SENTRON PAC2200 · Power factor Fase 1 (PF = P/S). "
            "Indirizzo: 40037 (offset 37), FLOAT32 ABCD, scale 1.0. "
            "Range: -1..+1. Tipico industriale: 0.85..0.98 induttivo."
        ),
    ),
    MeasurementTemplate(
        name="pac2200_pf_l2",
        label="PAC2200 · PF L2 · @40039 · FLOAT32 ABCD · scale=1 · —",
        unit="",
        data_type=DataType.FLOAT32, scale=1.0,
        min_value=-1.0, max_value=1.0, update_rate=1.0,
        description=(
            "Siemens SENTRON PAC2200 · Power factor Fase 2. "
            "Indirizzo: 40039 (offset 39), FLOAT32 ABCD, scale 1.0."
        ),
    ),
    MeasurementTemplate(
        name="pac2200_pf_l3",
        label="PAC2200 · PF L3 · @40041 · FLOAT32 ABCD · scale=1 · —",
        unit="",
        data_type=DataType.FLOAT32, scale=1.0,
        min_value=-1.0, max_value=1.0, update_rate=1.0,
        description=(
            "Siemens SENTRON PAC2200 · Power factor Fase 3. "
            "Indirizzo: 40041 (offset 41), FLOAT32 ABCD, scale 1.0."
        ),
    ),

    # ----- THD-R Tensione per fase (offset 43, 45, 47) ------------------------
    MeasurementTemplate(
        name="pac2200_thd_v_l1",
        label="PAC2200 · THD-R V L1 · @40043 · FLOAT32 ABCD · scale=1 · %",
        unit="%",
        data_type=DataType.FLOAT32, scale=1.0,
        min_value=0.0, max_value=100.0, update_rate=2.0,
        description=(
            "Siemens SENTRON PAC2200 · THD-R Tensione Fase 1 (distorsione armonica relativa). "
            "Indirizzo: 40043 (offset 43), FLOAT32 ABCD, scale 1.0. "
            "Tipico rete pulita: 1-3%. Carichi non lineari pesanti: fino 5-8%."
        ),
    ),
    MeasurementTemplate(
        name="pac2200_thd_v_l2",
        label="PAC2200 · THD-R V L2 · @40045 · FLOAT32 ABCD · scale=1 · %",
        unit="%",
        data_type=DataType.FLOAT32, scale=1.0,
        min_value=0.0, max_value=100.0, update_rate=2.0,
        description=(
            "Siemens SENTRON PAC2200 · THD-R Tensione Fase 2. "
            "Indirizzo: 40045 (offset 45), FLOAT32 ABCD, scale 1.0."
        ),
    ),
    MeasurementTemplate(
        name="pac2200_thd_v_l3",
        label="PAC2200 · THD-R V L3 · @40047 · FLOAT32 ABCD · scale=1 · %",
        unit="%",
        data_type=DataType.FLOAT32, scale=1.0,
        min_value=0.0, max_value=100.0, update_rate=2.0,
        description=(
            "Siemens SENTRON PAC2200 · THD-R Tensione Fase 3. "
            "Indirizzo: 40047 (offset 47), FLOAT32 ABCD, scale 1.0."
        ),
    ),

    # ----- THD-R Corrente per fase (offset 49, 51, 53) ------------------------
    MeasurementTemplate(
        name="pac2200_thd_i_l1",
        label="PAC2200 · THD-R I L1 · @40049 · FLOAT32 ABCD · scale=1 · %",
        unit="%",
        data_type=DataType.FLOAT32, scale=1.0,
        min_value=0.0, max_value=400.0, update_rate=2.0,
        description=(
            "Siemens SENTRON PAC2200 · THD-R Corrente Fase 1. "
            "Indirizzo: 40049 (offset 49), FLOAT32 ABCD, scale 1.0. "
            "Tipico carichi lineari: 5-10%. Drive/inverter: 30-80%."
        ),
    ),
    MeasurementTemplate(
        name="pac2200_thd_i_l2",
        label="PAC2200 · THD-R I L2 · @40051 · FLOAT32 ABCD · scale=1 · %",
        unit="%",
        data_type=DataType.FLOAT32, scale=1.0,
        min_value=0.0, max_value=400.0, update_rate=2.0,
        description=(
            "Siemens SENTRON PAC2200 · THD-R Corrente Fase 2. "
            "Indirizzo: 40051 (offset 51), FLOAT32 ABCD, scale 1.0."
        ),
    ),
    MeasurementTemplate(
        name="pac2200_thd_i_l3",
        label="PAC2200 · THD-R I L3 · @40053 · FLOAT32 ABCD · scale=1 · %",
        unit="%",
        data_type=DataType.FLOAT32, scale=1.0,
        min_value=0.0, max_value=400.0, update_rate=2.0,
        description=(
            "Siemens SENTRON PAC2200 · THD-R Corrente Fase 3. "
            "Indirizzo: 40053 (offset 53), FLOAT32 ABCD, scale 1.0."
        ),
    ),

    # ----- Frequenza (offset 55) ----------------------------------------------
    MeasurementTemplate(
        name="pac2200_frequency",
        label="PAC2200 · Frequenza · @40055 · FLOAT32 ABCD · scale=1 · Hz",
        unit="Hz",
        data_type=DataType.FLOAT32, scale=1.0,
        min_value=45.0, max_value=65.0, update_rate=1.0,
        description=(
            "Siemens SENTRON PAC2200 · Frequenza di rete. "
            "Indirizzo: 40055 (offset 55), FLOAT32 ABCD, scale 1.0. "
            "Range tipico Europa: 49.95-50.05 Hz. ENTSO-E target ±0.05 Hz."
        ),
    ),

    # ----- Medie e Totali (offset 57-69) --------------------------------------
    MeasurementTemplate(
        name="pac2200_v_ln_avg",
        label="PAC2200 · V L-N media · @40057 · FLOAT32 ABCD · scale=1 · V",
        unit="V",
        data_type=DataType.FLOAT32, scale=1.0,
        min_value=0.0, max_value=500.0, update_rate=1.0,
        description=(
            "Siemens SENTRON PAC2200 · Tensione media L-N (V_LN avg = (V_L1N+V_L2N+V_L3N)/3). "
            "Indirizzo: 40057 (offset 57), FLOAT32 ABCD, scale 1.0."
        ),
    ),
    MeasurementTemplate(
        name="pac2200_v_ll_avg",
        label="PAC2200 · V L-L media · @40059 · FLOAT32 ABCD · scale=1 · V",
        unit="V",
        data_type=DataType.FLOAT32, scale=1.0,
        min_value=0.0, max_value=900.0, update_rate=1.0,
        description=(
            "Siemens SENTRON PAC2200 · Tensione media L-L (= V_LN avg × √3). "
            "Indirizzo: 40059 (offset 59), FLOAT32 ABCD, scale 1.0."
        ),
    ),
    MeasurementTemplate(
        name="pac2200_i_avg",
        label="PAC2200 · I media · @40061 · FLOAT32 ABCD · scale=1 · A",
        unit="A",
        data_type=DataType.FLOAT32, scale=1.0,
        min_value=0.0, max_value=6500.0, update_rate=1.0,
        description=(
            "Siemens SENTRON PAC2200 · Corrente media (= (I_L1+I_L2+I_L3)/3). "
            "Indirizzo: 40061 (offset 61), FLOAT32 ABCD, scale 1.0."
        ),
    ),
    MeasurementTemplate(
        name="pac2200_s_total",
        label="PAC2200 · S totale · @40063 · FLOAT32 ABCD · scale=1 · VA",
        unit="VA",
        data_type=DataType.FLOAT32, scale=1.0,
        min_value=0.0, max_value=6_000_000.0, update_rate=1.0,
        description=(
            "Siemens SENTRON PAC2200 · Potenza apparente totale (S_tot = S_L1 + S_L2 + S_L3). "
            "Indirizzo: 40063 (offset 63), FLOAT32 ABCD, scale 1.0."
        ),
    ),
    MeasurementTemplate(
        name="pac2200_p_total",
        label="PAC2200 · P totale · @40065 · FLOAT32 ABCD · scale=1 · W",
        unit="W",
        data_type=DataType.FLOAT32, scale=1.0,
        min_value=-6_000_000.0, max_value=6_000_000.0, update_rate=1.0,
        description=(
            "Siemens SENTRON PAC2200 · Potenza attiva totale (P_tot = P_L1 + P_L2 + P_L3). "
            "Indirizzo: 40065 (offset 65), FLOAT32 ABCD, scale 1.0. "
            "Segno: positivo = import, negativo = export."
        ),
    ),
    MeasurementTemplate(
        name="pac2200_q_total",
        label="PAC2200 · Q totale · @40067 · FLOAT32 ABCD · scale=1 · var",
        unit="var",
        data_type=DataType.FLOAT32, scale=1.0,
        min_value=-6_000_000.0, max_value=6_000_000.0, update_rate=1.0,
        description=(
            "Siemens SENTRON PAC2200 · Potenza reattiva totale. "
            "Indirizzo: 40067 (offset 67), FLOAT32 ABCD, scale 1.0."
        ),
    ),
    MeasurementTemplate(
        name="pac2200_pf_total",
        label="PAC2200 · PF totale · @40069 · FLOAT32 ABCD · scale=1 · —",
        unit="",
        data_type=DataType.FLOAT32, scale=1.0,
        min_value=-1.0, max_value=1.0, update_rate=1.0,
        description=(
            "Siemens SENTRON PAC2200 · Power factor totale (= P_tot / S_tot). "
            "Indirizzo: 40069 (offset 69), FLOAT32 ABCD, scale 1.0."
        ),
    ),

    # ----- Stato e Tariffa (offset 205, 207, 209, 211) ------------------------
    MeasurementTemplate(
        name="pac2200_diagnostics",
        label="PAC2200 · Diagnostica · @40205 · UINT32 BE · scale=1 · bitmask",
        unit="",
        data_type=DataType.UINT32, scale=1.0,
        min_value=0.0, max_value=4_294_967_295.0, update_rate=5.0,
        description=(
            "Siemens SENTRON PAC2200 · Device diagnostics (bitmask). "
            "Indirizzo: 40205 (offset 205), UINT32 big-endian, scale 1.0. "
            "Bit assignment: vedere manuale 7KM PAC2200, sezione Diagnostica."
        ),
    ),
    MeasurementTemplate(
        name="pac2200_digital_input",
        label="PAC2200 · Digital Input · @40207 · UINT32 BE · scale=1 · bitmask",
        unit="",
        data_type=DataType.UINT32, scale=1.0,
        min_value=0.0, max_value=4_294_967_295.0, update_rate=2.0,
        description=(
            "Siemens SENTRON PAC2200 · Stato Digital Input (bitmask). "
            "Indirizzo: 40207 (offset 207), UINT32 big-endian. "
            "Bit 0 = DI1 (es. tariff switching), altri riservati."
        ),
    ),
    MeasurementTemplate(
        name="pac2200_digital_output",
        label="PAC2200 · Digital Output · @40209 · UINT32 BE · scale=1 · bitmask",
        unit="",
        data_type=DataType.UINT32, scale=1.0,
        min_value=0.0, max_value=4_294_967_295.0, update_rate=2.0,
        description=(
            "Siemens SENTRON PAC2200 · Stato Digital Output (bitmask). "
            "Indirizzo: 40209 (offset 209), UINT32 big-endian."
        ),
    ),
    MeasurementTemplate(
        name="pac2200_active_tariff",
        label="PAC2200 · Tariffa attiva · @40211 · UINT32 BE · scale=1 · 1|2",
        unit="",
        data_type=DataType.UINT32, scale=1.0,
        min_value=1.0, max_value=2.0, update_rate=5.0,
        description=(
            "Siemens SENTRON PAC2200 · Tariffa attiva corrente (1 o 2). "
            "Indirizzo: 40211 (offset 211), UINT32 big-endian. "
            "Determina su quale contatore (T1 o T2) si accumula l'energia."
        ),
    ),

    # ----- Energie totali (offset 801-841): DOUBLE 64-bit BE ------------------
    # Contatori cumulativi monotonici. R/W (resettabili), overflow a 1.0e+12.
    # NOTA: nel simulatore attuale i registri sono effettivamente R/W lato
    # client, ma lo scheduler li sovrascrive periodicamente con il valore
    # generato. Un eventuale reset via Modbus write verrebbe sovrascritto al
    # tick successivo. Comportamento accettabile per uso integrazione/test.
    MeasurementTemplate(
        name="pac2200_eact_imp_t1",
        label="PAC2200 · Energia Att. Import T1 · @40801 · FLOAT64 BE · scale=1 · Wh",
        unit="Wh",
        data_type=DataType.FLOAT64, scale=1.0,
        min_value=0.0, max_value=1.0e12, update_rate=5.0,
        description=(
            "Siemens SENTRON PAC2200 · Energia Attiva Import Tariffa 1. "
            "Indirizzo: 40801 (offset 801), 4 registri DOUBLE 64-bit big-endian. "
            "Scale: 1.0 (valore in Wh diretto). Contatore monotonico crescente, "
            "overflow a 1.0e+12 Wh. R/W (resettabile dal client; nel simulatore "
            "il reset sarebbe sovrascritto al tick successivo)."
        ),
    ),
    MeasurementTemplate(
        name="pac2200_eact_imp_t2",
        label="PAC2200 · Energia Att. Import T2 · @40805 · FLOAT64 BE · scale=1 · Wh",
        unit="Wh",
        data_type=DataType.FLOAT64, scale=1.0,
        min_value=0.0, max_value=1.0e12, update_rate=5.0,
        description=(
            "Siemens SENTRON PAC2200 · Energia Attiva Import Tariffa 2. "
            "Indirizzo: 40805 (offset 805), DOUBLE 64-bit big-endian, scale 1.0. "
            "Contatore Wh cumulativo, conta solo se la tariffa attiva = 2."
        ),
    ),
    MeasurementTemplate(
        name="pac2200_eact_exp_t1",
        label="PAC2200 · Energia Att. Export T1 · @40809 · FLOAT64 BE · scale=1 · Wh",
        unit="Wh",
        data_type=DataType.FLOAT64, scale=1.0,
        min_value=0.0, max_value=1.0e12, update_rate=5.0,
        description=(
            "Siemens SENTRON PAC2200 · Energia Attiva Export Tariffa 1. "
            "Indirizzo: 40809 (offset 809), DOUBLE 64-bit big-endian, scale 1.0. "
            "Contatore Wh cumulativo (accumula solo quando P_tot < 0)."
        ),
    ),
    MeasurementTemplate(
        name="pac2200_eact_exp_t2",
        label="PAC2200 · Energia Att. Export T2 · @40813 · FLOAT64 BE · scale=1 · Wh",
        unit="Wh",
        data_type=DataType.FLOAT64, scale=1.0,
        min_value=0.0, max_value=1.0e12, update_rate=5.0,
        description=(
            "Siemens SENTRON PAC2200 · Energia Attiva Export Tariffa 2. "
            "Indirizzo: 40813 (offset 813), DOUBLE 64-bit big-endian, scale 1.0."
        ),
    ),
    MeasurementTemplate(
        name="pac2200_ereact_imp_t1",
        label="PAC2200 · Energia React. Import T1 · @40817 · FLOAT64 BE · scale=1 · varh",
        unit="varh",
        data_type=DataType.FLOAT64, scale=1.0,
        min_value=0.0, max_value=1.0e12, update_rate=5.0,
        description=(
            "Siemens SENTRON PAC2200 · Energia Reattiva Import Tariffa 1. "
            "Indirizzo: 40817 (offset 817), DOUBLE 64-bit big-endian, scale 1.0. "
            "Contatore varh cumulativo (Q induttiva)."
        ),
    ),
    MeasurementTemplate(
        name="pac2200_ereact_imp_t2",
        label="PAC2200 · Energia React. Import T2 · @40821 · FLOAT64 BE · scale=1 · varh",
        unit="varh",
        data_type=DataType.FLOAT64, scale=1.0,
        min_value=0.0, max_value=1.0e12, update_rate=5.0,
        description=(
            "Siemens SENTRON PAC2200 · Energia Reattiva Import Tariffa 2. "
            "Indirizzo: 40821 (offset 821), DOUBLE 64-bit big-endian, scale 1.0."
        ),
    ),
    MeasurementTemplate(
        name="pac2200_ereact_exp_t1",
        label="PAC2200 · Energia React. Export T1 · @40825 · FLOAT64 BE · scale=1 · varh",
        unit="varh",
        data_type=DataType.FLOAT64, scale=1.0,
        min_value=0.0, max_value=1.0e12, update_rate=5.0,
        description=(
            "Siemens SENTRON PAC2200 · Energia Reattiva Export Tariffa 1. "
            "Indirizzo: 40825 (offset 825), DOUBLE 64-bit big-endian, scale 1.0. "
            "Contatore varh cumulativo (Q capacitiva)."
        ),
    ),
    MeasurementTemplate(
        name="pac2200_ereact_exp_t2",
        label="PAC2200 · Energia React. Export T2 · @40829 · FLOAT64 BE · scale=1 · varh",
        unit="varh",
        data_type=DataType.FLOAT64, scale=1.0,
        min_value=0.0, max_value=1.0e12, update_rate=5.0,
        description=(
            "Siemens SENTRON PAC2200 · Energia Reattiva Export Tariffa 2. "
            "Indirizzo: 40829 (offset 829), DOUBLE 64-bit big-endian, scale 1.0."
        ),
    ),
    MeasurementTemplate(
        name="pac2200_eapp_t1",
        label="PAC2200 · Energia App. T1 · @40833 · FLOAT64 BE · scale=1 · VAh",
        unit="VAh",
        data_type=DataType.FLOAT64, scale=1.0,
        min_value=0.0, max_value=1.0e12, update_rate=5.0,
        description=(
            "Siemens SENTRON PAC2200 · Energia Apparente Tariffa 1. "
            "Indirizzo: 40833 (offset 833), DOUBLE 64-bit big-endian, scale 1.0."
        ),
    ),
    MeasurementTemplate(
        name="pac2200_eapp_t2",
        label="PAC2200 · Energia App. T2 · @40837 · FLOAT64 BE · scale=1 · VAh",
        unit="VAh",
        data_type=DataType.FLOAT64, scale=1.0,
        min_value=0.0, max_value=1.0e12, update_rate=5.0,
        description=(
            "Siemens SENTRON PAC2200 · Energia Apparente Tariffa 2. "
            "Indirizzo: 40837 (offset 837), DOUBLE 64-bit big-endian, scale 1.0."
        ),
    ),
]


# ============================================================================
# Siemens QNA2820D (Symaro IAQ multi-sensor LoRaWAN)
# ============================================================================
# Sensore ambientale 7-in-1 (T, RH, CO2, TVOC, PM2.5, PM10, sound, lux).
# Trasmette in LoRaWAN con payload Protobuf proprietario; il gateway
# (Siemens Industrial Edge / TTN / ChirpStack / Node-RED) decodifica il
# payload e lo espone su Modbus. NON esiste una mappa Modbus standard
# Siemens per il QNA2..D.
#
# Convenzione adottata qui: "FLOAT32 verbose" — comune nei gateway
# LoRaWAN→Modbus moderni che hanno già parsato il payload.
#   - FLOAT32 (2 reg) per ogni misura
#   - byte_order=big, word_order=big (ABCD network order)
#   - Slave ID Modbus: dipende dal gateway. Default convenzionale: 1.
#   - Cadenza uplink LoRaWAN reale: 5-15 minuti (configurabile via downlink).
#
# Lettura lato consumer (gateway/SCADA/PLC):
#   - scale = 1.0 ovunque sul QNA in FLOAT32 verbose → NESSUNA conversione.
#     Il registro contiene direttamente 25.3 (°C), 55.4 (%RH), 600 (ppm), ecc.
#   - Configurazione tipica del data point sul gateway:
#       Address 4xxxx, Word count 2 (FLOAT32),
#       Byte order = Big endian, Word order = Big endian (ABCD),
#       Multiplier = 1, Divisor = 1, Offset = 0.
#
# NOTA: se il gateway target usa invece la convenzione "INT16 scaled" (registro
# contiene es. 253 da dividere per 10 per ottenere 25.3 °C), occorre creare
# template alternativi con data_type=INT16 e scale=10/100, e aggiornare i
# generator per produrre direttamente il valore già scalato.
#
# Riferimento: datasheet QNA2..D (Siemens A6V13589454).
# ============================================================================

QNA_TEMPLATES: List[MeasurementTemplate] = [
    MeasurementTemplate(
        name="qna_temperature",
        label="QNA2820D · Temperatura · @40001 · FLOAT32 ABCD · scale=1 · °C",
        unit="°C",
        data_type=DataType.FLOAT32, scale=1.0,
        min_value=-5.0, max_value=90.0, update_rate=300.0,
        description=(
            "Siemens QNA2820D (gateway LoRaWAN→Modbus, FLOAT32 verbose) · Temperatura ambiente. "
            "Indirizzo: 40001 (offset 1), FLOAT32 ABCD, scale 1.0 (valore in °C diretto). "
            "Range sensore: -5..+90 °C. Accuratezza ±0.3 °C @ 20 °C. "
            "Cadenza uplink LoRaWAN: 5-15 min (qui simulato @ 300 s)."
        ),
    ),
    MeasurementTemplate(
        name="qna_humidity",
        label="QNA2820D · Umidità · @40003 · FLOAT32 ABCD · scale=1 · %RH",
        unit="%RH",
        data_type=DataType.FLOAT32, scale=1.0,
        min_value=0.0, max_value=100.0, update_rate=300.0,
        description=(
            "Siemens QNA2820D · Umidità relativa. "
            "Indirizzo: 40003 (offset 3), FLOAT32 ABCD, scale 1.0 (valore in %RH diretto). "
            "Range: 0-100 %. Accuratezza ±3 %RH."
        ),
    ),
    MeasurementTemplate(
        name="qna_co2",
        label="QNA2820D · CO2 · @40005 · FLOAT32 ABCD · scale=1 · ppm",
        unit="ppm",
        data_type=DataType.FLOAT32, scale=1.0,
        min_value=400.0, max_value=5000.0, update_rate=300.0,
        description=(
            "Siemens QNA2820D · Anidride carbonica. "
            "Indirizzo: 40005 (offset 5), FLOAT32 ABCD, scale 1.0. "
            "Range: 400-5000 ppm. Accuratezza ±50 ppm + 3% lettura."
        ),
    ),
    MeasurementTemplate(
        name="qna_tvoc",
        label="QNA2820D · TVOC · @40007 · FLOAT32 ABCD · scale=1 · ppb",
        unit="ppb",
        data_type=DataType.FLOAT32, scale=1.0,
        min_value=0.0, max_value=60000.0, update_rate=300.0,
        description=(
            "Siemens QNA2820D · Composti Organici Volatili Totali. "
            "Indirizzo: 40007 (offset 7), FLOAT32 ABCD, scale 1.0. "
            "Range: 0-60000 ppb (Sensirion SGP-class). Tipico indoor: 50-500 ppb."
        ),
    ),
    MeasurementTemplate(
        name="qna_pm25",
        label="QNA2820D · PM 2.5 · @40009 · FLOAT32 ABCD · scale=1 · µg/m³",
        unit="µg/m³",
        data_type=DataType.FLOAT32, scale=1.0,
        min_value=0.0, max_value=1000.0, update_rate=300.0,
        description=(
            "Siemens QNA2820D · Polveri sottili PM 2.5. "
            "Indirizzo: 40009 (offset 9), FLOAT32 ABCD, scale 1.0. "
            "Range: 0-1000 µg/m³. Accuratezza ±10 µg/m³ < 100, ±10% > 100."
        ),
    ),
    MeasurementTemplate(
        name="qna_pm10",
        label="QNA2820D · PM 10 · @40011 · FLOAT32 ABCD · scale=1 · µg/m³",
        unit="µg/m³",
        data_type=DataType.FLOAT32, scale=1.0,
        min_value=0.0, max_value=1000.0, update_rate=300.0,
        description=(
            "Siemens QNA2820D · Polveri sottili PM 10. "
            "Indirizzo: 40011 (offset 11), FLOAT32 ABCD, scale 1.0. "
            "Range: 0-1000 µg/m³. Correlato al PM2.5."
        ),
    ),
    MeasurementTemplate(
        name="qna_sound",
        label="QNA2820D · Pressione sonora · @40013 · FLOAT32 ABCD · scale=1 · dB(A)",
        unit="dB(A)",
        data_type=DataType.FLOAT32, scale=1.0,
        min_value=35.0, max_value=100.0, update_rate=300.0,
        description=(
            "Siemens QNA2820D · Livello pressione sonora. "
            "Indirizzo: 40013 (offset 13), FLOAT32 ABCD, scale 1.0. "
            "Range: 35-100 dB(A). Accuratezza ±3 dB."
        ),
    ),
    MeasurementTemplate(
        name="qna_illuminance",
        label="QNA2820D · Illuminamento · @40015 · FLOAT32 ABCD · scale=1 · lx",
        unit="lx",
        data_type=DataType.FLOAT32, scale=1.0,
        min_value=0.0, max_value=2000.0, update_rate=300.0,
        description=(
            "Siemens QNA2820D · Illuminamento. "
            "Indirizzo: 40015 (offset 15), FLOAT32 ABCD, scale 1.0. "
            "Range: 0-2000 lx (varianti .US/.AU fino a 65535 lx). Accuratezza ±15%."
        ),
    ),
]


# ============================================================================
# Catalogo unificato
# ============================================================================
CATALOG: List[MeasurementTemplate] = PAC2200_TEMPLATES + QNA_TEMPLATES


_BY_NAME: Dict[str, MeasurementTemplate] = {t.name: t for t in CATALOG}


def get_template(name: str) -> Optional[MeasurementTemplate]:
    return _BY_NAME.get(name)


def list_templates() -> List[MeasurementTemplate]:
    return list(CATALOG)
