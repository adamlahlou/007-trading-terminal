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

function drawBricks(bricks, boxSize) {
  const canvas = document.getElementById('renko');
  const ratio = window.devicePixelRatio || 1;
  const w = canvas.clientWidth, h = canvas.clientHeight;
  canvas.width = w * ratio; canvas.height = h * ratio;
  const ctx = canvas.getContext('2d');
  ctx.scale(ratio, ratio);
  ctx.clearRect(0, 0, w, h);

  // background grid
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
  const brickH = Math.min(h / 16, 24);
  const radius = 4;

  // compute vertical levels (cumulative step per brick)
  let level = 0;
  const levels = shown.map((b) => {
    level += b.direction === 1 ? -1 : 1;
    return level;
  });
  const minLevel = Math.min(...levels, 0);
  const maxLevel = Math.max(...levels, 0);
  const span = (maxLevel - minLevel + 2) * brickH;
  const baseY = (h - span) / 2 + (maxLevel + 1) * brickH;

  const white = getComputedStyle(document.documentElement).getPropertyValue('--white').trim();
  const blue = getComputedStyle(document.documentElement).getPropertyValue('--blue').trim();

  shown.forEach((b, i) => {
    const x = padding + i * slotW + (slotW - brickW) / 2;
    const y = baseY + levels[i] * brickH;
    const bh = brickH * 0.9;

    roundedRectPath(ctx, x, y, brickW, bh, radius);
    if (b.direction === 1) {
      // up brick: hollow, white outline, faint blue tint
      ctx.fillStyle = 'rgba(74,154,232,0.14)';
      ctx.fill();
      ctx.strokeStyle = white;
      ctx.lineWidth = 1.5;
      ctx.stroke();
    } else {
      // down brick: solid white block
      ctx.fillStyle = white;
      ctx.fill();
    }
  });
}

async function loadBricks() {
  const res = await fetch('/api/bricks');
  const data = await res.json();
  const bricks = data.bricks || [];

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

  drawBricks(bricks, data.box_size);
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

window.addEventListener('resize', loadBricks);
loadBricks();
