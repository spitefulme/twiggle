import { element } from '../u1/api.mjs';
import { candidateEditor } from './candidate.mjs';

const selectionHeader = 'Evidence-Selection';
const statuses = [['', 'All statuses'], ['started', 'Started'], ['partial', 'Partial'],
  ['submitted', 'Submitted'], ['invalid', 'Invalid'], ['excluded', 'Excluded'], ['included', 'Included']];
const denied = error => [401, 403, 404, 410].includes(error.problem?.status) ||
  ['COHORT_STALE', 'GOVERNANCE_UNAVAILABLE', 'SUPPRESSED', 'ANALYSIS_FENCED',
    'ANALYSIS_ERASURE_PENDING'].includes(error.problem?.code);
const id = value => typeof value === 'string' && /^[\w-]{1,100}$/.test(value);

export async function protectedEvidenceAvailable() {
  try {
    const response = await fetch('/health/ready', {
      cache: 'no-store', credentials: 'same-origin', signal: AbortSignal.timeout(2500),
    });
    return response.ok && (await response.json()).u4GuardedSyncWorker === true;
  } catch { return false; }
}

async function exchange(path, { method = 'GET', data, key, revision, selector } = {}) {
  const csrf = document.cookie.split('; ').find(part => part.startsWith('csrftoken='))?.slice(10) ??
    document.querySelector('[name=csrfmiddlewaretoken]')?.value ?? '';
  const headers = { Accept: 'application/json' };
  if (method !== 'GET') Object.assign(headers, {
    'Content-Type': 'application/json', 'X-CSRFToken': csrf, 'Idempotency-Key': key,
  });
  if (revision !== undefined) headers['If-Match'] = String(revision);
  if (selector) headers[selectionHeader] = selector;
  const response = await fetch('/api/v1' + path, {
    method, headers, credentials: 'same-origin', cache: 'no-store',
    body: data === undefined ? undefined : JSON.stringify(data), signal: AbortSignal.timeout(10000),
  });
  const result = await response.json();
  if (!response.ok) throw Object.assign(new Error(result.title ?? 'Request failed'), { problem: result });
  return { result, selector: response.headers.get(selectionHeader) };
}

