/* ── Theme ───────────────────────────────────────────────────────────────── */
(function () {
  const saved = localStorage.getItem('gnps-theme') || 'dark';
  document.documentElement.setAttribute('data-theme', saved);
  document.addEventListener('DOMContentLoaded', function () {
    const btn = document.getElementById('theme-toggle');
    if (btn) btn.textContent = saved === 'dark' ? 'LIGHT' : 'DARK';
  });
})();

function toggleTheme() {
  const root    = document.documentElement;
  const current = root.getAttribute('data-theme') || 'dark';
  const next    = current === 'dark' ? 'light' : 'dark';
  root.setAttribute('data-theme', next);
  localStorage.setItem('gnps-theme', next);
  const btn = document.getElementById('theme-toggle');
  if (btn) btn.textContent = next === 'dark' ? 'LIGHT' : 'DARK';
  if (typeof window._onThemeChange === 'function') window._onThemeChange(next);
}

/* ── File-drop zones ─────────────────────────────────────────────────────── */
function _initDropZone(zone) {
  const input       = zone.querySelector('input[type=file]');
  const placeholder = zone.querySelector('.file-placeholder');
  const fileList    = zone.querySelector('.file-list');
  if (!input) return;

  zone.addEventListener('click', e => {
    if (e.target === input) return;
    input.click();
  });

  zone.addEventListener('dragover', e => {
    e.preventDefault();
    zone.classList.add('drag-over');
  });

  zone.addEventListener('dragleave', e => {
    if (!zone.contains(e.relatedTarget)) zone.classList.remove('drag-over');
  });

  zone.addEventListener('drop', e => {
    e.preventDefault();
    zone.classList.remove('drag-over');
    const dt = new DataTransfer();
    Array.from(e.dataTransfer.files).forEach(f => dt.items.add(f));
    input.files = dt.files;
    input.dispatchEvent(new Event('change'));
  });

  input.addEventListener('change', () => {
    const files = Array.from(input.files);
    if (placeholder) placeholder.style.display = files.length ? 'none' : 'block';
    if (fileList) {
      fileList.style.display = files.length ? 'block' : 'none';
      fileList.innerHTML     = files.map(f => `<div>${f.name}</div>`).join('');
    }
  });
}

document.addEventListener('DOMContentLoaded', () => {
  document.querySelectorAll('.file-drop').forEach(_initDropZone);
});

/* ── MCN form helpers (submit_molecular_networking, submit_fbmn) ─────────── */

/**
 * Clamp an <input type=number> to its own min/max attributes.
 */
function clampInput(input) {
  const min = input.min !== '' ? parseFloat(input.min) : -Infinity;
  const max = input.max !== '' ? parseFloat(input.max) : Infinity;
  const val = parseFloat(input.value);
  if (isNaN(val)) return;
  if (val < min) input.value = min;
  if (val > max) input.value = max;
}

/**
 * Wire live clamping to an input while MCN is enabled.
 * mcnSelectEl — the <select name="MOLECULAR_COMMUNITY_NETWORKING"> element
 */
function attachMCNClamping(input, mcnSelectEl) {
  input.addEventListener('input', () => {
    if (mcnSelectEl.value === '1') clampInput(input);
  });
}

/**
 * Apply or revert MCN-specific constraints on the networking parameters.
 *
 * scoreInput    — <input name="SCORE_THRESHOLD">
 * topkInput     — <input name="TOPK">
 * maxCompInput  — <input name="MAX_COMPONENT_SIZE">
 * mcnSelectEl   — <select name="MOLECULAR_COMMUNITY_NETWORKING">
 * hintEl        — optional element to show/hide as MCN hint
 * sigmoidParams — optional element to show/hide sigmoid params (FBMN only)
 */
function updateMCNSettings(scoreInput, topkInput, maxCompInput, mcnSelectEl, hintEl, sigmoidParams) {
  const enabled = mcnSelectEl.value === '1';

  if (hintEl)        hintEl.style.display        = enabled ? 'block' : 'none';
  if (sigmoidParams) sigmoidParams.style.display  = enabled ? 'grid'  : 'none';

  if (enabled) {
    scoreInput.value    = 0.1;
    scoreInput.min      = 0;
    scoreInput.max      = 0.1;
    scoreInput.step     = 0.01;

    topkInput.value     = 100;
    topkInput.min       = 100;
    topkInput.max       = 10000;

    maxCompInput.value           = 0;
    maxCompInput.disabled        = true;
    maxCompInput.style.opacity   = 0.5;

    clampInput(scoreInput);
    clampInput(topkInput);
  } else {
    scoreInput.value    = 0.7;
    scoreInput.min      = 0;
    scoreInput.max      = 1;

    topkInput.value     = 10;
    topkInput.min       = 1;
    topkInput.max       = '';

    maxCompInput.disabled        = false;
    maxCompInput.style.opacity   = 1;
    maxCompInput.value           = 100;
  }
}

