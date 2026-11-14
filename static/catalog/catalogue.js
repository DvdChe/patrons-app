// Catalogue page: client-side search, facets and pagination over the user's patterns.
// Ported from the MVP page (moteur-patrons.html). Size recommendations are computed by the
// server (/api/recommendations) for the selected profile: no sizing logic lives here.

const MMAX = 6; // yardage slider maximum = no limit
const PAGE_SIZE = 48;
const PROFILE_KEY = "patrons.profile"; // remembered profile (per browser convenience only)

// Same normalization as apps/catalog/text.py: ligatures, accents and any non-ASCII removed.
const norm = (s) =>
  (s || "")
    .replace(/[œŒ]/g, "oe")
    .replace(/[æÆ]/g, "ae")
    .normalize("NFKD")
    .replace(/[^\x00-\x7f]/g, "")
    .toLowerCase();

const fmt = (v) => String(v).replace(".", ",");

const csrfToken = () => (document.cookie.match(/(?:^|; )csrftoken=([^;]+)/) || [])[1] || "";

async function api(url, options = {}) {
  const res = await fetch(url, {
    ...options,
    headers: { "Content-Type": "application/json", "X-CSRFToken": csrfToken(), ...(options.headers || {}) },
  });
  if (res.status === 204) return null;
  const data = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(data.error || `Erreur ${res.status}`);
  return data;
}

const storage = {
  get(key) {
    try { return localStorage.getItem(key); } catch { return null; }
  },
  set(key, value) {
    try { localStorage.setItem(key, value); } catch { /* private mode: ignore */ }
  },
};

// Patterns are kept outside Alpine's reactive state: ~1000 proxied objects would slow every
// filter change. Only the computed `view` is reactive.
let ROWS = [];
let RECS = {};

const FIELD = {
  brand: (x) => x.brand,
  family: (x) => x.family,
  audience: (x) => x.audience,
  fabric: (x) => x.fabric,
  difficulty: (x) => x.difficulty,
  seam: (x) => x.seam,
  sewn: (x) => (x.sewn ? "yes" : "no"),
};

