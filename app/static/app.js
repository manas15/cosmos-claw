const statusEl = document.getElementById("status");
const sponsorsEl = document.getElementById("sponsors");

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
/* ========================== NAVIGATION ======================= */
// The sidebar lists projects; the main area shows the selected project's studio.
const listingsPanel = document.getElementById("tab-listings");
const detailEl = document.getElementById("listingDetail");
const projectNavEl = document.getElementById("projectNav");

// Friendly display names for specific listing folders (keeps a stable label
// without renaming the folder/id, which would orphan generated outputs).
const DISPLAY_NAMES = {
  "la-house-1": "House Rental",
};

// Placeholder projects we haven't wired to a real listing folder yet.
const PLACEHOLDER_PROJECTS = [];

let projects = [];
let activeProjectId = null;

async function loadProjects() {
  let listings = [];
  try {
    const r = await fetch("/api/listings");
    listings = (await r.json()).listings || [];
  } catch {
    /* fall through to placeholders only */
  }
  const real = listings.map((l) => ({
    id: l.id,
    label: DISPLAY_NAMES[l.id] || l.name,
    icon: "🏠",
    listing: l,
  }));
  projects = [...real, ...PLACEHOLDER_PROJECTS];
  renderProjectNav();

  // Default to the first real project (or the first placeholder).
  if (!activeProjectId && projects.length) selectProject(projects[0]);
}

function renderProjectNav() {
  projectNavEl.innerHTML = "";
  projects.forEach((p) => {
    const btn = document.createElement("button");
    btn.className = "project-item" + (p.empty ? " empty" : "") +
      (p.id === activeProjectId ? " active" : "");
    btn.innerHTML = `<span class="pi-ic">${p.icon}</span><span class="pi-label">${escapeHtml(p.label)}</span>`;
    btn.addEventListener("click", () => selectProject(p));
    projectNavEl.appendChild(btn);
  });
}

function selectProject(p) {
  activeProjectId = p.id;
  renderProjectNav();
  if (p.empty) showEmptyProject(p);
  else openListingStudio(p.id);
}

