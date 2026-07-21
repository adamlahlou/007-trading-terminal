function tick() {
  const now = new Date();
  document.getElementById('clock').textContent = now.toLocaleTimeString('en-GB', { hour12: false });
}
setInterval(tick, 1000); tick();

function timeAgo(isoString) {
  const t = new Date(isoString.replace(/(\.\d+)?Z$/, 'Z')).getTime();
  const diffMin = Math.floor((Date.now() - t) / 60000);
  if (diffMin < 1) return "just now";
  if (diffMin < 60) return `${diffMin}m ago`;
  const diffHr = Math.floor(diffMin / 60);
  if (diffHr < 24) return `${diffHr}h ago`;
  return `${Math.floor(diffHr / 24)}d ago`;
}

function roundedRectPath(ctx, x, y, w, h, r) {
  ctx.beginPath();
  ctx.moveTo(x + r, y);
  ctx.arcTo(x + w, y, x + w, y + h, r);
  ctx.arcTo(x + w, y + h, x, y + h, r);
  ctx.arcTo(x, y + h, x, y, r);
  ctx.arcTo(x, y, x + w, y, r);
  ctx.closePath();
}

// Shared state, updated independently by the bricks load (30min-ish) and
// the live price poll (every 3s) -- drawBricks() is re-run after either changes.
let state = { bricks: [], boxSize: 0.0022, livePrice: null };

function drawBricks() {
  const { bricks, livePrice } = state;
  const canvas = document.getElementById('renko');
  const ratio = window.devicePixelRatio || 1;
  const w = canvas.clientWidth, h = canvas.clientHeight;
  canvas.width = w * ratio; canvas.height = h * ratio;
  const ctx = canvas.getContext('2d');
  ctx.scale(ratio, ratio);
  ctx.clearRect(0, 0, w, h);

  ctx.strokeStyle = '#1c1c17';
  ctx.lineWidth = 1;
  for (let y = 0; y < h; y += h / 8) { ctx.beginPath(); ctx.moveTo(0, y); ctx.lineTo(w, y); ctx.stroke(); }

  if (!bricks.length) {
    ctx.fillStyle = '#77756b';
    ctx.font = '13px IBM Plex Mono';
    ctx.fillText('No bricks yet — waiting on the first scan.', 16, h / 2);
    return;
  }

  const padding = 24;
  const usableW = w - padding * 2;
  const maxBricks = Math.max(8, Math.floor(usableW / 32) - 1);
  const shown = bricks.slice(-maxBricks);

  // +1 slot reserved for the pending ghost bar, so it's always on-canvas
  const slotW = usableW / (shown.length + 1);
  const brickW = Math.min(slotW * 0.68, 26);
  const radius = 4;
  const vPad = 20;

  let level = 0;
  const levels = shown.map((b) => {
    level += b.direction === 1 ? -1 : 1;
    return level;
  });

  // Reserve half a row of headroom on both ends so the pending ghost bar
  // (which can sit half a level beyond the last brick) never clips.
  const minLevel = Math.min(...levels, 0) - 0.5;
  const maxLevel = Math.max(...levels, 0) + 0.5;
  const numRows = maxLevel - minLevel + 1;
  const brickH = Math.min(28, (h - vPad * 2) / numRows);
  const yFor = (lvl) => vPad + (lvl - minLevel) * brickH;

  const white = getComputedStyle(document.documentElement).getPropertyValue('--white').trim();
  const amber = getComputedStyle(document.documentElement).getPropertyValue('--amber').trim();

  shown.forEach((b, i) => {
    const x = padding + i * slotW + (slotW - brickW) / 2;
    const y = yFor(levels[i]);
    const bh = brickH * 0.9;

    roundedRectPath(ctx, x, y, brickW, bh, radius);
    if (b.direction === 1) {
      // up (bullish): clean gradient-white fill, white outline
      const grad = ctx.createLinearGradient(x, y, x, y + bh);
      grad.addColorStop(0, 'rgba(255,255,255,0.95)');
      grad.addColorStop(1, 'rgba(234,230,218,0.75)');
      ctx.fillStyle = grad;
      ctx.fill();
      ctx.strokeStyle = white;
    } else {
      // down (bearish): blue fill, orange outline -- deliberately distinct
      ctx.fillStyle = 'rgba(31,111,235,0.18)';
      ctx.fill();
      ctx.strokeStyle = amber;
    }
    ctx.lineWidth = 1.5;
    ctx.stroke();
  });

  // Pending "ghost" bar: shows whether live price is currently above or
  // below the last confirmed brick's close, since it takes a full box move
  // to actually form the next brick. Half-height, faint, in the next slot.
  if (livePrice != null) {
    const lastBrick = shown[shown.length - 1];
    const lastLevel = levels[levels.length - 1];
    const diff = livePrice - lastBrick.close;
    const pendingUp = diff >= 0;
    const pendingLevel = lastLevel + (pendingUp ? -0.5 : 0.5);

    const x = padding + shown.length * slotW + (slotW - brickW) / 2;
    const yStart = yFor(lastLevel);
    const yEnd = yFor(pendingLevel);
    const top = Math.min(yStart, yEnd);
    const barH = Math.abs(yEnd - yStart);

    roundedRectPath(ctx, x, top, brickW, barH, radius);
    ctx.fillStyle = pendingUp ? 'rgba(234,230,218,0.14)' : 'rgba(31,111,235,0.18)';
    ctx.fill();
    ctx.strokeStyle = pendingUp ? 'rgba(234,230,218,0.5)' : 'rgba(255,176,0,0.55)';
    ctx.lineWidth = 1;
    ctx.setLineDash([2, 2]);
    ctx.stroke();
    ctx.setLineDash([]);
  }
}

