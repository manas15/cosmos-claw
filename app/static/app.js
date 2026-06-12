const statusEl = document.getElementById("status");
const sponsorsEl = document.getElementById("sponsors");
const sleep = (ms) => new Promise((res) => setTimeout(res, ms));

function renderSponsors(sponsors) {
  if (!sponsorsEl) return;
  sponsorsEl.innerHTML = (sponsors || [])
    .map(
      (s) => `<span class="sponsor ${s.enabled ? "on" : "off"}"
        title="${escapeHtml(s.role)} — ${escapeHtml(s.detail || "")}">
        <span class="sp-dot"></span>
        <span class="sp-text">
          <b>${escapeHtml(s.name)}</b>
          <i>${escapeHtml(s.role)}</i>
        </span>
      </span>`
    )
    .join("");
}

async function checkHealth() {
  try {
    const r = await fetch("/api/health");
    const h = await r.json();
    if (statusEl) {
      if (h.backend_ready) {
        statusEl.textContent = "All systems ready";
        statusEl.className = "status ok";
      } else {
        statusEl.textContent = `Backend: ${h.backend_reason}`;
        statusEl.className = "status warn";
      }
    }
    renderSponsors(h.sponsors);
  } catch {
    if (statusEl) {
      statusEl.textContent = "backend unreachable";
      statusEl.className = "status warn";
    }
  }
}

// Poll a generation job until it finishes; onTick(current,total,label) for UI.
async function pollJob(jobId, onTick) {
  while (true) {
    await sleep(1500);
    const r = await fetch(`/api/job/${jobId}`);
    if (!r.ok) throw new Error("Lost track of the job.");
    const job = await r.json();
    if (onTick) onTick(job.current, job.total, job.label);
    if (job.status === "done") return job;
    if (job.status === "error") throw new Error(job.error || "Generation failed");
  }
}

/* ========================== NAVIGATION ======================= */
// Two views only: the listings grid (home) and a per-listing studio.
const listingsPanel = document.getElementById("tab-listings");
const detailEl = document.getElementById("listingDetail");

function showListings() {
  detailEl.hidden = true;
  listingsPanel.hidden = false;
  loadListings();
  window.scrollTo({ top: 0, behavior: "smooth" });
}

/* ========================== LISTINGS ========================== */
const listingsGrid = document.getElementById("listingsGrid");
const listingsDir = document.getElementById("listingsDir");
const listingsEmpty = document.getElementById("listingsEmpty");

async function loadListings() {
  try {
    const r = await fetch("/api/listings");
    const data = await r.json();
    listingsDir.textContent = `Reading listings from: ${data.listings_dir}`;
    listingsGrid.innerHTML = "";
    listingsEmpty.hidden = data.listings.length > 0;
    data.listings.forEach((l) => listingsGrid.appendChild(listingCard(l)));
  } catch (e) {
    listingsGrid.innerHTML = `<p class="error">Could not load listings.</p>`;
  }
}

function listingCard(l) {
  const card = document.createElement("div");
  card.className = "card listing-card clickable";

  const strip = l.photos
    .slice(0, 10)
    .map((u) => `<img loading="lazy" src="${u}" alt="" />`)
    .join("");

  const badges = `${l.photo_count} photos${l.has_pdf ? " · 📄 PDF" : ""}`;
  const videoChip = l.video_count
    ? `<span class="chip ok">🎬 ${l.video_count} video${l.video_count > 1 ? "s" : ""}</span>`
    : `<span class="chip">No trailer yet</span>`;

  card.innerHTML = `
    <div class="listing-head">
      <h3>${escapeHtml(l.name)}</h3>
      <span class="muted small">${badges}</span>
    </div>
    <div class="photo-strip">${strip}</div>
    <p class="facts muted small">${escapeHtml(l.facts_preview || "No PDF facts found.")}</p>
    <div class="listing-actions">
      ${videoChip}
      <button class="btn open-btn">Open studio →</button>
    </div>
  `;

  card.addEventListener("click", () => openListingStudio(l.id));
  return card;
}