export function evidencePanel({ studyId, versionId, jobId, cohortId, isCurrent, reauthorize, filterHost, draftHost, overview }) {
  if (!document.getElementById('u4-evidence-style')) document.head.append(element('link', '', {
    id: 'u4-evidence-style', rel: 'stylesheet', href: '/static/u4/evidence.css',
  }));
  const root = element('section', '', {
    class: filterHost ? 'u4-evidence u4-evidence--card' : 'u4-evidence', 'aria-label': 'Evidence delivery',
  });
  const filter = element('select', '', { 'aria-label': 'Evidence status' });
  for (const [value, label] of statuses) filter.append(element('option', label, { value }));
  const field = element('label', 'Status'); field.append(filter);
  const cohort = element('p', 'Published cohort · ' + cohortId, { class: 'u4-evidence-context' });
  const count = element('p', 'Choose filters to inspect the exact captured cohort.', { role: 'status' });
  const operation = element('p', '', { role: 'status' });
  const detail = element('div', '', { class: 'u4-detail' });
  const draft = element('div', '');
  const downloads = element('div', '');
  const format = element('select', '', { 'aria-label': 'Export format' });
  for (const value of ['json', 'csv', 'svg']) format.append(element('option', value.toUpperCase(), { value }));
  const formatField = element('label', 'Format'); formatField.append(format);
  const toolbox = element('div', '', { class: 'u4-evidence-controls' });
  const inspector = element('div', '', { class: 'u4-evidence-inspector' });
  let generation = 0, selection = null, busy = false, pending = null, exportGeneration = 0, lastJob = null;
  let draftKey = null, rememberedCandidate = null;
  const current = () => root.isConnected && isCurrent();
  function button(label, handler, className = 'secondary') {
    const b = element('button', label, { type: 'button', class: className });
    b.onclick = async () => {
      try { await handler(); } catch (error) {
        if (!current()) return;
        if (denied(error)) {
          selection = null; pending = null; generation++; exportGeneration++; lastJob = null;
          detail.replaceChildren(); draft.replaceChildren(); downloads.replaceChildren(); check.hidden = true;
          count.textContent = 'Evidence is no longer available for this selection.';
        }
        operation.textContent = pending
          ? 'Outcome unknown. Retry the exact original action.'
          : (error.problem?.title ?? 'Action could not be completed. Reload the authorized result and try again.');
        sync();
      }
    };
    return b;
  }
  function sync() {
    const waiting = pending !== null;
    filter.disabled = busy || waiting; format.disabled = busy || waiting;
    apply.disabled = busy || waiting;
    create.disabled = busy || !selection || draft.childElementCount > 0 || (waiting && pending.kind !== 'candidate');
    queue.disabled = busy || !selection || (waiting && pending.kind !== 'export');
    reopen.hidden = !rememberedCandidate || draft.childElementCount > 0;
    reopen.disabled = busy || waiting;
    create.textContent = pending?.kind === 'candidate' ? 'Retry exact candidate creation' : 'Create candidate IA';
    queue.textContent = pending?.kind === 'export' ? 'Retry exact export' : 'Export…';
  }
  async function checked(path, request) {
    await reauthorize();
    if (!current()) throw new Error('Result view changed');
    const reply = await exchange(path, request);
    await reauthorize();
    if (!current()) throw new Error('Result view changed');
    return reply;
  }
  function exactPlacements(row, cards) {
    const groups = new Map((row.groups ?? []).map(group => [group.groupId, group]));
    const table = element('table', '', { class: 'u4-placement-table' });
    table.append(element('caption', 'Original card placements'));
    const thead = element('thead', ''), headers = element('tr', ''), body = element('tbody', '');
    for (const column of ['Card', 'Original group path']) headers.append(element('th', column, { scope: 'col' }));
    thead.append(headers); table.append(thead, body);
    for (const card of cards) {
      const path = [], seen = new Set();
      let groupId = row.membership?.[card.cardId];
      while (groupId && !seen.has(groupId)) {
        seen.add(groupId);
        const group = groups.get(groupId);
        path.unshift(group?.label ?? groupId);
        groupId = group?.parentGroupId;
      }
      const entry = element('tr', '');
      entry.append(element('th', card.label, { scope: 'row' }), element('td', path.join(' › ') || 'Unsorted'));
      body.append(entry);
    }
    return table;
  }
  function displayRows(evidence, cards, append) {
    if (!append) detail.replaceChildren();
    if (!evidence.details.length && !append) detail.append(element('p', 'No records match this filter.'));
    for (const row of evidence.details) {
      const entry = element('details', '', { class: 'u4-detail-row' });
      const title = row.responseId ?? row.evidenceId ?? row.attemptId;
      entry.append(element('summary', (row.statuses ?? []).join(' · ') + ' — ' + title));
      if (Array.isArray(row.route)) {
        const list = element('ol', '', { 'aria-label': 'Exact recorded route' });
        for (const part of row.route) list.append(element('li', part || 'Root'));
        entry.append(element('p', [row.taskId, row.terminalCategory, row.directness].filter(Boolean).join(' · ')));
        entry.append(row.route.length ? list : element('p', 'No route recorded.'));
      } else {
        entry.append(element('p', 'Structure: ' + (row.structuralStatus ?? 'Unknown')));
        const groups = element('ul', '', { 'aria-label': 'Original group labels' });
        for (const group of row.groups ?? []) groups.append(element('li', group.label));
        if (groups.childElementCount) entry.append(groups);
        entry.append(exactPlacements(row, cards));
      }
      detail.append(entry);
    }
    detail.querySelector('[data-u4-more]')?.remove();
    if (evidence.nextOffset !== null) {
      const more = button('Load more matching records', () => load(evidence.nextOffset));
      more.dataset.u4More = ''; detail.append(more);
    }
  }
  async function load(offset = 0) {
    const desired = filter.value, request = ++generation;
    if (!offset) {
      selection = null; detail.replaceChildren(); downloads.replaceChildren(); lastJob = null; exportGeneration++; check.hidden = true;
      operation.textContent = '';
    }
    count.textContent = 'Checking authorized evidence…'; sync();
    const query = new URLSearchParams({ versionId });
    if (jobId) query.set('jobId', jobId);
    if (desired) query.set('status', desired);
    if (offset) query.set('detailOffset', String(offset));
    const { result, selector } = await checked('/studies/' + studyId + '/results?' + query, { selector: 'request' });
    if (!current() || request !== generation) return;
    const evidence = result.evidenceSelection;
    if (!id(selector) || !id(evidence?.analysisId) || result.versionId !== versionId ||
      result.cohortManifest?.cohortId !== cohortId || (evidence.status ?? '') !== desired ||
      !Array.isArray(evidence.details) || !Number.isSafeInteger(evidence.detailCount) ||
      (offset && selection?.selector !== selector)) throw new Error('Unrecognized evidence selection');
    selection = { selector, analysisId: evidence.analysisId };
    count.textContent = evidence.detailCount + (evidence.detailCount === 1 ? ' matching record' : ' matching records') +
      ' · original cohort denominators unchanged.';
    displayRows(evidence, result.cards ?? [], offset > 0); sync();
  }
  async function mutate(kind, path, request) {
    pending ??= { kind, path, request: { ...request, key: crypto.randomUUID() } };
    const intent = pending;
    if (intent.kind !== kind) return;
    busy = true; sync();
    try {
      const { result } = await checked(intent.path, intent.request);
      if (kind === 'export' ? !id(result.jobId) || result.versionId !== versionId || result.cohortId !== cohortId :
        !id(result.candidateIaId) || result.sourceVersionId !== versionId ||
        result.sourceAnalysisId !== intent.request.data.sourceAnalysisId ||
        !Number.isSafeInteger(result.revision) || !result.editableTopology)
        throw new Error('Unrecognized acknowledgement');
      pending = null;
      return result;
    } catch (error) {
      if (error.problem?.status >= 400 && error.problem.status < 500) pending = null;
      throw error;
    } finally { busy = false; sync(); }
  }
  async function poll(job) {
    const request = ++exportGeneration, limit = performance.now() + 62000;
    lastJob = job;
    check.hidden = false;
    downloads.replaceChildren();
    while (current() && request === exportGeneration) {
      operation.textContent = 'Export job · ' + (job.status ?? job.state) + ' · ' + job.format.toUpperCase();
      if (['failed', 'revoked'].includes(job.status ?? job.state)) return;
      if (/^[a-f0-9]{64}$/.test(job.downloadCapability ?? '') && (job.status ?? job.state) === 'completed') {
        const link = element('a', 'Download ' + job.format.toUpperCase(), {
          href: '/api/v1/downloads/' + job.downloadCapability, download: '',
          rel: 'noreferrer', referrerpolicy: 'no-referrer', class: 'secondary',
        });
        downloads.append(link);
        return;
      }
      if (performance.now() >= limit) {
        operation.textContent = 'Export status not confirmed. Check this same job again.';
        return;
      }
      try {
        ({ result: job } = await checked('/exports/' + lastJob.jobId));
        if (!current() || request !== exportGeneration) return;
        if (job.jobId !== lastJob.jobId || job.versionId !== versionId || job.cohortId !== cohortId ||
          job.format !== lastJob.format) throw new Error('Unexpected export response');
      } catch (error) {
        if (!current() || request !== exportGeneration) return;
        if (denied(error)) throw error;
        operation.textContent = 'Export status unavailable. Check this same job again.';
        return;
      }
      await new Promise(resolve => setTimeout(resolve, 750));
    }
  }
  const apply = button('Apply filters', () => load(), 'primary');
  const queue = button('Export…', async () => {
    const job = await mutate('export', '/studies/' + studyId + '/exports', {
      method: 'POST', selector: selection.selector, data: { versionId, cohortId, format: format.value },
    });
    if (job) await poll(job);
  });
  const create = button('Create candidate IA', async () => {
    const result = await mutate('candidate', '/candidate-ias', { method: 'POST', data: {
      sourceVersionId: versionId, sourceAnalysisId: selection.analysisId,
    } });
    if (!result) return;
    rememberedCandidate = result.candidateIaId;
    if (draftKey) try { sessionStorage.setItem(draftKey, rememberedCandidate); } catch { /* optional tab recovery */ }
    openCandidate(result);
  }, 'primary');
  const reopen = button('Reopen saved candidate', async () => {
    const candidateId = rememberedCandidate;
    if (!id(candidateId)) return;
    const { result } = await checked('/candidate-ias/' + candidateId);
    if (result.candidateIaId !== candidateId || result.sourceVersionId !== versionId ||
      !id(result.sourceAnalysisId) || !Number.isSafeInteger(result.revision) ||
      !result.editableTopology) throw new Error('Unexpected candidate identity');
    openCandidate(result);
  });
  function openCandidate(result) {
    const editor = candidateEditor(result, { checked, denied, current });
    draft.replaceChildren(editor);
    editor.querySelector('h3').focus(); sync();
  }
  const check = button('Check export status', async () => { if (lastJob) await poll(lastJob); });
  check.hidden = true;
  const detailDisclosure = element('details', '', { class: 'u4-evidence-detail' });
  detailDisclosure.append(element('summary', 'Inspect matching records'), detail);
  filter.onchange = () => {
    generation++; exportGeneration++; lastJob = null; selection = null; check.hidden = true;
    count.textContent = 'Filter changed. Apply filters to select the exact records.';
    detail.replaceChildren(); downloads.replaceChildren(); operation.textContent = ''; sync();
  };
  const jobControls = element('div', '', { class: 'u4-job-controls' });
  jobControls.append(formatField, queue, check, downloads, operation);
  toolbox.append(field, apply, cohort);
  inspector.append(count, jobControls, detailDisclosure);
  const draftActions = element('div', '', { class: 'u4-draft-launch' });
  draftActions.append(create, reopen);
  root.append(element('h2', 'Evidence delivery'));
  if (filterHost) filterHost.replaceChildren(toolbox);
  else root.append(toolbox);
  const draftNote = element('p', 'Creates a separate draft. Evidence stays unchanged.', { class: 'u4-evidence-context' });
  if (draftHost) draftHost.replaceChildren(draftActions, draftNote, draft);
  if (overview) {
    const views = element('div', '', { class: 'u2-result-tabs', 'aria-label': 'Evidence view' });
    const overviewButton = button('Overview', () => show(true));
    const rawButton = button('Raw responses', () => show(false));
    function show(showOverview) {
      overview.hidden = !showOverview;
      detailDisclosure.open = !showOverview;
      overviewButton.setAttribute('aria-pressed', String(showOverview));
      rawButton.setAttribute('aria-pressed', String(!showOverview));
    }
    views.append(overviewButton, rawButton); show(true);
    root.append(views, overview);
    jobControls.prepend(button('Inspect routes', () => {
      show(false); detailDisclosure.querySelector('summary').focus();
    }));
  }
  root.append(inspector);
  if (!draftHost) root.append(draftActions, draftNote, draft);
  sync();
  queueMicrotask(async () => {
    try {
      const { result } = await checked('/auth/session/current');
      if (!id(result.researcherId)) return;
      draftKey = 'u4-candidate-v1:' + JSON.stringify([result.researcherId, studyId, versionId]);
      const cached = sessionStorage.getItem(draftKey);
      if (id(cached)) rememberedCandidate = cached;
      else if (rememberedCandidate) sessionStorage.setItem(draftKey, rememberedCandidate);
      sync();
    } catch { /* authorization is checked again on every operation */ }
  });
  return root;
}
