import { el, getJSON, thumbURL } from "/ui/lib.js";

const form = document.getElementById("lessonForm");
const inputView = document.getElementById("inputView");
const projectView = document.getElementById("projectView");
const lessonText = document.getElementById("lessonText");
const lessonTitle = document.getElementById("lessonTitle");
const createButton = document.getElementById("createButton");
const formError = document.getElementById("formError");
const planButton = document.getElementById("planButton");
const shotGrid = document.getElementById("shotGrid");
const promptDialog = document.getElementById("promptDialog");
const promptDialogBody = document.getElementById("promptDialogBody");
let activeProject = new URLSearchParams(location.search).get("project");
let currentData = null;

function apiErrorMessage(error) {
  return error instanceof Error ? error.message : String(error || "请求失败");
}

async function requestJSON(url, options = {}) {
  const response = await fetch(url, {
    ...options,
    headers: { "Content-Type": "application/json", ...(options.headers || {}) },
  });
  const body = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(body.detail || `${response.status} ${response.statusText}`);
  return body;
}

function setBusy(button, busy, label) {
  button.disabled = busy;
  if (!button.dataset.original) button.dataset.original = button.textContent;
  button.textContent = busy ? label : button.dataset.original;
}

async function loadConfig() {
  const config = await getJSON("/api/lesson-studio/config");
  const badge = document.getElementById("providerStatus");
  badge.textContent = config.provider_ready ? "百炼云 · 已就绪" : "百炼云 Key 未载入";
  badge.classList.toggle("ready", config.provider_ready);
  badge.classList.toggle("blocked", !config.provider_ready);
  const videoOutput = config.video_output || {};
  const duration = document.getElementById("videoDuration");
  if (duration && videoOutput.duration_min_seconds && videoOutput.duration_max_seconds) {
    duration.textContent = `视频 · ${config.models.video} · 每镜 ${videoOutput.duration_min_seconds}–${videoOutput.duration_max_seconds} 秒`;
    duration.title = `默认 ${videoOutput.duration_default_seconds} 秒，当前分镜规划 ${videoOutput.planned_scene_seconds} 秒，${videoOutput.resolutions.join(" / ")}，${videoOutput.fps} fps`;
  }
}

function updateCharCount() {
  document.getElementById("charCount").textContent = `${lessonText.value.length} / 20000`;
}

function setProjectUrl(projectId) {
  activeProject = projectId;
  const url = new URL(location.href);
  url.searchParams.set("project", projectId);
  history.replaceState({}, "", url);
}

const STAGES = [
  ["source", "课文", "课文输入"],
  ["storyboard", "分镜", "故事与分镜"],
  ["images", "首帧", "首帧审图"],
  ["video", "视频", "镜头视频"],
  ["compose", "合成", "字幕与合成"],
];

const STORY_BEATS = new Map([
  ["hook", "开场钩子"],
  ["setup", "背景铺垫"],
  ["tension", "矛盾升级"],
  ["turning_point", "转折"],
  ["development", "发展"],
  ["payoff", "高潮回报"],
  ["reflection", "收束回望"],
]);

function storyBeatLabel(value) {
  return STORY_BEATS.get(value) || "镜头";
}

function stageIndex(workflow) {
  const stage = workflow.stage || "source_ready";
  if (stage.includes("image")) return 2;
  if (stage.includes("video")) return 3;
  if (["storyboard_ready", "planning_storyboard"].includes(stage)) return 1;
  if (["compose", "completed"].includes(stage)) return 4;
  return 0;
}

function renderRail(workflow) {
  const rail = document.getElementById("stageRail");
  rail.innerHTML = "";
  const active = stageIndex(workflow);
  STAGES.forEach(([id, name, label], index) => {
    const status = index < active ? "done" : index === active ? "active" : "";
    rail.append(el("li", { class: status },
      el("b", {}, `${String(index + 1).padStart(2, "0")} · ${name}`),
      el("span", {}, label)));
  });
}

function renderStatus(workflow) {
  const status = document.getElementById("studioStatus");
  status.textContent = workflow.message || "等待下一步操作。";
  status.classList.toggle("busy", workflow.status === "in_progress");
  status.classList.toggle("error", workflow.status === "error");
}

function promptBlock(label, text) {
  return el("section", { class: "dialog-block" },
    el("h3", {}, label),
    el("pre", {}, text || "尚未生成。"));
}

function openPrompts(card) {
  const video = card.video_prompt || {};
  const beats = Array.isArray(video.temporal_beats) ? video.temporal_beats : [];
  promptDialogBody.innerHTML = "";
  promptDialogBody.append(
    el("header", { class: "dialog-head" },
      el("p", { class: "studio-eyebrow" }, `${card.id} · ${storyBeatLabel(card.story_beat)}`),
      el("h2", {}, card.description || card.id)),
    card.story_contribution ? promptBlock("本镜头的叙事作用", card.story_contribution) : null,
    promptBlock("图片生成提示词", (card.visual && card.visual.prompt) || card.image_prompt_preview),
    promptBlock("视频生成提示词", video.prompt),
    promptBlock("负面提示词", video.negative_prompt),
    beats.length ? el("section", { class: "dialog-block" },
      el("h3", {}, "时间动作节拍"),
      el("div", { class: "dialog-beats" }, beats.map((beat) =>
        el("div", { class: "dialog-beat" },
          el("span", {}, `${beat.start_seconds}–${beat.end_seconds} 秒`),
          el("p", {}, beat.action || ""))))) : null,
  );
  promptDialog.showModal();
}

