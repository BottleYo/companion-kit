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
  templateGrid: document.querySelector("#templateGrid"),
  profileHost: document.querySelector("#profileHost"),
  hostChoiceHint: document.querySelector("#hostChoiceHint"),
  displayName: document.querySelector("#displayName"),
  startingMode: document.querySelector("#startingMode"),
  romanceEnabled: document.querySelector("#romanceEnabled"),
  saveProfile: document.querySelector("#saveProfile"),
  saveHint: document.querySelector("#saveHint"),
  profileStatus: document.querySelector("#profileStatus"),
  previewName: document.querySelector("#previewName"),
  previewTraits: document.querySelector("#previewTraits"),
  previewSpeech: document.querySelector("#previewSpeech"),
  previewAppearance: document.querySelector("#previewAppearance"),
  previewStyle: document.querySelector("#previewStyle"),
  previewRelationship: document.querySelector("#previewRelationship"),
  avatarLetter: document.querySelector("#avatarLetter"),
  photoHostLabel: document.querySelector("#photoHostLabel"),
  photoModeHeading: document.querySelector("#photoModeHeading"),
  photoIntro: document.querySelector("#photoIntro"),
  photoModes: document.querySelector("#photoModes"),
  photoHelp: document.querySelector("#photoHelp"),
  nativeModeTitle: document.querySelector("#nativeModeTitle"),
  nativeModeStatus: document.querySelector("#nativeModeStatus"),
  nativeModeDescription: document.querySelector("#nativeModeDescription"),
  strictModeTitle: document.querySelector("#strictModeTitle"),
  strictModeStatus: document.querySelector("#strictModeStatus"),
  strictModeDescription: document.querySelector("#strictModeDescription"),
  referenceStatus: document.querySelector("#referenceStatus"),
  hostGrid: document.querySelector("#hostGrid"),
  installDialog: document.querySelector("#installDialog"),
  dialogTitle: document.querySelector("#dialogTitle"),
  dialogSummary: document.querySelector("#dialogSummary"),
  confirmInstall: document.querySelector("#confirmInstall"),
  toast: document.querySelector("#toast"),
};

let state = null;
let selectedHost = "codex";
let selectedTemplate = null;
let profilePreview = null;
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
  }, 3600);
}

async function api(path, options = {}) {
  const headers = new Headers(options.headers || {});
  headers.set("X-Companion-Token", token);
  if (options.body) {
    headers.set("Content-Type", "application/json");
  }
  const response = await window.fetch(path, { ...options, headers });
  const payload = await response.json().catch(() => ({ error: "面板返回了无法读取的结果" }));
  if (!response.ok) {
    const error = new Error(payload.error || "操作失败");
    error.status = response.status;
    throw error;
  }
  return payload;
}

function createTemplateCard(template) {
  const button = document.createElement("button");
  button.type = "button";
  button.className = "template-card";
  button.setAttribute("aria-pressed", String(template.id === selectedTemplate?.id));

  const top = document.createElement("span");
  top.className = "template-card__top";
  const name = document.createElement("span");
  name.className = "template-card__name";
  setText(name, template.name);
  top.append(name);
  if (template.recommended) {
    const badge = document.createElement("span");
    badge.className = "template-card__badge";
    setText(badge, "推荐");
    top.append(badge);
  }
  const description = document.createElement("span");
  description.className = "template-card__description";
  setText(description, template.description);
  button.append(top, description);
  button.addEventListener("click", () => selectTemplate(template.id, true));
  return button;
}

const hostNames = {
  codex: "Codex",
  openclaw: "OpenClaw",
  hermes: "Hermes",
  claude: "Claude",
};

function currentProfile() {
  return state?.profiles?.[selectedHost] ?? (selectedHost === "codex" ? state?.profile : null);
}

function currentProfileError() {
  return state?.profile_errors?.[selectedHost] ?? (selectedHost === "codex" ? state?.profile_error : null);
}

function selectTemplate(templateId, useDefaultName = false) {
  selectedTemplate = state.templates.find((template) => template.id === templateId) || state.templates[0];
  const profile = currentProfile();
  profilePreview = profile?.id === selectedTemplate.id ? profile : null;
  if (profilePreview) {
    elements.displayName.value = profilePreview.display_name;
  } else if (useDefaultName || !elements.displayName.value.trim()) {
    elements.displayName.value = selectedTemplate.default_name;
  }
  updateSaveHint();
  renderTemplates();
  renderPreview();
}

