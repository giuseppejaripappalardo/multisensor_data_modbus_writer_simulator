"""
Example sensor configuration - Reference Guide
================================================

Run with:  python main.py -c configs/example_config.py


DATA TYPES DISPONIBILI (da models.DataType)
--------------------------------------------
  DataType.UINT16   ->  1 registro   (0 .. 65535)                    intero senza segno
  DataType.INT16    ->  1 registro   (-32768 .. 32767)               intero con segno
  DataType.UINT32   ->  2 registri   (0 .. 4294967295)               intero senza segno
  DataType.INT32    ->  2 registri   (-2147483648 .. 2147483647)     intero con segno
  DataType.FLOAT32  ->  2 registri   (IEEE 754 single precision)     virgola mobile
  DataType.FLOAT64  ->  4 registri   (IEEE 754 double precision)     virgola mobile


MISURE NOTE (con generatore realistico)
----------------------------------------
  temperature()  ->  Temperatura in C     |  pattern sinusoidale ~24C +-2C, rumore +-0.1C
  humidity()     ->  Umidita relativa %   |  pattern sinusoidale ~55% +-7%, rumore +-0.3%
  co2()          ->  CO2 in ppm           |  base ~520ppm con picchi fino a +300ppm
  tvoc()         ->  TVOC in ppb          |  correlato al CO2 (sale quando sale CO2)
  pm25()         ->  PM2.5 in ug/m3       |  base ~12 con picchi casuali (simula eventi)
  pm10()         ->  PM10 in ug/m3        |  correlato al PM2.5 (sempre >= PM2.5)
  lux()          ->  Illuminamento lux    |  pattern giorno/notte sinusoidale 50..850 lux
  noise()        ->  Rumore in dB         |  base ~38dB +-3dB, rumore +-1dB

  measurement()  ->  Misura generica      |  sinusoidale centrato su (min+max)/2, per tipi custom


PARAMETRI DI OGNI MISURA
--------------------------
  offset       ->  Spiazzamento in registri rispetto a base_address del sensore.
                   ATTENZIONE: se una misura occupa 2 registri (float32, uint32, int32),
                   la misura successiva deve partire da offset+2.
                   Se occupa 4 registri (float64), la successiva da offset+4.

  data_type    ->  Tipo di dato Modbus (vedi tabella sopra). Default diverso per ogni misura.

  scale        ->  Moltiplicatore applicato PRIMA della codifica nel registro.
                   Utile per tipi interi: scale=10 significa 1 cifra decimale
                   (es. 25.3C * 10 = 253 nel registro).
                   Per float32/float64 di solito scale=1.0 (il valore reale va diretto).

  min_value    ->  Valore minimo in unita reali (prima dello scaling). Usato per:
                   - Clampare il valore generato
                   - Definire il range del generatore generico

  max_value    ->  Valore massimo in unita reali.

  update_rate  ->  Intervallo di aggiornamento in secondi (es. 1.0 = ogni secondo, 5.0 = ogni 5s).


PARAMETRI DEL SENSORE
----------------------
  id            ->  Identificativo univoco (usato per logging e seed del generatore)
  base_address  ->  Indirizzo base Modbus. Registro fisico = base_address + offset
  byte_order    ->  Ordine dei byte dentro ogni registro 16-bit: "big" o "little"
  word_order    ->  Ordine dei registri per tipi multi-registro: "big" o "little"

                   Combinazioni endianness (esempio float32):
                     big/big     = AB CD   (Big Endian, standard Modbus)
                     big/little  = CD AB   (word-swapped, comune in alcuni PLC)
                     little/big  = BA DC   (byte-swapped)
                     little/little = DC BA (Little Endian)
"""
from models import AppConfig, ModbusConfig, SensorConfig, DataType
from measurements import temperature, humidity, co2, tvoc, pm25, pm10, lux, noise, measurement