async function loadBricks() {
  const res = await fetch('/api/bricks');
  const data = await res.json();
  const bricks = data.bricks || [];

  state.bricks = bricks;
  state.boxSize = data.box_size;

  document.getElementById('box-size').textContent = data.box_size;
  document.getElementById('brick-count-legend').textContent = `${bricks.length} bricks stored`;

  if (bricks.length) {
    const last = bricks[bricks.length - 1];
    document.getElementById('price-now').textContent = last.close.toFixed(5);
    document.getElementById('price-now').style.color = last.direction === 1 ? 'var(--white)' : 'var(--blue)';
    document.getElementById('price-meta').textContent = `last brick ${timeAgo(last.formed_at)}`;
  } else {
    document.getElementById('price-now').textContent = '--';
    document.getElementById('price-meta').textContent = 'no data yet';
  }

  drawBricks();
}

const scanBtn = document.getElementById('scan-now-btn');
scanBtn.addEventListener('click', async () => {
  scanBtn.disabled = true;
  scanBtn.textContent = 'SCANNING...';
  const statusEl = document.getElementById('scan-status');
  try {
    const res = await fetch('/api/scan-now', { method: 'POST' });
    const result = await res.json();
    if (result.error) {
      statusEl.textContent = `STATUS: ERROR — ${result.error}`;
    } else {
      statusEl.textContent = `STATUS: OK — ${result.new_bricks} new brick(s)`;
    }
  } catch (e) {
    statusEl.textContent = 'STATUS: REQUEST FAILED';
  }
  await loadBricks();
  scanBtn.disabled = false;
  scanBtn.textContent = 'SCAN NOW';
});

async function pollLivePrice() {
  const el = document.getElementById('live-price');
  if (!el) { console.error('live-price element not found in DOM'); return; }
  try {
    const res = await fetch('/api/price');
    const data = await res.json();
    if (data.error) {
      el.textContent = `N/A: ${data.error}`;
      state.livePrice = null;
    } else {
      el.textContent = `${data.mid.toFixed(5)} (${data.bid.toFixed(5)}/${data.ask.toFixed(5)})`;
      state.livePrice = data.mid;
    }
  } catch (e) {
    el.textContent = `N/A: ${e.message}`;
    state.livePrice = null;
  }
  drawBricks();
}
setInterval(pollLivePrice, 3000);
pollLivePrice();

window.addEventListener('resize', drawBricks);
loadBricks();

// ---- Macro calendar ----
let nextEventTime = null; // Date object for the countdown ticker

function parseEventTime(t) {
  if (!t) return null;
  // Finnhub gives "YYYY-MM-DD HH:MM:SS" in UTC, no offset marker
  const iso = t.includes('T') ? t : t.replace(' ', 'T') + 'Z';
  return new Date(iso);
}

function fmtEventTime(t) {
  const d = parseEventTime(t);
  if (!d) return '--';
  return d.toLocaleString(undefined, { weekday: 'short', hour: '2-digit', minute: '2-digit', month: 'short', day: 'numeric' });
}