/* ====================== LISTING STUDIO ======================= */
// What the trailer can contain. Toggled on/off in the configuration panel.
const INCLUDE_OPTIONS = [
  { key: "photos", icon: "🛏", label: "Room tour" },
  { key: "map", icon: "🗺", label: "Neighborhood map" },
  { key: "transit", icon: "🚇", label: "Transit & connectivity" },
  { key: "amenities", icon: "✨", label: "Amenities" },
  { key: "host", icon: "⭐", label: "Host & reviews" },
  { key: "price", icon: "💰", label: "Nightly price" },
];
const PHOTO_LIMIT = 5; // show this many, then a "+N more" tile

let currentDetail = null;
let showAllPhotos = false;

// Format an epoch-ms timestamp in US Pacific time (auto PST/PDT).
function fmtPST(ms) {
  return new Date(ms).toLocaleString("en-US", {
    timeZone: "America/Los_Angeles",
    month: "short",
    day: "numeric",
    year: "numeric",
    hour: "numeric",
    minute: "2-digit",
    timeZoneName: "short",
  });
}

function versionItem(v, isLatest) {
  const stats = [
    v.scene_count ? `${v.scene_count} scenes` : "",
    v.info_card_count ? `${v.info_card_count} info cards` : "",
    v.location ? `📍 ${v.location}` : "",
    v.price ? `💰 ${v.price}` : "",
  ]
    .filter(Boolean)
    .map((s) => `<span class="vstat">${escapeHtml(s)}</span>`)
    .join("");
  return `
    <div class="version">
      <button class="version-video" data-src="${v.video_url}" ${v.poster ? `data-poster="${v.poster}"` : ""}>
        ${v.poster ? `<img class="vv-poster" src="${v.poster}" alt="" />` : `<span class="vv-blank"></span>`}
        <span class="vv-play">▶</span>
        ${isLatest ? `<span class="latest-chip">Latest</span>` : ""}
      </button>
      <div class="version-meta">
        ${v.title ? `<h4>${escapeHtml(v.title)}</h4>` : ""}
        <span class="ts">🕒 ${fmtPST(v.created_at)}</span>
        ${stats ? `<div class="vstats">${stats}</div>` : ""}
        <a class="btn btn-ghost btn-sm" download href="${v.video_url}">Download MP4</a>
      </div>
    </div>`;
}

// Big popup player. Created once and reused.
function openLightbox(src, poster) {
  let ov = document.getElementById("lightbox");
  if (!ov) {
    ov = document.createElement("div");
    ov.id = "lightbox";
    ov.className = "lightbox";
    ov.innerHTML = `<button class="lb-close" aria-label="Close">×</button>
      <video class="lb-video" controls playsinline></video>`;
    document.body.appendChild(ov);
    const close = () => {
      const vid = ov.querySelector("video");
      vid.pause();
      ov.classList.remove("open");
    };
    ov.addEventListener("click", (e) => { if (e.target === ov) close(); });
    ov.querySelector(".lb-close").addEventListener("click", close);
    document.addEventListener("keydown", (e) => {
      if (e.key === "Escape" && ov.classList.contains("open")) close();
    });
  }
  const vid = ov.querySelector("video");
  if (poster) vid.poster = poster; else vid.removeAttribute("poster");
  vid.src = src;
  ov.classList.add("open");
  vid.currentTime = 0;
  vid.play().catch(() => {});
}

const photoTile = (u) =>
  `<div class="thumb"><img loading="lazy" src="${u}" alt="" /></div>`;

// ---- editable chip lists (amenities, nearby) ----
function makeChip(text) {
  const span = document.createElement("span");
  span.className = "ed-chip";
  span.title = text;
  const lab = document.createElement("span");
  lab.className = "ed-label";
  lab.textContent = text;
  const x = document.createElement("button");
  x.className = "ed-x";
  x.type = "button";
  x.textContent = "×";
  x.addEventListener("click", () => span.remove());
  span.append(lab, x);
  return span;
}
function wireChipEditor(el, items) {
  if (!el) return;
  const list = el.querySelector(".chips");
  const input = el.querySelector(".chip-add");
  (items || []).forEach((t) => list.appendChild(makeChip(t)));
  input.addEventListener("keydown", (e) => {
    if (e.key === "Enter" && input.value.trim()) {
      e.preventDefault();
      list.appendChild(makeChip(input.value.trim()));
      input.value = "";
    }
  });
}
const readChips = (el) =>
  el ? [...el.querySelectorAll(".ed-label")].map((l) => l.textContent) : [];