/**
 * Enforce MCN constraints at submit time (call before FormData is read).
 */
function enforceMCNOnSubmit(scoreInput, topkInput, maxCompInput, mcnSelectEl) {
  if (mcnSelectEl.value === '1') {
    clampInput(scoreInput);
    clampInput(topkInput);
    maxCompInput.value = 0;
  }
}

/* ── Duration formatting ─────────────────────────────────────────────────── */
function formatDuration(startIso, endIso) {
  if (!startIso) return '—';
  const start    = new Date(startIso);
  const end      = endIso ? new Date(endIso) : new Date();
  const totalSec = Math.floor((end - start) / 1000);
  if (totalSec < 0) return '—';
  const h = Math.floor(totalSec / 3600);
  const m = Math.floor((totalSec % 3600) / 60);
  const s = totalSec % 60;
  if (h > 0) return `${h}h ${String(m).padStart(2, '0')}m`;
  if (m > 0) return `${m}m ${String(s).padStart(2, '0')}s`;
  return `${s}s`;
}

/** Format seconds into a short human string (1.2s, 3.4m, 1.1h). */
function fmtDur(s) {
  if (s >= 3600) return (s / 3600).toFixed(1) + 'h';
  if (s >= 60)   return (s / 60).toFixed(1)   + 'm';
  return s.toFixed(1) + 's';
}

/* ── CSS variable helper ─────────────────────────────────────────────────── */
function getCssVar(v) {
  return getComputedStyle(document.documentElement).getPropertyValue(v).trim();
}

/* ── Byte formatting ─────────────────────────────────────────────────────── */
function formatBytes(bytes) {
  if (bytes >= 1e12) return (bytes / 1e12).toFixed(1) + ' TB';
  if (bytes >= 1e9)  return (bytes / 1e9).toFixed(1)  + ' GB';
  if (bytes >= 1e6)  return (bytes / 1e6).toFixed(1)  + ' MB';
  return (bytes / 1e3).toFixed(1) + ' KB';
}

/* ── Step-track mapping (job.html + settings.html) ───────────────────────── */
const STEP_TRACKS = [
  { match: /metadata_merge|input_validation/i,                                                 cssVar: '--t-metadata',   label: 'Metadata'        },
  { match: /reformat_quant|filter_spectra/i,                                                   cssVar: '--t-spectra',    label: 'Spectra'         },
  { match: /prep_networking_params|networking_pairs|merge_pairs|filter_edges/i,                cssVar: '--t-networking', label: 'Networking'      },
  { match: /library_search_prep|library_search|merge_libsearch|libsearch_db_annot/i,           cssVar: '--t-library',    label: 'Library'         },
  { match: /clusterinfosummary_for_featurenetworks|network_edges_display|enrich_clusterinfo/i, cssVar: '--t-cluster',    label: 'Cluster & Edges' },
  { match: /graphml|convert_graph|mcn/i,                                                       cssVar: '--t-graphml',    label: 'GraphML / MCN'   },
];

function stepTrack(stepName) {
  for (const t of STEP_TRACKS) {
    if (t.match.test(stepName)) return t;
  }
  return { cssVar: '--t-other', label: 'Other' };
}

/* ── Timing bar renderer ─────────────────────────────────────────────────── */
/**
 * Render a stacked timing bar + legend.
 *
 * timings   — array of { step, duration_s, status }
 * barEl     — the bar container element
 * legendEl  — the legend container element
 * totalEl   — element to write "Total: Xs" into (nullable)
 * tipEl     — tooltip element (nullable — creates one if absent)
 */
