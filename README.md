# Multisensor Modbus Simulator

Simulatore Modbus TCP multi-sensore con server integrato, web UI plug-and-play
e iniezione di guasti.

Pensato per stress-testare client Modbus reali (PLC, SCADA, gateway IoT) senza
hardware: dichiari i sensori dalla UI o da YAML, il simulatore espone uno o più
slave Modbus sullo stesso listener TCP e ci scrive sopra i valori generati.

## Caratteristiche

- **Server Modbus TCP integrato** — un solo processo Python, niente Modbus
  Slave esterno da configurare.
- **Multi-slave gateway-style** — ogni sensore può vivere su un proprio
  `unit_id`; lo stesso listener serve tutti gli slave (come un gateway che
  aggrega più device).
- **Web UI plug-and-play** — catalogo di 15 misure note (temperatura, CO2,
  PM, energia, tensione, ...), CRUD sensori/misure, valori live, register
  dump per debug.
- **Tutti gli spazi Modbus**: coil (FC 01/05/15), discrete input (FC 02),
  input register (FC 04), holding register (FC 03/06/16). Tutti coesistono
  contemporaneamente sullo stesso `unit_id`, ognuno con il proprio address space.
- **Tutti i tipi Modbus** sui registri 16-bit: `uint16`, `int16`, `uint32`,
  `int32`, `float32`, `float64`, con qualunque combinazione di `byte_order` /
  `word_order`. Coil e discrete input sono booleani (1 bit).
