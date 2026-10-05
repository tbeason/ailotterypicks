(async function () {
  const $ = (s) => document.querySelector(s);
  const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) =>
    ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  const money = (n) => (n < 0 ? "-" : "") + "$" + Math.abs(n).toLocaleString();
  const GAME_NAMES = { powerball: "Powerball", megamillions: "Mega Millions" };

  let data;
  try {
    data = await (await fetch("data.json", { cache: "no-cache" })).json();
  } catch (e) {
    $("#board").innerHTML = "<tbody><tr><td>No data yet.</td></tr></tbody>";
    return;
  }
  let game = "all";
  const contestants = Object.fromEntries((data.contestants || []).map((c) => [c.id, c]));

  const family = (id, label) => contestants[id]?.family || label;
  // Specific version text; falls back to the model slug when the label is just the family name.
  function version(id, label, model) {
    if (!model || model === "baseline") return "";
    if (label !== family(id, label)) return label;
    return model.split("/").pop().split("-").map((w) =>
      w.toLowerCase() === "deepseek" ? "DeepSeek" : w.charAt(0).toUpperCase() + w.slice(1)).join(" ");
  }
  const currentVersion = (id) => {
    const c = contestants[id];
    return c ? version(id, c.label, c.model) : "";
  };

  // Rows for the leaderboard, for "all" or a single game.
  function boardFor(g) {
    const e = data.expected_random;
    return data.leaderboard.map((b) => {
      const bg = b.by_game || {};
      if (g === "all") {
        const pt = bg.powerball?.tickets || 0, mt = bg.megamillions?.tickets || 0;
        const chance = pt + mt
          ? (pt * e.powerball.white_matches + mt * e.megamillions.white_matches) / (pt + mt)
          : e.powerball.white_matches;
        return { id: b.id, label: b.label, baseline: b.baseline, tickets: b.tickets, spent: b.spent,
          won: b.won, net: b.net, avg: b.avg_white_matches, bonus: b.bonus_rate,
          best: b.best ? b.best.tier : null, chance };
      }
      const x = bg[g];
      if (!x || !x.tickets) return null;
      return { id: b.id, label: b.label, baseline: b.baseline, tickets: x.tickets, spent: x.spent,
        won: x.won, net: x.won - x.spent, avg: (x.white_matches || 0) / x.tickets,
        bonus: (x.bonus_matches || 0) / x.tickets, best: x.best || null, chance: e[g].white_matches };
    }).filter(Boolean).sort((x, y) =>
      (y.net - x.net) || (y.avg - x.avg) || family(x.id, x.label).localeCompare(family(y.id, y.label)));
  }

  const whoHtml = (b) => `<span class="who"><span class="fam">${esc(family(b.id, b.label))}</span>` +
    (b.baseline ? `<span class="tag tag-base">Baseline</span>` : "") +
    `<span class="ver">${esc(currentVersion(b.id))}</span></span>`;
  const netClass = (n) => (n > 0 ? "net-pos" : "");
  const bar = (b, max) => `<span class="bar"><span class="bar-fill" data-w="${Math.min(100, 100 * b.avg / max).toFixed(1)}"></span>` +
    `<span class="bar-tick" data-x="${Math.min(100, 100 * b.chance / max).toFixed(1)}"></span></span>`;
  const bestText = (b) => (b ? `${b[0]}${b[1] ? "+B" : ""}` : "–");

  // CSP forbids style attributes in markup; dynamic widths are set through the CSSOM.
  function applyBars(root) {
    root.querySelectorAll("[data-w]").forEach((el) => { el.style.width = el.dataset.w + "%"; });
    root.querySelectorAll("[data-x]").forEach((el) => { el.style.left = el.dataset.x + "%"; });
  }

  function renderBoard() {
    const rows = boardFor(game);
    const wide = $("#board"), narrow = $("#board-narrow");
    if (!rows.length) {
      const msg = "No scored drawings yet. Check back after the next drawing.";
      wide.innerHTML = `<tbody><tr><td>${msg}</td></tr></tbody>`;
      narrow.innerHTML = `<p class="board-empty muted">${msg}</p>`;
    } else {
      const max = Math.max(1, ...rows.map((b) => b.avg));
      wide.innerHTML =
        `<thead><tr><th>Contestant</th><th class="r">Tickets</th><th class="r">Spent</th><th class="r">Won</th>` +
        `<th class="r">Net</th><th>Avg white hits <small>(| = chance)</small></th>` +
        `<th class="r">Bonus hits</th><th class="r">Best</th></tr></thead><tbody>` +
        rows.map((b) => `<tr><td>${whoHtml(b)}</td><td class="r">${b.tickets}</td>
          <td class="r">${money(b.spent)}</td><td class="r">${money(b.won)}</td>
          <td class="r ${netClass(b.net)}">${money(b.net)}</td>
          <td><span class="avgcell"><span class="avgnum">${b.avg.toFixed(2)}</span>${bar(b, max)}</span></td>
          <td class="r">${(100 * b.bonus).toFixed(1)}%</td>
          <td class="r best">${bestText(b.best)}</td></tr>`).join("") + "</tbody>";
      narrow.innerHTML = rows.map((b) => `<div class="nrow">${whoHtml(b)}
        <span class="nnet ${netClass(b.net)}">${money(b.net)}</span>
        <span class="nbar">${bar(b, max)}<span class="ver">${b.avg.toFixed(2)} white · ${(100 * b.bonus).toFixed(1)}% bonus · ${b.tickets} tix</span></span>
        </div>`).join("") +
        `<p class="muted tiny">Bar: avg white hits per ticket. Tick: what chance predicts.</p>`;
      applyBars(wide); applyBars(narrow);
    }
    const e = data.expected_random;
    $("#expect").innerHTML = Object.keys(e).filter((k) => game === "all" || k === game).map((k) => `<div>
      <div class="panel-title">${GAME_NAMES[k] || esc(k)}</div><div class="stats">` + [
        [e[k].white_matches.toFixed(2), "white hits"],
        [(100 * e[k].bonus_match).toFixed(1) + "%", "bonus hits"],
        [(100 * e[k].win_rate).toFixed(1) + "%", "wins something"],
        [(100 * e[k].return_per_dollar_ex_jackpot).toFixed(0) + "¢", "back per $1"],
        ["1 in " + (e[k].jackpot_odds / 1e6).toFixed(0) + "M", "jackpot odds"],
      ].map(([v, l]) => `<div class="stat"><span class="stat-v">${v}</span><span class="stat-k">${l}</span></div>`).join("") +
      `</div></div>`).join("");
    document.querySelectorAll("#seg .seg-opt").forEach((b) =>
      b.setAttribute("aria-pressed", String(b.dataset.game === game)));
  }

  const cell = (n, cls) => `<span class="n ${cls}">${n}</span>`;
  function pickCells(nums, bonus, result) {
    const hit = new Set(result ? result.numbers : []);
    return `<span class="nums">${nums.map((n) => cell(n, hit.has(n) ? "n-hit" : "")).join("")}` +
      cell(bonus, result && result.bonus === bonus ? "n-bonus-hit" : "n-bonus") + `</span>`;
  }
  const drawnCells = (r) => `<span class="nums">${r.numbers.map((n) => cell(n, "n-drawn")).join("")}` +
    cell(r.bonus, "n-drawn-bonus") + `</span>`;
  const fmtDate = (s) => new Date(s + "T12:00:00").toLocaleDateString("en-US",
    { weekday: "short", month: "short", day: "numeric" });

  function pickRow(d, id) {
    const p = d.picks[id], s = d.scores?.[id], r = d.result;
    const fam = esc(family(id, p.label));
    if (p.error) return `<div class="prow"><div class="pwho"><span class="fam">${fam}</span>
      <span class="ver">No valid pick</span></div></div>`;
    const ver = version(id, p.label, p.model);
    const sub = r
      ? [ver, s ? `${s.white_matches} of 5${s.bonus_match ? " + " + d.bonus_name : ""}` : ""]
      : [ver, p.confidence != null ? `Confidence ${p.confidence}%` : "Baseline"];
    const win = s && s.prize > 0 ? `<span class="tag tag-win">Won ${money(s.prize)}</span>` : "";
    const why = p.strategy || p.rationale
      ? `<div class="why"><details><summary><b>${esc(p.strategy || "Strategy")}</b>
         <span class="muted">· rationale +</span></summary><p>${esc(p.rationale)}</p></details></div>` : "";
    return `<div class="prow"><div class="pwho"><span class="pname"><span class="fam">${fam}</span>${win}</span>
      <span class="ver">${sub.filter(Boolean).map(esc).join(" · ")}</span></div>
      ${pickCells(p.numbers, p.bonus, r)}${why}</div>`;
  }

  function column(d) {
    const r = d.result;
    const ids = Object.keys(d.picks).sort((a, b) =>
      ((d.scores?.[b]?.prize ?? -1) - (d.scores?.[a]?.prize ?? -1)) ||
      ((d.scores?.[b]?.white_matches ?? 0) - (d.scores?.[a]?.white_matches ?? 0)) || a.localeCompare(b));
    const meta = r ? [r.jackpot ? "Jackpot $" + r.jackpot : "", r.multiplier ? r.multiplier + "x multiplier" : ""]
      .filter(Boolean).map(esc).join(" · ") : "";
    const head = `<header class="col-head"><h3>${esc(d.game_name)}</h3><span class="col-date">${fmtDate(d.date)}</span>` +
      (r ? `<span class="col-meta">${meta}</span>` : `<span class="tag tag-accent">Awaiting drawing</span>`) + `</header>`;
    const drawn = r ? `<div class="prow drawn"><span class="drawn-label">Drawn</span>${drawnCells(r)}</div>` : "";
    return `<article>${head}${drawn}${ids.map((id) => pickRow(d, id)).join("")}</article>`;
  }

  function renderDraws() {
    const up = data.draws.filter((d) => !d.result).sort((a, b) => Object.keys(GAME_NAMES).indexOf(a.game) - Object.keys(GAME_NAMES).indexOf(b.game) || a.date.localeCompare(b.date));
    const past = data.draws.filter((d) => d.result).sort((a, b) => b.date.localeCompare(a.date));
    const latest = ["powerball", "megamillions"].map((g) => past.find((d) => d.game === g)).filter(Boolean);
    $("#upcoming").hidden = !up.length;
    $("#upcoming-cols").innerHTML = up.map(column).join("");
    $("#results-cols").innerHTML = latest.map(column).join("") || "<p class='muted'>Nothing scored yet.</p>";
  }

  $("#seg").addEventListener("click", (ev) => {
    const b = ev.target.closest(".seg-opt");
    if (b) { game = b.dataset.game; renderBoard(); }
  });
  const gen = new Date(data.generated_at).toLocaleString("en-US",
    { month: "short", day: "numeric", hour: "numeric", minute: "2-digit" });
  $("#gen").textContent = "Updated " + gen + ".";
  $("#gen2").textContent = "Updated " + gen + ".";
  renderBoard(); renderDraws();
})();
