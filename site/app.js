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
    $("#board-narrow").innerHTML = `<p class="board-empty muted">No data yet.</p>`;
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
      (y.won - x.won) || (y.avg - x.avg) || family(x.id, x.label).localeCompare(family(y.id, y.label)));
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


  // Correlation between contestants' picks. Each ticket (all six numbers, bonus ball included and
  // treated like any other) becomes a 0/1 vector over (drawing, ball number); the Pearson
  // correlation of two such vectors is the phi coefficient. Only drawings where both contestants
  // have a valid pick count.
  const BALL_MAX = { powerball: 69, megamillions: 70 };
  function corrMatrix(g) {
    const ids = data.leaderboard.map((b) => b.id);
    const draws = data.draws.filter((d) => g === "all" || d.game === g);
    const pick = (d, id) => {
      const p = d.picks[id];
      return p && p.numbers ? new Set(p.bonus != null ? [...p.numbers, p.bonus] : p.numbers) : null;
    };
    const present = ids.filter((id) => draws.some((d) => pick(d, id)));
    const cell = {};
    for (const a of present) for (const b of present) {
      if (a >= b) continue;
      let n = 0, N = 0, both = 0, sa = 0, sb = 0, expected = 0, varSum = 0;
      for (const d of draws) {
        const pa = pick(d, a), pb = pick(d, b);
        if (!pa || !pb) continue;
        n++; N += BALL_MAX[d.game]; sa += pa.size; sb += pb.size;
        const M = BALL_MAX[d.game];
        expected += pa.size * pb.size / M;
        varSum += pa.size * pb.size * (M - pa.size) * (M - pb.size) / (M * M * (M - 1));
        for (const x of pa) if (pb.has(x)) both++;
      }
      const den = Math.sqrt(sa * (N - sa) * sb * (N - sb));
      cell[a + "|" + b] = { n, shared: both, expected, z: n && varSum ? (both - expected) / Math.sqrt(varSum) : null,
        r: n && den ? (both * N - sa * sb) / den : null };
    }
    return { ids: present, cell };
  }

  const MODES = {
    r: { self: "1", text: (c) => c.r == null ? null : c.r.toFixed(2),
      tone: (c) => c.r / 0.4,
      note: "Correlation of the two tickets' number-by-number picks (0/1 vectors over every ball in every drawing). 0 means no more overlap than chance; 1 means identical tickets." },
    overlap: { self: "–", text: (c) => c.n ? (c.shared / c.n).toFixed(2) : null,
      tone: (c) => (c.shared - c.expected) / c.n,
      note: "Average count of numbers two contestants' tickets share per drawing. Two random six-number tickets share about 0.5; hover a cell for the exact chance level for that pair." },
    z: { self: "–", text: (c) => c.z == null ? null : c.z.toFixed(1),
      tone: (c) => c.z / 4,
      note: "How far the shared-number count sits from what two random tickets would share, in standard errors (hypergeometric). Roughly, beyond ±2 is unlikely to be chance for one pair, but with ~28 pairs a few will cross it by luck." },
  };
  let corrMode = "r";

  function renderCorr() {
    const { ids, cell } = corrMatrix(game);
    const el = $("#corr"), m = MODES[corrMode];
    document.querySelectorAll("#corr-seg .seg-opt").forEach((b) =>
      b.setAttribute("aria-pressed", String(b.dataset.mode === corrMode)));
    $("#corr-note").textContent = m.note + " Uses all six numbers (bonus ball treated like any other) over drawings both contestants entered, and follows the game filter above. With only a few dozen drawings, small differences are mostly noise.";
    if (ids.length < 2) { el.innerHTML = `<tbody><tr><td>Not enough picks yet.</td></tr></tbody>`; return; }
    const get = (a, b) => cell[(a < b ? a : b) + "|" + (a < b ? b : a)];
    const short = (id) => esc(family(id, contestants[id]?.label || id));
    el.innerHTML = `<thead><tr><th></th>${ids.map((id) => `<th class="r">${short(id)}</th>`).join("")}</tr></thead><tbody>` +
      ids.map((a) => `<tr><th scope="row" class="corr-row">${short(a)}</th>` + ids.map((b) => {
        if (a === b) return `<td class="r corr-self">${m.self}</td>`;
        const c = get(a, b), t = c && m.text(c);
        if (t == null) return `<td class="r muted">–</td>`;
        const tone = m.tone(c);
        const alpha = Math.min(60, Math.round(Math.abs(tone) * 60));
        return `<td class="r corr-c" data-a="${alpha}" data-neg="${tone < 0 ? 1 : 0}" ` +
          `title="${c.n} drawings; ${c.shared} numbers in common vs ${c.expected.toFixed(1)} expected by chance">${t}</td>`;
      }).join("") + "</tr>").join("") + "</tbody>";
    el.querySelectorAll(".corr-c").forEach((td) => {
      td.style.background = `color-mix(in srgb, var(${td.dataset.neg === "1" ? "--color-text" : "--color-accent"}) ${td.dataset.a}%, transparent)`;
    });
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

  $("#corr-seg").addEventListener("click", (ev) => {
    const b = ev.target.closest(".seg-opt");
    if (b) { corrMode = b.dataset.mode; renderCorr(); }
  });
  $("#seg").addEventListener("click", (ev) => {
    const b = ev.target.closest(".seg-opt");
    if (b) { game = b.dataset.game; renderBoard(); renderCorr(); }
  });
  const gen = new Date(data.generated_at).toLocaleString("en-US",
    { month: "short", day: "numeric", hour: "numeric", minute: "2-digit" });
  $("#gen").textContent = "Updated " + gen + ".";
  $("#gen2").textContent = "Updated " + gen + ".";
  renderBoard(); renderCorr(); renderDraws();
})();
