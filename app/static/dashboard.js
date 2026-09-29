const PHASES = ["create", "implement", "test", "security", "architect", "document"];
const COUNT_KEYS = ["QUEUED", "RUNNING", "IDLE", "AWAITING_INPUT", "FAILED"];

function escapeHtml(value) {
  return String(value ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;");
}

function selectedRepo() {
  return new URLSearchParams(window.location.search).get("repo") || sessionStorage.getItem("devbot.repo") || "";
}

function shortRepo(repository) {
  const parts = String(repository || "").split("/");
  return parts[parts.length - 1] || repository;
}

function repoAlias(job) {
  return job.repo_alias || shortRepo(job.repository) || job.repository || "";
}

function repoMatches(job, selected) {
  if (!selected) return true;
  const key = selected.toLowerCase();
  const repository = String(job.repository || "");
  return [repository, repoAlias(job), shortRepo(repository)].some((value) => String(value).toLowerCase() === key);
}

function uniqueRepoOptions(jobs, listed) {
  const options = new Map();
  for (const repo of listed || []) {
    if (!repo || !(repo.alias || repo.repository)) continue;
    const alias = repo.alias || shortRepo(repo.repository);
    options.set((repo.repository || alias).toLowerCase(), { repository: repo.repository || "", alias });
  }
  for (const job of jobs) {
    if (!job.repository) continue;
    const key = job.repository.toLowerCase();
    if (options.has(key)) continue;
    options.set(key, { repository: job.repository, alias: repoAlias(job) });
  }
  return [...options.values()].sort((left, right) => left.alias.localeCompare(right.alias));
}

function visibleJobs(jobs) {
  const repo = selectedRepo();
  return repo ? jobs.filter((job) => repoMatches(job, repo)) : jobs;
}

function countsFor(jobs) {
  const counts = Object.fromEntries(COUNT_KEYS.map((key) => [key, 0]));
  for (const job of jobs) {
    if (job.status in counts) counts[job.status] += 1;
  }
  return counts;
}

function renderCard(job) {
  const results = job.phase_results || {};
  const dots = Object.entries(results)
    .map(
      ([name, entry]) =>
        `<i class="dot" data-phase="${escapeHtml(name)}" data-status="${escapeHtml((entry || {}).status || "pending")}"></i>`,
    )
    .join("");
  const status = escapeHtml(job.status || "");
  const stamp = escapeHtml((job.status || "").replaceAll("_", " ").toLowerCase());
  const owner = job.credential_user
    ? `<span class="owner">${escapeHtml(job.credential_user)}</span>`
    : "";
  return `<a class="ticket" href="/tickets/${escapeHtml(job.display_id)}" data-id="${escapeHtml(job.display_id)}" data-status="${status}">
    <span class="stub">${escapeHtml(job.issue_ref)}</span>
    <span class="ticket-main">
      <span class="ticket-title">${escapeHtml(job.issue_title || job.request)}</span>
      <span class="ticket-meta"><span>${escapeHtml(repoAlias(job))}</span>${owner}</span>
      <span class="dots" aria-hidden="true">${dots}</span>
      <span class="ticket-next">${escapeHtml(job.next_action)}</span>
    </span>
    <span class="stamp stamp-${escapeHtml((job.status || "").toLowerCase())}">${stamp}</span>
  </a>`;
}

function syncRepoFilter(repos) {
  const select = document.getElementById("repo-filter");
  if (!select) return;
  const selected = selectedRepo();
  const options = ['<option value="">All</option>'].concat(
    repos.map((repo) => {
      const alias = repo.alias || shortRepo(repo.repository);
      const active = selected && [alias, repo.repository].some((value) => String(value).toLowerCase() === selected.toLowerCase());
      return `<option value="${escapeHtml(alias)}"${active ? " selected" : ""}>${escapeHtml(alias)}</option>`;
    }),
  );
  if (selected && !repos.some((repo) => [repo.alias, repo.repository].some((value) => String(value || "").toLowerCase() === selected.toLowerCase()))) {
    options.push(`<option value="${escapeHtml(selected)}" selected>${escapeHtml(shortRepo(selected))}</option>`);
  }
  select.innerHTML = options.join("");
}

function ensureRack(desk) {
  let rack = desk.querySelector(".rack");
  const empty = desk.querySelector(".empty");
  if (!rack) {
    empty?.remove();
    rack = document.createElement("section");
    rack.className = "rack";
    rack.setAttribute("aria-label", "Pipeline phases");
    rack.innerHTML = PHASES.map(
      (phase) => `<section class="bin" data-phase="${phase}">
        <header class="bin-head"><h2>${phase}</h2><span class="bin-count">0</span></header>
        <div class="bin-cards"></div>
      </section>`,
    ).join("");
    desk.appendChild(rack);
  }
  return rack;
}

function showEmpty(desk, repo) {
  desk.querySelector(".rack")?.remove();
  let empty = desk.querySelector(".empty");
  if (!empty) {
    empty = document.createElement("section");
    empty.className = "empty";
    empty.setAttribute("aria-label", "Empty pipeline");
    desk.appendChild(empty);
  }
  const scoped = repo
    ? `No tickets for <code>${escapeHtml(repo)}</code> yet.`
    : "Start with <code>@devbot create web …</code> in Slack. Tickets will sort into the phase that needs you next.";
  empty.innerHTML = `<h2>No pipeline jobs yet</h2><p>${scoped}</p>`;
}

function renderBoard(data) {
  const desk = document.querySelector(".desk");
  const live = document.getElementById("live-state");
  if (!desk) return;
  const allJobs = data.jobs || [];
  syncRepoFilter(uniqueRepoOptions(allJobs, data.repos));
  const jobs = visibleJobs(allJobs);
  const counts = countsFor(jobs);
  for (const key of COUNT_KEYS) {
    const node = document.querySelector(`[data-status="${key}"] .count-n`);
    if (node) node.textContent = String(counts[key] || 0);
  }
  if (!jobs.length) {
    showEmpty(desk, selectedRepo());
    if (live) {
      live.dataset.state = "live";
      live.textContent = "live";
    }
    return;
  }
  const rack = ensureRack(desk);
  desk.querySelector(".empty")?.remove();
  const grouped = Object.fromEntries(PHASES.map((phase) => [phase, []]));
  for (const job of jobs) {
    const phase = grouped[job.board_phase] ? job.board_phase : "create";
    grouped[phase].push(job);
  }
  for (const phase of PHASES) {
    const bin = rack.querySelector(`.bin[data-phase="${phase}"]`);
    if (!bin) continue;
    const count = bin.querySelector(".bin-count");
    const cards = bin.querySelector(".bin-cards");
    if (count) count.textContent = String(grouped[phase].length);
    if (cards) cards.innerHTML = grouped[phase].map(renderCard).join("");
  }
  if (live) {
    live.dataset.state = "live";
    live.textContent = "live";
  }
}

async function poll() {
  const live = document.getElementById("live-state");
  try {
    const response = await fetch("/jobs");
    if (!response.ok) throw new Error("poll failed");
    renderBoard(await response.json());
  } catch {
    if (live) {
      live.dataset.state = "stale";
      live.textContent = "offline";
    }
  }
}

function persistRepo(value) {
  const url = new URL(window.location.href);
  if (value) {
    url.searchParams.set("repo", value);
    sessionStorage.setItem("devbot.repo", value);
  } else {
    url.searchParams.delete("repo");
    sessionStorage.removeItem("devbot.repo");
  }
  history.replaceState({}, "", url);
}

function bindRepoFilter() {
  const select = document.getElementById("repo-filter");
  if (!select) return;
  select.addEventListener("change", () => {
    persistRepo(select.value);
    poll();
  });
}

function initRepoFilter() {
  const urlRepo = new URLSearchParams(window.location.search).get("repo") || "";
  const stored = sessionStorage.getItem("devbot.repo") || "";
  if (urlRepo) sessionStorage.setItem("devbot.repo", urlRepo);
  else if (stored) persistRepo(stored);
  bindRepoFilter();
}

if (document.querySelector(".rack, .empty")) {
  initRepoFilter();
  poll();
  window.setInterval(poll, 4000);
}
