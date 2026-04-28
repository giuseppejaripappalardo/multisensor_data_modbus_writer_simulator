document.addEventListener("alpine:init", () => {
  Alpine.data("simApp", () => ({
    catalog: [],
    dataTypes: [],
    registerTypes: [],
    config: { sensors: [], modbus: {}, server: {} },
    status: {
      server: { running: false, host: "-", port: 0, unit_id: 0 },
      simulator: { running: false },
      values: [],
    },
    events: [],
    latestMap: {},   // { "sensor:measurement": value-object }
    slaves: [],      // raw register dump per unit_id
    dumpOpen: false,
    selectedSensorId: null,
    newSensor: { id: "", unit_id: 1, base_address: 0, byte_order: "big", word_order: "big", write_rate_seconds: 1.0 },
    message: "",
    pollHandle: null,

    async init() {
      await this.refreshCatalog();
      await this.refreshConfig();
      await this.refreshStatus();
      this.pollHandle = setInterval(() => this.refreshStatus(), 1000);
    },

    // ---- API helpers ----
    async fetchJSON(method, url, body = null) {
      const opts = { method, headers: { "Content-Type": "application/json" } };
      if (body !== null) opts.body = JSON.stringify(body);
      const res = await fetch(url, opts);
      if (!res.ok) {
        let detail = await res.text();
        try { detail = JSON.parse(detail).detail || detail; } catch (_) {}
        this.flash("Errore: " + detail, true);
        throw new Error(detail);
      }
      return await res.json();
    },

    flash(text) {
      this.message = text;
      clearTimeout(this._msgT);
      this._msgT = setTimeout(() => (this.message = ""), 3500);
    },

    // ---- Loaders ----
    async refreshCatalog() {
      const c = await this.fetchJSON("GET", "/api/catalog");
      this.catalog = c.templates;
      this.dataTypes = c.data_types;
      this.registerTypes = c.register_types || [
        "coil", "discrete_input", "input_register", "holding_register",
      ];
    },
    async refreshConfig() {
      this.config = await this.fetchJSON("GET", "/api/config");
      if (!this.selectedSensorId && this.config.sensors.length > 0) {
        this.selectedSensorId = this.config.sensors[0].id;
      }
    },
    async refreshStatus() {
      try {
        const s = await this.fetchJSON("GET", "/api/status");
        this.status = s;
        const map = {};
        for (const v of s.values) map[`${v.sensor_id}:${v.name}`] = v;
        this.latestMap = map;
        const ev = await this.fetchJSON("GET", "/api/events?limit=40");
        this.events = ev.events;
        if (this.dumpOpen) {
          const sl = await this.fetchJSON("GET", "/api/slaves");
          this.slaves = sl.slaves;
        }
      } catch (_) { /* poller error: silent */ }
    },

    async toggleDump() {
      this.dumpOpen = !this.dumpOpen;
      if (this.dumpOpen) await this.refreshStatus();
    },

    addressOwner(slave, space, addr) {
      const rt = space.register_type;
      for (const s of slave.sensors) {
        for (const m of s.measurements) {
          if (m.register_type !== rt) continue;
          if (addr >= m.address && addr < m.address + m.register_count) {
            const idx = addr - m.address;
            const total = m.register_count;
            const hint = total > 1
              ? `[${idx + 1}/${total}] ${m.data_type}`
              : `${m.data_type}${m.scale !== 1 ? ' · scale=' + m.scale : ''}${m.unit ? ' · ' + m.unit : ''}`;
            return { sensorId: s.id, measurementName: m.name, hint };
          }
        }
      }
      return null;
    },

    isBitSpace(rt) {
      return rt === "coil" || rt === "discrete_input";
    },

    rtLabel(rt) {
      const map = {
        coil: "Coil",
        discrete_input: "Discrete Input",
        input_register: "Input Register",
        holding_register: "Holding Register",
      };
      return map[rt] || rt;
    },

    // ---- Sensor ops ----
    async createSensor() {
      if (!this.newSensor.id.trim()) {
        this.flash("Devi specificare un id univoco");
        return;
      }
      const created = await this.fetchJSON("POST", "/api/sensors", { ...this.newSensor });
      await this.refreshConfig();
      this.selectedSensorId = created.id;
      this.newSensor = { id: "", unit_id: this.suggestNextUnitId(), base_address: this.suggestNextBase(), byte_order: "big", word_order: "big", write_rate_seconds: 1.0 };
      this.flash(`Sensore '${created.id}' creato`);
    },
    async patchSensor(id, patch) {
      await this.fetchJSON("PUT", `/api/sensors/${id}`, patch);
      await this.refreshConfig();
    },
    async deleteSensor(id) {
      if (!confirm(`Eliminare il sensore '${id}'?`)) return;
      await this.fetchJSON("DELETE", `/api/sensors/${id}`);
      if (this.selectedSensorId === id) this.selectedSensorId = null;
      await this.refreshConfig();
    },
    suggestNextBase() {
      const bases = this.config.sensors.map((s) => s.base_address);
      if (bases.length === 0) return 0;
      return Math.max(...bases) + 20;
    },
    suggestNextUnitId() {
      const used = new Set(this.config.sensors.map((s) => s.unit_id));
      const def = this.status.server.default_unit_id || 1;
      if (used.size === 0) return def;
      let next = Math.max(...used) + 1;
      if (next < 1) next = 1;
      return Math.min(next, 247);
    },

    // ---- Measurement ops ----
    async addMeasurementFromTemplate(t) {
      if (!this.selectedSensorId) {
        this.flash("Seleziona prima un sensore");
        return;
      }
      const sensor = this.config.sensors.find((s) => s.id === this.selectedSensorId);
      const next = this.nextOffset(sensor, t.register_type);
      try {
        await this.fetchJSON("POST", `/api/sensors/${sensor.id}/measurements`, {
          template_name: t.name,
          offset: next,
        });
        await this.refreshConfig();
        this.flash(`Aggiunta '${t.label}' al sensore ${sensor.id}`);
      } catch (e) { /* already flashed */ }
    },
    nextOffset(sensor, registerType) {
      // Each address space has its own offsets — only consider measurements
      // that share the same register_type.
      if (!sensor) return 0;
      const same = sensor.measurements.filter(
        (m) => !registerType || m.register_type === registerType,
      );
      if (same.length === 0) return 0;
      let max = 0;
      for (const m of same) {
        const regs = this.regCount(m.data_type);
        max = Math.max(max, m.offset + regs);
      }
      return max;
    },
    regCount(dt) {
      switch (dt) {
        case "uint16":
        case "int16": return 1;
        case "uint32":
        case "int32":
        case "float32": return 2;
        case "float64": return 4;
        default: return 1;
      }
    },
    async patchMeasurement(sensorId, name, patch) {
      await this.fetchJSON("PUT", `/api/sensors/${sensorId}/measurements/${name}`, patch);
      await this.refreshConfig();
    },
    async deleteMeasurement(sensorId, name) {
      await this.fetchJSON("DELETE", `/api/sensors/${sensorId}/measurements/${name}`);
      await this.refreshConfig();
    },

    // ---- Lifecycle ----
    async toggleServer() {
      const url = this.status.server.running ? "/api/server/stop" : "/api/server/start";
      await this.fetchJSON("POST", url);
      await this.refreshStatus();
    },
    async toggleSimulator() {
      const url = this.status.simulator.running ? "/api/simulator/stop" : "/api/simulator/start";
      await this.fetchJSON("POST", url);
      await this.refreshStatus();
    },

    // ---- Helpers for templates ----
    latestFor(sensorId, name) {
      return this.latestMap[`${sensorId}:${name}`] || null;
    },
    formatValue(v) {
      if (!v) return "—";
      const num = (typeof v.roundtrip === "number" && !isNaN(v.roundtrip))
        ? v.roundtrip
        : v.scaled;
      const isFloat = v.data_type === "float32" || v.data_type === "float64";
      const formatted = isFloat ? num.toFixed(3) : num.toFixed(2);
      return `${formatted} ${v.unit || ""}`.trim();
    },
    liveAddrInfo(v) {
      if (!v) return "";
      return `@${v.address} ${v.hex} ${v.byte_order}/${v.word_order}`;
    },
    formatTime(ts) {
      if (!ts) return "";
      const d = new Date(ts * 1000);
      return d.toLocaleTimeString();
    },
    totalMeasurements() {
      return this.config.sensors.reduce((acc, s) => acc + s.measurements.length, 0);
    },
  }));
});
