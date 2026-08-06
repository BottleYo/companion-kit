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
  identityPanelStatus: document.querySelector("#identityPanelStatus"),
  identityDropzone: document.querySelector("#identityDropzone"),
  identityFile: document.querySelector("#identityFile"),
  identityConsentRow: document.querySelector("#identityConsentRow"),
  identityConsent: document.querySelector("#identityConsent"),
  stageIdentity: document.querySelector("#stageIdentity"),
  confirmIdentity: document.querySelector("#confirmIdentity"),
  resetIdentity: document.querySelector("#resetIdentity"),
  identityRequirement: document.querySelector("#identityRequirement"),
  identityPreview: document.querySelector("#identityPreview"),
  identityPreviewPlaceholder: document.querySelector("#identityPreviewPlaceholder"),
  identityPlaceholderTitle: document.querySelector("#identityPlaceholderTitle"),
  identityPlaceholderCopy: document.querySelector("#identityPlaceholderCopy"),
  identityCandidateStatus: document.querySelector("#identityCandidateStatus"),
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
let identityPng = null;
let stagedIdentity = null;
let identityRenderSequence = 0;

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
    error.payload = payload;
    throw error;
  }
  return payload;
}

async function uploadIdentityPng(blob) {
  const response = await window.fetch("/api/identity/candidate", {
    method: "POST",
    headers: {
      "X-Companion-Token": token,
      "X-Companion-Image-Consent": "adult-authorized",
      "Content-Type": "image/png",
    },
    body: blob,
  });
  const payload = await response.json().catch(() => ({ error: "面板返回了无法读取的结果" }));
  if (!response.ok) {
    const error = new Error(payload.error || "参考图上传失败");
    error.status = response.status;
    error.payload = payload;
    throw error;
  }
  return payload;
}

function readAsDataUrl(fileOrBlob) {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => resolve(String(reader.result || ""));
    reader.onerror = () => reject(new Error("这张图片无法读取"));
    reader.readAsDataURL(fileOrBlob);
  });
}

function loadImage(dataUrl) {
  return new Promise((resolve, reject) => {
    const image = new Image();
    image.onload = () => resolve(image);
    image.onerror = () => reject(new Error("这张图片无法解码"));
    image.src = dataUrl;
  });
}

function canvasPng(canvas) {
  return new Promise((resolve, reject) => {
    canvas.toBlob((blob) => {
      if (blob) resolve(blob);
      else reject(new Error("这张图片无法转换为 PNG"));
    }, "image/png");
  });
}

async function prepareIdentityFile(file) {
  const allowed = new Set(["image/png", "image/jpeg", "image/webp"]);
  if (!allowed.has(file.type)) throw new Error("请选择 PNG、JPG 或 WebP 图片");
  if (file.size <= 0 || file.size > 10 * 1024 * 1024) {
    throw new Error("图片需要小于 10 MB");
  }
  const original = await readAsDataUrl(file);
  const image = await loadImage(original);
  if (!image.naturalWidth || !image.naturalHeight) throw new Error("图片尺寸无效");
  const scale = Math.min(1, 2048 / Math.max(image.naturalWidth, image.naturalHeight));
  const width = Math.max(1, Math.round(image.naturalWidth * scale));
  const height = Math.max(1, Math.round(image.naturalHeight * scale));
  const canvas = document.createElement("canvas");
  canvas.width = width;
  canvas.height = height;
  const context = canvas.getContext("2d", { alpha: false });
  if (!context) throw new Error("浏览器无法处理这张图片");
  context.fillStyle = "#ffffff";
  context.fillRect(0, 0, width, height);
  context.drawImage(image, 0, 0, width, height);
  const blob = await canvasPng(canvas);
  if (blob.size > 12 * 1024 * 1024) throw new Error("转换后的图片仍然过大，请换一张更小的图");
  return { blob, preview: canvas.toDataURL("image/png"), width, height };
}

function showIdentityPreview(src) {
  elements.identityPreview.src = src;
  elements.identityPreview.hidden = false;
  elements.identityPreviewPlaceholder.hidden = true;
}

function showIdentityPlaceholder(title, copy) {
  elements.identityPreview.removeAttribute("src");
  elements.identityPreview.hidden = true;
  elements.identityPreviewPlaceholder.hidden = false;
  setText(elements.identityPlaceholderTitle, title);
  setText(elements.identityPlaceholderCopy, copy);
}