// Editable text field cell. ``val`` is pre-filled from the listing PDF.
function field(cls, label, val, ph = "") {
  return `<div class="field">
    <label>${label}</label>
    <input class="${cls}" type="text" value="${escapeHtml(val || "")}" placeholder="${escapeHtml(ph)}" />
  </div>`;
}

// ---- Big, narrated generation overlay (for the demo) ----
const GEN_STAGES = [
  { id: "research", icon: "🔎", title: "Researching the neighborhood", sub: "Tavily search · nearby spots, transit & dining" },
  { id: "curate", icon: "🖼️", title: "Curating the best photos", sub: "GPT-4o picks & orders your strongest shots" },
  { id: "film", icon: "🎬", title: "Filming scenes with NVIDIA Cosmos 3", sub: "Image-to-video on Nebius GPUs" },
  { id: "cards", icon: "🗺️", title: "Designing maps & info cards", sub: "Neighborhood map, price & highlights" },
  { id: "stitch", icon: "🎞️", title: "Stitching the trailer", sub: "Concatenate, color-grade & add titles" },
  { id: "score", icon: "🔊", title: "Scoring & narrating", sub: "OpenAI voiceover + music bed" },
];
let genT0 = 0;
let genReached = 0;

function buildGenOverlay() {
  let ov = document.getElementById("genOverlay");
  if (ov) return ov;
  ov = document.createElement("div");
  ov.id = "genOverlay";
  ov.className = "gen-overlay";
  ov.innerHTML = `
    <div class="gen-card">
      <div class="gen-head">
        <div class="gen-spinner"></div>
        <div class="gen-headtext">
          <h2>Creating your cinematic trailer</h2>
          <p class="gen-now">Warming up…</p>
        </div>
      </div>
      <div class="gen-stages">
        ${GEN_STAGES.map(
          (s) => `<div class="gen-stage" data-id="${s.id}">
            <span class="gs-ic">${s.icon}</span>
            <div class="gs-text"><b>${s.title}</b><i>${s.sub}</i></div>
            <span class="gs-state"></span>
          </div>`
        ).join("")}
      </div>
      <div class="gen-bar"><div class="gen-bar-fill"></div></div>
      <div class="gen-foot"><span class="gen-pct">0%</span><span class="gen-err"></span></div>
    </div>`;
  document.body.appendChild(ov);
  return ov;
}

function openGenOverlay() {
  const ov = buildGenOverlay();
  genT0 = Date.now();
  genReached = 0;
  ov.querySelector(".gen-err").textContent = "";
  ov.querySelector(".gen-spinner").classList.remove("done");
  ov.classList.add("open");
  updateGenOverlay(0, 0, "Researching the neighborhood…", "running");
}

function genStageIndex(label, current, status) {
  if (status === "done") return GEN_STAGES.length;
  const l = (label || "").toLowerCase();
  if (l.includes("scoring") || l.includes("narrat")) return 5;
  if (l.includes("stitch") || l.includes("concat")) return 4;
  if (l.includes("mapping") || l.includes("price") || l.includes("rating") || l.includes("detail")) return 3;
  if (l.includes("filming") || current > 0) return 2;
  // planning phase: show research first, then curation
  return (Date.now() - genT0) / 1000 < 5 ? 0 : 1;
}

function updateGenOverlay(current, total, label, status) {
  const ov = document.getElementById("genOverlay");
  if (!ov) return;
  const idx = Math.max(genReached, genStageIndex(label, current, status));
  genReached = idx;

  ov.querySelectorAll(".gen-stage").forEach((el, i) => {
    el.classList.toggle("done", i < idx);
    el.classList.toggle("active", i === idx);
    el.classList.toggle("pending", i > idx);
  });

  const active = GEN_STAGES[Math.min(idx, GEN_STAGES.length - 1)];
  let now = active ? active.title : "Finishing up";
  if (idx === 2 && total > 0) now = `Filming scene ${Math.min(current + 1, total)} of ${total} · Cosmos 3`;
  if (status === "done") now = "Done — your trailer is ready";
  ov.querySelector(".gen-now").textContent = now;

  const pct = status === "done" ? 100 : total > 0 ? Math.round((current / total) * 100) : Math.min(95, Math.round((Date.now() - genT0) / 120));
  ov.querySelector(".gen-bar-fill").style.width = `${pct}%`;
  ov.querySelector(".gen-pct").textContent = `${pct}%`;
  if (status === "done") ov.querySelector(".gen-spinner").classList.add("done");
}