function renderTimingBar(timings, barEl, legendEl, totalEl, tipEl) {
  if (!barEl || !timings.length) return;

  const total = timings.reduce((s, t) => s + t.duration_s, 0);
  if (totalEl) totalEl.textContent = `Total: ${fmtDur(total)}`;

  const seenLabels = new Set();
  const legendItems = [];

  barEl.innerHTML = '';

  timings.forEach(t => {
    const track = stepTrack(t.step);
    const pct   = total > 0 ? (t.duration_s / total) * 100 : 0;
    const color = getCssVar(track.cssVar);

    const seg = document.createElement('div');
    seg.style.cssText = [
      `width:${pct}%`,
      `background:${color}`,
      `min-width:${pct > 0 ? '3px' : '0'}`,
      'cursor:pointer',
      'transition:filter 0.15s',
      t.status !== 'ok'
        ? 'background-image:repeating-linear-gradient(45deg,rgba(0,0,0,0.15) 0px,rgba(0,0,0,0.15) 4px,transparent 4px,transparent 8px)'
        : '',
    ].filter(Boolean).join(';');

    seg.addEventListener('mouseenter', e => {
      seg.style.filter = 'brightness(1.25)';
      if (tipEl) {
        tipEl.style.display = 'block';
        tipEl.innerHTML = `
          <div style="font-weight:600;margin-bottom:4px;word-break:break-all;overflow-wrap:break-word;line-height:1.4;">${t.step}</div>
          <div style="color:var(--muted);">${track.label}</div>
          <div style="margin-top:6px;">${fmtDur(t.duration_s)}</div>
          <div style="color:${t.status === 'ok' ? 'var(--success)' : 'var(--danger)'};font-size:10px;margin-top:2px;">${t.status.toUpperCase()}</div>
        `;
      }
    });
    seg.addEventListener('mousemove', e => {
      if (tipEl) {
        tipEl.style.left = (e.clientX + 12) + 'px';
        tipEl.style.top  = (e.clientY - 8)  + 'px';
      }
    });
    seg.addEventListener('mouseleave', () => {
      seg.style.filter = '';
      if (tipEl) tipEl.style.display = 'none';
    });

    barEl.appendChild(seg);

    if (!seenLabels.has(track.label)) {
      seenLabels.add(track.label);
      legendItems.push({ label: track.label, cssVar: track.cssVar });
    }
  });

  if (legendEl) {
    legendEl.innerHTML = legendItems.map(item => `
      <span style="display:flex;align-items:center;gap:5px;">
        <span style="width:10px;height:10px;border-radius:2px;background:${getCssVar(item.cssVar)};flex-shrink:0;"></span>
        ${item.label}
      </span>
    `).join('');
  }
}

/* ── Generic form submit handler ─────────────────────────────────────────── */
/**
 * Wire up a standard workflow submission form.
 *
 * formId      — id of the <form>
 * submitBtnId — id of the submit button
 * statusId    — id of the status <span>
 * apiEndpoint — POST URL, e.g. '/api/submit/fbmn'
 * preSubmit   — optional function() called before FormData is built
 */
function wireSubmitForm(formId, submitBtnId, statusId, apiEndpoint, preSubmit) {
  const form   = document.getElementById(formId);
  const btn    = document.getElementById(submitBtnId);
  const status = document.getElementById(statusId);
  if (!form) return;

  form.addEventListener('submit', async e => {
    e.preventDefault();
    btn.disabled    = true;
    btn.textContent = 'Submitting…';
    if (status) status.textContent = '';

    if (typeof preSubmit === 'function') preSubmit();

    const data = new FormData(form);
    try {
      const r = await fetch(apiEndpoint, { method: 'POST', body: data });
      const d = await r.json();
      if (r.ok) {
        window.location.href = `/job/${d.job_id}`;
      } else {
        if (status) status.textContent = d.detail || 'Submission failed';
        btn.disabled    = false;
        btn.textContent = 'Submit Job';
      }
    } catch (err) {
      if (status) status.textContent = 'Network error: ' + err.message;
      btn.disabled    = false;
      btn.textContent = 'Submit Job';
    }
  });
}

/* ── ISO timestamp → locale string ──────────────────────────────────────── */
function localiseTimestamps(selector) {
  document.querySelectorAll(selector).forEach(el => {
    const iso = el.dataset.iso || el.getAttribute('data-iso');
    if (!iso) return;
    try {
      el.textContent = new Date(iso).toLocaleString(undefined, {
        year: 'numeric', month: 'short', day: 'numeric',
        hour: '2-digit', minute: '2-digit',
      });
    } catch (e) {}
  });
}

/* ═══════════════════════════════════════════════════════════════════════════
   ── Home page ──────────────────────────────────────────────────────────── */

