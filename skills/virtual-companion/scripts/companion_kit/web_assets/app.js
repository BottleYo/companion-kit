const tokenKey = "companion-panel-token";
const fragmentToken = new URLSearchParams(window.location.hash.slice(1)).get("token") || "";
let storedToken = "";
try {
  if (fragmentToken) window.sessionStorage.setItem(tokenKey, fragmentToken);
  storedToken = window.sessionStorage.getItem(tokenKey) || "";
} catch {
  storedToken = "";
}
if (fragmentToken) {
  window.history.replaceState(null, "", `${window.location.pathname}${window.location.search}`);
}
const token = fragmentToken || storedToken;

const elements = {
  authError: document.querySelector("#authError"),
  personaDescription: document.querySelector("#personaDescription"),
  templateGrid: document.querySelector("#templateGrid"),
  displayName: document.querySelector("#displayName"),
  previewPersona: document.querySelector("#previewPersona"),
  profileStatus: document.querySelector("#profileStatus"),
  draftStatus: document.querySelector("#draftStatus"),
  previewName: document.querySelector("#previewName"),
  previewIntent: document.querySelector("#previewIntent"),
  avatarLetter: document.querySelector("#avatarLetter"),
  traits: document.querySelector("#traits"),
  values: document.querySelector("#values"),
  speakingStyle: document.querySelector("#speakingStyle"),
  background: document.querySelector("#background"),
  interests: document.querySelector("#interests"),
  taskStyle: document.querySelector("#taskStyle"),
  appearance: document.querySelector("#appearance"),
  defaultStyle: document.querySelector("#defaultStyle"),
  defaultHairstyle: document.querySelector("#defaultHairstyle"),
  defaultExpression: document.querySelector("#defaultExpression"),
  defaultMakeup: document.querySelector("#defaultMakeup"),
  defaultWardrobe: document.querySelector("#defaultWardrobe"),
  startingMode: document.querySelector("#startingMode"),
  romanceEnabled: document.querySelector("#romanceEnabled"),
  identityStatus: document.querySelector("#identityStatus"),
  saveProfile: document.querySelector("#saveProfile"),
  saveHint: document.querySelector("#saveHint"),
  referenceStatus: document.querySelector("#referenceStatus"),
  hostGrid: document.querySelector("#hostGrid"),
  installDialog: document.querySelector("#installDialog"),
  dialogTitle: document.querySelector("#dialogTitle"),
  dialogSummary: document.querySelector("#dialogSummary"),
  confirmInstall: document.querySelector("#confirmInstall"),
  toast: document.querySelector("#toast"),
};

const editableFields = {
  traits: { element: elements.traits, list: true },
  values: { element: elements.values, list: true },
  speaking_style: { element: elements.speakingStyle },
  background: { element: elements.background },
  interests: { element: elements.interests, list: true },
  task_style: { element: elements.taskStyle },
  appearance: { element: elements.appearance },
  default_style: { element: elements.defaultStyle },
  default_hairstyle: { element: elements.defaultHairstyle },
  default_expression: { element: elements.defaultExpression },
  default_makeup: { element: elements.defaultMakeup },
  default_wardrobe: { element: elements.defaultWardrobe },
};

let state = null;
let selectedTemplateId = null;
let draft = null;
let baseline = null;
let currentVersion = null;
let pendingHost = null;

function setText(element, value) {
  element.textContent = value == null ? "" : String(value);
}

function showToast(message) {
  setText(elements.toast, message);
  elements.toast.hidden = false;
  window.setTimeout(() => {
    elements.toast.hidden = true;
  }, 3800);
}

async function api(path, options = {}) {
  const headers = new Headers(options.headers || {});
  headers.set("X-Companion-Token", token);
  if (options.body) headers.set("Content-Type", "application/json");
  const response = await window.fetch(path, { ...options, headers });
  const payload = await response.json().catch(() => ({ error: "面板返回了无法读取的结果" }));
  if (!response.ok) {
    const error = new Error(payload.error || "操作失败");
    error.status = response.status;
    throw error;
  }
  return payload;
}

function splitList(value) {
  return String(value || "")
    .split(/[、,，]/)
    .map((item) => item.trim())
    .filter(Boolean);
}

function normalizeForCompare(value) {
  return Array.isArray(value) ? value.join("\u0000") : String(value || "").trim();
}

function fieldValue(field) {
  const config = editableFields[field];
  return config.list ? splitList(config.element.value) : config.element.value.trim();
}

function collectOverrides() {
  if (!baseline) return {};
  const overrides = {};
  for (const field of Object.keys(editableFields)) {
    const value = fieldValue(field);
    if (normalizeForCompare(value) !== normalizeForCompare(baseline[field])) {
      overrides[field] = value;
    }
  }
  return overrides;
}

function sourcePayload() {
  const description = elements.personaDescription.value.trim();
  const body = { display_name: elements.displayName.value.trim() || null };
  if (selectedTemplateId) body.template_id = selectedTemplateId;
  if (description) body.description = description;
  return body;
}

