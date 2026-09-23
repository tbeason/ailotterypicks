(async function () {
  const $ = (s) => document.querySelector(s);
  const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) =>
    ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  const money = (n) => (n < 0 ? "-" : "") + "$" + Math.abs(n).toLocaleString();

  let data;
  try {
    data = await (await fetch("data.json", { cache: "no-cache" })).json();
  } catch (e) {
    $("#board").innerHTML = "<tr><td>No data yet.</td></tr>";
    return;
  }
  let game = "all";
  let shown = 12;

  function boardRows() {
    return data.leaderboard.map((b) => {
      if (game === "all") return b;
      const g = b.by_game[game];
      return g ? { ...b, tickets: g.tickets, spent: g.spent, won: g.won, net: g.won - g.spent, perGame: true } : null;
    }).filter(Boolean).sort((a, b) => b.net - a.net);
  }

  function renderBoard() {
    const rows = boardRows();
    if (!rows.length) {
      $("#board").innerHTML = "<tr><td>No scored drawings yet. Check back after the next drawing.</td></tr>";
    } else {
      $("#board").innerHTML =
        "<thead><tr><th>Contestant</th><th>Tickets</th><th>Spent</th><th>Won</th><th>Net</th>" +
        (game === "all" ? "<th>Avg white hits</th><th>Bonus hit rate</th><th>Best</th>" : "") +
        "</tr></thead><tbody>" +
        rows.map((b) => `<tr class="${b.baseline ? "baseline" : ""}">
          <td>${esc(b.label)}</td><td>${b.tickets}</td><td>${money(b.spent)}</td>
          <td>${money(b.won)}</td><td class="${b.net > 0 ? "pos" : "neg"}">${money(b.net)}</td>
          ${game === "all" ? `<td>${b.avg_white_matches.toFixed(2)}</td>
          <td>${(100 * b.bonus_rate).toFixed(1)}%</td>
          <td>${b.best ? `${b.best.tier[0]}${b.best.tier[1] ? "+B" : ""}` : "-"}</td>` : ""}
        </tr>`).join("") + "</tbody>";
    }
    const e = data.expected_random;
    const keys = game === "all" ? Object.keys(e) : [game];
    $("#expect").innerHTML = "What pure chance predicts per ticket: " + keys.map((k) =>
      `<b>${k === "powerball" ? "Powerball" : "Mega Millions"}</b>: ${e[k].white_matches.toFixed(2)} white hits, ` +
      `${(100 * e[k].bonus_match).toFixed(1)}% bonus hits, wins something ${(100 * e[k].win_rate).toFixed(1)}% of the time, ` +
      `returns about ${(100 * e[k].return_per_dollar_ex_jackpot).toFixed(0)}&cent; per $1 in base prizes, before the jackpot ` +
      `(jackpot odds 1 in ${e[k].jackpot_odds.toLocaleString()}).`).join(" ") +
      " A model beating these by a little over a few dozen tickets is luck, not skill.";
  }

  function balls(nums, bonus, result) {
    const hitW = new Set(result ? result.numbers : []);
    return `<span class="balls">${nums.map((n) =>
      `<span class="b ${hitW.has(n) ? "hit" : ""}">${n}</span>`).join("")}` +
      `<span class="b bonus ${result && result.bonus === bonus ? "hit" : ""}">${bonus}</span></span>`;
  }

  function card(d) {
    const r = d.result;
    const ids = Object.keys(d.picks).sort((a, b) =>
      ((d.scores?.[b]?.prize ?? -1) - (d.scores?.[a]?.prize ?? -1)) || a.localeCompare(b));
    const rows = ids.map((id) => {
      const p = d.picks[id];
      if (p.error) return `<div class="row"><span class="who">${esc(p.label)}</span><span class="err">no valid pick</span><span></span></div>`;
      const s = d.scores?.[id];
      const score = s ? `<b>${s.white_matches}/5${s.bonus_match ? " + bonus" : ""}</b> ${s.prize ? "won " + money(s.prize) : ""}` : "";
      const conf = p.confidence != null ? ` · confidence ${p.confidence}%` : "";
      return `<div class="row"><span class="who">${esc(p.label)}</span>${balls(p.numbers, p.bonus, r)}
        <span class="meta">${score}
          <details><summary>${esc(p.strategy || "strategy")}${conf}</summary><p>${esc(p.rationale)}</p></details>
        </span></div>`;
    }).join("");
    const head = r ? `<div class="row result"><span class="who">Drawn</span>${balls(r.numbers, r.bonus, null)}
        <span class="meta">${r.multiplier ? r.multiplier + "x multiplier" : ""}${r.jackpot ? " · jackpot " + esc(r.jackpot) : ""}</span></div>` : "";
    const link = r
      ? `<a href="${esc(d.official_url)}" rel="noopener noreferrer" target="_blank">official results</a>`
      : `<a href="${esc(d.home_url)}" rel="noopener noreferrer" target="_blank">official site</a>`;
    return `<article class="card"><h3>${esc(d.game_name)} <small>${d.date}${r ? "" : " · awaiting drawing"} · ${link}</small></h3>${head}${rows}</article>`;
  }

  function renderDraws() {
    const list = data.draws.filter((d) => game === "all" || d.game === game);
    const up = list.filter((d) => !d.result);
    const past = list.filter((d) => d.result);
    $("#upcoming-sec").hidden = !up.length;
    $("#upcoming").innerHTML = up.map(card).join("");
    $("#past").innerHTML = past.slice(0, shown).map(card).join("") || "<p class='note'>Nothing scored yet.</p>";
    $("#more").hidden = past.length <= shown;
  }

  document.querySelectorAll(".tabs button").forEach((b) => b.addEventListener("click", () => {
    document.querySelectorAll(".tabs button").forEach((x) => x.classList.toggle("on", x === b));
    game = b.dataset.game; shown = 12; renderBoard(); renderDraws();
  }));
  $("#more").addEventListener("click", () => { shown += 12; renderDraws(); });
  $("#gen").textContent = "Updated " + new Date(data.generated_at).toLocaleString() + ".";
  renderBoard(); renderDraws();
})();