function renderRecentJobs(jobs) {
  const container = document.getElementById('recent-jobs-container');
  const recent = jobs.slice(0, 5);
  if (!recent.length) {
    container.innerHTML = `<div class="card placeholder-card">No jobs yet.</div>`;
    return;
  }
  const rows = recent.map(job => {
    const dur = formatDuration(job.started_at, job.finished_at) || '—';
    const wf  = job.workflow.replace(/_/g, ' ').replace(/\b\w/g, c => c.toUpperCase());
    let createdStr;
    try {
      createdStr = new Date(job.created_at).toLocaleString(undefined, {
        year: 'numeric', month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit'
      });
    } catch (e) { createdStr = job.created_at.slice(0, 16); }
    return `<tr>
      <td>${job.params.JOB_NAME || '—'}</td>
      <td class="mono">${job.id}</td>
      <td>${wf}</td>
      <td><span class="badge badge-${job.status}">${job.status.toUpperCase()}</span></td>
      <td class="muted">${createdStr}</td>
      <td class="muted">${dur}</td>
      <td><a href="/job/${job.id}" class="btn btn-ghost btn-sm">View</a></td>
    </tr>`;
  }).join('');
  container.innerHTML = `<div class="card card-flush">
    <table><thead><tr>
      <th>Job Name</th><th>Job ID</th><th>Workflow</th><th>Status</th><th>Created</th><th>Duration</th><th></th>
    </tr></thead><tbody>${rows}</tbody></table></div>`;
}

document.addEventListener('DOMContentLoaded', function () {
  if (!document.getElementById('recent-jobs-container')) return;
  fetch('/api/jobs')
    .then(r => r.json())
    .then(jobs => {
      jobs.sort((a, b) => b.created_at.localeCompare(a.created_at));
      renderRecentJobs(jobs);
    })
    .catch(() => {
      document.getElementById('recent-jobs-container').innerHTML =
        `<div class="card placeholder-card">Could not load jobs.</div>`;
    });
});

/* ═══════════════════════════════════════════════════════════════════════════
   ── Jobs list (index) ──────────────────────────────────────────────────── */

function filterJobs() {
  const q  = document.getElementById('jobSearch').value.toLowerCase();
  const wf = document.getElementById('workflowFilter').value;
  document.querySelectorAll('#job-table-body tr').forEach(row => {
    const name = row.cells[0].textContent.toLowerCase();
    const id   = row.cells[1].textContent.toLowerCase();
    const matchSearch   = name.includes(q) || id.includes(q);
    const matchWorkflow = wf === 'all' || row.dataset.workflow === wf;
    row.style.display = (matchSearch && matchWorkflow) ? '' : 'none';
  });
}

function updateDurations() {
  document.querySelectorAll('tr[data-job-id]').forEach(row => {
    const cell = row.querySelector('.duration-cell');
    if (!cell || !row.dataset.started) return;
    const status = row.dataset.status;
    if (status === 'running' || status === 'queued') {
      cell.textContent = formatDuration(row.dataset.started, null);
    } else if (row.dataset.finished) {
      cell.textContent = formatDuration(row.dataset.started, row.dataset.finished);
    }
  });
}

function updateJobStatuses(pollTimer, durationTimer) {
  if (document.hidden) return;
  fetch('/api/jobs')
    .then(res => res.json())
    .then(jobs => {
      let activeFound = false;
      jobs.forEach(job => {
        const row = document.querySelector(`tr[data-job-id="${job.id}"]`);
        if (!row) return;
        if (job.status === 'running' || job.status === 'queued') activeFound = true;
        if (row.dataset.status === job.status) return;
        row.dataset.status   = job.status;
        row.dataset.started  = job.started_at  || '';
        row.dataset.finished = job.finished_at || '';
        const badge = row.querySelector('.badge');
        badge.className   = `badge badge-${job.status}`;
        badge.textContent = job.status.toUpperCase();
      });
      if (!activeFound) {
        clearInterval(pollTimer);
        clearInterval(durationTimer);
      }
    })
    .catch(err => console.error('Polling error:', err));
}

document.addEventListener('DOMContentLoaded', function () {
  if (!document.getElementById('job-table-body')) return;
  updateDurations();
  localiseTimestamps('.ts-cell[data-iso]');

  const hasActive = document.querySelectorAll('tr[data-status="running"], tr[data-status="queued"]').length > 0;
  if (hasActive) {
    const durationTimer = setInterval(updateDurations, 1000);
    const pollTimer     = setInterval(() => updateJobStatuses(pollTimer, durationTimer), 5000);
  }
});

