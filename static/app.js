// Shared across every gauge so the verdict language is identical everywhere --
// the whole point is you shouldn't have to interpret each gauge differently.
function gbpusdVerdict(score, threshold = 0.15) {
  if (score > threshold) return { text: 'BULLISH GBPUSD', color: 'var(--white)' };
  if (score < -threshold) return { text: 'BEARISH GBPUSD', color: 'var(--blue)' };
  return { text: 'NEUTRAL', color: 'var(--amber)' };
}

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
  const { bricks, livePrice, boxSize } = state;
  const canvas = document.getElementById('renko');
  const ratio = window.devicePixelRatio || 1;
  const w = canvas.clientWidth, h = canvas.clientHeight;
  canvas.width = w * ratio; canvas.height = h * ratio;
  const ctx = canvas.getContext('2d');
  ctx.scale(ratio, ratio);
  ctx.clearRect(0, 0, w, h);

  const priceGutter = 54; // right-side space reserved for the price axis
  const gridEndX = w - priceGutter;

  ctx.strokeStyle = '#1c1c17';
  ctx.lineWidth = 1;
  for (let y = 0; y < h; y += h / 8) { ctx.beginPath(); ctx.moveTo(0, y); ctx.lineTo(gridEndX, y); ctx.stroke(); }

  if (!bricks.length) {
    ctx.fillStyle = '#77756b';
    ctx.font = '13px IBM Plex Mono';
    ctx.fillText('No bricks yet — waiting on the first scan.', 16, h / 2);
    return;
  }

  const padding = 24;
  const usableW = (w - priceGutter) - padding * 2;
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
  const dim = getComputedStyle(document.documentElement).getPropertyValue('--dim').trim();

  // ---- Price axis: label each gridline with the real price it represents ----
  if (boxSize && shown.length) {
    const lastLevel = levels[levels.length - 1];
    const lastClose = shown[shown.length - 1].close;
    ctx.fillStyle = dim;
    ctx.font = "10px 'IBM Plex Mono', monospace";
    ctx.textAlign = 'left';
    for (let y = 0; y < h; y += h / 8) {
      const lvlAtY = minLevel + (y - vPad) / brickH;
      const priceAtY = lastClose - (lvlAtY - lastLevel) * boxSize;
      ctx.fillText(priceAtY.toFixed(4), gridEndX + 6, y + 3);
    }
  }


  // Bricks sharing the same formed_at came from the same 30min candle --
  // i.e. price moved more than one box within a single bar. Worth flagging.
  const formedAtCounts = {};
  bricks.forEach(b => { formedAtCounts[b.formed_at] = (formedAtCounts[b.formed_at] || 0) + 1; });

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

    // Multi-brick-in-one-candle marker
    if (formedAtCounts[b.formed_at] > 1) {
      ctx.fillStyle = '#a855f7';
      ctx.beginPath();
      ctx.arc(x + brickW / 2, y - 6, 2.5, 0, Math.PI * 2);
      ctx.fill();
    }

    // Label the most recent brick with its actual date+time, so "how stale
    // is this" is always visible at a glance -- previously this only showed
    // the time, so an old brick sitting there for hours could easily be
    // misread as being from today.
    if (i === shown.length - 1) {
      const t = new Date(b.formed_at.replace(/(\.\d+)?Z?$/, 'Z'));
      const datePart = t.toLocaleDateString('en-GB', { month: 'short', day: 'numeric' });
      let timePart = t.toLocaleTimeString('en-US', { hour: 'numeric', minute: '2-digit', hour12: true });
      timePart = timePart.replace(' ', '').toLowerCase(); // "11:30 AM" -> "11:30am"
      const label = `${datePart} ${timePart}`;
      ctx.fillStyle = dim;
      ctx.font = "10px 'IBM Plex Mono', monospace";
      ctx.textAlign = 'center';
      const labelY = Math.min(y + bh + 14, h - 4);
      ctx.fillText(label, x + brickW / 2, labelY);
      ctx.textAlign = 'left';
    }
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
setInterval(loadBricks, 60000); // keep brick data (and the "Xh ago" text) fresh without needing a manual reload

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