function createTemplateCard(template) {
  const button = document.createElement("button");
  button.type = "button";
  button.className = "template-card";
  button.setAttribute("aria-pressed", String(template.id === selectedTemplateId));

  const top = document.createElement("span");
  top.className = "template-card__top";
  const name = document.createElement("span");
  name.className = "template-card__name";
  setText(name, template.name);
  top.append(name);
  if (template.recommended) {
    const badge = document.createElement("span");
    badge.className = "template-card__badge";
    setText(badge, "轻松起步");
    top.append(badge);
  }
  const description = document.createElement("span");
  description.className = "template-card__description";
  setText(description, template.description);
  button.append(top, description);
  button.addEventListener("click", async () => {
    selectedTemplateId = template.id;
    elements.personaDescription.value = "";
    if (!elements.displayName.value.trim() || !currentVersion) {
      elements.displayName.value = template.default_name;
    }
    renderTemplates();
    await previewPersona();
  });
  return button;
}

function renderTemplates() {
  elements.templateGrid.replaceChildren(...state.templates.map(createTemplateCard));
}

function draftFieldValues(value) {
  return {
    traits: value.traits,
    values: value.values,
    speaking_style: value.speaking_style,
    background: value.background,
    interests: value.interests,
    task_style: value.task_style,
    appearance: value.appearance.direction,
    default_style: value.appearance.default_style,
    default_hairstyle: value.appearance.default_hairstyle,
    default_expression: value.appearance.default_expression,
    default_makeup: value.appearance.default_makeup,
    default_wardrobe: value.appearance.default_wardrobe,
  };
}

function fillEditors(value) {
  const fields = draftFieldValues(value);
  baseline = structuredClone(fields);
  for (const [field, config] of Object.entries(editableFields)) {
    const content = fields[field];
    config.element.value = config.list ? content.join("、") : content;
  }
}

function renderDraft(value, { fill = true } = {}) {
  if (!value) return;
  draft = value;
  if (fill) fillEditors(value);
  const name = elements.displayName.value.trim() || value.display_name;
  setText(elements.previewName, name);
  setText(elements.previewIntent, value.intent_summary);
  setText(elements.avatarLetter, name.slice(0, 1) || "伴");
  const locked = value.visual_identity.status === "locked";
  setText(elements.identityStatus, locked ? "人物长相：已经确认固定脸" : "人物长相：暂时跳过");
  setText(elements.draftStatus, "可以继续调整");
}

function profileAsDraft(profile) {
  return {
    id: profile.id,
    display_name: profile.display_name,
    template_id: profile.template_id,
    intent_summary: profile.intent_summary,
    traits: profile.traits,
    speaking_style: profile.speaking_style,
    background: profile.background,
    values: profile.values,
    interests: profile.interests,
    task_style: profile.task_style,
    appearance: {
      direction: profile.appearance.direction,
      default_style: profile.appearance.default_style,
      default_hairstyle: profile.appearance.default_hairstyle,
      default_expression: profile.appearance.default_expression,
      default_makeup: profile.appearance.default_makeup,
      default_wardrobe: profile.appearance.default_wardrobe,
    },
    visual_identity: profile.visual_identity,
  };
}

async function previewPersona() {
  const body = sourcePayload();
  if (!body.template_id && !body.description) {
    showToast("先说一句你喜欢的感觉，或者选一个起点");
    elements.personaDescription.focus();
    return false;
  }
  elements.previewPersona.disabled = true;
  setText(elements.previewPersona, "正在补全…");
  setText(elements.draftStatus, "正在补全");
  try {
    const payload = await api("/api/persona/draft", {
      method: "POST",
      body: JSON.stringify(body),
    });
    elements.displayName.value = payload.draft.display_name;
    renderDraft(payload.draft);
    document.querySelector("#previewSection").scrollIntoView({ behavior: "smooth", block: "start" });
    return true;
  } catch (error) {
    showToast(error.message);
    setText(elements.draftStatus, "补全失败");
    return false;
  } finally {
    elements.previewPersona.disabled = false;
    setText(elements.previewPersona, "帮我补完整");
  }
}

function hostDescription(host) {
  const family = host.family === "event" ? "消息型工具" : "桌面任务工具";
  if (host.method === "native_cli") return `${family} · 使用 ${host.name} 原生安装器`;
  if (host.method === "codex_plugin") return `${family} · 安装完整 Codex Plugin`;
  return `${family} · 安装独立 Skill`;
}

function createHostCard(host) {
  const card = document.createElement("article");
  card.className = `host-card${host.host === "codex" ? " host-card--primary" : ""}`;
  const icon = document.createElement("div");
  icon.className = "host-card__icon";
  setText(icon, host.name.slice(0, 1));
  const copy = document.createElement("div");
  const title = document.createElement("h3");
  setText(title, host.host === "codex" ? `${host.name} · 本轮优先` : host.name);
  const detail = document.createElement("p");
  setText(detail, host.error || hostDescription(host));
  copy.append(title, detail);
  const button = document.createElement("button");
  button.type = "button";
  if (host.existing) {
    setText(button, "已安装");
    button.disabled = true;
  } else if (!host.available) {
    setText(button, "环境不可用");
    button.disabled = true;
  } else {
    setText(button, host.host === "codex" ? "安装到 Codex" : "查看安装方式");
    button.addEventListener("click", () => openInstallDialog(host));
  }
  card.append(icon, copy, button);
  return card;
}

