document.addEventListener("alpine:init", () => {
  Alpine.data("simApp", () => ({
    catalog: [],
    dataTypes: [],
    registerTypes: [],
    config: { servers: [], modbus: {}, tick_seconds: 1.0, log_level: "INFO" },
    status: { servers: [], values: [] },
    events: [],
    // Latest values keyed by `${serverId}:${sensorId}:${name}`.
    latestMap: {},
    // Per-server raw register dump.
    slavesByServer: [],
    dumpOpen: false,
    // (serverId, sensorId) currently expanded in the catalog "+ Add" target.
    selectedTarget: { serverId: null, sensorId: null },
    // One pending "new sensor" form per server, keyed by serverId.
    newSensorByServer: {},
    newServer: {
      id: "", label: "", host: "0.0.0.0", port: 5020,
      default_unit_id: 1, register_count_min: 16, auto_start: false,
    },
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
        if (Array.isArray(detail)) {
          detail = detail.map(d => d.msg || JSON.stringify(d)).join("; ");
        }
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
      // Ensure each server has a "new sensor" form scaffold.
      for (const srv of this.config.servers) {
        if (!this.newSensorByServer[srv.id]) {
          this.newSensorByServer[srv.id] = this.blankNewSensor(srv);
        }
      }
      // Drop scaffolds for deleted servers.
      const liveIds = new Set(this.config.servers.map(s => s.id));
      for (const sid of Object.keys(this.newSensorByServer)) {
        if (!liveIds.has(sid)) delete this.newSensorByServer[sid];
      }
    },
    async refreshStatus() {
      try {
        const s = await this.fetchJSON("GET", "/api/status");
        this.status = s;
        const map = {};
        for (const v of s.values) {
          map[`${v.server_id}:${v.sensor_id}:${v.name}`] = v;
        }
        this.latestMap = map;
        const ev = await this.fetchJSON("GET", "/api/events?limit=40");
        this.events = ev.events;
        if (this.dumpOpen) {
          const sl = await this.fetchJSON("GET", "/api/slaves");
          this.slavesByServer = sl.servers;
        }
      } catch (_) { /* poller error: silent */ }
    },

    async toggleDump() {
      this.dumpOpen = !this.dumpOpen;
      if (this.dumpOpen) await this.refreshStatus();
    },

    // ---- Status helpers ----
    serverStatus(serverId) {
      return (this.status.servers || []).find(s => s.id === serverId)
        || { running: false, simulator_running: false, slaves: [] };
    },
    anyServerRunning() {
      return (this.status.servers || []).some(s => s.running);
    },
    runningServerCount() {
      return (this.status.servers || []).filter(s => s.running).length;
    },
    isLocked(serverId) {
      return this.serverStatus(serverId).running;
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

    // ---- Server CRUD + lifecycle ----
    blankNewSensor(srv) {
      return {
        id: "",
        unit_id: srv.default_unit_id || 1,
        base_address: 0,
        byte_order: "big",
        word_order: "big",
        write_rate_seconds: 1.0,
      };
    },
    async createServer() {
      if (!this.newServer.id.trim()) {
        this.flash("Devi specificare un id univoco per il server");
        return;
      }
      try {
        await this.fetchJSON("POST", "/api/servers", { ...this.newServer });
        await this.refreshConfig();
        await this.refreshStatus();
        this.flash(`Server '${this.newServer.id}' creato`);
        this.newServer = {
          id: "", label: "", host: "0.0.0.0",
          port: this.suggestNextPort(),
          default_unit_id: 1, register_count_min: 16, auto_start: false,
        };
      } catch (_) { /* flashed */ }
    },
    suggestNextPort() {
      const used = (this.config.servers || []).map(s => s.port);
      if (used.length === 0) return 5020;
      return Math.max(...used) + 1;
    },
    async patchServer(serverId, patch) {
      try {
        await this.fetchJSON("PUT", `/api/servers/${serverId}`, patch);
        await this.refreshConfig();
      } catch (_) { /* flashed */ }
    },
    async deleteServer(serverId) {
      if (!confirm(`Eliminare il server '${serverId}' e tutti i suoi sensori?`)) return;
      try {
        await this.fetchJSON("DELETE", `/api/servers/${serverId}`);
        await this.refreshConfig();
        await this.refreshStatus();
      } catch (_) { /* flashed */ }
    },
    async toggleServerLifecycle(serverId) {
      const running = this.serverStatus(serverId).running;
      const url = running
        ? `/api/servers/${serverId}/stop`
        : `/api/servers/${serverId}/start`;
      try {
        await this.fetchJSON("POST", url);
        await this.refreshStatus();
      } catch (_) { /* flashed */ }
    },
    async toggleSimulator(serverId) {
      const running = this.serverStatus(serverId).simulator_running;
      const url = running
        ? `/api/servers/${serverId}/simulator/stop`
        : `/api/servers/${serverId}/simulator/start`;
      try {
        await this.fetchJSON("POST", url);
        await this.refreshStatus();
      } catch (_) { /* flashed */ }
    },
    async kickServer(serverId) {
      try {
        await this.fetchJSON("POST", `/api/servers/${serverId}/kick`);
        await this.refreshStatus();
      } catch (_) { /* flashed */ }
    },
    async startAll() {
      try {
        await this.fetchJSON("POST", "/api/servers/start-all");
        await this.fetchJSON("POST", "/api/simulator/start-all");
        await this.refreshStatus();
      } catch (_) {}
    },
    async stopAll() {
      try {
        await this.fetchJSON("POST", "/api/simulator/stop-all");
        await this.fetchJSON("POST", "/api/servers/stop-all");
        await this.refreshStatus();
      } catch (_) {}
    },

    // ---- Sensor CRUD ----
    async createSensor(serverId) {
      const draft = this.newSensorByServer[serverId];
      if (!draft || !draft.id.trim()) {
        this.flash("Devi specificare un id univoco per il sensore");
        return;
      }
      try {
        const created = await this.fetchJSON(
          "POST", `/api/servers/${serverId}/sensors`, { ...draft },
        );
        await this.refreshConfig();
        this.selectedTarget = { serverId, sensorId: created.id };
        const srv = this.config.servers.find(s => s.id === serverId);
        this.newSensorByServer[serverId] = this.blankNewSensor(srv);
        this.newSensorByServer[serverId].unit_id = this.suggestNextUnitId(serverId);
        this.newSensorByServer[serverId].base_address = this.suggestNextBase(serverId);
        this.flash(`Sensore '${created.id}' creato in '${serverId}'`);
      } catch (_) { /* flashed */ }
    },
    async patchSensor(serverId, sensorId, patch) {
      try {
        await this.fetchJSON("PUT", `/api/servers/${serverId}/sensors/${sensorId}`, patch);
        await this.refreshConfig();
      } catch (_) { /* flashed */ }
    },
    async deleteSensor(serverId, sensorId) {
      if (!confirm(`Eliminare il sensore '${sensorId}' dal server '${serverId}'?`)) return;
      try {
        await this.fetchJSON("DELETE", `/api/servers/${serverId}/sensors/${sensorId}`);
        if (this.selectedTarget.sensorId === sensorId) {
          this.selectedTarget = { serverId: null, sensorId: null };
        }
        await this.refreshConfig();
      } catch (_) {}
    },
    suggestNextBase(serverId) {
      const srv = this.config.servers.find(s => s.id === serverId);
      if (!srv || srv.sensors.length === 0) return 0;
      const bases = srv.sensors.map(s => s.base_address);
      return Math.max(...bases) + 20;
    },
    suggestNextUnitId(serverId) {
      const srv = this.config.servers.find(s => s.id === serverId);
      if (!srv) return 1;
      const used = new Set(srv.sensors.map(s => s.unit_id));
      const def = srv.default_unit_id || 1;
      if (used.size === 0) return def;
      let next = Math.max(...used) + 1;
      if (next < 1) next = 1;
      return Math.min(next, 247);
    },

    // ---- Measurement CRUD ----
    selectTarget(serverId, sensorId) {
      this.selectedTarget = { serverId, sensorId };
    },
    isTargetSelected(serverId, sensorId) {
      return this.selectedTarget.serverId === serverId
        && this.selectedTarget.sensorId === sensorId;
    },
    async addMeasurementFromTemplate(t) {
      const { serverId, sensorId } = this.selectedTarget;
      if (!serverId || !sensorId) {
        this.flash("Seleziona prima un sensore (cliccando sull'header)");
        return;
      }
      const srv = this.config.servers.find(s => s.id === serverId);
      const sensor = srv && srv.sensors.find(s => s.id === sensorId);
      if (!sensor) return;
      const next = this.nextOffset(sensor, t.register_type);
      try {
        await this.fetchJSON(
          "POST",
          `/api/servers/${serverId}/sensors/${sensorId}/measurements`,
          { template_name: t.name, offset: next },
        );
        await this.refreshConfig();
        this.flash(`Aggiunta '${t.label}' a ${serverId}/${sensorId}`);
      } catch (_) {}
    },
    nextOffset(sensor, registerType) {
      if (!sensor) return 0;
      const same = sensor.measurements.filter(
        m => !registerType || m.register_type === registerType,
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
    async patchMeasurement(serverId, sensorId, name, patch) {
      try {
        await this.fetchJSON(
          "PUT",
          `/api/servers/${serverId}/sensors/${sensorId}/measurements/${name}`,
          patch,
        );
        await this.refreshConfig();
      } catch (_) {}
    },
    async deleteMeasurement(serverId, sensorId, name) {
      try {
        await this.fetchJSON(
          "DELETE",
          `/api/servers/${serverId}/sensors/${sensorId}/measurements/${name}`,
        );
        await this.refreshConfig();
      } catch (_) {}
    },

    // ---- Live helpers ----
    latestFor(serverId, sensorId, name) {
      return this.latestMap[`${serverId}:${sensorId}:${name}`] || null;
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
    totalSensors() {
      return (this.config.servers || []).reduce((acc, s) => acc + s.sensors.length, 0);
    },
    totalMeasurements() {
      return (this.config.servers || []).reduce(
        (acc, s) => acc + s.sensors.reduce((a, x) => a + x.measurements.length, 0),
        0,
      );
    },
  }));
});
