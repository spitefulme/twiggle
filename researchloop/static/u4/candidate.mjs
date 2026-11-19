import { element } from '../u1/api.mjs';

export function candidateEditor(initial, { checked, denied, current }) {
  let saved = initial, working = structuredClone(initial.editableTopology), pending = null, busy = false;
  let operations = [], resourcePage = 0, placementPage = 0;
  const root = element('section', '', { class: 'u4-candidate', 'aria-label': 'Candidate IA draft' });
  const status = element('p', 'Saved candidate draft · revision ' + saved.revision, { role: 'status' });
  const heading = element('h3', 'Candidate IA draft', { tabindex: '-1' });
  const form = element('div', '', { class: 'u4-candidate-fields' });
  const path = '/candidate-ias/' + initial.candidateIaId;
  const same = result => result?.candidateIaId === initial.candidateIaId &&
    result.sourceVersionId === initial.sourceVersionId && result.sourceAnalysisId === initial.sourceAnalysisId &&
    Number.isSafeInteger(result.revision) && Array.isArray(result.editableTopology?.resources) &&
    Array.isArray(result.editableTopology?.occurrences);
  function errorState(error) {
    if (!current()) return;
    if (denied(error)) {
      root.replaceChildren(heading, status);
      status.textContent = 'This candidate is no longer available.';
    } else if (pending) {
      status.textContent = 'Save outcome unknown. Retry the exact original change; edits are retained.';
    } else {
      status.textContent = error.problem?.title ??
        'Change could not be confirmed. The last acknowledged revision is unchanged.';
    }
  }
  function makeButton(label, run, className = 'secondary') {
    const button = element('button', label, { type: 'button', class: className });
    button.onclick = async () => {
      if (busy) return;
      try { await run(); } catch (error) { errorState(error); }
    };
    return button;
  }
  const dirty = () => operations.length > 0;
  const apply = (next, acknowledged = 0) => {
    operations = operations.slice(acknowledged);
    saved = next; working = structuredClone(next.editableTopology);
    for (const operation of operations) edit(operation);
    redraw();
    status.textContent = dirty() ? 'Saved earlier changes · newer candidate edits remain unsaved' :
      'Saved candidate draft · revision ' + next.revision;
  };
  const save = makeButton('Save candidate', async () => {
    if (!pending) {
      const batch = operations.slice(0, 32);
      const bytes = new TextEncoder();
      while (batch.length && bytes.encode(JSON.stringify({ operations: batch })).length > 262144) batch.pop();
      if (!batch.length) {
        status.textContent = 'The first unsaved edit exceeds the request limit. Reduce it before saving.';
        return;
      }
      pending = { count: batch.length, request: {
        method: 'PATCH', data: { operations: structuredClone(batch) },
        revision: saved.revision, key: crypto.randomUUID(),
      } };
    }
    const intent = pending;
    busy = true; sync();
    status.textContent = 'Saving candidate changes…';
    try {
      const { result } = await checked(path, intent.request);
      if (!same(result) || result.revision !== intent.request.revision + 1)
        throw new Error('Unexpected candidate acknowledgement');
      pending = null; apply(result, intent.count);
    } catch (error) {
      if (error.problem?.status >= 400 && error.problem.status < 500) pending = null;
      throw error;
    } finally { busy = false; sync(); }
  }, 'primary');
  const reload = makeButton('Reload saved candidate', async () => {
    if (dirty() || pending) return;
    busy = true; sync();
    try {
      const { result } = await checked(path);
      if (!same(result) || result.revision < saved.revision) throw new Error('Candidate identity changed');
      apply(result);
    } finally { busy = false; sync(); }
  });
  const discard = makeButton('Discard unsaved changes', () => {
    if (pending) return;
    operations = []; working = structuredClone(saved.editableTopology); redraw();
    status.textContent = 'Unsaved edits discarded. Saved revision ' + saved.revision + ' is unchanged.';
    heading.focus();
  });
  function sync() {
    save.disabled = busy || (!dirty() && !pending);
    save.textContent = pending ? 'Retry exact save' : 'Save candidate';
    reload.disabled = busy || Boolean(pending) || dirty();
    discard.disabled = busy || Boolean(pending) || !dirty();
  }
  function update(operation) {
    const last = operations.at(-1);
    if (operation.type === 'renameResource' && last?.type === operation.type &&
      last.resourceId === operation.resourceId && operations.length > (pending?.count ?? 0)) operations.pop();
    operations.push(operation);
    edit(operation);
    status.textContent = pending ? 'Earlier save pending; newer candidate edits are unsaved' : 'Unsaved candidate changes';
    sync();
  }
  const label = node => node.displayLabel || working.resources.find(resource =>
    resource.resourceId === node.resourceId)?.label || 'Heading';
  function descendants(node) {
    const blocked = new Set([node.occurrenceId]), queue = [node.occurrenceId];
    for (const id of queue) for (const child of working.occurrences) {
      if (child.parentOccurrenceId === id && !blocked.has(child.occurrenceId)) {
        blocked.add(child.occurrenceId); queue.push(child.occurrenceId);
      }
    }
    return blocked;
  }
  const siblings = parent => working.occurrences.filter(node => (node.parentOccurrenceId ?? '') === parent)
    .sort((left, right) => left.order - right.order);
  function edit(operation) {
    if (operation.type === 'renameResource') {
      working.resources.find(row => row.resourceId === operation.resourceId).label = operation.label;
      return;
    }
    const node = working.occurrences.find(row => row.occurrenceId === operation.occurrenceId);
    const parent = node.parentOccurrenceId ?? '', next = operation.parentOccurrenceId ?? '';
    if (parent !== next) siblings(parent).filter(item => item !== node).forEach((item, order) => { item.order = order; });
    const destination = siblings(next).filter(item => item !== node);
    node.parentOccurrenceId = next || null;
    destination.splice(operation.position, 0, node);
    destination.forEach((item, order) => { item.order = order; });
  }
  function appendPaging(title, page, count, selectPage) {
    const size = 20;
    if (count <= size) return;
    form.append(element('p', title + ' ' + (page * size + 1) + '–' + Math.min(count, (page + 1) * size) + ' of ' + count));
    const previous = makeButton('Previous ' + title.toLowerCase(), () => {
      selectPage(page - 1); redraw(); form.querySelector('button:not([disabled])')?.focus();
    });
    const next = makeButton('Next ' + title.toLowerCase(), () => {
      selectPage(page + 1); redraw(); form.querySelector('button:not([disabled])')?.focus();
    });
    previous.disabled = !page; next.disabled = (page + 1) * size >= count; form.append(previous, next);
  }
  function redraw() {
    form.replaceChildren(element('h4', 'Resource labels'));
    appendPaging('Resources', resourcePage, working.resources.length, page => { resourcePage = page; });
    for (const [index, resource] of working.resources.slice(resourcePage * 20, (resourcePage + 1) * 20).entries()) {
      const field = element('label', 'Resource label ' + (resourcePage * 20 + index + 1));
      const input = element('input', '', { type: 'text' }); input.value = resource.label;
      input.oninput = () => update({ type: 'renameResource', resourceId: resource.resourceId, label: input.value });
      field.append(input); form.append(field);
    }
    form.append(element('h4', 'Occurrence placements'));
    appendPaging('Placements', placementPage, working.occurrences.length, page => { placementPage = page; });
    for (const [offset, node] of working.occurrences.slice(placementPage * 20, (placementPage + 1) * 20).entries()) {
      const row = element('div', '', { class: 'u4-node' });
      const currentLabel = label(node), parent = node.parentOccurrenceId ?? '';
      const field = element('label', 'Parent for ' + currentLabel + ' (' + (placementPage * 20 + offset + 1) + ')');
      const select = element('select', ''); select.append(element('option', 'Root', { value: '' }));
      if (parent) select.append(element('option', label(working.occurrences.find(item => item.occurrenceId === parent)), { value: parent }));
      select.value = parent;
      function populate() {
        if (select.dataset.choices) return;
        select.dataset.choices = 'loaded';
        const blocked = descendants(node);
        for (const other of working.occurrences)
          if (!blocked.has(other.occurrenceId) && other.occurrenceId !== parent)
            select.append(element('option', label(other), { value: other.occurrenceId }));
      }
      select.onfocus = populate; select.onpointerdown = populate;
      select.onchange = () => {
        const next = select.value;
        update({ type: 'moveOccurrence', occurrenceId: node.occurrenceId, parentOccurrenceId: next || null,
          position: siblings(next).filter(item => item !== node).length });
        redraw(); form.querySelectorAll('select')[offset]?.focus();
      };
      field.append(select); row.append(field);
      const peers = siblings(parent), index = peers.indexOf(node);
      for (const [direction, delta] of [['earlier', -1], ['later', 1]]) {
        const control = makeButton('Move ' + currentLabel + ' ' + direction, () => {
          const swap = peers[index + delta];
          if (!swap) return;
          update({ type: 'moveOccurrence', occurrenceId: node.occurrenceId,
            parentOccurrenceId: parent || null, position: index + delta });
          redraw();
          form.querySelectorAll('select')[offset]?.focus();
        });
        control.disabled = !peers[index + delta]; row.append(control);
      }
      form.append(row);
    }
    sync();
  }
  const actions = element('div', '', { class: 'u4-candidate-actions' });
  actions.append(save, reload, discard);
  root.append(heading, element('p', 'Editing this draft does not change the study or evidence.'),
    element('p', 'Source version ' + initial.sourceVersionId, { class: 'u4-evidence-context' }), status, form, actions);
  redraw();
  return root;
}