/* ═══════════════════════════════════════════════════════════════════════════
   ── Job detail ─────────────────────────────────────────────────────────── */

document.addEventListener('DOMContentLoaded', function () {
  if (!window.GNPS_JOB) return;

  const jobId       = window.GNPS_JOB.id;
  const logEl       = document.getElementById('log-output');
  const badgeEl     = document.getElementById('status-badge');
  const tsEl        = document.getElementById('log-ts');
  const cancelZone  = document.getElementById('cancel-zone');
  const restartZone = document.getElementById('restart-zone');
  let lastFinishedAt = window.GNPS_JOB.finishedAt;
  let _lastTimings   = [];
  let _logTsInterval = null;

  function _renderJobTimingBar(timings) {
    renderTimingBar(
      timings,
      document.getElementById('timing-bar'),
      document.getElementById('timing-legend'),
      document.getElementById('timing-total'),
      document.getElementById('timing-tooltip')
    );
  }

  window._onThemeChange = () => { if (_lastTimings.length) _renderJobTimingBar(_lastTimings); };

  function updateLogTs(status, finishedAt) {
    if (status === 'running' || status === 'queued') {
      tsEl.textContent = new Date().toLocaleTimeString();
    } else if (finishedAt) {
      tsEl.textContent = new Date(finishedAt).toLocaleTimeString();
    }
  }

  async function fetchLog() {
    const r = await fetch(`/api/job/${jobId}/log`);
    const d = await r.json();
    const atBottom = logEl.scrollHeight - logEl.scrollTop <= logEl.clientHeight + 40;
    logEl.textContent = d.log || '(no output yet)';
    if (atBottom) logEl.scrollTop = logEl.scrollHeight;
  }

  async function fetchStatus() {
    const r = await fetch(`/api/job/${jobId}`);
    const d = await r.json();
    badgeEl.textContent = d.status.toUpperCase();
    badgeEl.className   = `badge badge-${d.status}`;
    const isActive = d.status === 'running' || d.status === 'queued';
    cancelZone.style.display  = isActive ? 'block' : 'none';
    restartZone.style.display = isActive ? 'none'  : 'block';
    lastFinishedAt = d.finished_at || '';
    updateLogTs(d.status, lastFinishedAt);
    return isActive;
  }

  window.cancelJob = async function () {
    if (!confirm('Are you sure you want to cancel this job? This will stop all current processing.')) return;
    try {
      const r = await fetch(`/api/job/${jobId}/cancel`, { method: 'POST', headers: { 'Content-Type': 'application/json' } });
      if (r.ok) window.location.reload();
      else { const e = await r.json(); alert('Error cancelling: ' + (e.detail || 'Unknown')); }
    } catch (e) { alert('Network error: ' + e.message); }
  };

  window.restartJob = async function () {
    if (!confirm('Restart this job? This will clear logs and output files.')) return;
    try {
      const r = await fetch(`/api/job/${jobId}/restart`, { method: 'POST' });
      if (r.ok) window.location.reload();
      else { const e = await r.json(); alert('Error restarting: ' + (e.detail || 'Unknown')); }
    } catch (e) { alert('Network error: ' + e.message); }
  };

  async function fetchFiles() {
    const r     = await fetch(`/api/job/${jobId}/files`);
    const files = await r.json();
    const container = document.getElementById('output-files-container');
    if (!container || !files.length) return;
    const dlBtn = document.getElementById('download-all-btn');
    if (dlBtn) dlBtn.classList.add('ready');
    let html = '<table><tbody>';
    files.forEach(f => {
      const kb = (f.size / 1024).toFixed(1);
      html += `<tr>
        <td>${f.name}</td>
        <td class="muted file-size-cell">${kb} KB</td>
        <td class="file-dl-cell">
          <a href="/api/job/${jobId}/download/${f.path}" class="btn btn-ghost btn-sm">↓</a>
        </td>
      </tr>`;
    });
    html += '</tbody></table>';
    container.innerHTML = html;
  }

  async function fetchTimings() {
    try {
      const r       = await fetch(`/api/job/${jobId}/timings`);
      const timings = await r.json();
      if (timings.length) { _lastTimings = timings; _renderJobTimingBar(timings); }
    } catch (e) {}
  }

  async function poll() {
    await fetchLog();
    const active = await fetchStatus();
    await fetchTimings();
    if (active) {
      if (!_logTsInterval) {
        _logTsInterval = setInterval(() => updateLogTs('running', ''), 1000);
      }
      setTimeout(poll, 2500);
    } else {
      if (_logTsInterval) { clearInterval(_logTsInterval); _logTsInterval = null; }
      await fetchFiles();
    }
  }

  if (document.querySelector('#output-files-container table')) {
    const btn = document.getElementById('download-all-btn');
    if (btn) btn.classList.add('ready');
  }

  localiseTimestamps('#job-created-ts');
  updateLogTs(window.GNPS_JOB.status, window.GNPS_JOB.finishedAt);
  poll();
});

