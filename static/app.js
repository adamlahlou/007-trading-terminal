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
  const maxBricks = Math.max(8, Math.floor(usableW / 32));
  const shown = bricks.slice(-maxBricks);

  const slotW = usableW / shown.length;
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
  const yFor = (lvl) => vPad + (maxLevel - lvl) * brickH;

  const white = getComputedStyle(document.documentElement).getPropertyValue('--white').trim();

  shown.forEach((b, i) => {
    const x = padding + i * slotW + (slotW - brickW) / 2;
    const y = yFor(levels[i]);
    const bh = brickH * 0.9;

    roundedRectPath(ctx, x, y, brickW, bh, radius);
    if (b.direction === 1) {
      ctx.fillStyle = 'rgba(74,154,232,0.14)';
      ctx.fill();
      ctx.strokeStyle = white;
      ctx.lineWidth = 1.5;
      ctx.stroke();
    } else {
      ctx.fillStyle = white;
      ctx.fill();
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
    ctx.fillStyle = pendingUp ? 'rgba(74,154,232,0.10)' : 'rgba(234,230,218,0.10)';
    ctx.fill();
    ctx.strokeStyle = pendingUp ? 'rgba(255,255,255,0.35)' : 'rgba(234,230,218,0.35)';
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
    document.getElementById('price-now').style.color = last.direction === 1 ? 'var(--blue)' : 'var(--white)';
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
  try {
    const res = await fetch('/api/price');
    const data = await res.json();
    if (data.error) {
      el.textContent = 'PRICE FEED N/A';
      state.livePrice = null;
    } else {
      el.textContent = `${data.mid.toFixed(5)} (${data.bid.toFixed(5)}/${data.ask.toFixed(5)})`;
      state.livePrice = data.mid;
    }
  } catch (e) {
    el.textContent = 'PRICE FEED N/A';
    state.livePrice = null;
  }
  drawBricks();
}
setInterval(pollLivePrice, 3000);
pollLivePrice();

window.addEventListener('resize', drawBricks);
loadBricks();