document.addEventListener("alpine:init", () => {
  Alpine.data("catalogue", () => ({
    MMAX,
    MEASURES: [
      { key: "chest", label: "Poitrine" },
      { key: "underbust", label: "Dessous de poitrine" },
      { key: "waist", label: "Taille" },
      { key: "hip", label: "Hanches" },
      { key: "back_width", label: "Carrure dos" },
      { key: "back_waist_length", label: "Longueur taille-dos" },
      { key: "inseam", label: "Entrejambe" },
      { key: "height", label: "Stature" },
    ],
    SELECTS: [
      { key: "family", label: "Famille", all: "Toutes" },
      { key: "audience", label: "Public", all: "Tous" },
      { key: "fabric", label: "Tissu", all: "Tous" },
      { key: "seam", label: "Marges de couture", all: "Toutes" },
      { key: "difficulty", label: "Niveau", all: "Tous" },
      { key: "sewn", label: "Cousu", all: "Tous" },
    ],
    SEASONS: [
      { value: "summer", label: "Été" },
      { value: "midseason", label: "Mi-saison" },
      { value: "winter", label: "Hiver" },
    ],

    // filters
    q: "",
    sort: "name",
    brand: "",
    filters: { family: "", audience: "", fabric: "", seam: "", difficulty: "", sewn: "" },
    seasons: [],
    maxYardage: MMAX,
    withThumb: false,
    fitOnly: false,
    chartOnly: false,
    tags: [],
    page: 1,

    // data
    loading: true,
    brands: [],
    choices: {},
    tagGroups: {},
    recVersion: 0, // bumped when RECS changes, so cards re-read their recommendation
    view: { count: 0, cards: [], pages: 1, counts: {}, seasonCounts: {}, tagCounts: {} },

    // profiles
    profiles: [],
    profileId: null,
    measures: {},
    profileMessage: "",
    saveTimer: null,

    // sheet
    sheetOpen: false,
    sheetHtml: "",
    quickMessage: "",

    async init() {
      const [catalogue] = await Promise.all([api("/api/catalogue"), this.loadProfiles()]);
      ROWS = catalogue.patterns.map((x) => ({ ...x, tset: new Set(x.tags) }));
      this.choices = catalogue.choices;
      this.tagGroups = catalogue.tags;
      this.brands = [...new Set(ROWS.map((x) => x.brand))].sort((a, b) => a.localeCompare(b, "fr"));
      await this.loadRecommendations();
      this.loading = false;
      this.render();
      // Back from the edit page: reopen the pattern sheet.
      const m = location.hash.match(/^#pattern-(\d+)$/);
      if (m) this.openSheet(Number(m[1]));
    },

    // ---- profiles

    async loadProfiles() {
      this.profiles = (await api("/api/profiles")).profiles;
      const remembered = Number(storage.get(PROFILE_KEY));
      const exists = this.profiles.some((p) => p.id === remembered);
      this.profileId = exists ? remembered : this.profiles[0].id;
      this.syncMeasures();
    },

    profile() {
      return this.profiles.find((p) => p.id === this.profileId);
    },

    syncMeasures() {
      const m = this.profile().measures;
      this.measures = Object.fromEntries(this.MEASURES.map(({ key }) => [key, m[key] ?? ""]));
    },

    async loadRecommendations() {
      RECS = await api(`/api/recommendations?profile=${this.profileId}`);
      this.recVersion++;
    },

    rec(x) {
      this.recVersion; // reactive dependency
      return RECS[x.id] || { text: "", muted: true, fits: null };
    },

    async selectProfile() {
      storage.set(PROFILE_KEY, this.profileId);
      this.syncMeasures();
      await this.loadRecommendations();
      this.render();
    },

    queueSave() {
      clearTimeout(this.saveTimer);
      this.saveTimer = setTimeout(() => this.saveMeasures(), 400);
    },

    async saveMeasures() {
      const measures = Object.fromEntries(
        Object.entries(this.measures).map(([k, v]) => [k, v === "" || v === null ? null : Number(v)]),
      );
      try {
        const saved = await api(`/api/profiles/${this.profileId}`, {
          method: "PATCH",
          body: JSON.stringify({ measures }),
        });
        Object.assign(this.profile(), saved);
        this.profileMessage = "";
        await this.loadRecommendations();
        this.render(false);
      } catch (e) {
        this.profileMessage = e.message;
      }
    },

    async newProfile() {
      const name = prompt("Nom du nouveau profil :", "Mon compagnon");
      if (!name) return;
      try {
        const created = await api("/api/profiles", { method: "POST", body: JSON.stringify({ name }) });
        this.profiles.push(created);
        this.profileId = created.id;
        await this.selectProfile();
      } catch (e) {
        this.profileMessage = e.message;
      }
    },

    async renameProfile() {
      const name = prompt("Nouveau nom :", this.profile().name);
      if (!name) return;
      try {
        const saved = await api(`/api/profiles/${this.profileId}`, {
          method: "PATCH",
          body: JSON.stringify({ name }),
        });
        Object.assign(this.profile(), saved);
      } catch (e) {
        this.profileMessage = e.message;
      }
    },

    async deleteProfile() {
      if (this.profiles.length < 2) {
        this.profileMessage = "Il faut garder au moins un profil.";
        return;
      }
      if (!confirm(`Supprimer le profil « ${this.profile().name} » ?`)) return;
      try {
        await api(`/api/profiles/${this.profileId}`, { method: "DELETE" });
        this.profiles = this.profiles.filter((p) => p.id !== this.profileId);
        this.profileId = this.profiles[0].id;
        await this.selectProfile();
      } catch (e) {
        this.profileMessage = e.message;
      }
    },

    // ---- filters

    choicesFor(key) {
      return this.choices[key] || [];
    },

    facet(key, value) {
      const counts = this.view.counts[key] || {};
      return (value === "" ? counts[""] : counts[value]) || 0;
    },

    selected(key) {
      return key === "brand" ? this.brand : this.filters[key];
    },

    seasonsOk(x, selected) {
      return !selected.length || x.seasons.includes("all") || selected.some((s) => x.seasons.includes(s));
    },

    pass(x, skip, words) {
      if (words.length && !words.every((w) => x.search.includes(w))) return false;
      for (const key of Object.keys(FIELD)) {
        const value = this.selected(key);
        if (skip !== key && value && FIELD[key](x) !== value) return false;
      }
      if (skip !== "seasons" && !this.seasonsOk(x, this.seasons)) return false;
      if (this.maxYardage < MMAX && (x.yardage_max === null || x.yardage_max > this.maxYardage)) return false;
      if (this.withThumb && !x.thumb) return false;
      if (this.chartOnly && !x.has_body_chart) return false;
      if (this.fitOnly && (RECS[x.id] || {}).fits === false) return false;
      if (skip !== "tags" && !this.tags.every((k) => x.tset.has(k))) return false;
      return true;
    },

    toggleTag(key) {
      this.tags = this.tags.includes(key) ? this.tags.filter((k) => k !== key) : [...this.tags, key];
      this.refresh();
    },

    reset() {
      Object.assign(this, { q: "", brand: "", seasons: [], maxYardage: MMAX, withThumb: false, fitOnly: false, chartOnly: false, tags: [] });
      this.filters = { family: "", audience: "", fabric: "", seam: "", difficulty: "", sewn: "" };
      this.refresh();
    },

    refresh() {
      this.render();
    },

    render(resetPage = true) {
      if (resetPage) this.page = 1;
      const words = norm(this.q.trim()).split(/\s+/).filter(Boolean);
      const list = ROWS.filter((x) => this.pass(x, "", words));

      const tagCounts = {};
      list.forEach((x) => x.tset.forEach((k) => (tagCounts[k] = (tagCounts[k] || 0) + 1)));

      const counts = {};
      for (const key of Object.keys(FIELD)) {
        const c = { "": 0 };
        for (const x of ROWS) {
          if (!this.pass(x, key, words)) continue;
          c[""]++;
          const v = FIELD[key](x);
          c[v] = (c[v] || 0) + 1;
        }
        counts[key] = c;
      }

      const seasonBase = ROWS.filter((x) => this.pass(x, "seasons", words));
      const seasonCounts = {};
      for (const { value } of this.SEASONS) {
        const sel = this.seasons.includes(value) ? this.seasons : [...this.seasons, value];
        seasonCounts[value] = seasonBase.filter((x) => this.seasonsOk(x, sel)).length;
      }

      const byName = (a, b) => a.name.localeCompare(b.name, "fr");
      const sorters = {
        name: byName,
        brand: (a, b) => a.brand.localeCompare(b.brand, "fr") || byName(a, b),
        yardage: (a, b) => (a.yardage_max ?? 99) - (b.yardage_max ?? 99) || byName(a, b),
      };
      list.sort(sorters[this.sort]);

      const pages = Math.max(1, Math.ceil(list.length / PAGE_SIZE));
      this.page = Math.min(Math.max(1, this.page), pages);
      this.view = {
        count: list.length,
        cards: list.slice((this.page - 1) * PAGE_SIZE, this.page * PAGE_SIZE),
        pages,
        counts,
        seasonCounts,
        tagCounts,
      };
    },

    // ---- pagination (same markup as the MVP)

    pagerHtml() {
      const { count, pages } = this.view;
      if (!count) return '<span class="pi">Aucun résultat</span>';
      const a = (this.page - 1) * PAGE_SIZE + 1;
      const b = Math.min(this.page * PAGE_SIZE, count);
      let h = `<span class="pi">Cartes ${a}–${b} sur ${count} · page ${this.page} / ${pages}</span>`;
      if (pages > 1) {
        h += `<button data-p="${this.page - 1}"${this.page === 1 ? " disabled" : ""}>‹</button>`;
        for (let i = 1; i <= pages; i++) h += `<button data-p="${i}"${i === this.page ? ' class="on"' : ""}>${i}</button>`;
        h += `<button data-p="${this.page + 1}"${this.page === pages ? " disabled" : ""}>›</button>`;
      }
      return h;
    },

    pagerClick(event) {
      const p = event.target.dataset && event.target.dataset.p;
      if (!p) return;
      this.page = Number(p);
      this.render(false);
      document.querySelector("main").scrollIntoView();
    },

    // ---- pattern sheet

    async openSheet(id) {
      this.quickMessage = "";
      const res = await fetch(`/patterns/${id}/sheet?profile=${this.profileId}`);
      this.sheetHtml = res.ok ? await res.text() : "<p>Impossible de charger la fiche.</p>";
      this.sheetOpen = true;
    },

    closeSheet() {
      this.sheetOpen = false;
      if (location.hash) history.replaceState(null, "", location.pathname);
    },

    async saveQuick(id, data) {
      try {
        const saved = await api(`/api/patterns/${id}`, { method: "PATCH", body: JSON.stringify(data) });
        // Replace the row instead of mutating it: ROWS is not reactive, a new object makes
        // Alpine refresh the card.
        const i = ROWS.findIndex((x) => x.id === id);
        if (i >= 0) ROWS[i] = { ...ROWS[i], sewn: saved.sewn };
        this.quickMessage = "Enregistré";
        this.render(false);
      } catch (e) {
        this.quickMessage = e.message;
      }
    },

    fmt,
  }));
});