/* ═══════════════════════════════════════════════════════════════════════════
   ── Settings ───────────────────────────────────────────────────────────── */

function switchTab(name) {
  document.querySelectorAll('.settings-tab').forEach(t => t.classList.remove('active'));
  document.querySelectorAll('.settings-pane').forEach(p => p.classList.remove('active'));
  document.getElementById('tab-'  + name).classList.add('active');
  document.getElementById('pane-' + name).classList.add('active');
  if (name === 'performance' && !window._lastAggData) loadAggregate();
  if (name === 'storage') refreshStorageDisplay();
}

async function uploadLibraries() {
  const fileInput    = document.getElementById('lib-file-input');
  const uploadBtn    = document.getElementById('upload-btn');
  const uploadStatus = document.getElementById('upload-status');
  const files = Array.from(fileInput.files);
  if (!files.length) return;

  uploadBtn.disabled    = true;
  uploadBtn.textContent = 'Uploading…';
  uploadStatus.textContent = '';

  const progressWrap = document.getElementById('upload-progress-wrap');
  const progressBar  = document.getElementById('upload-progress-bar');
  const progressLbl  = document.getElementById('upload-progress-label');
  const progressPct  = document.getElementById('upload-progress-pct');

  progressWrap.style.display = 'block';
  progressBar.style.width    = '0%';
  progressPct.textContent    = '0%';

  const saved = [], errors = [];
  for (let i = 0; i < files.length; i++) {
    const f = files[i];
    progressLbl.textContent = `Uploading ${f.name} (${i + 1} of ${files.length})…`;
    progressBar.style.width = Math.round((i / files.length) * 100) + '%';
    progressPct.textContent = Math.round((i / files.length) * 100) + '%';
    const data = new FormData();
    data.append('files', f);
    try {
      const r = await fetch('/api/libraries/upload', { method: 'POST', body: data });
      const d = await r.json();
      if (r.ok && d.saved) saved.push(...d.saved);
      else errors.push(f.name);
    } catch (e) { errors.push(f.name); }
  }

  progressBar.style.width = '100%';
  progressPct.textContent = '100%';
  await new Promise(res => setTimeout(res, 400));
  progressWrap.style.display = 'none';

  if (errors.length) {
    uploadStatus.style.color   = 'var(--danger)';
    uploadStatus.textContent   = `Failed: ${errors.join(', ')}` + (saved.length ? ` · Saved: ${saved.join(', ')}` : '');
  } else {
    uploadStatus.style.color   = 'var(--success)';
    uploadStatus.textContent   = `✓ Saved ${saved.length} file${saved.length !== 1 ? 's' : ''}`;
  }

  uploadBtn.textContent = 'Upload';
  uploadBtn.disabled    = true;
  document.getElementById('lib-file-list').innerHTML = '';
  fileInput.value = '';
  await refreshLibraries();
}

function onLibCheckChange() {
  const all     = document.querySelectorAll('.lib-row-check');
  const checked = document.querySelectorAll('.lib-row-check:checked');
  const bar     = document.getElementById('libs-selection-bar');
  const cnt     = document.getElementById('libs-sel-count');
  const selAll  = document.getElementById('lib-select-all');
  if (cnt)    cnt.textContent = `${checked.length} selected`;
  bar.classList.toggle('visible', checked.length > 0);
  if (selAll) {
    selAll.indeterminate = checked.length > 0 && checked.length < all.length;
    selAll.checked       = all.length > 0 && checked.length === all.length;
  }
}

function toggleSelectAll(cb) {
  document.querySelectorAll('.lib-row-check').forEach(c => { c.checked = cb.checked; });
  onLibCheckChange();
}

function clearLibrarySelection() {
  document.querySelectorAll('.lib-row-check').forEach(c => { c.checked = false; });
  const selAll = document.getElementById('lib-select-all');
  if (selAll) { selAll.checked = false; selAll.indeterminate = false; }
  onLibCheckChange();
}