function genError(msg) {
  const ov = document.getElementById("genOverlay");
  if (!ov) return;
  ov.querySelector(".gen-err").textContent = "⚠ " + msg;
}

function closeGenOverlay() {
  const ov = document.getElementById("genOverlay");
  if (ov) ov.classList.remove("open");
}

function renderStudio(d) {
  currentDetail = d;
  const det = d.details || {};

  const addTile = `<button class="thumb add-tile" title="Add photos"><span class="at-plus">＋</span><span>Add photos</span></button>`;
  let fileTiles;
  if (showAllPhotos || d.photos.length <= PHOTO_LIMIT) {
    fileTiles = d.photos.map(photoTile).join("") + addTile;
  } else {
    fileTiles = d.photos.slice(0, PHOTO_LIMIT).map(photoTile).join("");
    fileTiles += `<button class="thumb more-tile">+${d.photos.length - PHOTO_LIMIT}<span>more</span></button>`;
    fileTiles += addTile;
  }

  // Only options the PDF actually backs are enabled; the rest show disabled.
  const available = new Set(d.available || INCLUDE_OPTIONS.map((o) => o.key));
  const toggles = INCLUDE_OPTIONS.map((o) => {
    const off = !available.has(o.key);
    return `<button class="inc-toggle ${off ? "off" : "active"}" data-include="${o.key}"
      ${off ? `disabled title="Not found in the listing PDF"` : ""}>
      <span class="inc-ic">${o.icon}</span><span>${o.label}</span>
      <span class="inc-check">${off ? "—" : "✓"}</span>
    </button>`;
  }).join("");

  const versionsHtml = d.versions.length
    ? d.versions.map((v, i) => versionItem(v, i === 0)).join("")
    : "";

  detailEl.innerHTML = `
    <div class="studio-grid">
      <section class="card files-card">
        <div class="card-h"><h2>Property images</h2><span class="muted small">${d.photo_count} photos</span></div>
        <div class="thumbs detail-files">${fileTiles}</div>
      </section>

      <section class="card config-card">
        <div class="card-h"><h2>Property details</h2></div>

        <div class="cfg-cols">
          <div class="cfg-col">
            <div class="form-grid">
              ${field("f-title", "Property name", det.title, "Wiley's Cottage")}
              ${field("f-location", "Location", det.location, "Hollywood, Los Angeles")}
              ${field("f-price", "Nightly price", det.price, "$239 / night")}
              ${field("f-host", "Host", det.host, "Joanne")}
              ${field("f-guests", "Guests", det.guests, "3 guests")}
              ${field("f-bedrooms", "Bedrooms", det.bedrooms, "1 bedroom")}
              ${field("f-beds", "Beds", det.beds, "1 bed")}
              ${field("f-baths", "Baths", det.baths, "1 bath")}
            </div>

            <label class="cfg-label">Amenities</label>
            <div class="chip-editor f-amenities compact">
              <span class="chips"></span>
              <input class="chip-add" type="text" placeholder="Add amenity + Enter" />
            </div>

            <label class="cfg-label">Summary</label>
            <textarea class="textarea f-summary" rows="2"
              placeholder="One warm sentence about the place…">${escapeHtml(det.summary || "")}</textarea>
          </div>

          <div class="cfg-col">
            <label class="cfg-label">Include in the video</label>
            <div class="include-grid">${toggles}</div>

            <button class="btn regen-btn">
              <span class="rb-main">✨ Generate video with NVIDIA Cosmos 3</span>
              <span class="rb-sub">on NVIDIA® H200 NVLink GPU</span>
            </button>
            <label class="tavily-opt">
              <input type="checkbox" class="f-tavily" checked />
              <span>Allow using Tavily search to research the neighborhood</span>
            </label>
          </div>
        </div>
      </section>
    </div>

    <section class="card versions-card">
      <div class="card-h"><h2>Past Generations <span class="muted small">(${d.versions.length})</span></h2></div>
      <div class="versions">${versionsHtml}</div>
      <p class="muted empty-note" ${d.versions.length ? "hidden" : ""}>
        No videos yet — hit “Generate new trailer” to make your first cut.
      </p>
    </section>
  `;

  // Expand photos in place (don't re-render — keeps the edited form intact).
  const moreTile = detailEl.querySelector(".more-tile");
  if (moreTile) {
    moreTile.addEventListener("click", () => {
      showAllPhotos = true;
      detailEl.querySelector(".detail-files").innerHTML =
        currentDetail.photos.map(photoTile).join("") + addTile;
    });
  }

  wireChipEditor(detailEl.querySelector(".f-amenities"), det.amenities);

  detailEl.querySelectorAll(".version-video").forEach((b) => {
    b.addEventListener("click", () => openLightbox(b.dataset.src, b.dataset.poster));
  });

  detailEl.querySelectorAll(".inc-toggle").forEach((btn) => {
    btn.addEventListener("click", () => btn.classList.toggle("active"));
  });

  const regenBtn = detailEl.querySelector(".regen-btn");
  const val = (cls) => (detailEl.querySelector(cls)?.value || "").trim();

  regenBtn.addEventListener("click", async () => {
    const includes = [...detailEl.querySelectorAll(".inc-toggle.active")].map((b) => b.dataset.include);
    if (detailEl.querySelector(".f-tavily")?.checked) includes.push("tavily");

    // Build the brief from the (edited) form fields.
    const lines = [];
    if (val(".f-title")) lines.push(`Property: ${val(".f-title")}`);
    if (val(".f-location")) lines.push(`Location: ${val(".f-location")}`);
    const cfg = [val(".f-guests"), val(".f-bedrooms"), val(".f-beds"), val(".f-baths")].filter(Boolean);
    if (cfg.length) lines.push(cfg.join(" · "));
    if (val(".f-host")) lines.push(`Host: ${val(".f-host")}`);
    if (val(".f-summary")) lines.push(val(".f-summary"));
    const amenities = readChips(detailEl.querySelector(".f-amenities"));
    if (amenities.length) lines.push("Amenities: " + amenities.join(", "));

    regenBtn.disabled = true;
    openGenOverlay();
    try {
      const form = new FormData();
      form.append("instructions", lines.join("\n"));
      form.append("price", val(".f-price"));
      form.append("include", includes.join(","));
      const r = await fetch(`/api/listings/${d.id}/generate`, { method: "POST", body: form });
      const data = await r.json();
      if (!r.ok) throw new Error(data.detail || "Generation failed");
      await pollJob(data.job_id, (c, t, label) => updateGenOverlay(c, t, label, "running"));
      updateGenOverlay(1, 1, "Done", "done");
      await sleep(1100);
      closeGenOverlay();
      await openListingStudio(d.id); // re-render with the new version on top
    } catch (err) {
      genError(err.message);
      regenBtn.disabled = false;
      setTimeout(closeGenOverlay, 3000);
    }
  });
}

async function openListingStudio(id) {
  listingsPanel.hidden = true;
  detailEl.hidden = false;
  showAllPhotos = false;
  detailEl.innerHTML = `<p class="muted">Loading…</p>`;
  window.scrollTo({ top: 0, behavior: "smooth" });
  try {
    const r = await fetch(`/api/listings/${id}`);
    if (!r.ok) throw new Error("Could not load listing.");
    renderStudio(await r.json());
  } catch (e) {
    detailEl.innerHTML = `<p class="error">${e.message}</p>
      <button class="btn btn-ghost back-btn">← All listings</button>`;
    detailEl.querySelector(".back-btn").addEventListener("click", showListings);
  }
}

function escapeHtml(s) {
  return (s || "").replace(/[&<>"']/g, (c) =>
    ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c])
  );
}

/* ============================ INIT ============================ */
checkHealth();
showListings();