function renderTemplates() {
  elements.templateGrid.replaceChildren(...state.templates.map(createTemplateCard));
}

function renderPreview() {
  if (!selectedTemplate) return;
  const details = profilePreview || selectedTemplate;
  const name = elements.displayName.value.trim() || selectedTemplate.default_name;
  setText(elements.previewName, name);
  setText(elements.avatarLetter, name.slice(0, 1) || "伴");
  setText(elements.previewTraits, details.traits.join(" · "));
  setText(elements.previewSpeech, details.speaking_style);
  setText(elements.previewAppearance, details.visual?.appearance || details.appearance);
  setText(elements.previewStyle, details.visual?.default_style || details.default_style);
  const startingText = elements.startingMode.value === "familiar"
    ? "像已经熟悉一阵"
    : "从自然认识开始";
  const direction = elements.romanceEnabled.checked
    ? "，允许逐步发展为恋爱式陪伴"
    : "，保持非恋爱式陪伴";
  setText(elements.previewRelationship, `${startingText}${direction}`);
}

function updateSaveHint() {
  const profile = currentProfile();
  const profileError = currentProfileError();
  if (profileError) {
    setText(elements.saveHint, profileError);
  } else if (profile && selectedTemplate?.id !== profile.id) {
    setText(elements.saveHint, "切换风格并保存后，会用新模板替换现有人格的高级字段。");
  } else if (profile) {
    setText(elements.saveHint, "修改称呼会保留现有高级字段，并检查版本避免覆盖其他窗口的新内容。");
  } else {
    setText(elements.saveHint, "配置只保存在这台电脑的私有目录。");
  }
}

function hostDescription(host) {
  const family = host.family === "event" ? "消息型工具" : "桌面任务工具";
  if (host.method === "native_cli") {
    return `${family} · 使用 ${host.name} 原生安装器`;
  }
  return `${family} · 安装独立 Skill`;
}

function createHostCard(host) {
  const card = document.createElement("article");
  card.className = "host-card";
  const icon = document.createElement("div");
  icon.className = "host-card__icon";
  setText(icon, host.name.slice(0, 1));
  const copy = document.createElement("div");
  const title = document.createElement("h3");
  setText(title, host.name);
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
    setText(button, "查看安装方案");
    button.addEventListener("click", () => openInstallDialog(host));
  }
  card.append(icon, copy, button);
  return card;
}

function renderHosts() {
  elements.hostGrid.replaceChildren(...state.hosts.map(createHostCard));
}

function renderPhotoModes() {
  const modes = state.photo_modes_by_host?.[selectedHost] || state.photo_modes;
  const nativeMode = modes?.codex_native;
  const strictMode = modes?.openai_strict;
  if (!nativeMode || !strictMode) return;
  setText(elements.photoHostLabel, `${hostNames[selectedHost]} 图片能力`);
  const heading = {
    codex: "两种模式，按需选择",
    openclaw: "两种模式，按需选择",
    hermes: "固定形象，配置一次即可",
    claude: "当前先保留安全规划",
  };
  const intro = {
    codex: "平时可用 Codex 原生能力快速预览；需要固定人物和明确 high 画质时，再选择严格模式。",
    openclaw: "可以先用宿主原生能力快速试拍；想固定人物形象时，再配置严格模式。",
    hermes: "当前通过严格模式固定人物并生成照片，成图只交给本次入站会话。",
    claude: "当前版本不会冒充图片工具执行生图；人格聊天和原有问题解决能力不受影响。",
  };
  setText(elements.photoModeHeading, heading[selectedHost]);
  setText(elements.photoIntro, intro[selectedHost]);
  elements.photoModes.setAttribute("aria-label", `${hostNames[selectedHost]} 图片模式`);
  setText(elements.nativeModeTitle, nativeMode.title);
  setText(elements.nativeModeStatus, nativeMode.status);
  setText(elements.nativeModeDescription, nativeMode.description);
  setText(elements.strictModeTitle, strictMode.title);
  setText(elements.strictModeStatus, strictMode.status);
  setText(elements.strictModeDescription, strictMode.description);
  setText(
    elements.referenceStatus,
    modes.reference_configured ? "配置已绑定固定人物参考" : "尚未固定人物原型",
  );
  const help = {
    codex: "在当前任务里说“照片：画面描述”即可。严格模式会先确认本次计费与数据去向。",
    openclaw: "可先用原生模式快速试拍；需要固定形象时使用严格模式，结果只回当前入站会话。",
    hermes: "严格模式生成后只通过当前回复交给 Hermes Gateway，不填写或猜测联系人。",
    claude: "当前只生成安全的照片计划，等宿主附件契约明确后再开放执行。",
  };
  setText(elements.photoHelp, help[selectedHost]);
}