function resetIdentitySelection() {
  identityPng = null;
  stagedIdentity = null;
  elements.identityFile.value = "";
  elements.identityConsent.disabled = false;
  elements.stageIdentity.hidden = false;
  elements.stageIdentity.disabled = true;
  setText(elements.stageIdentity, "先选择一张图");
  elements.confirmIdentity.hidden = true;
  elements.confirmIdentity.disabled = false;
  setText(elements.confirmIdentity, "设为固定主脸");
  elements.resetIdentity.hidden = true;
}

async function loadConfirmedIdentityPreview(sequence) {
  try {
    const response = await window.fetch("/api/identity/primary", {
      headers: { "X-Companion-Token": token },
    });
    if (!response.ok) throw new Error("人物主脸暂时无法读取");
    const preview = await readAsDataUrl(await response.blob());
    if (sequence === identityRenderSequence) showIdentityPreview(preview);
  } catch (error) {
    if (sequence !== identityRenderSequence) return;
    showIdentityPlaceholder("主脸已经确认", "参考暂时无法预览；人物生图会停止，不会绕过固定身份换脸。");
  }
}

function renderIdentitySetup(profile) {
  const sequence = ++identityRenderSequence;
  resetIdentitySelection();
  elements.identityConsent.checked = false;
  const locked = profile?.visual_identity?.status === "locked" && profile.visual_identity.reference_count === 1;
  elements.identityDropzone.hidden = locked;
  elements.identityConsentRow.hidden = locked;
  elements.identityFile.disabled = !profile || locked;
  elements.identityConsent.disabled = !profile || locked;
  elements.identityDropzone.classList.toggle("identity-dropzone--disabled", !profile || locked);

  if (!profile) {
    setText(elements.identityPanelStatus, "等待 Persona");
    setText(elements.identityRequirement, "请先保存上面的 Persona。人物照片是可选项，不设置也能先聊天和解决问题。");
    setText(elements.identityCandidateStatus, "上传后仍只是候选，只有你明确确认才会固定。");
    showIdentityPlaceholder("先认识 TA", "保存 Persona 后，这里就能直接上传一张人物参考图。");
    return;
  }
  if (locked) {
    elements.stageIdentity.hidden = true;
    elements.resetIdentity.hidden = true;
    setText(elements.identityPanelStatus, "主脸已确认");
    setText(elements.identityRequirement, "当前面板不会静默替换已经固定的脸。以后如需更换，会走单独的身份轮换流程。");
    setText(elements.identityCandidateStatus, "这张参考只固定人物身份；发型、妆容、服饰和场景仍会随每次照片变化。");
    showIdentityPlaceholder("正在读取固定主脸", "图片只会通过本地授权接口显示，不会放进公开页面地址。");
    void loadConfirmedIdentityPreview(sequence);
    return;
  }

  setText(elements.identityPanelStatus, "可以上传，也可以跳过");
  setText(elements.identityRequirement, "选图不是必填项。没想好就先跳过，以后在 Codex 聊天时上传或让 TA 生成候选也可以。");
  setText(elements.identityCandidateStatus, "上传后仍只是候选，只有你明确确认才会固定。");
  showIdentityPlaceholder("还没有人物参考", "建议选脸部清楚、没有重度滤镜的成年人物或虚构形象；一张就够开始。");
}