function showEmptyProject(p) {
  listingsPanel.hidden = true;
  detailEl.hidden = false;
  detailEl.innerHTML = `
    <div class="empty-project">
      <div class="ep-icon">${p.icon}</div>
      <h2>${escapeHtml(p.label)}</h2>
      <p class="muted">Your videographer is ready for ${escapeHtml(p.label)} next —
      add its photos and it'll start filming a full social calendar on autopilot.</p>
    </div>`;
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

// Derive a brand handle for cuts generated before we stored one.
function postHandle(v) {
  if (v.handle) return v.handle;
  const slug = (v.title || v.name || "stay").toLowerCase().replace(/[^a-z0-9]/g, "").slice(0, 24);
  return "@" + (slug || "stay");
}

// Fallback caption for older cuts that predate the post builder.
function postCaption(v) {
  if (v.caption) return v.caption;
  const bits = [];
  if (v.title) bits.push(v.title);
  const info = [];
  if (v.location) info.push(`📍 ${v.location}`);
  if (v.price) info.push(`💰 ${v.price}`);
  if (info.length) bits.push(info.join("  ·  "));
  bits.push("📩 DM to book — link in bio");
  return bits.join("\n\n");
}

// Color the #hashtags inside an already-escaped caption string.
function highlightHashtags(escaped) {
  return escaped.replace(/(#[A-Za-z0-9_]+)/g, '<span class="hash">$1</span>');
}

// North-star goals header: the big targets the agents chase for months.
function northStar(goalList) {
  if (!goalList || !goalList.length) return "";
  const bars = goalList
    .map((g) => {
      const pct = Math.max(0, Math.min(100, g.pct || 0));
      const cur = Math.round(g.current || 0).toLocaleString();
      const tgt = Math.round(g.target || 0).toLocaleString();
      return `<div class="ns-goal ${g.met ? "met" : ""}">
        <div class="ns-top"><span class="ns-label">${escapeHtml(g.label || g.id)}${g.met ? " 🏆" : ""}</span>
          <span class="ns-num">${cur} / ${tgt}</span></div>
        <div class="ns-track"><span class="ns-fill" style="width:${pct}%"></span></div>
      </div>`;
    })
    .join("");
  return `<div class="north-star">
    <div class="ns-h">🎯 North-star goals <span class="muted">— the experiment runs until these are met</span></div>
    <div class="ns-goals">${bars}</div>
  </div>`;
}

// The Checker UI: every cut starts pending; the human posts it or flags slop,
// then later logs how it did. State drives what's shown.
function feedbackBlock(v) {
  const status = v.status || "pending_review";
  if (status === "discarded") {
    const why = v.slop_notes ? ` — ${escapeHtml(v.slop_notes)}` : "";
    return `<div class="pc-feedback discarded"><span class="fb-state">🗑 Discarded as slop${why}</span></div>`;
  }
  if (status === "posted") {
    const perf = v.performance;
    if (perf && Object.keys(perf).length) {
      const chips = Object.entries(perf)
        .map(([k, val]) => `<span class="vstat">${escapeHtml(k)}: ${Math.round(val)}</span>`)
        .join("");
      return `<div class="pc-feedback posted"><span class="fb-state">📮 Posted</span><div class="vstats">${chips}</div></div>`;
    }
    return `<div class="pc-feedback posted">
        <span class="fb-state">📮 Posted — how did it do?</span>
        <div class="fb-perf">
          <input class="fb-views" type="number" min="0" placeholder="views" />
          <input class="fb-likes" type="number" min="0" placeholder="likes" />
          <input class="fb-followers" type="number" min="0" placeholder="+followers" />
          <button class="btn btn-sm fb-log">Log</button>
        </div>
        <span class="fb-status"></span>
      </div>`;
  }
  // pending_review
  return `<div class="pc-feedback pending">
      <span class="fb-state">⏳ Awaiting your call</span>
      <div class="fb-decide">
        <button class="btn btn-sm fb-post">📮 Post it</button>
        <button class="btn btn-ghost btn-sm fb-discard">🗑 Slop</button>
      </div>
      <input class="fb-note" type="text" placeholder="why is it slop? (optional, teaches the agent)" hidden />
      <span class="fb-status"></span>
    </div>`;
}

// A ready-to-post social card: format-shaped preview + caption + actions.
function versionItem(v, isLatest) {
  const ar = (v.ratio || "9:16").replace(":", " / ");
  const handle = postHandle(v);
  const caption = postCaption(v);
  const stats = [
    v.scene_count ? `${v.scene_count} scenes` : "",
    v.voice ? `🎙 ${v.voice}` : "",
  ]
    .filter(Boolean)
    .map((s) => `<span class="vstat">${escapeHtml(s)}</span>`)
    .join("");
  return `
    <div class="postcard" data-vid="${v.vid}">
      <div class="pc-media" style="aspect-ratio:${ar}">
        <button class="version-video" data-src="${v.video_url}" ${v.poster ? `data-poster="${v.poster}"` : ""}>
          ${v.poster ? `<img class="vv-poster" src="${v.poster}" alt="" />` : `<span class="vv-blank"></span>`}
          <span class="vv-play">▶</span>
          ${isLatest ? `<span class="latest-chip">Latest</span>` : ""}
        </button>
      </div>
      <div class="pc-body">
        <div class="pc-head">
          <span class="pc-handle">${escapeHtml(handle)}</span>
          <span class="pc-fmt">${escapeHtml(v.format_label || "Social")}${v.ratio ? ` · ${v.ratio}` : ""}</span>
        </div>
        <div class="pc-caption">${highlightHashtags(escapeHtml(caption))}</div>
        <div class="pc-music">🎵 Recommended audio: <b>${escapeHtml(v.music || "warm")}</b> bed${v.voice ? ` · ${escapeHtml(v.voice)} voiceover` : ""}</div>
        ${stats ? `<div class="vstats">${stats}</div>` : ""}
        <div class="pc-actions">
          <button class="btn btn-sm pc-copy" data-cap="${encodeURIComponent(caption)}">📋 Copy caption</button>
          <a class="btn btn-ghost btn-sm" download href="${v.video_url}">⬇ Download MP4</a>
        </div>
        ${feedbackBlock(v)}
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
          <h2>Your videographer is on set</h2>
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

// ---- Soul as a nicely-formatted, editable markdown document ----
function composeSoulMarkdown(det) {
  const lines = [`# ${det.title || "Untitled venue"}`];
  if (det.summary) lines.push("", det.summary);
  const facts = [];
  if (det.location) facts.push(`**Location:** ${det.location}`);
  if (det.price) facts.push(`**Price:** ${det.price}`);
  if (det.host) facts.push(`**Host:** ${det.host}`);
  const cfg = [det.guests, det.bedrooms, det.beds, det.baths].filter(Boolean).join(" · ");
  if (cfg) facts.push(`**Layout:** ${cfg}`);
  if (facts.length) lines.push("", "## Details", ...facts.map((f) => "- " + f));
  if (det.amenities && det.amenities.length)
    lines.push("", "## Amenities", ...det.amenities.map((a) => "- " + a));
  return lines.join("\n");
}

// Tiny markdown -> HTML renderer (headings, bold/italic, bullet lists, paragraphs).
function renderMarkdown(md) {
  const inline = (s) =>
    escapeHtml(s)
      .replace(/\*\*(.+?)\*\*/g, "<strong>$1</strong>")
      .replace(/(^|[^*])\*(?!\s)(.+?)\*/g, "$1<em>$2</em>");
  let html = "", inList = false;
  const closeList = () => { if (inList) { html += "</ul>"; inList = false; } };
  (md || "").split("\n").forEach((raw) => {
    const line = raw.trimEnd();
    if (/^#\s+/.test(line)) { closeList(); html += `<h1>${inline(line.replace(/^#\s+/, ""))}</h1>`; }
    else if (/^##\s+/.test(line)) { closeList(); html += `<h2>${inline(line.replace(/^##\s+/, ""))}</h2>`; }
    else if (/^###\s+/.test(line)) { closeList(); html += `<h3>${inline(line.replace(/^###\s+/, ""))}</h3>`; }
    else if (/^[-*]\s+/.test(line)) { if (!inList) { html += "<ul>"; inList = true; } html += `<li>${inline(line.replace(/^[-*]\s+/, ""))}</li>`; }
    else if (line.trim() === "") { closeList(); }
    else { closeList(); html += `<p>${inline(line)}</p>`; }
  });
  closeList();
  return html;
}

// ---- Agent Loop activity feed ----
function feedOutputItem(v, isLatest) {
  return `<div class="feed-item output">
    <span class="fi-rail"><span class="fi-dot">🎬</span></span>
    <div class="fi-body">
      <div class="fi-line"><span class="fi-ts">${fmtPST(v.created_at)}</span><b>Published a new cut${isLatest ? " · latest" : ""}</b></div>
      ${versionItem(v, isLatest)}
    </div>
  </div>`;
}

// One non-video timeline entry (a thing the agent did).
function feedActionItem(icon, text, tsMs) {
  return `<div class="feed-item action done">
    <span class="fi-rail"><span class="fi-dot">${icon || "•"}</span></span>
    <div class="fi-body"><div class="fi-line"><span class="fi-ts">${tsMs ? fmtPST(tsMs) : ""}</span>
    <b>${escapeHtml(text)}</b></div></div>
  </div>`;
}

// Merge the persisted activity log with published cuts into one timeline.
function buildAgentFeed(activity, versions) {
  const items = [];
  (activity || []).forEach((a) =>
    items.push({ ts: (a.ts || 0) * 1000, kind: "action", icon: a.icon, text: a.text })
  );
  (versions || []).forEach((v, i) =>
    items.push({ ts: v.created_at || 0, kind: "output", v, latest: i === 0 })
  );
  items.sort((x, y) => y.ts - x.ts);
  return items
    .map((it) =>
      it.kind === "output" ? feedOutputItem(it.v, it.latest) : feedActionItem(it.icon, it.text, it.ts)
    )
    .join("");
}

function renderStudio(d) {
  currentDetail = d;
  const det = d.details || {};

  const addTile = `<button class="thumb add-tile" title="Add photos"><span class="at-plus">＋</span><span>Add photos</span></button>`;
  const allTiles = d.photos.map(photoTile).join("") + addTile;

  const genCount = d.versions.length;
  const soulMd = composeSoulMarkdown(det);

  // Marketing dossier: the manager agent's brand, brief, and activity.
  const dossier = d.brand || {};
  const brandInfo = dossier.brand || {};
  const brief = dossier.brief || {};
  const activity = dossier.activity || [];
  const feedHtml = buildAgentFeed(activity, d.versions);
  const briefAssets = (brief.assets || [])
    .map((a) => {
      const url = d.photos[a.index];
      return url
        ? `<div class="ba-tile" title="${escapeHtml(a.reason || "")}"><img src="${url}" alt="" /><span class="ba-i">${a.index}</span></div>`
        : "";
    })
    .join("");

  const vids = d.videos || [];
  const videoTiles = vids.length
    ? vids.map((u) => `<div class="thumb"><video src="${u}" muted></video></div>`).join("")
    : `<p class="muted empty-note">No videos uploaded yet — drop clips into this project's folder to use them as source footage.</p>`;

  detailEl.innerHTML = `
    <div class="project-head">
      <div class="proj-tabs">
        <button class="proj-tab active" data-tab="soul">🪶 Soul</button>
        <button class="proj-tab" data-tab="assets">🖼️ Images &amp; Videos</button>
        <button class="proj-tab" data-tab="memory">🧠 Memory</button>
        <button class="proj-tab" data-tab="agent">🤖 Agent Loop${genCount ? ` <span class="tab-badge">${genCount}</span>` : ""}</button>
      </div>
    </div>
    ${northStar(d.goals)}

    <!-- ============ TAB: SOUL (formatted, editable markdown identity) ============ -->
    <div class="proj-panel" data-panel="soul">
      <p class="panel-intro muted">The Soul is the source of truth for this place — its facts, story and vibe, as a living document. Your videographer reads it before every shoot.</p>
      <section class="card soul-sheet">
        <div class="card-h"><h2>Soul</h2><span class="muted small">editable · markdown</span></div>
        <div class="md-doc" contenteditable="true" spellcheck="false">${renderMarkdown(soulMd)}</div>
      </section>
    </div>

    <!-- ============ TAB: IMAGES & VIDEOS (uploaded assets) ============ -->
    <div class="proj-panel" data-panel="assets" hidden>
      <p class="panel-intro muted">Everything you've uploaded for this project — the raw material your videographer shoots from.</p>
      <section class="card">
        <div class="card-h"><h2>Images</h2><span class="muted small">${d.photo_count} photos</span></div>
        <div class="thumbs detail-files gallery">${allTiles}</div>
      </section>
      <section class="card">
        <div class="card-h"><h2>Videos</h2><span class="muted small">${vids.length} clip${vids.length === 1 ? "" : "s"}</span></div>
        <div class="thumbs detail-files gallery">${videoTiles}</div>
      </section>
    </div>

    <!-- ============ TAB: MEMORY (marketing manager dossier) ============ -->
    <div class="proj-panel" data-panel="memory" hidden>
      <p class="panel-intro muted">An always-on marketing manager researches the venue, locks in a consistent brand, and keeps a durable creative brief — the memory every video is grounded on.</p>

      <section class="card mgr-card">
        <div class="card-h"><h2>🧠 Marketing manager</h2><span class="muted small">OpenClaw-style · GPT-4o</span></div>
        ${brandInfo.oneliner
          ? `<p class="mgr-oneliner">“${escapeHtml(brandInfo.oneliner)}”</p>`
          : `<p class="muted">No brand yet. Run the marketing manager from the CLI — <code>python -m app.agent run ${d.id}</code> — and it'll research the venue, invent the missing brand facts (kept consistent across videos), and draft a creative brief here.</p>`}
        <div class="mgr-meta">
          ${brandInfo.audience ? `<span class="vstat">🎯 ${escapeHtml(brandInfo.audience)}</span>` : ""}
          ${brandInfo.tone ? `<span class="vstat">🎨 ${escapeHtml(brandInfo.tone)}</span>` : ""}
        </div>
        ${brief.pitch ? `<p class="mgr-pitch"><b>Pitch:</b> ${escapeHtml(brief.pitch)}</p>` : ""}
        ${(brief.hooks || []).length
          ? `<div class="mgr-hooks">${brief.hooks.map((h) => `<span class="hook-chip">“${escapeHtml(h)}”</span>`).join("")}</div>`
          : ""}
        ${brief.voiceover
          ? `<p class="mgr-vo"><span class="mgr-vo-l">🎙 Voiceover</span> ${escapeHtml(brief.voiceover)}</p>`
          : ""}
        ${briefAssets
          ? `<label class="cfg-label" style="margin-top:12px">Recommended assets &amp; order</label><div class="ba-row">${briefAssets}</div>`
          : ""}
        <div class="mgr-meta">
          ${brief.music ? `<span class="vstat">🎵 ${escapeHtml(brief.music)}</span>` : ""}
          ${brief.voice ? `<span class="vstat">🎙 ${escapeHtml(brief.voice)}</span>` : ""}
          ${brief.format ? `<span class="vstat">🎞 ${escapeHtml(brief.format)}</span>` : ""}
        </div>
        <p class="muted small mgr-cli">Driven from the CLI — <code>python -m app.agent run ${d.id}</code> refreshes this brief; <code>python -m app.agent generate ${d.id}</code> films a cut.</p>
      </section>
    </div>

    <!-- ============ TAB: AGENT LOOP (activity + published cuts) ============ -->
    <div class="proj-panel" data-panel="agent" hidden>
      <p class="panel-intro muted">Everything your videographer does — and every ready-to-post cut it publishes — lands here in real time.</p>

      <section class="card feed-card">
        <div class="card-h"><h2>Agent activity</h2></div>
        <div class="agent-feed">${feedHtml}</div>
        <p class="muted empty-note feed-empty" ${genCount ? "hidden" : ""}>
          Idle — start the loop from the CLI (<code>python -m app.agent generate ${d.id}</code> or <code>scripts/marketing_loop.py</code>) and its work streams here.
        </p>
      </section>
    </div>
  `;

  // ---- Tab switching ----
  const panels = detailEl.querySelectorAll(".proj-panel");
  detailEl.querySelectorAll(".proj-tab").forEach((tab) => {
    tab.addEventListener("click", () => {
      detailEl.querySelectorAll(".proj-tab").forEach((t) => t.classList.toggle("active", t === tab));
      panels.forEach((p) => (p.hidden = p.dataset.panel !== tab.dataset.tab));
    });
  });

  const wireVideoButtons = (root) =>
    root.querySelectorAll(".version-video").forEach((b) => {
      b.addEventListener("click", () => openLightbox(b.dataset.src, b.dataset.poster));
    });

  // Copy-to-clipboard caption + display-only publish button on each post card.
  const wirePostButtons = (root) => {
    root.querySelectorAll(".pc-copy").forEach((b) => {
      b.addEventListener("click", async () => {
        try {
          await navigator.clipboard.writeText(decodeURIComponent(b.dataset.cap || ""));
        } catch (_e) {
          /* clipboard may be blocked; ignore */
        }
        const prev = b.textContent;
        b.textContent = "✓ Copied!";
        b.classList.add("ok");
        setTimeout(() => {
          b.textContent = prev;
          b.classList.remove("ok");
        }, 1600);
      });
    });
    root.querySelectorAll(".pc-publish").forEach((b) => {
      b.addEventListener("click", () => {
        const prev = b.textContent;
        b.textContent = "🔌 Connect account";
        setTimeout(() => (b.textContent = prev), 1800);
      });
    });
  };

  // The Checker: post / discard / log-performance on each cut.
  const wireFeedbackButtons = (root) => {
    const reRender = (card, fresh) => {
      const wrap = document.createElement("div");
      wrap.innerHTML = versionItem(fresh, card.querySelector(".latest-chip") != null);
      const node = wrap.firstElementChild;
      card.replaceWith(node);
      wireCards(node.closest(".feed-item") || node);
    };
    const post = async (vid, path, body, statusEl) => {
      const form = new FormData();
      Object.entries(body).forEach(([k, val]) => form.append(k, val));
      const r = await fetch(`/api/listings/${d.id}/versions/${vid}/${path}`, { method: "POST", body: form });
      if (!r.ok) throw new Error((await r.json().catch(() => ({}))).detail || `HTTP ${r.status}`);
      return r.json();
    };
    root.querySelectorAll(".postcard[data-vid]").forEach((card) => {
      const vid = card.dataset.vid;
      const statusEl = card.querySelector(".fb-status");
      const setStatus = (t) => statusEl && (statusEl.textContent = t);

      card.querySelector(".fb-post")?.addEventListener("click", async (e) => {
        e.target.disabled = true;
        setStatus("Posting…");
        try { reRender(card, await post(vid, "decision", { decision: "posted" }, statusEl)); }
        catch (err) { setStatus("✗ " + err.message); e.target.disabled = false; }
      });

      const discardBtn = card.querySelector(".fb-discard");
      const note = card.querySelector(".fb-note");
      discardBtn?.addEventListener("click", async () => {
        if (note && note.hidden) {  // first click reveals the "why" field
          note.hidden = false;
          note.focus();
          discardBtn.textContent = "🗑 Confirm slop";
          return;
        }
        discardBtn.disabled = true;
        setStatus("Discarding…");
        try { reRender(card, await post(vid, "decision", { decision: "discarded", notes: note ? note.value : "" }, statusEl)); }
        catch (err) { setStatus("✗ " + err.message); discardBtn.disabled = false; }
      });

      card.querySelector(".fb-log")?.addEventListener("click", async (e) => {
        const body = {
          views: card.querySelector(".fb-views")?.value || "",
          likes: card.querySelector(".fb-likes")?.value || "",
          followers: card.querySelector(".fb-followers")?.value || "",
        };
        if (!body.views && !body.likes && !body.followers) { setStatus("Enter a number first"); return; }
        e.target.disabled = true;
        setStatus("Logging…");
        try { reRender(card, await post(vid, "performance", body, statusEl)); }
        catch (err) { setStatus("✗ " + err.message); e.target.disabled = false; }
      });
    });
  };

  const wireCards = (root) => {
    wireVideoButtons(root);
    wirePostButtons(root);
    wireFeedbackButtons(root);
  };
  wireCards(detailEl);
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
      <button class="btn btn-ghost back-btn">↺ Reload projects</button>`;
    detailEl.querySelector(".back-btn").addEventListener("click", loadProjects);
  }
}

function escapeHtml(s) {
  return (s || "").replace(/[&<>"']/g, (c) =>
    ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c])
  );
}

/* ============================ INIT ============================ */
checkHealth();
loadProjects();