- **Multi-rate** — ogni misura ha il suo `update_rate`; ogni sensore può
  imporre un `write_rate_seconds` (es. "scrivi al massimo una volta al
  minuto").
- **Fault injection** — per sensore: latenza, offline, exception code casuale.
  Per misura: exception code casuale, valore bloccato (`frozen`), scrittura
  saltata (`drop_writes`).
- **Live reload** — modifiche dalla UI applicate al volo, senza droppare le
  connessioni (il server viene rilanciato solo se cambia la struttura degli
  `unit_id`).
- **Persistenza automatica** — la UI salva ogni edit su `configs/runtime.yaml`.
- **Modalità legacy CLI** — il simulatore può anche scrivere su un server
  Modbus *esterno* (compatibile col vecchio entry point `python main.py`).

## Architettura

```
+------------------+       +-----------------------+
|     Web UI       |       |   REST API (FastAPI)  |
|  (Alpine.js)     |<----->|   /api/sensors  ...   |
+------------------+       +-----------+-----------+
                                       |
                                       v
                           +-----------+-----------+
                           |        Runtime        |
                           |  (config + state)     |
                           +--+-----------------+--+
                              |                 |
                              v                 v
              +---------------+----+    +-------+----------------+
              | EmbeddedModbusServer|    |   SensorSimulator      |
              |  - 1 listener TCP   |<-->|  - generatori          |
              |  - N slave context  |    |  - encoder endianness  |
              |  - fault rules      |    |  - scheduler multi-rate|
              +---------------------+    +------------------------+
```

## Requisiti

- Python 3.11+
- (Per il deploy in container) Docker 20.10+ e Docker Compose v2

Le dipendenze Python sono pinnate in `requirements.txt`:

```
pymodbus==3.7.0
PyYAML>=6.0
pydantic>=2.0
fastapi>=0.110
uvicorn[standard]>=0.27
```

> `pymodbus` è pinnato a 3.7.0 perché versioni più recenti hanno introdotto
> breaking changes nelle API dei datastore.

## Avvio rapido

### 1. In locale (Python)

```bash
# Crea un virtualenv (consigliato).
python3 -m venv .venv
source .venv/bin/activate          # macOS / Linux
# .venv\Scripts\activate           # Windows

pip install -r requirements.txt

# Avvia UI + server Modbus integrato.
python -m webui
```

- Web UI:    http://localhost:8000
- Modbus TCP: `localhost:502` (la porta IANA standard di Modbus TCP)

> **Nota porta privilegiata:** la 502 è < 1024, quindi su macOS/Linux il
> bind richiede privilegi root. Tre opzioni: (1) `sudo python -m webui`,
> (2) imposta una porta non privilegiata in `runtime.yaml` (es. 5020), o
> (3) usa Docker che gestisce il binding via mapping di compose.

La UI carica la config da `configs/runtime.yaml` (se esiste). Ogni modifica
viene salvata automaticamente sullo stesso file.

### 2. In Docker

```bash
docker compose up -d --build
```

Espone le stesse due porte (8000 UI, 502 Modbus) e monta `./configs` come
volume, così il `runtime.yaml` sopravvive ai restart.

```bash
docker compose logs -f simulator   # log in tempo reale
docker compose down                # stop e rimuovi container
```

### 3. Modalità legacy CLI (verso server Modbus esterno)

Per scrivere su un Modbus Slave già esistente (Witte Software, PLC, ...):

```bash
python main.py -c configs/example.yaml
```

In questa modalità si usa la sezione `modbus:` del YAML e tutti i sensori
vengono scritti col `modbus.unit_id` configurato.

## Variabili d'ambiente

| Variabile         | Default                       | Descrizione                       |
| ----------------- | ----------------------------- | --------------------------------- |
| `UI_HOST`         | `0.0.0.0`                     | Interfaccia di bind del web server|
| `UI_PORT`         | `8000`                        | Porta del web server              |
| `SIM_CONFIG_PATH` | `./configs/runtime.yaml`      | File YAML caricato/persistito     |
| `LOG_LEVEL`       | `INFO`                        | Livello log (DEBUG/INFO/WARN/ERR) |

## Web UI — flusso tipico

1. **Crea un sensore** dalla colonna centrale (id, `unit_id`, `base_address`,
   endianness, `write_rate_seconds`).
2. **Aggiungi misure** scegliendo dal catalogo a sinistra o creando una
   misura custom (`custom`). Il pannello suggerisce automaticamente il
   prossimo `offset` libero.
3. **Avvia server e simulatore** dai pulsanti in alto a destra. Vedi gli
   slave attivi e i registri allocati.
4. **Inietta guasti** dal pannello sensore (latenza, offline) o dalla riga
   misura (frozen / drop_writes / error_rate). Il client Modbus che ci sta
   leggendo vedrà il comportamento desiderato in tempo reale.
5. **Register dump** in basso: tabella per ogni `unit_id` con
   `address / hex / dec / owner`, utile per capire cosa il client legge.

## Spazi Modbus supportati

Ogni `unit_id` espone tutti e 4 gli spazi Modbus, esattamente come un device
reale. Sono tutti indipendenti: un coil all'address 0 e un holding register
all'address 0 sono indirizzi distinti.

| Spazio (`register_type`) | Larghezza | R/W | Function code (read) | Function code (write) | Tipico utilizzo |
| ------------------------ | --------- | --- | -------------------- | --------------------- | --------------- |
| `coil`                   | 1 bit     | R/W | FC 01                | FC 05 / 15            | Comandi/uscite digitali (start/stop, relè) |
| `discrete_input`         | 1 bit     | R/O | FC 02                | —                     | Ingressi digitali (finecorsa, presenza, allarmi) |
| `input_register`         | 16 bit    | R/O | FC 04                | —                     | Misure/contatori firmware (R/O) |
| `holding_register`       | 16 bit    | R/W | FC 03                | FC 06 / 16            | Misure analogiche, setpoint, configurazione |

Per ogni misura si imposta il `register_type` (default: `holding_register`).
Per `coil` e `discrete_input` il `data_type` è forzato a `bool` (1 bit).

## Tipi di dato e endianness

Validi per `input_register` e `holding_register` (registri a 16 bit):

| Tipo      | Registri | Range                                              |
| --------- | -------- | -------------------------------------------------- |
| `uint16`  | 1        | `0 .. 65535`                                       |
| `int16`   | 1        | `-32768 .. 32767`                                  |
| `uint32`  | 2        | `0 .. 4_294_967_295`                               |
| `int32`   | 2        | `-2_147_483_648 .. 2_147_483_647`                  |
| `float32` | 2        | IEEE 754 single                                    |
| `float64` | 4        | IEEE 754 double                                    |
| `bool`    | 1 bit    | `0` / `1` (solo per `coil` / `discrete_input`)     |

Combinazioni byte/word order (esempio `uint32 = 0xAABBCCDD`):

| `byte_order` | `word_order` | Layout in memoria |
| ------------ | ------------ | ----------------- |
| `big`        | `big`        | `AABB CCDD`       |
| `big`        | `little`     | `CCDD AABB`       |
| `little`     | `big`        | `BBAA DDCC`       |
| `little`     | `little`     | `DDCC BBAA`       |

Tutte le 4 combinazioni × 6 tipi sono coperte dai test.

## Fault injection

Tutto si configura per sensore o per misura, e si può modificare a runtime
dalla UI senza fermare il simulatore.

### Per sensore (`SensorFault`)

| Campo        | Effetto                                                                  |
| ------------ | ------------------------------------------------------------------------ |
| `latency_ms` | Latenza artificiale aggiunta dal server prima di rispondere alle read.   |
| `offline`    | Il sensore intero risponde con exception (es. `GATEWAY TARGET FAILED`).  |
| `error_rate` | Probabilità (0..1) per read di restituire `error_code`.                  |
| `error_code` | Codice exception Modbus (default `11` = GATEWAY TARGET DEVICE FAILED).   |

### Per misura (`MeasurementFault`)

| Campo               | Effetto                                                                              |
| ------------------- | ------------------------------------------------------------------------------------ |
| `error_rate`        | Probabilità di restituire exception sui registri di **questa** misura (0..1).        |
| `error_code`        | Default `2` = ILLEGAL DATA ADDRESS.                                                  |
| `frozen`            | Il valore non viene più aggiornato dal generatore.                                   |
| `drop_writes`       | Il simulatore calcola il valore (UI lo vede) ma **non** scrive il registro.          |
| `bit_flip_rate`     | Probabilità per scrittura di flippare un bit casuale nel payload codificato (0..1).  |
| `drift_per_second`  | Deriva lineare in unità reali al secondo (positiva o negativa). Si accumula.         |

> **Nota.** `error_rate` lavora sulle *read* (lato client). Tutti gli altri
> lavorano lato *simulatore* (alterando ciò che viene scritto). `frozen` e
> `drop_writes` mantengono il valore stabile, `bit_flip_rate` corrompe i
> singoli bit (utile per testare CRC / sanity check), `drift_per_second`
> simula un sensore che si scalibra nel tempo (utile per testare alarm
> threshold dello SCADA).

### Fault one-shot e di rete (via API, non in config)

| Endpoint                                                          | Effetto                                                                  |
| ----------------------------------------------------------------- | ------------------------------------------------------------------------ |
| `POST /api/server/kick`                                           | Droppa **tutte** le connessioni TCP attive (test del reconnect lato client). I registri sono preservati, il listener riparte subito. |
| `POST /api/sensors/{id}/measurements/{name}/spike`                | Inietta un valore arbitrario per `duration_seconds`. Body: `{"value": 99.0, "duration_seconds": 5.0}`. |
| `DELETE /api/sensors/{id}/measurements/{name}/spike`              | Cancella uno spike attivo prima della scadenza.                          |

## Configurazione YAML — schema completo

Vedi `configs/example.yaml` per un esempio commentato. Riassunto dei campi:

```yaml
server:                       # Server Modbus integrato (modalità embedded)
  enabled: true
  host: "0.0.0.0"
  port: 502
  default_unit_id: 1          # pre-compilato dalla UI quando crei un sensore
  register_count_min: 16      # min. registri allocati per ciascuno slave

modbus:                       # Client (modalità legacy CLI verso server esterno)
  host: "127.0.0.1"
  port: 502
  unit_id: 1
  connect_timeout_ms: 3000
  write_timeout_ms: 1000
  max_retry_attempts: 3
  backoff_seconds: [1.0, 2.0, 5.0]

tick_seconds: 1.0
log_level: "INFO"

sensors:
  - id: "office"
    unit_id: 1                # slave Modbus esposto
    base_address: 0
    byte_order: "big"
    word_order: "big"
    write_rate_seconds: 1.0   # rate-limit minimo per le scritture
    fault:                    # opzionale
      latency_ms: 0
      offline: false
      error_rate: 0.0
      error_code: 11
    measurements:
      - name: "temperature"
        offset: 0
        data_type: "int16"
        scale: 10
        min_value: -40.0
        max_value: 80.0
        update_rate: 1.0
        unit: "°C"
        fault:                # opzionale
          error_rate: 0.0
          error_code: 2
          frozen: false
          drop_writes: false
```

### Sizing automatico dei registri

Ogni slave alloca esattamente
`max(server.register_count_min, max_address_usato + 1)` registri. Letture
fuori dal range restituiscono `ILLEGAL DATA ADDRESS`, esattamente come un
device reale.

### `write_rate_seconds` vs `update_rate`

Il rate effettivo è il **più lento** tra il `update_rate` della misura e il
`write_rate_seconds` del sensore. Tipico: misure a 1s nel generatore ma
scritture Modbus a 60s per imitare un device frugale.

## Misure note (catalogo)

Le seguenti misure hanno generatori realistici e sono già pronte nel
catalogo della UI:

| Nome             | Spazio           | Tipo         | Unit  | Pattern                                       |
| ---------------- | ---------------- | ------------ | ----- | --------------------------------------------- |
| temperature      | holding          | int16 ×10    | °C    | sinusoide ~24°C ±2°C                          |
| humidity         | holding          | uint16 ×10   | %     | sinusoide ~55% ±7%                            |
| co2              | holding          | uint16       | ppm   | base 520ppm + picchi sinusoidali              |
| tvoc             | holding          | uint16       | ppb   | correlato al CO2                              |
| pm25             | holding          | uint16       | µg/m³ | base ~12, picchi casuali ogni 60-120s         |
| pm10             | holding          | uint16       | µg/m³ | correlato al PM2.5                            |
| lux              | holding          | uint16       | lux   | pattern giorno/notte 50..850 lux              |
| noise            | holding          | uint16 ×10   | dB    | base ~38dB ±3dB                               |
| pressure         | holding          | uint16       | hPa   | atmosferica generica                          |
| voltage          | holding          | float32      | V     | tensione di rete ~230V                        |
| current          | holding          | float32      | A     | corrente generica                             |
| power            | holding          | float32      | W     | potenza generica                              |
| energy           | holding          | float64      | kWh   | contatore (alta precisione)                   |
| frequency        | holding          | float32      | Hz    | frequenza di rete ~50Hz                       |
| custom           | holding          | uint16       | —     | misura libera, sinusoidale generico           |
| alarm_active     | **coil**         | bool         | —     | mostly OFF, picchi rari (~5%)                 |
| motor_run        | **coil**         | bool         | —     | onda quadra ~30s                              |
| presence         | **discrete_in**  | bool         | —     | onda quadra ~30s                              |
| limit_switch     | **discrete_in**  | bool         | —     | onda quadra ~30s                              |
| uptime_seconds   | **input_reg**    | uint32       | s     | contatore monotonico (secondi dall'avvio)     |

Per nomi non in catalogo viene usato un generatore sinusoidale generico
basato su `min_value` / `max_value`.

## REST API

| Metodo  | Endpoint                                       | Descrizione                                |
| ------- | ---------------------------------------------- | ------------------------------------------ |
| `GET`   | `/api/catalog`                                 | Lista template misure + tipi dato          |
| `GET`   | `/api/config`                                  | Configurazione corrente                    |
| `PUT`   | `/api/config`                                  | Sostituisce l'intera configurazione        |
| `POST`  | `/api/sensors`                                 | Crea un sensore                            |
| `PUT`   | `/api/sensors/{id}`                            | Aggiorna un sensore (PATCH style)          |
| `DELETE`| `/api/sensors/{id}`                            | Elimina un sensore                         |
| `POST`  | `/api/sensors/{id}/measurements`               | Crea una misura                            |
| `PUT`   | `/api/sensors/{id}/measurements/{name}`        | Aggiorna una misura                        |
| `DELETE`| `/api/sensors/{id}/measurements/{name}`        | Elimina una misura                         |
| `POST`  | `/api/server/start`                            | Avvia il server Modbus integrato           |
| `POST`  | `/api/server/stop`                             | Stop                                       |
| `POST`  | `/api/server/kick`                             | Droppa tutte le connessioni TCP attive     |
| `POST`  | `/api/simulator/start`                         | Avvia il loop di simulazione               |
| `POST`  | `/api/simulator/stop`                          | Stop                                       |
| `POST`  | `/api/sensors/{id}/measurements/{name}/spike`  | Inietta un valore one-shot                 |
| `DELETE`| `/api/sensors/{id}/measurements/{name}/spike`  | Cancella uno spike attivo                  |
| `GET`   | `/api/status`                                  | Stato server + simulatore + valori live    |
| `GET`   | `/api/events?limit=N`                          | Ultimi N eventi (aggiornamenti misure)     |
| `GET`   | `/api/slaves`                                  | Dump registri per ciascuno slave           |

Documentazione interattiva (Swagger) su `http://localhost:8000/docs`.

## Test

```bash
python -m pytest tests/ -v
# oppure:
python tests/test_encoder.py
```

I test coprono il round-trip `encode_value` ↔ `decode_value` per tutti i 6
tipi e tutte le 4 combinazioni di endianness.

## Struttura del progetto

```
.
├── Dockerfile
├── docker-compose.yml
├── .dockerignore
├── README.md
├── requirements.txt
├── main.py                      # entry point CLI legacy
├── config.py                    # caricamento YAML + override CLI/env
├── catalog.py                   # template misure note
├── models.py                    # modelli Pydantic (AppConfig, Sensor, ...)
├── modbus_server.py             # EmbeddedModbusServer (multi-slave, fault rules)
├── modbus_client.py             # client Modbus (modalità legacy)
├── simulator/
│   ├── encoder.py               # encode/decode tutti i tipi + endianness
│   ├── generator.py             # generatori realistici per misura
│   └── scheduler.py             # SensorSimulator + RegisterSink (embedded/client)
├── webui/
│   ├── __main__.py              # python -m webui
│   ├── app.py                   # FastAPI routes
│   ├── runtime.py               # glue: config + server + simulatore
│   ├── templates/index.html
│   └── static/{app.js, style.css}
├── configs/
│   ├── example.yaml             # schema YAML documentato (riferimento)
│   └── runtime.yaml             # configurazione persistita dalla UI
├── tests/
│   └── test_encoder.py
└── utils/
    ├── clamp.py
    └── logging.py
```

## Licenza

MIT License
