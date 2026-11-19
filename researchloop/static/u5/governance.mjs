import {api, element} from "../u1/api.mjs";

const find = name => document.querySelector(`[data-testid="governance-${name}"]`);
const main = document.getElementById("main");
const scope = `/workspaces/${main.dataset.workspace}`;
const status = find("status");
const selector = find("study");
const deletion = find("delete-study");
const admission = find("admit-form");
const actor = find("audit-actor");
const actionFilter = find("audit-action");
const tabs = [...main.querySelectorAll('[role="tab"]')];
let studies = new Map();
let audit = [];

function activate(tab, focus) {
  for (const item of tabs) {
    const active = item === tab;
    item.setAttribute("aria-selected", String(active));
    item.tabIndex = active ? 0 : -1;
    document.getElementById(item.getAttribute("aria-controls")).hidden = !active;
  }
  if (focus) tab.focus();
}

for (const [index, tab] of tabs.entries()) {
  tab.addEventListener("click", () => activate(tab, false));
  tab.addEventListener("keydown", event => {
    const next = {
      ArrowLeft: (index - 1 + tabs.length) % tabs.length,
      ArrowRight: (index + 1) % tabs.length,
      Home: 0,
      End: tabs.length - 1,
    }[event.key];
    if (next === undefined) return;
    event.preventDefault();
    activate(tabs[next], true);
  });
}
find("open-audit").addEventListener("click", () => activate(tabs[1], true));

function announce(message, focus = false) {
  status.textContent = message;
  if (focus) status.focus();
}

async function change(member) {
  const action = member.active ? "Revoke" : "Restore";
  const consequence = member.active ? "They will no longer be able to open workspace studies." : "They will be able to open workspace studies again.";
  if (!window.confirm(`${action} workspace access for ${member.username}? ${consequence}`)) return;
  try {
    await api(`${scope}/members/${member.userId}`, {
      method: "PATCH", data: {active: !member.active, revision: member.revision},
    });
    await refresh();
    announce(member.active ? "Access revoked." : "Access restored.", true);
  } catch (error) {
    announce(error.message, true);
  }
}

function showMembers(members) {
  find("members").replaceChildren(...members.map(member => {
    const row = element("li", "", {"data-testid": "governance-membership"});
    row.append(element("strong", member.username),
      element("span", member.role === "workspace-admin" ? "Administrator" : member.active ? "Member" : "Revoked",
        {class: "governance-role"}));
    if (member.role === "member") {
      const button = element("button", member.active ? "Revoke access" : "Restore access", {
        class: "secondary", type: "button",
        "data-testid": member.active ? "governance-revoke" : "governance-restore",
      });
      button.addEventListener("click", () => change(member));
      row.append(button);
    }
    return row;
  }));
}

function setOptions(select, empty, pairs) {
  const current = select.value;
  select.replaceChildren(element("option", empty, {value: ""}),
    ...[...pairs].sort((first, second) => first[1].localeCompare(second[1]))
      .map(([value, label]) => element("option", label, {value})));
  if (pairs.has(current)) select.value = current;
}

function auditRow(item) {
  const row = element("li", "", {class: "governance-audit-row"});
  const when = new Date(item.occurredAt);
  row.append(
    element("time", when.toLocaleString(), {datetime: item.occurredAt}),
    element("strong", item.actionLabel),
    element("span", item.actor),
  );
  return row;
}

function auditRows(items) {
  return items.length
    ? items.map(auditRow)
    : [element("li", "No events match these filters.")];
}

function renderAudit() {
  const matching = audit.filter(item =>
    (!actor.value || item.actor === actor.value) &&
    (!actionFilter.value || item.action === actionFilter.value));
  find("audit").replaceChildren(...auditRows(matching));
  find("audit-count").textContent = `${matching.length} ${matching.length === 1 ? "event" : "events"}`;
}

function showAudit(facts) {
  audit = facts;
  setOptions(actor, "All actors", new Map(audit.map(item => [item.actor, item.actor])));
  setOptions(actionFilter, "All actions", new Map(audit.map(item => [item.action, item.actionLabel])));
  find("audit-preview").replaceChildren(...auditRows(audit.slice(0, 3)));
  renderAudit();
}

function caseRow(item) {
  const row = element("li");
  row.append(
    element("strong", `${item.kind}: ${item.status}`),
    element("small", `Case ${item.caseId}`, {class: "governance-case-reference"}),
  );
  return row;
}

actor.addEventListener("change", renderAudit);
actionFilter.addEventListener("change", renderAudit);

async function refresh() {
  const [membership, cases, list] = await Promise.all([
    api(`${scope}/members`), api(`${scope}/erasure-cases`),
    selector ? api("/studies") : Promise.resolve([]),
  ]);
  showMembers(membership.members);
  find("cases").replaceChildren(...cases.cases.map(caseRow));
  showAudit(cases.audit);
  if (selector) {
    const selectedStudy = selector.value;
    studies = new Map(list.map(study => [study.studyId, study]));
    selector.replaceChildren(...list.map(study => element("option", study.title, {value: study.studyId})));
    if (studies.has(selectedStudy)) selector.value = selectedStudy;
    deletion.disabled = list.length === 0;
  }
}

find("refresh").addEventListener("click", async () => {
  try {
    await refresh();
    announce("Case status refreshed.", true);
  } catch (error) {
    announce(error.message, true);
  }
});

admission.addEventListener("submit", async event => {
  event.preventDefault();
  const username = new FormData(admission).get("username");
  if (!window.confirm(`Grant workspace access to existing account ${username}? They will be able to open workspace studies.`)) return;
  try {
    await api(`${scope}/members`, {method: "POST", data: {username}});
    admission.reset();
    admission.closest("details").open = false;
    await refresh();
    announce("Member access granted.", true);
  } catch (error) {
    announce(error.message, true);
  }
});

deletion?.addEventListener("click", async () => {
  const study = studies.get(selector.value);
  if (!study || !window.confirm(`Permanently erase study ${study.title}?`)) return;
  try {
    const result = await api(`${scope}/erasure-cases`, {
      method: "POST",
      data: {studyId: study.studyId, revision: study.revision, confirmation: study.studyId},
    });
    await refresh();
    announce(`Erasure request: ${result.status}.`, true);
  } catch (error) {
    announce(error.problem?.code === "STALE_REVISION"
      ? "The selected study changed. Refresh case status and review the selected study before confirming again."
      : error.message, true);
  }
});

api("/auth/session/current").then(refresh).then(() => announce("Workspace administration ready."))
  .catch(error => announce(error.message, true));