function activateHost(host) {
  selectedHost = Object.hasOwn(hostNames, host) ? host : "codex";
  elements.profileHost.value = selectedHost;
  const profile = currentProfile();
  currentVersion = profile?.version || null;
  const matchingTemplate = state.templates.find((template) => template.id === profile?.id);
  selectedTemplate = matchingTemplate
    || (profile ? {
      id: profile.id,
      name: "自定义配置",
      default_name: profile.display_name,
      traits: profile.traits,
      speaking_style: profile.speaking_style,
      appearance: profile.visual.appearance,
      default_style: profile.visual.default_style,
    } : null)
    || state.templates.find((template) => template.recommended)
    || state.templates[0];
  profilePreview = profile || null;
  elements.displayName.value = profile?.display_name || selectedTemplate.default_name;
  elements.startingMode.value = profile?.relationship?.starting_mode || "natural";
  elements.romanceEnabled.checked = profile?.relationship?.romance_enabled === true;
  setText(elements.profileStatus, profile ? (matchingTemplate ? "已配置" : "自定义配置") : "尚未配置");
  setText(
    elements.hostChoiceHint,
    `${hostNames[selectedHost]} 使用独立配置，不会覆盖其他工具里的陪伴对象。`,
  );
  updateSaveHint();
  renderTemplates();
  renderPreview();
  renderPhotoModes();
}

function openInstallDialog(host) {
  pendingHost = host.host;
  setText(elements.dialogTitle, `安装到 ${host.name}`);
  const destination = host.destination ? `目标位置：${host.destination}` : "将使用宿主自己的安全安装机制。";
  setText(elements.dialogSummary, `${hostDescription(host)}。${destination} 只会加入通用 Skill，不会修改全局人格或读取现有私人资料。`);
  elements.installDialog.showModal();
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

  activateHost(selectedHost);
  renderHosts();
}

elements.displayName.addEventListener("input", renderPreview);
elements.startingMode.addEventListener("change", renderPreview);
elements.romanceEnabled.addEventListener("change", renderPreview);
elements.profileHost.addEventListener("change", () => activateHost(elements.profileHost.value));

elements.saveProfile.addEventListener("click", async () => {
  const displayName = elements.displayName.value.trim();
  if (!selectedTemplate || !displayName) {
    showToast("请先选择风格并填写称呼");
    elements.displayName.focus();
    return;
  }
  elements.saveProfile.disabled = true;
  setText(elements.saveProfile, "保存中…");
  try {
    const body = {
      host: selectedHost,
      template_id: selectedTemplate.id,
      display_name: displayName,
      starting_mode: elements.startingMode.value,
      romance_enabled: elements.romanceEnabled.checked,
    };
    if (currentVersion) body.expected_version = currentVersion;
    const payload = await api("/api/profile", {
      method: "POST",
      body: JSON.stringify(body),
    });
    currentVersion = payload.profile.version;
    state.profiles[selectedHost] = payload.profile;
    if (selectedHost === "codex") state.profile = payload.profile;
    profilePreview = payload.profile;
    setText(elements.profileStatus, "已保存");
    updateSaveHint();
    renderPreview();
    showToast("陪伴对象已安全保存");
  } catch (error) {
    showToast(error.message);
    if (error.status === 409) await loadState();
  } finally {
    elements.saveProfile.disabled = false;
    setText(elements.saveProfile, "保存陪伴对象");
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
    showToast("安装完成，请在新会话中启用 virtual-companion");
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
