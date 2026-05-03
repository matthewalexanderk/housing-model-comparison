const state = {
  levels: [],
  currentIndex: 0,
  progress: new Set(),
};

const elements = {
  levelList: document.getElementById("level-list"),
  levelTitle: document.getElementById("level-title"),
  levelCategory: document.getElementById("level-category"),
  levelXp: document.getElementById("level-xp"),
  levelPrompt: document.getElementById("level-prompt"),
  levelDocs: document.getElementById("level-docs"),
  levelHints: document.getElementById("level-hints"),
  codeEditor: document.getElementById("code-editor"),
  runButton: document.getElementById("run-button"),
  nextButton: document.getElementById("next-button"),
  stdout: document.getElementById("stdout"),
  messages: document.getElementById("messages"),
  levelsComplete: document.getElementById("levels-complete"),
  levelsTotal: document.getElementById("levels-total"),
  xpTotal: document.getElementById("xp-total"),
};

function loadProgress() {
  try {
    const saved = JSON.parse(localStorage.getItem("pythonQuestProgress") || "[]");
    state.progress = new Set(saved);
  } catch (error) {
    console.warn("Unable to read saved progress, resetting progress data.");
    state.progress = new Set();
  }
}

function saveProgress() {
  localStorage.setItem("pythonQuestProgress", JSON.stringify([...state.progress]));
}

function updateStats() {
  const completed = state.levels.filter((level) => state.progress.has(level.id));
  const xp = completed.reduce((total, level) => total + (level.xp || 0), 0);
  elements.levelsComplete.textContent = String(completed.length);
  elements.levelsTotal.textContent = String(state.levels.length);
  elements.xpTotal.textContent = String(xp);
}

function clearOutput() {
  elements.stdout.textContent = "";
  elements.messages.innerHTML = "";
}

function renderLevelList() {
  elements.levelList.innerHTML = "";
  state.levels.forEach((level, index) => {
    const listItem = document.createElement("li");
    const button = document.createElement("button");
    button.textContent = `${index + 1}. ${level.title}`;
    button.classList.toggle("active", index === state.currentIndex);
    button.classList.toggle("completed", state.progress.has(level.id));
    button.addEventListener("click", () => selectLevel(index));
    listItem.appendChild(button);
    elements.levelList.appendChild(listItem);
  });
}

function renderDocs(docs = []) {
  elements.levelDocs.innerHTML = "";
  docs.forEach((doc) => {
    const item = document.createElement("li");
    const link = document.createElement("a");
    link.href = doc.url;
    link.target = "_blank";
    link.rel = "noreferrer";
    link.textContent = doc.title;
    item.appendChild(link);
    elements.levelDocs.appendChild(item);
  });
}

function renderHints(hints = []) {
  elements.levelHints.innerHTML = "";
  hints.forEach((hint) => {
    const item = document.createElement("li");
    item.textContent = hint;
    elements.levelHints.appendChild(item);
  });
}

function renderMessages(messages = [], kind = "success") {
  elements.messages.innerHTML = "";
  messages.forEach((message) => {
    const div = document.createElement("div");
    div.className = `message ${kind}`;
    div.textContent = message;
    elements.messages.appendChild(div);
  });
}

function selectLevel(index) {
  const level = state.levels[index];
  if (!level) return;
  state.currentIndex = index;
  elements.levelTitle.textContent = level.title;
  elements.levelCategory.textContent = level.category || "";
  elements.levelXp.textContent = `${level.xp || 0} XP`;
  elements.levelPrompt.textContent = level.prompt;
  elements.codeEditor.value = level.starter_code || "";
  renderDocs(level.docs || []);
  renderHints(level.hints || []);
  renderLevelList();
  clearOutput();
}

async function submitCode() {
  const level = state.levels[state.currentIndex];
  if (!level) return;
  clearOutput();
  elements.runButton.disabled = true;
  try {
    const response = await fetch("/api/submit", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        level_id: level.id,
        code: elements.codeEditor.value,
      }),
    });
    const result = await response.json();
    if (!result.ok) {
      elements.stdout.textContent = result.stdout || "";
      renderMessages([result.error || "Something went wrong."], "error");
      return;
    }
    elements.stdout.textContent = result.stdout || "";
    const kind = result.passed ? "success" : "error";
    renderMessages(result.messages || [], kind);
    if (result.passed) {
      state.progress.add(level.id);
      saveProgress();
      updateStats();
      renderLevelList();
    }
  } catch (error) {
    renderMessages(["Unable to reach the server. Is the Python app running?"], "error");
  } finally {
    elements.runButton.disabled = false;
  }
}

function selectNextLevel() {
  const nextIndex = Math.min(state.currentIndex + 1, state.levels.length - 1);
  selectLevel(nextIndex);
}

async function init() {
  loadProgress();
  const response = await fetch("/api/levels");
  const data = await response.json();
  state.levels = data;
  updateStats();
  renderLevelList();
  selectLevel(0);
}

elements.runButton.addEventListener("click", submitCode);
elements.nextButton.addEventListener("click", selectNextLevel);

init();