function sceneCard(card, index, providerReady) {
  const media = el("div", { class: "shot-media" });
  if (card.visual && card.visual.exists && card.visual.type === "image") {
    media.append(el("img", {
      src: thumbURL(activeProject, card.visual.path, 640),
      alt: `${card.id} 已生成的首帧`,
      loading: "lazy",
    }));
  } else {
    media.append(el("div", { class: "shot-placeholder" }, card.description || "等待分镜画面"));
  }
  media.append(
    el("span", { class: "shot-number" }, `镜头 ${String(index + 1).padStart(2, "0")}`),
    el("span", { class: "shot-model" }, card.visual ? "已生成" : "计划首帧"),
  );

  const generate = el("button", {
    class: "shot-generate",
    type: "button",
    disabled: providerReady ? null : "disabled",
    title: providerReady
      ? "调用 qwen-image-2.0-pro 生成一张首帧"
      : "当前 Backlot 进程未载入 DASHSCOPE_API_KEY",
  }, card.visual ? "重新生成" : "生成首帧");
  generate.addEventListener("click", async () => {
    setBusy(generate, true, "生成中…");
    renderTemporaryStatus(`正在为 ${card.id} 调用 qwen-image-2.0-pro；一次一张，无模型回退。`, true);
    try {
      await requestJSON(`/api/lesson-studio/projects/${encodeURIComponent(activeProject)}/scenes/${encodeURIComponent(card.id)}/image`, { method: "POST" });
      await loadProject();
    } catch (error) {
      renderTemporaryStatus(apiErrorMessage(error), false, true);
    } finally {
      setBusy(generate, false, "");
    }
  });

  return el("article", { class: "studio-shot", "data-scene-id": card.id },
    media,
    el("div", { class: "shot-body" },
      el("div", { class: "shot-meta" },
        el("span", {}, storyBeatLabel(card.story_beat)),
        el("span", {}, `${Math.round(card.duration_seconds || 0)} 秒 · ${card.takes.length || 0} 个版本`)),
      el("h4", {}, card.description || card.id),
      card.story_contribution ? el("p", { class: "shot-contribution" }, card.story_contribution) : null,
      el("div", { class: "shot-actions" },
        generate,
        el("button", { class: "shot-prompts", type: "button", onclick: () => openPrompts(card) }, "查看提示词")),
      el("p", { class: "shot-contribution" }, "qwen-image-2.0-pro · 2688×1536 · 免费额度用完即停"),
    ));
}

function renderTemporaryStatus(message, busy = false, error = false) {
  const status = document.getElementById("studioStatus");
  status.textContent = message;
  status.classList.toggle("busy", busy);
  status.classList.toggle("error", error);
}

function renderProject(data) {
  currentData = data;
  inputView.hidden = true;
  projectView.hidden = false;
  document.getElementById("projectId").textContent = data.project_id;
  document.getElementById("projectTitle").textContent = data.title;
  document.getElementById("openBoard").href = `/p/${encodeURIComponent(data.project_id)}`;
  renderRail(data.workflow || {});
  renderStatus(data.workflow || {});

  const storyboard = data.board && data.board.storyboard;
  const scenes = storyboard && Array.isArray(storyboard.scenes) ? storyboard.scenes : [];
  const arc = storyboard && storyboard.visual_story_arc;
  const summary = document.getElementById("storySummary");
  if (arc) {
    summary.hidden = false;
    summary.innerHTML = "";
    summary.append(
      el("h3", {}, "视觉故事"),
      el("div", {},
        el("p", {}, arc.theme || ""),
        el("p", { class: "shot-contribution" }, arc.visual_premise || "")));
  } else {
    summary.hidden = true;
  }

  planButton.hidden = scenes.length > 0;
  document.getElementById("shotsHead").hidden = scenes.length === 0;
  shotGrid.innerHTML = "";
  scenes.forEach((card, index) => shotGrid.append(sceneCard(card, index, data.provider_ready)));
}

async function loadProject() {
  if (!activeProject) return;
  const data = await getJSON(`/api/lesson-studio/projects/${encodeURIComponent(activeProject)}`);
  renderProject(data);
}

async function runPlan() {
  setBusy(planButton, true, "qwen3.7-plus 规划中…");
  renderTemporaryStatus("正在阅读全文并设计文章级视觉故事，通常需要 1–2 分钟。", true);
  try {
    await requestJSON(`/api/lesson-studio/projects/${encodeURIComponent(activeProject)}/plan`, { method: "POST" });
    await loadProject();
  } catch (error) {
    renderTemporaryStatus(apiErrorMessage(error), false, true);
  } finally {
    setBusy(planButton, false, "");
  }
}

form.addEventListener("submit", async (event) => {
  event.preventDefault();
  formError.textContent = "";
  if (lessonText.value.trim().length < 40) {
    formError.textContent = "请至少输入 40 个字符的完整英文课文。";
    return;
  }
  setBusy(createButton, true, "创建项目…");
  try {
    const created = await requestJSON("/api/lesson-studio/projects", {
      method: "POST",
      body: JSON.stringify({ title: lessonTitle.value, source_text: lessonText.value }),
    });
    setProjectUrl(created.project_id);
    await loadProject();
    await runPlan();
  } catch (error) {
    formError.textContent = apiErrorMessage(error);
  } finally {
    setBusy(createButton, false, "");
  }
});

planButton.addEventListener("click", runPlan);
lessonText.addEventListener("input", updateCharCount);
document.getElementById("newProject").addEventListener("click", () => {
  const url = new URL(location.href);
  url.searchParams.delete("project");
  location.href = url.toString();
});
promptDialog.addEventListener("click", (event) => {
  if (event.target === promptDialog) promptDialog.close();
});

updateCharCount();
loadConfig().catch(() => {
  document.getElementById("providerStatus").textContent = "配置检查失败";
});
if (activeProject) loadProject().catch((error) => {
  inputView.hidden = false;
  projectView.hidden = true;
  formError.textContent = apiErrorMessage(error);
});
