/* Call Center Analysis – small client helpers (Chart.js wrappers, audio/transcript sync, upload). */
(function () {
  "use strict";

  const css = (name) => getComputedStyle(document.documentElement).getPropertyValue(name).trim();
  const PALETTE = ["#2563eb", "#7c3aed", "#0d9488", "#d97706", "#db2777", "#64748b", "#16a34a", "#dc2626"];
  const SEMANTIC = {
    positive: () => css("--good"), negative: () => css("--bad"), neutral: () => css("--neutral"), mixed: () => css("--ok"),
    resolved: () => css("--good"), unresolved: () => css("--bad"), escalated: () => css("--ok"), follow_up: () => css("--info"),
    satisfied: () => css("--good"), happy: () => "#22c55e", calm: () => "#0d9488", angry: () => css("--bad"),
    frustrated: () => "#f97316", disappointed: () => "#db2777", anxious: () => "#a855f7", confused: () => "#eab308",
  };
  const label = (s) => String(s || "").replace(/_/g, " ").replace(/^\w/, (c) => c.toUpperCase());
  const data = (id) => { const el = document.getElementById(id); return el ? JSON.parse(el.textContent) : null; };

  function baseOptions(extra) {
    const grid = css("--border"), text = css("--text-2");
    return Object.assign({
      responsive: true, maintainAspectRatio: false, animation: { duration: 300 },
      interaction: { mode: "index", intersect: false },
      plugins: { legend: { labels: { color: text, boxWidth: 10, boxHeight: 10, usePointStyle: true } },
                 tooltip: { padding: 10, cornerRadius: 6 } },
      scales: {
        x: { grid: { display: false }, ticks: { color: text, maxRotation: 0, autoSkipPadding: 12 }, border: { color: grid } },
        y: { grid: { color: grid }, ticks: { color: text }, border: { display: false } },
      },
    }, extra || {});
  }

  function make(el, config) {
    const canvas = typeof el === "string" ? document.getElementById(el) : el;
    if (!canvas || !window.Chart) return null;
    Chart.defaults.font.family = css("--font") || "system-ui";
    return new Chart(canvas, config);
  }

  const charts = {
    trend(el, rows) {
      return make(el, {
        type: "bar",
        data: {
          labels: rows.map((r) => r.date.slice(5)),
          datasets: [
            { type: "bar", label: "Calls", data: rows.map((r) => r.calls), backgroundColor: css("--brand-soft"),
              borderColor: css("--brand"), borderWidth: 1, borderRadius: 4, yAxisID: "y" },
            { type: "line", label: "Avg customer sentiment", data: rows.map((r) => r.sentiment), borderColor: css("--customer"),
              backgroundColor: css("--customer"), tension: .35, pointRadius: 2, yAxisID: "y1" },
          ],
        },
        options: baseOptions({
          scales: {
            x: baseOptions().scales.x,
            y: Object.assign(baseOptions().scales.y, { beginAtZero: true, ticks: { color: css("--text-2"), precision: 0 }, title: { display: true, text: "Calls", color: css("--text-3") } }),
            y1: { position: "right", min: -1, max: 1, grid: { display: false }, ticks: { color: css("--text-2") },
                  title: { display: true, text: "Sentiment", color: css("--text-3") } },
          },
        }),
      });
    },
    qaTrend(el, rows) {
      return make(el, {
        type: "line",
        data: { labels: rows.map((r) => r.date.slice(5)),
                datasets: [{ label: "QA score %", data: rows.map((r) => r.qa), borderColor: css("--brand"), backgroundColor: css("--brand"),
                             tension: .3, pointRadius: 3 }] },
        options: baseOptions({ plugins: { legend: { display: false } },
                               scales: { x: baseOptions().scales.x, y: Object.assign(baseOptions().scales.y, { min: 0, max: 100 }) } }),
      });
    },
    hbar(el, labels, values, opts) {
      opts = opts || {};
      return make(el, {
        type: "bar",
        data: { labels, datasets: [{ label: opts.label || "", data: values, borderRadius: 4, maxBarThickness: 22,
                                     backgroundColor: opts.colors || values.map(() => css("--brand")) }] },
        options: baseOptions({
          indexAxis: "y", plugins: { legend: { display: false } },
          scales: { x: Object.assign(baseOptions().scales.y, opts.max ? { min: 0, max: opts.max } : { beginAtZero: true }),
                    y: { grid: { display: false }, ticks: { color: css("--text-2") }, border: { display: false } } },
        }),
      });
    },
    doughnut(el, counts) {
      const keys = Object.keys(counts || {});
      return make(el, {
        type: "doughnut",
        data: { labels: keys.map(label),
                datasets: [{ data: keys.map((k) => counts[k]), borderWidth: 2, borderColor: css("--surface"),
                             backgroundColor: keys.map((k, i) => (SEMANTIC[k] ? SEMANTIC[k]() : PALETTE[i % PALETTE.length])) }] },
        options: { responsive: true, maintainAspectRatio: false, cutout: "62%",
                   plugins: { legend: { position: "right", labels: { color: css("--text-2"), boxWidth: 10, usePointStyle: true } } } },
      });
    },
    scoreColor(v) { return v >= 85 ? css("--good") : v >= 70 ? css("--ok") : css("--bad"); },
  };

  /* ---------- call detail: audio <-> transcript ---------- */
  function initPlayer() {
    const audio = document.getElementById("call-audio");
    const lines = Array.from(document.querySelectorAll(".transcript .line"));
    if (!audio || !lines.length) return;
    const follow = document.getElementById("follow-audio");
    const seek = (t) => { audio.currentTime = t; audio.play().catch(() => {}); };

    lines.forEach((line) => line.addEventListener("click", () => seek(parseFloat(line.dataset.start))));
    document.querySelectorAll(".sentiment-strip span").forEach((s) =>
      s.addEventListener("click", () => seek(parseFloat(s.dataset.start))));

    let current = null;
    audio.addEventListener("timeupdate", () => {
      const t = audio.currentTime;
      let active = null;
      for (const line of lines) {
        if (parseFloat(line.dataset.start) <= t + 0.05) active = line; else break;
      }
      if (active !== current) {
        if (current) current.classList.remove("active");
        if (active) {
          active.classList.add("active");
          if (!follow || follow.checked) active.scrollIntoView({ block: "nearest", behavior: "smooth" });
        }
        current = active;
      }
    });
  }

  /* ---------- upload drop zone ---------- */
  function initDropzone() {
    const zone = document.querySelector(".dropzone");
    if (!zone) return;
    const input = zone.querySelector("input[type=file]");
    const name = zone.querySelector(".file-name");
    const show = () => { if (name) name.textContent = input.files.length ? input.files[0].name : ""; };
    ["dragenter", "dragover"].forEach((e) => zone.addEventListener(e, (ev) => { ev.preventDefault(); zone.classList.add("drag"); }));
    ["dragleave", "drop"].forEach((e) => zone.addEventListener(e, (ev) => { ev.preventDefault(); zone.classList.remove("drag"); }));
    zone.addEventListener("drop", (ev) => { input.files = ev.dataTransfer.files; show(); });
    input.addEventListener("change", show);
  }

  /* ---------- clickable rows ---------- */
  document.addEventListener("click", (ev) => {
    const row = ev.target.closest("tr[data-href]");
    if (row && !ev.target.closest("a, button, input, form")) window.location = row.dataset.href;
  });

  document.addEventListener("DOMContentLoaded", () => { initPlayer(); initDropzone(); });
  window.CC = { charts, data, label, css };
})();