function renderHosts() {
  const ordered = [...state.hosts].sort((left, right) => (left.host === "codex" ? -1 : right.host === "codex" ? 1 : 0));
  elements.hostGrid.replaceChildren(...ordered.map(createHostCard));
}

function openInstallDialog(host) {
  pendingHost = host.host;
  setText(elements.dialogTitle, `安装到 ${host.name}`);
  const destination = host.destination ? `目标位置：${host.destination}` : "将使用宿主自己的安全安装机制。";
  const separation = host.host === "codex"
    ? "会读取刚刚保存的 Codex Persona，但不会改动 Codex 全局个性化或全局记忆。"
    : "这个宿主有独立 Persona；不会自动复制当前 Codex Persona。";
  setText(elements.dialogSummary, `${hostDescription(host)}。${destination} ${separation}`);
  elements.installDialog.showModal();
}

function renderProfile(profile) {
  currentVersion = profile?.version || null;
  if (!profile) {
    selectedTemplateId = state.templates.find((item) => item.recommended)?.id || state.templates[0]?.id || null;
    setText(elements.profileStatus, "尚未配置");
    setText(elements.referenceStatus, "尚未固定人物原型");
    return;
  }
  selectedTemplateId = profile.template_id === "custom" ? null : profile.template_id;
  elements.personaDescription.value = profile.template_id === "custom" ? profile.intent_summary : "";
  elements.displayName.value = profile.display_name;
  elements.startingMode.value = profile.relationship.starting_mode;
  elements.romanceEnabled.checked = profile.relationship.romance_enabled === true;
  renderDraft(profileAsDraft(profile));
  setText(elements.profileStatus, "已配置");
  const locked = profile.visual_identity.status === "locked" && profile.visual_identity.reference_count === 1;
  setText(elements.referenceStatus, locked ? "已确认固定脸；后续照片只锁脸部身份" : "尚未固定人物原型；第一次要照片时再决定");
}

async function loadState() {
  if (!token) {
    elements.authError.hidden = false;
    return;
  }
  try {
    state = await api("/api/state");
  } catch (error) {
    if (error.status === 401) elements.authError.hidden = false;
    showToast(error.message);
    return;
  }
  renderProfile(state.profile);
  renderTemplates();
  renderHosts();
}

document.querySelectorAll("[data-example]").forEach((button) => {
  button.addEventListener("click", async () => {
    selectedTemplateId = null;
    elements.personaDescription.value = button.dataset.example;
    renderTemplates();
    await previewPersona();
  });
});

elements.personaDescription.addEventListener("input", () => {
  if (elements.personaDescription.value.trim()) {
    selectedTemplateId = null;
    renderTemplates();
  }
});
elements.displayName.addEventListener("input", () => {
  const name = elements.displayName.value.trim() || draft?.display_name || "伴";
  setText(elements.previewName, name);
  setText(elements.avatarLetter, name.slice(0, 1) || "伴");
});
elements.previewPersona.addEventListener("click", previewPersona);

elements.saveProfile.addEventListener("click", async () => {
  if (!draft) {
    const ready = await previewPersona();
    if (!ready) return;
  }
  const body = {
    ...sourcePayload(),
    overrides: collectOverrides(),
    starting_mode: elements.startingMode.value,
    romance_enabled: elements.romanceEnabled.checked,
  };
  if (currentVersion) body.expected_version = currentVersion;
  elements.saveProfile.disabled = true;
  setText(elements.saveProfile, "正在保存…");
  try {
    const payload = await api("/api/profile", {
      method: "POST",
      body: JSON.stringify(body),
    });
    state.profile = payload.profile;
    renderProfile(payload.profile);
    renderTemplates();
    setText(elements.saveHint, "已经保存。新开一个 Codex 任务就可以自然聊天、解决问题或要照片。");
    showToast("TA 已经在 Codex 里准备好了");
  } catch (error) {
    showToast(error.message);
    if (error.status === 409) await loadState();
  } finally {
    elements.saveProfile.disabled = false;
    setText(elements.saveProfile, "确认并保存");
  }
});

elements.confirmInstall.addEventListener("click", async (event) => {
  event.preventDefault();
  if (!pendingHost) return;
  elements.confirmInstall.disabled = true;
  setText(elements.confirmInstall, "安装中…");
  try {
    await api("/api/install", {
      method: "POST",
      body: JSON.stringify({ host: pendingHost, confirm: true }),
    });
    elements.installDialog.close();
    showToast(pendingHost === "codex" ? "安装完成，新开一个 Codex 任务就可以开始" : "安装完成");
    await loadState();
  } catch (error) {
    showToast(error.message);
  } finally {
    elements.confirmInstall.disabled = false;
    setText(elements.confirmInstall, "确认安装");
    pendingHost = null;
  }
});

loadState();