async function selectIdentityFile(file) {
  if (!state?.profile) {
    showToast("请先保存 Persona，再选择人物参考图");
    return;
  }
  if (state.profile.visual_identity.status === "locked") {
    showToast("人物主脸已经固定，不能在这里直接覆盖");
    return;
  }
  setText(elements.identityCandidateStatus, "正在浏览器里整理图片…");
  elements.stageIdentity.disabled = true;
  try {
    const prepared = await prepareIdentityFile(file);
    identityPng = prepared.blob;
    stagedIdentity = null;
    showIdentityPreview(prepared.preview);
    elements.stageIdentity.hidden = false;
    elements.stageIdentity.disabled = !elements.identityConsent.checked;
    setText(elements.stageIdentity, "上传为候选");
    elements.confirmIdentity.hidden = true;
    elements.resetIdentity.hidden = false;
    setText(elements.identityCandidateStatus, `${prepared.width} × ${prepared.height} · 还只在浏览器预览，上传后也不会自动固定。`);
  } catch (error) {
    identityPng = null;
    showToast(error.message);
    setText(elements.identityCandidateStatus, "图片没有进入候选槽，请换一张再试。");
  }
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
    renderIdentitySetup(null);
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
  const pack = state?.photo_modes?.identity_pack;
  if (!locked) {
    setText(elements.referenceStatus, "尚未固定人物原型；第一次要照片时再决定");
  } else if (!pack?.ready) {
    setText(elements.referenceStatus, "身份参考暂时不可用；人物生图会暂停，不会偷偷换脸");
  } else if (pack.level === "basic") {
    setText(elements.referenceStatus, "形象稳定性：基础 · 主脸已确认，普通自拍已经可以复用");
  } else {
    const additions = [];
    if (pack.roles.includes("profile_face")) additions.push("侧脸");
    if (pack.roles.includes("body_shape")) additions.push("体型");
    setText(elements.referenceStatus, `形象稳定性：已增强 · 已补充${additions.join("和")}`);
  }
  renderIdentitySetup(profile);
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

elements.identityConsent.addEventListener("change", () => {
  if (stagedIdentity) return;
  elements.stageIdentity.disabled = !identityPng || !elements.identityConsent.checked;
  if (!identityPng) {
    setText(elements.stageIdentity, "先选择一张图");
  } else if (elements.identityConsent.checked) {
    setText(elements.stageIdentity, "上传为候选");
  } else {
    setText(elements.stageIdentity, "请先确认图片使用权");
  }
});

elements.identityFile.addEventListener("change", () => {
  const [file] = elements.identityFile.files || [];
  if (file) void selectIdentityFile(file);
});

elements.identityDropzone.addEventListener("keydown", (event) => {
  if (event.key !== "Enter" && event.key !== " ") return;
  if (elements.identityFile.disabled) return;
  event.preventDefault();
  elements.identityFile.click();
});

for (const eventName of ["dragenter", "dragover"]) {
  elements.identityDropzone.addEventListener(eventName, (event) => {
    event.preventDefault();
    if (!elements.identityFile.disabled) elements.identityDropzone.classList.add("identity-dropzone--active");
  });
}

for (const eventName of ["dragleave", "drop"]) {
  elements.identityDropzone.addEventListener(eventName, (event) => {
    event.preventDefault();
    elements.identityDropzone.classList.remove("identity-dropzone--active");
  });
}

elements.identityDropzone.addEventListener("drop", (event) => {
  if (elements.identityFile.disabled) return;
  const [file] = event.dataTransfer?.files || [];
  if (file) void selectIdentityFile(file);
});

elements.stageIdentity.addEventListener("click", async () => {
  if (!identityPng || !elements.identityConsent.checked || !state?.profile) return;
  elements.stageIdentity.disabled = true;
  setText(elements.stageIdentity, "正在安全保存…");
  try {
    const payload = await uploadIdentityPng(identityPng);
    stagedIdentity = payload.candidate;
    elements.identityFile.disabled = true;
    elements.identityDropzone.classList.add("identity-dropzone--disabled");
    elements.identityConsent.disabled = true;
    elements.stageIdentity.hidden = true;
    elements.confirmIdentity.hidden = false;
    elements.resetIdentity.hidden = false;
    setText(elements.identityCandidateStatus, "已经进入本地候选槽，但还没有固定。确认长相没问题后，再按一次“设为固定主脸”。");
  } catch (error) {
    elements.stageIdentity.disabled = false;
    setText(elements.stageIdentity, "上传为候选");
    showToast(error.message);
  }
});

elements.confirmIdentity.addEventListener("click", async () => {
  if (!stagedIdentity) return;
  elements.confirmIdentity.disabled = true;
  setText(elements.confirmIdentity, "正在固定…");
  try {
    await api("/api/identity/confirm", {
      method: "POST",
      body: JSON.stringify({
        candidate_id: stagedIdentity.candidate_id,
        profile_version: stagedIdentity.profile_version,
        confirm: true,
      }),
    });
    showToast("主脸已经固定，以后换场景也不会随便换人");
    await loadState();
  } catch (error) {
    if (error.payload?.retry_profile_version) {
      stagedIdentity.profile_version = error.payload.retry_profile_version;
      setText(elements.identityCandidateStatus, "Persona 刚刚发生了变化。候选图还在，请再确认一次就好。");
    }
    showToast(error.message);
  } finally {
    elements.confirmIdentity.disabled = false;
    setText(elements.confirmIdentity, "设为固定主脸");
  }
});

elements.resetIdentity.addEventListener("click", () => {
  renderIdentitySetup(state?.profile || null);
  setText(elements.identityCandidateStatus, "已退出当前预览。下次上传会安全替换本地候选槽；没有确认就不会固定成主脸。");
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