function updateAlertStrip(events) {
  const strip = document.getElementById('alert-strip');
  const now = Date.now();
  const HOUR = 3600000;

  // Look for a high-impact event within the next 24h, or one that fired
  // within the last hour (post-event reaction window still matters).
  let urgent = null; // within 2h either side
  let warn = null;   // within 24h ahead

  for (const e of (events || [])) {
    if (e.impact !== 'high') continue;
    const t = parseEventTime(e.time);
    if (!t) continue;
    const diffMs = t.getTime() - now;

    if (diffMs >= -HOUR && diffMs <= 2 * HOUR) {
      urgent = { event: e, diffMs };
      break; // most urgent case, stop looking
    }
    if (diffMs > 0 && diffMs <= 24 * HOUR) {
      if (!warn || diffMs < warn.diffMs) warn = { event: e, diffMs };
    }
  }

  // Calendar urgency takes priority; if none, check the geopolitical gauge
  if (urgent) {
    const mins = Math.round(Math.abs(urgent.diffMs) / 60000);
    const when = urgent.diffMs >= 0 ? `in ${mins}m` : `${mins}m ago`;
    strip.textContent = `⚠ ${urgent.event.event} (${urgent.event.country}) ${when} — expect volatility`;
    strip.className = 'alert-strip alert-urgent';
    strip.style.display = 'block';
    return;
  }

  const geoScore = window.__lastGeoScore;
  if (geoScore !== undefined && geoScore !== null) {
    if (geoScore < -0.4) {
      strip.textContent = `⚠ Significant negative geopolitical news detected — elevated risk-off conditions`;
      strip.className = 'alert-strip alert-urgent';
      strip.style.display = 'block';
      return;
    }
    if (geoScore < -0.2) {
      strip.textContent = `Elevated geopolitical risk sentiment — watch for volatility`;
      strip.className = 'alert-strip alert-warn';
      strip.style.display = 'block';
      return;
    }
  }

  if (warn) {
    const hrs = Math.round(warn.diffMs / HOUR);
    strip.textContent = `${warn.event.event} (${warn.event.country}) in ~${hrs}h — high-impact, position with care`;
    strip.className = 'alert-strip alert-warn';
    strip.style.display = 'block';
  } else {
    strip.style.display = 'none';
  }
}
setInterval(() => { updateAlertStrip(window.__lastCalendarEvents); }, 30000);