# ==========================================================================
# SENSORE 1: Qualita aria indoor - encoding uint16 classico
# ==========================================================================
# Ogni misura occupa 1 registro -> offset incrementa di 1
#
#   Registro 0: temperature  (int16,  val = C * 10)     es. 25.3C  -> 253
#   Registro 1: humidity     (uint16, val = % * 10)     es. 55.2%  -> 552
#   Registro 2: co2          (uint16, val = ppm)        es. 520    -> 520
#   Registro 3: tvoc         (uint16, val = ppb)        es. 198    -> 198
#   Registro 4: pm25         (uint16, val = ug/m3)      es. 12     -> 12
#   Registro 5: pm10         (uint16, val = ug/m3)      es. 25     -> 25
#   Registro 6: lux          (uint16, val = lux)        es. 450    -> 450
#   Registro 7: noise        (uint16, val = dB * 10)    es. 38.5dB -> 385
#
indoor_aq = SensorConfig(
    id="indoor_aq",
    base_address=0,
    byte_order="little",
    word_order="little",
    measurements=[
        temperature(offset=0),          # default: int16, scale=10
        # humidity(offset=1),             # default: uint16, scale=10
        # co2(offset=2),                  # default: uint16, scale=1, rate=5s
        # tvoc(offset=3),                 # default: uint16, scale=1, rate=5s
        # pm25(offset=4),                 # default: uint16, scale=1, rate=10s
        # pm10(offset=5),                 # default: uint16, scale=1, rate=10s
        # lux(offset=6),                  # default: uint16, scale=1
        # noise(offset=7),                # default: uint16, scale=10
    ],
)


# ==========================================================================
# SENSORE 2: Stazione meteo - encoding float32 (alta precisione)
# ==========================================================================
# Ogni misura occupa 2 registri -> offset incrementa di 2
#
#   Registri 100-101: temperature  (float32)   es. 25.3 -> IEEE 754
#   Registri 102-103: humidity     (float32)   es. 55.2 -> IEEE 754
#   Registri 104-105: lux          (float32)   es. 450.0 -> IEEE 754
#
# weather = SensorConfig(
#     id="weather_station",
#     base_address=10,
#     byte_order="big",
#     word_order="big",
#     measurements=[
#         temperature(offset=0, data_type=DataType.FLOAT32, scale=1.0),
#         # humidity(offset=2, data_type=DataType.FLOAT32, scale=1.0),
#         # lux(offset=4, data_type=DataType.FLOAT32, scale=1.0, max_value=100000.0, update_rate=2.0),
#     ],
# )


# ==========================================================================
# SENSORE 3: Contatore energia - tipi misti, word order diverso
# ==========================================================================
# Alcuni dispositivi (es. Eastron, ABB) usano word order "little" (CD AB)
#
#   Registri 200-201: potenza    (uint32)    es. 75000 W
#   Registri 202-205: energia    (float64)   es. 123456.789 kWh  (4 registri!)
#   Registro  206:    tensione   (uint16)    es. 230 V
#
# energy_meter = SensorConfig(
#     id="energy_meter",
#     base_address=50,
#     byte_order="big",
#     word_order="big",
#     measurements=[
#         measurement("power",   offset=0, data_type=DataType.FLOAT32,  min_value=0, max_value=10000, update_rate=1.0),
#         # measurement("energy",  offset=2, data_type=DataType.FLOAT64, min_value=0, max_value=999999999, update_rate=5.0),
#         # measurement("voltage", offset=6, data_type=DataType.UINT16,  min_value=210, max_value=250, update_rate=1.0),
#     ],
# )

# ==========================================================================
# CONFIGURAZIONE COMPLETA
# ==========================================================================
config = AppConfig(
    modbus=ModbusConfig(
        host="127.0.0.1",
        port=502,
        unit_id=1,
        connect_timeout_ms=3000,
        max_retry_attempts=3,
    ),
    tick_seconds=1.0,               # intervallo main loop
    log_level="INFO",               # DEBUG per vedere i dettagli encoding
    sensors=[
        indoor_aq,
        # weather,
        # energy_meter,             # decommentare per attivare
    ],
)
