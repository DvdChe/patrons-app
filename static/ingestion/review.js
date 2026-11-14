// Review screen of a new pattern: thumbnail picker with crop, brand size charts, own size grid.
// Server-side values come from the "review-config" JSON script (see ingestion/views.py).

document.addEventListener("alpine:init", () => {
  const config = JSON.parse(document.getElementById("review-config").textContent);
  const clamp = (v) => Math.min(1, Math.max(0, v));

  Alpine.data("review", () => ({
    candidates: config.candidates,
    measures: config.measures,
    thumbRef: config.thumbRef,
    selected: null,
    crop: config.crop ? config.crop.split(",").map(Number) : [0, 0, 1, 1],
    dragging: null,
    charts: [],
    chosenCharts: config.chosenCharts ?? [],
    autoChooseCharts: config.chosenCharts === null,
    rows: config.rows,

    init() {
      this.selected = this.candidates.find((c) => c.ref === this.thumbRef) || null;
      const brand = document.getElementById("id_brand");
      const sizes = document.getElementById("id_size_range");
      brand.addEventListener("change", () => {
        this.autoChooseCharts = true;
        this.loadCharts();
      });
      sizes.addEventListener("change", () => this.loadCharts());
      this.loadCharts();
    },

    // ---- thumbnail

    pick(c) {
      this.thumbRef = c.ref;
      this.selected = c;
      this.resetCrop();
    },

    resetCrop() {
      this.crop = [0, 0, 1, 1];
    },

    cropValue() {
      return this.thumbRef ? this.crop.map((v) => v.toFixed(4)).join(",") : "";
    },

    point(event) {
      const r = event.currentTarget.getBoundingClientRect();
      return [clamp((event.clientX - r.left) / r.width), clamp((event.clientY - r.top) / r.height)];
    },

    cropStart(event) {
      event.currentTarget.setPointerCapture(event.pointerId);
      this.dragging = this.point(event);
      this.crop = [...this.dragging, ...this.dragging];
    },

    cropMove(event) {
      if (!this.dragging) return;
      const [x, y] = this.point(event);
      const [x0, y0] = this.dragging;
      this.crop = [Math.min(x0, x), Math.min(y0, y), Math.max(x0, x), Math.max(y0, y)];
    },

    cropEnd() {
      if (!this.dragging) return;
      this.dragging = null;
      const [x0, y0, x1, y1] = this.crop;
      if (x1 - x0 < 0.05 || y1 - y0 < 0.05) this.resetCrop(); // a click, not a rectangle
    },

    cropStyle() {
      const [x0, y0, x1, y1] = this.crop;
      return `left:${x0 * 100}%;top:${y0 * 100}%;width:${(x1 - x0) * 100}%;height:${(y1 - y0) * 100}%`;
    },

    // ---- size charts

    async loadCharts() {
      const brand = document.getElementById("id_brand").value;
      const sizes = document.getElementById("id_size_range").value;
      if (!brand) {
        this.charts = [];
        this.chosenCharts = [];
        return;
      }
      const res = await fetch(`/api/brands/${brand}/charts?sizes=${encodeURIComponent(sizes)}`);
      this.charts = res.ok ? (await res.json()).charts : [];
      const ids = this.charts.map((c) => c.id);
      if (this.autoChooseCharts) {
        // Pre-check the brand body charts that cover the pattern sizes.
        this.chosenCharts = this.charts.filter((c) => c.body && c.covers).map((c) => c.id);
        this.autoChooseCharts = false;
      } else {
        this.chosenCharts = this.chosenCharts.filter((id) => ids.includes(id));
      }
    },

    toggleChart(id) {
      this.chosenCharts = this.chosenCharts.includes(id)
        ? this.chosenCharts.filter((c) => c !== id)
        : [...this.chosenCharts, id];
    },

    addRow() {
      this.rows.push({ label: "" });
    },

    serialize() {
      // Rows without any measure (pre-filled size labels left empty) are not sent.
      const filled = (r) => this.measures.some((m) => String(r[m.key] ?? "").trim());
      this.rows = this.rows.filter(filled);
    },
  }));
});
