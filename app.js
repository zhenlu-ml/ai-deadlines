(() => {
  "use strict";
  const DATA = window.DEADLINES;
  const $ = (s, el = document) => el.querySelector(s);
  const main = $("#main");
  if (!DATA) {
    main.innerHTML = `<p class="empty">No data yet — run <code>uv run scripts/update.py</code> first.</p>`;
    return;
  }

  const CATS = DATA.categories;
  const DAY = 864e5;
  const now = () => Date.now();
  const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  const tzName = Intl.DateTimeFormat().resolvedOptions().timeZone || "local time";
  $("#tzname").textContent = tzName;

  // ---------------------------------------------------------------- state (URL ?sub=ML,CV compatible with aideadlines.org)
  const store = {
    get(k) { try { return localStorage.getItem("aidl:" + k); } catch { return null; } },
    set(k, v) { try { localStorage.setItem("aidl:" + k, v); } catch {} },
  };
  const params = new URLSearchParams(location.search);
  const state = {
    subs: new Set((params.get("sub") || store.get("sub") || "").split(",").filter((s) => CATS[s])),
    q: params.get("q") || "",
    past: params.get("past") === "1",
    top: params.get("top") === "1" || store.get("top") === "1",
  };
  function syncUrl() {
    const p = new URLSearchParams();
    if (state.subs.size) p.set("sub", [...state.subs].join(","));
    if (state.q) p.set("q", state.q);
    if (state.past) p.set("past", "1");
    if (state.top) p.set("top", "1");
    const qs = p.toString();
    history.replaceState(null, "", location.pathname + (qs ? "?" + qs : ""));
    store.set("sub", [...state.subs].join(","));
    store.set("top", state.top ? "1" : "0");
  }

  // ---------------------------------------------------------------- data prep
  const confs = DATA.conferences.map((c) => ({
    ...c,
    dls: c.deadlines.map((d) => ({ ...d, t: Date.parse(d.utc) })).sort((a, b) => a.t - b.t),
  }));
  const isTop = (c) => c.ranks?.CCF === "A" || c.ranks?.CORE === "A*";

  // Upcoming = has a deadline in the future. Each conference series shows only its nearest edition.
  function partition() {
    const t = now();
    const bySeries = new Map();
    for (const c of confs) {
      if (!bySeries.has(c.key)) bySeries.set(c.key, []);
      bySeries.get(c.key).push(c);
    }
    const upcoming = [], expected = [], past = [];
    for (const [key, eds] of bySeries) {
      eds.sort((a, b) => a.year - b.year);
      const live = eds.filter((c) => c.dls.some((d) => d.t > t));
      if (live.length) {
        for (const c of live) upcoming.push({ c, next: c.dls.find((d) => d.t > t) });
        continue;
      }
      // Nothing announced: estimate from the latest edition that had a paper deadline.
      const withDl = eds.filter((c) => c.dls.some((d) => d.type === "paper"));
      const last = withDl[withDl.length - 1];
      const latest = eds[eds.length - 1];
      if (last) {
        const lastPaper = last.dls.filter((d) => d.type === "paper").at(-1);
        past.push({ c: last, last: lastPaper });
        // annual vs biennial (ICCV/ECCV), judged from editions that actually had deadlines
        const years = [...new Set(withDl.map((c) => c.year))];
        const period = years.length >= 2 ? Math.min(2, Math.max(1, years.at(-1) - years.at(-2))) : 1;
        let nextYear = last.year + period;
        let est = new Date(lastPaper.t);
        est.setUTCFullYear(est.getUTCFullYear() + period);
        while (est.getTime() < t) { est.setUTCFullYear(est.getUTCFullYear() + period); nextYear += period; }
        const known = eds.find((c) => c.year === nextYear);
        expected.push({ c: known || { ...last, year: nextYear, link: latest.link }, est: est.getTime(), basis: last });
      } else {
        expected.push({ c: latest, est: null, basis: null });
      }
    }
    upcoming.sort((a, b) => a.next.t - b.next.t);
    expected.sort((a, b) => (a.est ?? Infinity) - (b.est ?? Infinity));
    past.sort((a, b) => b.last.t - a.last.t);
    return { upcoming, expected, past };
  }

  function matches(c) {
    if (state.subs.size && !c.subs.some((s) => state.subs.has(s))) return false;
    if (state.top && !isTop(c)) return false;
    if (state.q) {
      const hay = `${c.title} ${c.year} ${c.full_name || ""} ${c.place || ""} ${c.subs.join(" ")}`.toLowerCase();
      if (!state.q.toLowerCase().split(/\s+/).every((w) => hay.includes(w))) return false;
    }
    return true;
  }

  // ---------------------------------------------------------------- formatting
  const fmtLocal = new Intl.DateTimeFormat(undefined, { weekday: "short", month: "short", day: "numeric", year: "numeric", hour: "2-digit", minute: "2-digit" });
  const fmtDay = new Intl.DateTimeFormat(undefined, { month: "short", day: "numeric", year: "numeric" });
  const fmtMonth = new Intl.DateTimeFormat(undefined, { month: "short", year: "numeric" });
  const pad = (n) => String(n).padStart(2, "0");

  function countdown(ms) {
    if (ms <= 0) return "closed";
    const s = Math.floor(ms / 1000);
    const d = Math.floor(s / 86400), h = Math.floor((s % 86400) / 3600), m = Math.floor((s % 3600) / 60), sec = s % 60;
    return `${d}<small>d</small>${pad(h)}<small>h</small>${pad(m)}<small>m</small>${pad(sec)}<small>s</small>`;
  }
  function rel(ms) {
    const d = Math.round(Math.abs(ms) / DAY);
    const txt = d === 0 ? (Math.abs(ms) < 36e5 ? `${Math.max(1, Math.round(Math.abs(ms) / 6e4))} min` : `${Math.round(Math.abs(ms) / 36e5)} h`) : `${d} day${d > 1 ? "s" : ""}`;
    return ms >= 0 ? `in ${txt}` : `${txt} ago`;
  }
  // "2027-01-22 23:59:59 AoE" -> "Jan 22, 23:59 AoE"
  function original(o) {
    const m = /^(\d{4})-(\d{2})-(\d{2})[ T](\d{2}):(\d{2})(?::\d{2})?\s*(.*)$/.exec(o || "");
    if (!m) return esc(o);
    const mon = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"][+m[2] - 1];
    return `${mon} ${+m[3]}, ${m[1]} ${m[4]}:${m[5]} ${esc(m[6])}`;
  }
  const STATUS = {
    verified: ["✓", "on official site", "The date appears on the conference's official page"],
    unverified: ["○", "not confirmed", "Official page reachable, but this date wasn't found on it"],
    unreachable: ["○", "site unreachable", "Couldn't load the official page during the last update"],
    manual: ["✎", "manual", "Set by hand in overrides.yaml"],
    unchecked: ["·", "unchecked", "Updater ran with --no-verify"],
  };
  const statusHtml = (s) => (s && STATUS[s] ? `<span class="st ${s}" title="${STATUS[s][2]}">${STATUS[s][0]} ${STATUS[s][1]}</span>` : "");
  const tag = (s) => `<span class="tag" style="--c:var(--c-${s})" title="${esc(CATS[s])}">${s}</span>`;
  const ranks = (r) => {
    const parts = Object.entries(r || {}).filter(([k]) => k !== "THCPL").map(([k, v]) => `${k} <b>${esc(v)}</b>`);
    return parts.length ? `<span class="rank">${parts.join(" · ")}</span>` : "";
  };

  // ---------------------------------------------------------------- calendar export
  function icsDate(t) { return new Date(t).toISOString().replace(/[-:]/g, "").replace(/\.\d{3}/, ""); }
  function icsEvents(items) {
    const stamp = icsDate(now());
    const lines = ["BEGIN:VCALENDAR", "VERSION:2.0", "PRODID:-//ai-deadlines//EN", "CALSCALE:GREGORIAN"];
    for (const { c, d } of items) {
      const name = `${c.title} ${c.year} – ${d.type === "abstract" ? "Abstract" : "Paper"} deadline`;
      lines.push("BEGIN:VEVENT", `UID:${c.id}-${d.type}-${d.t}@ai-deadlines`, `DTSTAMP:${stamp}`,
        `DTSTART:${icsDate(d.t - 36e5)}`, `DTEND:${icsDate(d.t)}`,
        `SUMMARY:${name.replace(/[,;]/g, "\\$&")}`,
        `DESCRIPTION:${(d.label + " — " + d.original + (c.link ? "\\n" + c.link : "")).replace(/[,;]/g, "\\$&")}`,
        ...(c.link ? [`URL:${c.link}`] : []),
        "BEGIN:VALARM", "TRIGGER:-P7D", "ACTION:DISPLAY", `DESCRIPTION:${name} in 7 days`, "END:VALARM",
        "BEGIN:VALARM", "TRIGGER:-P1D", "ACTION:DISPLAY", `DESCRIPTION:${name} tomorrow`, "END:VALARM",
        "END:VEVENT");
    }
    lines.push("END:VCALENDAR");
    return lines.join("\r\n");
  }
  function download(name, text) {
    const a = document.createElement("a");
    a.href = URL.createObjectURL(new Blob([text], { type: "text/calendar" }));
    a.download = name;
    document.body.appendChild(a); a.click(); a.remove();
    setTimeout(() => URL.revokeObjectURL(a.href), 1000);
  }
  function gcal(c, d) {
    const p = new URLSearchParams({ action: "TEMPLATE", text: `${c.title} ${c.year} deadline`, dates: `${icsDate(d.t - 36e5)}/${icsDate(d.t)}`, details: `${d.label} — ${d.original}\n${c.link || ""}` });
    return "https://calendar.google.com/calendar/render?" + p;
  }

  // ---------------------------------------------------------------- render
  let visibleUpcoming = [];
  function card({ c, next }, isPast) {
    const t = now();
    const target = next || c.dls.filter((d) => d.type === "paper").at(-1) || c.dls.at(-1);
    const left = target ? target.t - t : 0;
    const cls = !target || left <= 0 ? "done" : left < 3 * DAY ? "urgent" : left < 14 * DAY ? "soon" : "";
    const mainColor = `var(--c-${c.subs[0]})`;
    const rows = c.dls.filter((d) => isPast || d.t > t - 2 * DAY).map((d) => {
      const gone = d.t <= t;
      const label = d.label && !/^(paper submission|abstract)$/i.test(d.label) ? `<b>${d.type === "abstract" ? "Abstract" : "Paper"}</b> · ${esc(d.label.replace(/^Abstract · /, ""))}` : `<b>${d.type === "abstract" ? "Abstract" : "Paper"}</b>`;
      return `<div class="dl${gone ? " gone" : ""}"><span class="lbl" title="${esc(d.label)}">${label}</span>
        <span class="when">${original(d.original)} · ${rel(d.t - t)}${gone ? "" : statusHtml(c.conflict && d.status !== "verified" ? null : d.status)}${c.conflict && !gone && d.type === "paper" ? `<span class="st conflict" title="ccfddl and huggingface/ai-deadlines list different dates">⚠ sources differ</span>` : ""}</span></div>`;
    }).join("");
    return `<article class="card${isPast ? " past" : ""}" style="--c:${mainColor}">
      <div>
        <div class="title"><a href="${esc(c.link || "#")}" target="_blank" rel="noopener">${esc(c.title)} ${c.year}</a>${c.subs.map(tag).join("")}${ranks(c.ranks)}</div>
        <div class="full">${esc(c.full_name || "")}</div>
        <div class="where">${[c.date, c.place].filter(Boolean).map(esc).join(" · ")}</div>
      </div>
      <div class="cd ${cls}">
        ${target ? `<div class="big" data-t="${target.t}">${isPast ? fmtDay.format(target.t) : countdown(left)}</div>
        <div class="what">${target.type === "abstract" ? "Abstract deadline" : "Paper deadline"}</div>
        <div class="local">${fmtLocal.format(target.t)}</div>` : `<div class="big">TBA</div>`}
      </div>
      ${rows ? `<div class="dls">${rows}</div>` : ""}
      ${isPast ? "" : `<div class="actions">
        ${c.link ? `<a class="btn" href="${esc(c.link)}" target="_blank" rel="noopener">Website ↗</a>` : ""}
        ${target && left > 0 ? `<button class="btn" data-ics="${esc(c.id)}">+ .ics</button><a class="btn" href="${esc(gcal(c, target))}" target="_blank" rel="noopener">+ Google Calendar</a>` : ""}
      </div>`}
    </article>`;
  }

  function render() {
    const { upcoming, expected, past } = partition();
    const up = upcoming.filter(({ c }) => matches(c));
    const ex = expected.filter(({ c }) => matches(c));
    const pa = past.filter(({ c }) => matches(c));
    visibleUpcoming = up;

    let html = `<h2>Upcoming · ${up.length}</h2>`;
    html += up.length ? up.map((x) => card(x)).join("") : `<p class="empty">No upcoming deadlines match these filters.</p>`;
    if (ex.length) {
      html += `<h2>Not announced yet · estimated from previous edition</h2><table class="exp"><tbody>`;
      html += ex.map(({ c, est, basis }) => `<tr style="--c:var(--c-${c.subs[0]})">
        <td class="nm"><a href="${esc(c.link || "#")}" target="_blank" rel="noopener">${esc(c.title)} ${c.year}</a></td>
        <td class="full">${esc(c.full_name || "")}</td>
        <td class="est" title="${basis ? `${esc(basis.title)} ${basis.year} deadline was ${esc(original(basis.dls.filter((d) => d.type === "paper").at(-1).original))}` : ""}">${est ? "≈ " + fmtMonth.format(est) : "TBA"}</td></tr>`).join("");
      html += `</tbody></table>`;
    }
    if (state.past && pa.length) {
      html += `<h2>Past deadlines · ${pa.length}</h2>` + pa.map((x) => card({ c: x.c }, true)).join("");
    }
    main.innerHTML = html;
    renderChips(upcoming);
    tick();
  }

  function renderChips(upcoming) {
    const counts = {};
    for (const { c } of upcoming) for (const s of c.subs) counts[s] = (counts[s] || 0) + 1;
    const el = $("#chips");
    el.innerHTML = `<button class="chip" data-sub="" aria-pressed="${state.subs.size === 0}">All</button>` +
      Object.entries(CATS).map(([k, v]) => `<button class="chip" data-sub="${k}" style="--c:var(--c-${k})" title="${esc(v)}" aria-pressed="${state.subs.has(k)}"><span class="sw"></span>${esc(v)}<span class="n">${counts[k] || 0}</span></button>`).join("");
  }

  function tick() {
    const t = now();
    for (const el of main.querySelectorAll(".cd .big[data-t]")) {
      if (el.closest(".past")) continue;
      const left = +el.dataset.t - t;
      el.innerHTML = countdown(left);
      const cd = el.parentElement;
      cd.classList.toggle("urgent", left > 0 && left < 3 * DAY);
      cd.classList.toggle("soon", left >= 3 * DAY && left < 14 * DAY);
      cd.classList.toggle("done", left <= 0);
    }
  }

  // ---------------------------------------------------------------- meta line
  const gen = Date.parse(DATA.generated);
  const counts = { verified: 0, total: 0 };
  for (const c of confs) for (const d of c.dls) if (d.t > now() && d.status) { counts.total++; if (d.status === "verified") counts.verified++; }
  const age = now() - gen;
  $("#meta").innerHTML = `Data updated ${rel(-age).replace(" ago", "")} ago (${fmtLocal.format(gen)})` +
    (age > 7 * DAY ? ` <b style="color:var(--warn)">— stale, run the updater</b>` : "") +
    ` · ${counts.verified}/${counts.total} upcoming dates confirmed on official sites`;

  // ---------------------------------------------------------------- events
  $("#q").value = state.q;
  $("#showPast").setAttribute("aria-pressed", state.past);
  $("#topOnly").setAttribute("aria-pressed", state.top);
  $("#q").addEventListener("input", (e) => { state.q = e.target.value.trim(); syncUrl(); render(); });
  $("#showPast").addEventListener("click", (e) => { state.past = !state.past; e.currentTarget.setAttribute("aria-pressed", state.past); syncUrl(); render(); });
  $("#topOnly").addEventListener("click", (e) => { state.top = !state.top; e.currentTarget.setAttribute("aria-pressed", state.top); syncUrl(); render(); });
  $("#chips").addEventListener("click", (e) => {
    const b = e.target.closest(".chip");
    if (!b) return;
    const s = b.dataset.sub;
    if (!s) state.subs.clear();
    else state.subs.has(s) ? state.subs.delete(s) : state.subs.add(s);
    syncUrl(); render();
  });
  main.addEventListener("click", (e) => {
    const b = e.target.closest("[data-ics]");
    if (!b) return;
    const c = confs.find((x) => x.id === b.dataset.ics);
    const items = c.dls.filter((d) => d.t > now()).map((d) => ({ c, d }));
    download(`${c.id}-deadlines.ics`, icsEvents(items));
  });
  $("#icsAll").addEventListener("click", () => {
    const items = visibleUpcoming.flatMap(({ c }) => c.dls.filter((d) => d.t > now()).map((d) => ({ c, d })));
    if (items.length) download("ai-deadlines.ics", icsEvents(items));
  });
  document.addEventListener("keydown", (e) => {
    if (e.key === "/" && document.activeElement !== $("#q")) { e.preventDefault(); $("#q").focus(); }
    if (e.key === "Escape" && document.activeElement === $("#q")) { $("#q").value = ""; state.q = ""; syncUrl(); render(); }
  });

  render();
  setInterval(tick, 1000);
  setInterval(render, 5 * 60 * 1000); // re-bucket when a deadline passes
})();