function renderCountdown() {
  if (!nextEventTime) return;
  const diff = Math.max(0, Math.floor((nextEventTime.getTime() - Date.now()) / 1000));
  const d = Math.floor(diff / 86400);
  const h = Math.floor((diff % 86400) / 3600);
  const m = Math.floor((diff % 3600) / 60);
  const s = diff % 60;
  const set = (id, v) => { const el = document.getElementById(id); if (el) el.textContent = String(v).padStart(2, '0'); };
  set('cd-d', d); set('cd-h', h); set('cd-m', m); set('cd-s', s);
}
setInterval(renderCountdown, 1000);

async function loadCalendar() {
  const nextBlock = document.getElementById('next-event-block');
  const listEl = document.getElementById('event-list');
  try {
    const res = await fetch('/api/calendar');
    const data = await res.json();
    const events = data.events || [];
    const now = Date.now();
    const upcoming = events.filter(e => {
      const t = parseEventTime(e.time);
      return t && t.getTime() >= now;
    });

    // Prefer the next high-impact event; fall back to the next event of any impact
    const nextHigh = upcoming.find(e => e.impact === 'high');
    const next = nextHigh || upcoming[0];

    if (next) {
      nextEventTime = parseEventTime(next.time);
      nextBlock.innerHTML = `
        <div class="next-event-name">${next.event} <span class="dim-small">(${next.country})</span></div>
        <div class="next-event-sub">${fmtEventTime(next.time)}${next.estimate ? ` · Forecast ${next.estimate}` : ''}${next.prev ? `, Prior ${next.prev}` : ''}</div>
        <span class="impact-tag impact-${next.impact}">${next.impact.toUpperCase()} IMPACT</span>
        <div class="countdown">
          <div class="cd-box"><div class="cd-num" id="cd-d">00</div><div class="cd-label">DAYS</div></div>
          <div class="cd-box"><div class="cd-num" id="cd-h">00</div><div class="cd-label">HRS</div></div>
          <div class="cd-box"><div class="cd-num" id="cd-m">00</div><div class="cd-label">MIN</div></div>
          <div class="cd-box"><div class="cd-num" id="cd-s">00</div><div class="cd-label">SEC</div></div>
        </div>
      `;
      renderCountdown();
    } else {
      nextEventTime = null;
      nextBlock.innerHTML = `<div class="dim-small">No upcoming US/GB events in the cached window.</div>`;
    }

    if (events.length) {
      listEl.innerHTML = events.map(e => `
        <div class="ev-row">
          <div class="ev-left"><span class="impact-dot impact-${e.impact}"></span>${e.event} (${e.country})</div>
          <div class="ev-time">${fmtEventTime(e.time)}</div>
        </div>
      `).join('');
    } else {
      listEl.innerHTML = `<div class="dim-small">No calendar data cached yet.</div>`;
    }
  } catch (e) {
    nextBlock.innerHTML = `<div class="dim-small">Calendar unavailable: ${e.message}</div>`;
  }
}

const calBtn = document.getElementById('calendar-refresh-btn');
if (calBtn) {
  calBtn.addEventListener('click', async () => {
    calBtn.disabled = true;
    calBtn.textContent = 'REFRESHING...';
    try {
      await fetch('/api/calendar-refresh-now', { method: 'POST' });
    } catch (e) { /* fall through, still reload cache below */ }
    await loadCalendar();
    calBtn.disabled = false;
    calBtn.textContent = 'REFRESH';
  });
}

loadCalendar();

// ---- Yield differential / temperament gauge ----
// Normalization: +-1.5 percentage points of spread maps to the full gauge
// width. This is a simple, transparent heuristic (not a calibrated model) --
// wide enough that normal spread movement doesn't peg the gauge every day,
// narrow enough that a real dislocation (like a gilt crisis) shows clearly.
const GAUGE_CLAMP = 1.5;

