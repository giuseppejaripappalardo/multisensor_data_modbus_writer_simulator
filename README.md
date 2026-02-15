# Siemens QNA2..D Sensor Simulator

Simulatore Python per multisensori Siemens QNA2..D che scrive valori su un server Modbus TCP.

## Caratteristiche

- ✅ Simulazione di N sensori multipli contemporaneamente
- ✅ 8 misure per sensore: temperature, humidity, co2, tvoc, pm25, pm10, lux, noise
- ✅ Scrittura su Holding Registers via Modbus TCP
- ✅ Multi-rate: ogni misura può avere un rate di aggiornamento diverso
- ✅ Scaling automatico (int16/uint16, 1 registro per misura)
- ✅ Configurazione YAML + override da ENV + override da CLI
- ✅ Generazione realistica dei valori con correlazioni e rumore
- ✅ Retry con backoff esponenziale per la connessione Modbus

## Requisiti

- Python 3.11+
- Server Modbus TCP (es. [Modbus Slave](https://www.modbustools.com/modbus_slave.html) di Witte Software)

## Installazione

```bash
# Clona il repository o copia i file
cd Sensor_Simulator

# Crea un virtual environment (opzionale ma consigliato)
python -m venv .venv
.venv\Scripts\activate  # Windows
# source .venv/bin/activate  # Linux/Mac

# Installa le dipendenze
pip install -r requirements.txt
```

## Configurazione Modbus Slave (Witte Software)

1. Scarica e installa [Modbus Slave](https://www.modbustools.com/modbus_slave.html)
2. Avvia l'applicazione
3. Menu **Connection** → **Connect** → Seleziona **Modbus TCP/IP**
4. Configura:
   - **IP Address**: 127.0.0.1 (o l'IP della macchina)
   - **Port**: 502
   - **Slave ID**: 1
5. Menu **Setup** → **Slave Definition**:
   - **Function**: 03 Holding Registers
   - **Address**: 0
   - **Quantity**: almeno 48 (per 3 sensori con stride 20)
6. Clicca **OK** per creare la tabella dei registri

## Utilizzo

### Avvio con file di configurazione

```bash
python -m src.main --config configs/example.yaml
```

### Avvio con parametri CLI

```bash
# Singolo sensore, configurazione minima
python -m src.main --host 127.0.0.1 --port 502 --sensor-count 1

# 5 sensori, tick di 2 secondi
python -m src.main --config configs/example.yaml --sensor-count 5 --tick 2

# Override dell'host e porta
python -m src.main --config configs/example.yaml --host 192.168.1.100 --port 5020
```

### Parametri CLI disponibili

| Parametro | Abbreviazione | Descrizione |
|-----------|---------------|-------------|
| `--config` | `-c` | Percorso al file YAML di configurazione |
| `--host` | | Indirizzo del server Modbus TCP |
| `--port` | `-p` | Porta del server Modbus TCP |
| `--unit-id` | `-u` | ID dello slave Modbus |
| `--tick` | `-t` | Intervallo del loop principale (secondi) |
| `--sensor-count` | `-n` | Numero di sensori da simulare |
| `--base-address` | `-b` | Indirizzo base del primo sensore |
| `--stride` | `-s` | Incremento di indirizzo tra sensori |
| `--log-level` | `-l` | Livello di logging (DEBUG, INFO, WARNING, ERROR) |

### Variabili d'ambiente

Le variabili d'ambiente hanno priorità sul file di configurazione ma sono sovrascritte dai parametri CLI.

| Variabile | Descrizione |
|-----------|-------------|
| `SIM_CONFIG_PATH` | Percorso al file di configurazione |
| `MODBUS_HOST` | Indirizzo server Modbus |
| `MODBUS_PORT` | Porta server Modbus |
| `MODBUS_UNIT_ID` | ID slave Modbus |
| `SIM_TICK_SECONDS` | Intervallo tick in secondi |
| `SIM_SENSOR_COUNT` | Numero di sensori (se non definiti nel file) |
| `SIM_BASE_ADDRESS` | Indirizzo base primo sensore |
| `SIM_SENSOR_STRIDE` | Stride tra sensori |
| `LOG_LEVEL` | Livello di logging |

## Formato Registri Modbus

### Scaling delle misure

| Misura | Tipo | Scaling | Esempio |
|--------|------|---------|---------|
| temperature | int16 | °C × 10 | 25.3°C → 253 |
| humidity | uint16 | % × 10 | 55.2% → 552 |
| noise | uint16 | dB × 10 | 38.5dB → 385 |
| co2 | uint16 | intero | 520 ppm → 520 |
| tvoc | uint16 | intero | 150 ppb → 150 |
| pm25 | uint16 | intero | 12 µg/m³ → 12 |
| pm10 | uint16 | intero | 25 µg/m³ → 25 |
| lux | uint16 | intero | 450 lux → 450 |

### Mappa registri di default

Con configurazione di esempio (`base_address=0`, offset standard):

| Offset | Misura | Range tipico |
|--------|--------|--------------|
| 0 | temperature | 18.0°C - 30.0°C |
| 1 | humidity | 30% - 80% |
| 2 | co2 | 400 - 1500 ppm |
| 3 | tvoc | 50 - 1200 ppb |
| 4 | pm25 | 0 - 200 µg/m³ |
| 5 | pm10 | 0 - 300 µg/m³ |
| 6 | lux | 0 - 2000 lux |
| 7 | noise | 25 - 75 dB |

### Indirizzamento multipli sensori

Con `base_address` e `stride`:

- Sensore 1: registri 0-7 (base=0)
- Sensore 2: registri 20-27 (base=20)
- Sensore 3: registri 40-47 (base=40)

## Simulazione Dati

I valori sono generati con pattern realistici:

- **Temperature**: base 24°C, variazione sinusoidale ±2°C, rumore ±0.1°C
- **Humidity**: base 55%, variazione sinusoidale ±7%, rumore ±0.3%
- **CO2**: base 520ppm, picchi sinusoidali fino a +300ppm, rumore ±20ppm
- **TVOC**: correlato con CO2 (fattore 0.4), rumore ±25ppb
- **PM2.5**: base 12µg/m³, eventi di picco casuali ogni 60-120s
- **PM10**: correlato con PM2.5 (offset 3-20µg/m³)
- **Lux**: variazione sinusoidale 50-850 lux, rumore ±10
- **Noise**: base 38dB, variazione sinusoidale ±3dB, rumore ±1dB

Ogni sensore ha un seed RNG derivato dal suo ID, garantendo valori diversi ma riproducibili.

## Multi-Rate

Ogni misura può avere un rate di aggiornamento diverso:

```yaml
rates_seconds:
  temperature: 1    # Aggiornamento ogni secondo
  humidity: 1
  lux: 1
  noise: 1
  co2: 5            # Aggiornamento ogni 5 secondi
  tvoc: 5
  pm25: 10          # Aggiornamento ogni 10 secondi
  pm10: 10
```

## Struttura Progetto

```
Sensor_Simulator/
├── configs/
│   └── example.yaml        # Configurazione di esempio
├── src/
│   ├── __init__.py
│   ├── main.py             # Entry point CLI
│   ├── config.py           # Caricamento configurazione
│   ├── modbus_client.py    # Client Modbus TCP
│   ├── models.py           # Modelli dati (pydantic)
│   ├── simulator/
│   │   ├── __init__.py
│   │   ├── encoder.py      # Scaling e codifica valori
│   │   ├── generator.py    # Generazione valori realistici
│   │   └── scheduler.py    # Scheduling multi-rate
│   └── utils/
│       ├── __init__.py
│       ├── clamp.py        # Funzione di clamp
│       └── logging.py      # Configurazione logging
├── requirements.txt
└── README.md
```

## Note

### Endianness

Non è necessario gestire l'endianness perché ogni misura occupa un singolo registro a 16 bit. Il byte order è gestito automaticamente dal protocollo Modbus.

### Compatibilità

Testato con:
- Python 3.11+
- pymodbus 3.6+
- Modbus Slave (Witte Software) su Windows

## Licenza

MIT License