async function deleteSelectedLibraries() {
  const checked = Array.from(document.querySelectorAll('.lib-row-check:checked'));
  if (!checked.length) return;
  const names  = checked.map(cb => cb.closest('tr').dataset.name).filter(Boolean);
  const plural = names.length === 1 ? 'that library' : `${names.length} libraries`;
  if (!confirm(`Are you sure? This will delete ${plural} causing library search to fail.`)) return;
  for (const name of names) {
    try { await fetch(`/api/libraries/${encodeURIComponent(name)}`, { method: 'DELETE' }); } catch (e) {}
  }
  await refreshLibraries();
}

async function deleteLibrary(filename) {
  if (!confirm('Are you sure? This will delete that library causing library search to fail.')) return;
  try {
    const r = await fetch(`/api/libraries/${encodeURIComponent(filename)}`, { method: 'DELETE' });
    if (r.ok) await refreshLibraries();
    else alert('Delete failed.');
  } catch (e) { alert('Network error: ' + e.message); }
}

async function deleteAllLibraries() {
  const rows = document.querySelectorAll('#libs-tbody tr');
  if (!rows.length) return;
  if (!confirm('Are you sure? This will delete all libraries causing library search to fail.')) return;
  const names = Array.from(rows).map(r => r.dataset.name).filter(Boolean);
  for (const name of names) {
    try { await fetch(`/api/libraries/${encodeURIComponent(name)}`, { method: 'DELETE' }); } catch (e) {}
  }
  await refreshLibraries();
}

function sortLibraries() {
  const mode  = document.getElementById('lib-sort').value;
  const tbody = document.getElementById('libs-tbody');
  if (!tbody) return;
  const rows = Array.from(tbody.querySelectorAll('tr'));
  rows.sort((a, b) => {
    const nameA = a.dataset.name || '', nameB = b.dataset.name || '';
    const sizeA = parseInt(a.dataset.size || '0'), sizeB = parseInt(b.dataset.size || '0');
    if (mode === 'name-asc')  return nameA.localeCompare(nameB);
    if (mode === 'name-desc') return nameB.localeCompare(nameA);
    if (mode === 'size-desc') return sizeB - sizeA;
    if (mode === 'size-asc')  return sizeA - sizeB;
    return 0;
  });
  rows.forEach(r => tbody.appendChild(r));
}

function filterLibraries() {
  const q = document.getElementById('lib-search').value.toLowerCase();
  document.querySelectorAll('#libs-table-wrap table tbody tr').forEach(row => {
    const name = row.cells[1] ? row.cells[1].textContent.toLowerCase() : '';
    row.style.display = name.includes(q) ? '' : 'none';
  });
}

async function refreshStorageDisplay() {
  try {
    const [rs, rl]  = await Promise.all([fetch('/api/storage'), fetch('/api/libraries')]);
    const [d, libs] = await Promise.all([rs.json(), rl.json()]);
    const libBytes   = libs.reduce((s, l) => s + (l.size_bytes || 0), 0);
    const totalBytes = (d.total_bytes || 0) + libBytes;
    const set = (id, v) => { const el = document.getElementById(id); if (el) el.textContent = v; };
    set('storage-job-count',  `${d.job_count} ${d.job_count === 1 ? 'job' : 'jobs'}`);
    set('storage-jobs-size',  formatBytes(d.total_bytes || 0));
    set('storage-lib-count',  `${libs.length} ${libs.length === 1 ? 'library' : 'libraries'}`);
    set('storage-libs-size',  formatBytes(libBytes));
    set('storage-total-size', formatBytes(totalBytes));
  } catch (e) {}
}

