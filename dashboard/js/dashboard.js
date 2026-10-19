const ENDPOINTS = [
  "http://web-frontend1.lab.rippa.io:8014/api/stats/logged-in",
  "http://web-frontend2.lab.rippa.io:8014/api/stats/logged-in",
];

const localSeries = [];
const canvas = document.getElementById("chart");
const ctx = canvas.getContext("2d");
const countEl = document.getElementById("count");
const statusEl = document.getElementById("status");

function draw(points) {
  const w = canvas.width;
  const h = canvas.height;
  ctx.clearRect(0, 0, w, h);
  ctx.strokeStyle = "#2a2e24";
  ctx.beginPath();
  for (let y = 0; y <= 4; y += 1) {
    const py = 16 + ((h - 32) * y) / 4;
    ctx.moveTo(24, py);
    ctx.lineTo(w - 8, py);
  }
  ctx.stroke();
  if (points.length < 2) return;
  const max = Math.max(10, ...points.map((p) => p.n));
  ctx.strokeStyle = "#d7b377";
  ctx.lineWidth = 2;
  ctx.beginPath();
  points.forEach((p, i) => {
    const x = 24 + ((w - 40) * i) / (points.length - 1);
    const y = h - 16 - ((h - 32) * p.n) / max;
    if (i === 0) ctx.moveTo(x, y);
    else ctx.lineTo(x, y);
  });
  ctx.stroke();
}

async function fetchStats() {
  let lastError = "unreachable";
  for (const url of ENDPOINTS) {
    try {
      const response = await fetch(url, { cache: "no-store" });
      if (!response.ok) {
        lastError = `${url} ${response.status}`;
        continue;
      }
      const data = await response.json();
      countEl.textContent = String(data.count);
      const series = (data.series && data.series.length ? data.series : localSeries).slice(-60);
      if (!data.series || !data.series.length) {
        localSeries.push({ t: new Date().toISOString(), n: data.count });
        if (localSeries.length > 60) localSeries.shift();
      }
      draw(series.length ? series : localSeries);
      statusEl.textContent = `Source ${url} · ${new Date().toLocaleTimeString()}`;
      return;
    } catch (err) {
      lastError = `${url}: ${err.message}`;
    }
  }
  statusEl.textContent = lastError;
}

fetchStats();
setInterval(fetchStats, 5000);