async function loadCalendar() {
  const nextBlock = document.getElementById('next-event-block');
  const listEl = document.getElementById('event-list');
  try {
    const res = await fetch('/api/calendar');
    const data = await res.json();
    const events = data.events || [];
    window.__lastCalendarEvents = events;
    updateAlertStrip(events);
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
    // spread is already in "positive = GBP favorable" terms, but the raw
    // magnitude (~0.1-0.3 typical) needs its own threshold, not the shared 0.15 default
    const verdict = gbpusdVerdict(clamped / GAUGE_CLAMP, 0.1);

    body.innerHTML = `
      <div class="gauge-track"><div class="gauge-marker" style="left:calc(${pct}% - 1.5px)"></div></div>
      <div class="gauge-labels"><span>BEARISH</span><span>NEUTRAL</span><span>BULLISH</span></div>
      <div class="gauge-read" style="color:${verdict.color}">${verdict.text} · spread ${d.spread > 0 ? '+' : ''}${d.spread.toFixed(3)}</div>
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
    const verdict = gbpusdVerdict(d.score);
    const breakdown = (d.gbp_score !== null && d.gbp_score !== undefined)
      ? ` (GBP news ${d.gbp_score > 0 ? '+' : ''}${d.gbp_score.toFixed(2)}, USD news ${d.usd_score > 0 ? '+' : ''}${d.usd_score.toFixed(2)})`
      : '';

    body.innerHTML = `
      <div class="gauge-track"><div class="gauge-marker" style="left:calc(${pct}% - 1.5px)"></div></div>
      <div class="gauge-labels"><span>BEARISH</span><span>NEUTRAL</span><span>BULLISH</span></div>
      <div class="gauge-read" style="color:${verdict.color}">${verdict.text}</div>
      <div class="dim-small" style="margin-top:4px;">${d.article_count} articles${breakdown}</div>
    `;

    const heads = d.headlines || [];
    if (heads.length) {
      headlinesEl.innerHTML = heads.map(h => `
        <div class="nh-row">
          <span class="nh-sentiment" style="color:${sentimentColor(h.sentiment)}">${h.side ? `[${h.side}]` : ''} ${h.sentiment !== null ? (h.sentiment > 0 ? '+' : '') + h.sentiment.toFixed(2) : '--'}</span>
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

// ---- COT positioning gauge ----
async function loadCotGauge() {
  const body = document.getElementById('cot-gauge-body');
  try {
    const res = await fetch('/api/cot');
    const d = await res.json();
    if (!d || d.gauge_score === undefined) {
      body.innerHTML = `<div class="dim-small">No COT data yet.</div>`;
      return;
    }

    const pct = 50 + Math.max(-1, Math.min(1, d.gauge_score)) * 50;
    const verdict = gbpusdVerdict(d.gauge_score, 0.1);
    const positioningNote = d.gauge_score > 0.1 ? 'Leveraged funds net long GBP'
      : (d.gauge_score < -0.1 ? 'Leveraged funds net short GBP' : 'Leveraged funds roughly flat');

    let changeNote = '';
    if (d.prior_net !== null && d.prior_net !== undefined) {
      const change = d.lev_net - d.prior_net;
      const dir = change > 0 ? 'more long' : (change < 0 ? 'more short' : 'unchanged');
      changeNote = `<div class="dim-small" style="margin-top:4px;">vs prior week: ${dir} (${change > 0 ? '+' : ''}${Math.round(change).toLocaleString()} contracts)</div>`;
    }

    body.innerHTML = `
      <div class="gauge-track"><div class="gauge-marker" style="left:calc(${pct}% - 1.5px)"></div></div>
      <div class="gauge-labels"><span>BEARISH</span><span>NEUTRAL</span><span>BULLISH</span></div>
      <div class="gauge-read" style="color:${verdict.color}">${verdict.text}</div>
      <div class="dim-small" style="margin-top:2px;">${positioningNote}</div>
      <div class="gauge-numbers">
        <div>Long: <b>${Math.round(d.lev_long).toLocaleString()}</b></div>
        <div>Short: <b>${Math.round(d.lev_short).toLocaleString()}</b></div>
      </div>
      <div class="dim-small" style="margin-top:4px;">Report date: ${d.report_date}</div>
      ${changeNote}
    `;
  } catch (e) {
    body.innerHTML = `<div class="dim-small">COT data unavailable: ${e.message}</div>`;
  }
}

const cotBtn = document.getElementById('cot-refresh-btn');
if (cotBtn) {
  cotBtn.addEventListener('click', async () => {
    cotBtn.disabled = true;
    cotBtn.textContent = 'REFRESHING...';
    try {
      await fetch('/api/cot-refresh-now', { method: 'POST' });
    } catch (e) { /* fall through, still reload cache below */ }
    await loadCotGauge();
    cotBtn.disabled = false;
    cotBtn.textContent = 'REFRESH';
  });
}

loadCotGauge();

// ---- US data momentum gauge (NFP/CPI) ----
async function loadMomentumGauge() {
  const body = document.getElementById('momentum-gauge-body');
  try {
    const res = await fetch('/api/momentum');
    const d = await res.json();
    if (!d || d.gauge_score === undefined) {
      body.innerHTML = `<div class="dim-small">No momentum data yet.</div>`;
      return;
    }

    const pct = 50 + Math.max(-1, Math.min(1, d.gauge_score)) * 50;
    const verdict = gbpusdVerdict(d.gauge_score);
    const dataNote = d.gauge_score > 0.15 ? 'Cooling US data'
      : (d.gauge_score < -0.15 ? 'Hot US data' : 'US data roughly in line');

    body.innerHTML = `
      <div class="gauge-track"><div class="gauge-marker" style="left:calc(${pct}% - 1.5px)"></div></div>
      <div class="gauge-labels"><span>BEARISH</span><span>NEUTRAL</span><span>BULLISH</span></div>
      <div class="gauge-read" style="color:${verdict.color}">${verdict.text}</div>
      <div class="dim-small" style="margin-top:2px;">${dataNote}</div>
      <div class="gauge-numbers">
        <div>CPI YoY: <b>${d.cpi_yoy}%</b> <span class="dim-small">(${d.cpi_date})</span></div>
        <div>NFP: <b>${d.nfp_change > 0 ? '+' : ''}${d.nfp_change}k</b> <span class="dim-small">(${d.nfp_date})</span></div>
      </div>
    `;
  } catch (e) {
    body.innerHTML = `<div class="dim-small">Momentum data unavailable: ${e.message}</div>`;
  }
}

const momentumBtn = document.getElementById('momentum-refresh-btn');
if (momentumBtn) {
  momentumBtn.addEventListener('click', async () => {
    momentumBtn.disabled = true;
    momentumBtn.textContent = 'REFRESHING...';
    try {
      await fetch('/api/momentum-refresh-now', { method: 'POST' });
    } catch (e) { /* fall through, still reload cache below */ }
    await loadMomentumGauge();
    momentumBtn.disabled = false;
    momentumBtn.textContent = 'REFRESH';
  });
}

loadMomentumGauge();

// ---- Geopolitical / global-risk gauge ----
async function loadGeoGauge() {
  const body = document.getElementById('geo-gauge-body');
  const headlinesEl = document.getElementById('geo-headlines');
  try {
    const res = await fetch('/api/geo');
    const d = await res.json();
    if (!d || d.gauge_score === undefined) {
      body.innerHTML = `<div class="dim-small">No global risk data yet.</div>`;
      window.__lastGeoScore = null;
      return;
    }

    window.__lastGeoScore = d.gauge_score;
    updateAlertStrip(window.__lastCalendarEvents);

    const pct = 50 + Math.max(-1, Math.min(1, d.gauge_score)) * 50;
    const verdict = gbpusdVerdict(d.gauge_score, 0.15);
    const quietNote = d.article_count < 3
      ? 'No significant global risk events detected right now'
      : `${d.article_count} relevant articles`;

    body.innerHTML = `
      <div class="gauge-track"><div class="gauge-marker" style="left:calc(${pct}% - 1.5px)"></div></div>
      <div class="gauge-labels"><span>BEARISH</span><span>NEUTRAL</span><span>BULLISH</span></div>
      <div class="gauge-read" style="color:${verdict.color}">${verdict.text}</div>
      <div class="dim-small" style="margin-top:4px;">${quietNote}</div>
    `;

    const heads = d.headlines || [];
    if (heads.length && d.article_count >= 3) {
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
    body.innerHTML = `<div class="dim-small">Global risk data unavailable: ${e.message}</div>`;
  }
}

const geoBtn = document.getElementById('geo-refresh-btn');
if (geoBtn) {
  geoBtn.addEventListener('click', async () => {
    geoBtn.disabled = true;
    geoBtn.textContent = 'REFRESHING...';
    try {
      await fetch('/api/geo-refresh-now', { method: 'POST' });
    } catch (e) { /* fall through, still reload cache below */ }
    await loadGeoGauge();
    geoBtn.disabled = false;
    geoBtn.textContent = 'REFRESH';
  });
}

loadGeoGauge();