async function refreshLibraries() {
  const r    = await fetch('/api/libraries');
  const libs = await r.json();
  const wrap      = document.getElementById('libs-table-wrap');
  const countEl   = document.getElementById('lib-count');
  const delAllBtn = document.getElementById('delete-all-btn');
  if (countEl)   countEl.textContent = `${libs.length} file(s)`;
  if (delAllBtn) delAllBtn.disabled  = libs.length === 0;
  document.getElementById('libs-selection-bar').classList.remove('visible');

  if (!libs.length) {
    wrap.innerHTML = `<div id="libs-empty" class="placeholder-card">No libraries installed. Upload .mgf files above.</div>`;
    refreshStorageDisplay();
    return;
  }

  let html = `<table id="libs-table"><thead><tr>
    <th class="lib-col-check"><input type="checkbox" class="lib-checkbox" id="lib-select-all" onchange="toggleSelectAll(this)"></th>
    <th>Filename</th>
    <th class="lib-col-size">Size</th>
    <th class="lib-col-action"></th>
  </tr></thead><tbody id="libs-tbody">`;

  libs.forEach(lib => {
    html += `<tr data-name="${lib.name}" data-size="${lib.size_bytes}">
      <td class="lib-col-check"><input type="checkbox" class="lib-checkbox lib-row-check" onchange="onLibCheckChange()"></td>
      <td class="mono lib-name-cell">${lib.name}</td>
      <td class="muted lib-size-cell" data-bytes="${lib.size_bytes}">${formatBytes(lib.size_bytes)}</td>
      <td class="lib-col-action">
        <button class="btn btn-ghost btn-danger-ghost btn-sm" onclick="deleteLibrary('${lib.name}')">Delete</button>
      </td>
    </tr>`;
  });
  html += '</tbody></table>';
  wrap.innerHTML = html;
  filterLibraries();
  sortLibraries();
  refreshStorageDisplay();
}

async function loadAggregate() {
  const wf = document.getElementById('agg-workflow-filter').value;
  let data;
  try {
    const r = await fetch(`/api/timings/aggregate?workflow=${wf}`);
    data = await r.json();
  } catch (e) { return; }
  window._lastAggData = data;
  renderAggregate(data);
}

function renderAggregate(data) {
  const countEl = document.getElementById('agg-job-count');
  if (countEl) countEl.textContent = `${data.job_count} ${data.job_count === 1 ? 'job' : 'jobs'}`;

  const summaryEl = document.getElementById('agg-summary');
  if (summaryEl) {
    const mostExpensive = data.steps.length ? data.steps[0].step : '—';
    const mostFailed    = data.most_failed_step || '—';
    summaryEl.innerHTML = [
      { label: 'Avg total runtime', value: fmtDur(data.avg_total_s) },
      { label: 'Slowest step',      value: mostExpensive },
      { label: 'Most failed step',  value: mostFailed },
    ].map(c => `
      <div class="about-tile">
        <div class="perf-section-label">${c.label}</div>
        <div class="perf-summary-value">${c.value}</div>
      </div>
    `).join('');
  }

  if (!data.steps.length) {
    document.getElementById('agg-bar').innerHTML    = '';
    document.getElementById('agg-steps').innerHTML  = '<div class="muted perf-empty">No timing data yet. Run a job to see performance metrics.</div>';
    document.getElementById('agg-legend').innerHTML = '';
    return;
  }

  const aggTimings = data.steps.map(st => ({ step: st.step, duration_s: st.avg_s, status: 'ok' }));
  renderTimingBar(aggTimings, document.getElementById('agg-bar'), document.getElementById('agg-legend'), null, document.getElementById('agg-tooltip'));

  const maxAvg  = data.steps[0].avg_s;
  const stepsEl = document.getElementById('agg-steps');
  stepsEl.innerHTML = data.steps.map(st => {
    const track   = stepTrack(st.step);
    const color   = getCssVar(track.cssVar);
    const barPct  = maxAvg > 0 ? (st.avg_s / maxAvg) * 100 : 0;
    const failPct = st.count > 0 ? Math.round(st.failure_count / st.count * 100) : 0;
    return `<div class="perf-step-row">
      <div class="perf-step-name" title="${st.step}">${st.step}</div>
      <div class="perf-step-track"><div class="perf-step-fill" style="width:${barPct}%;background:${color};"></div></div>
      <div class="muted perf-step-dur">${fmtDur(st.avg_s)}</div>
      <div class="perf-step-fail ${failPct > 0 ? 'has-failures' : ''}">${failPct > 0 ? failPct + '% fail' : ''}</div>
    </div>`;
  }).join('');
}

document.addEventListener('DOMContentLoaded', function () {
  if (!document.getElementById('pane-storage')) return;

  window._lastAggData = null;

  const fileInput = document.getElementById('lib-file-input');
  if (fileInput) {
    fileInput.addEventListener('change', () => {
      document.getElementById('upload-btn').disabled = fileInput.files.length === 0;
    });
  }

  window._onThemeChange = () => { if (window._lastAggData) renderAggregate(window._lastAggData); };

  if (document.getElementById('pane-performance').classList.contains('active')) {
    loadAggregate();
  }

  refreshStorageDisplay();

  const hash = window.location.hash.replace('#', '');
  if (['storage', 'libraries', 'performance', 'about'].includes(hash)) switchTab(hash);
});