async function loadYields() {
  const body = document.getElementById('gauge-body');
  try {
    const res = await fetch('/api/yields');
    const d = await res.json();
    if (!d || d.spread === undefined) {
      body.innerHTML = `<div class="dim-small">No yield data yet.</div>`;
      return;
    }

    const clamped = Math.max(-GAUGE_CLAMP, Math.min(GAUGE_CLAMP, d.spread));
    const pct = 50 + (clamped / GAUGE_CLAMP) * 50;

    let label, labelColor;
    if (d.spread > 0.3) { label = 'Leaning bullish (GBP)'; labelColor = 'var(--white)'; }
    else if (d.spread < -0.3) { label = 'Leaning bearish (GBP)'; labelColor = 'var(--blue)'; }
    else { label = 'Neutral'; labelColor = 'var(--amber)'; }

    body.innerHTML = `
      <div class="gauge-track"><div class="gauge-marker" style="left:calc(${pct}% - 1.5px)"></div></div>
      <div class="gauge-labels"><span>BEARISH (GBP)</span><span>NEUTRAL</span><span>BULLISH (GBP)</span></div>
      <div class="gauge-read" style="color:${labelColor}">${label} · spread ${d.spread > 0 ? '+' : ''}${d.spread.toFixed(3)}</div>
      <div class="gauge-numbers">
        <div>US 10Y: <b>${d.us_yield.toFixed(3)}%</b> <span class="dim-small">(${d.us_date})</span></div>
        <div>UK 10Y: <b>${d.uk_yield.toFixed(3)}%</b> <span class="dim-small">(${d.uk_date})</span></div>
      </div>
    `;
  } catch (e) {
    body.innerHTML = `<div class="dim-small">Yield data unavailable: ${e.message}</div>`;
  }
}

const yieldsBtn = document.getElementById('yields-refresh-btn');
if (yieldsBtn) {
  yieldsBtn.addEventListener('click', async () => {
    yieldsBtn.disabled = true;
    yieldsBtn.textContent = 'REFRESHING...';
    try {
      await fetch('/api/yields-refresh-now', { method: 'POST' });
    } catch (e) { /* fall through, still reload cache below */ }
    await loadYields();
    yieldsBtn.disabled = false;
    yieldsBtn.textContent = 'REFRESH';
  });
}

loadYields();

// ---- News sentiment gauge ----
function sentimentColor(score) {
  if (score === null || score === undefined) return 'var(--dim)';
  if (score > 0.15) return 'var(--white)';
  if (score < -0.15) return 'var(--blue)';
  return 'var(--amber)';
}

async function loadNewsGauge() {
  const body = document.getElementById('news-gauge-body');
  const headlinesEl = document.getElementById('news-headlines');
  try {
    const res = await fetch('/api/news');
    const d = await res.json();
    if (!d || d.score === undefined) {
      body.innerHTML = `<div class="dim-small">No news data yet.</div>`;
      headlinesEl.innerHTML = '';
      return;
    }

    const pct = 50 + Math.max(-1, Math.min(1, d.score)) * 50;
    let label = 'Neutral';
    if (d.score > 0.15) label = 'Leaning positive';
    else if (d.score < -0.15) label = 'Leaning negative';

    body.innerHTML = `
      <div class="gauge-track"><div class="gauge-marker" style="left:calc(${pct}% - 1.5px)"></div></div>
      <div class="gauge-labels"><span>NEGATIVE</span><span>NEUTRAL</span><span>POSITIVE</span></div>
      <div class="gauge-read" style="color:${sentimentColor(d.score)}">${label} · ${d.score > 0 ? '+' : ''}${d.score.toFixed(3)} (${d.article_count} articles)</div>
    `;

    const heads = d.headlines || [];
    if (heads.length) {
      headlinesEl.innerHTML = heads.map(h => `
        <div class="nh-row">
          <span class="nh-sentiment" style="color:${sentimentColor(h.sentiment)}">${h.sentiment !== null ? (h.sentiment > 0 ? '+' : '') + h.sentiment.toFixed(2) : '--'}</span>
          <a href="${h.url}" target="_blank" rel="noopener">${h.title}</a>
        </div>
      `).join('');
    } else {
      headlinesEl.innerHTML = '';
    }
  } catch (e) {
    body.innerHTML = `<div class="dim-small">News data unavailable: ${e.message}</div>`;
  }
}

const newsBtn = document.getElementById('news-refresh-btn');
if (newsBtn) {
  newsBtn.addEventListener('click', async () => {
    newsBtn.disabled = true;
    newsBtn.textContent = 'REFRESHING...';
    try {
      await fetch('/api/news-refresh-now', { method: 'POST' });
    } catch (e) { /* fall through, still reload cache below */ }
    await loadNewsGauge();
    newsBtn.disabled = false;
    newsBtn.textContent = 'REFRESH';
  });
}

loadNewsGauge();
