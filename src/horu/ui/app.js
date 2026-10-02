"use strict";

// State shared between Python pushes (window.horu.*) and user interaction.
const state = {
  lines: new Map(), // id -> line object from Python
  selected: null, // the .word element whose popover is open
  prefs: {}, // preferences, loaded from and saved through Python
  ai: { enabled: false, label: null, error: null },
  tutor: { session: null, answer: null, text: "", busy: false }, // the tutor panel's conversation
  nextSession: 1,
};

const $ = (selector) => document.querySelector(selector);
const linesEl = $("#lines");
const popover = $("#popover");
const settings = $("#settings");
const tutor = $("#tutor");

const SENSES_SHOWN = 5;
const KIND_LABEL = { screen: "Screen text" };
const POS_LABEL = { "na-adjective": "な-adjective", "pre-noun": "pre-noun adjectival" };
// Used until Python's saved preferences arrive, and by "Reset to defaults".
const DISPLAY_DEFAULTS = { textSize: 26, furiganaScale: 0.5 };
const AI_SETUP_HINT =
  "AI explanations need a provider. Add one in the [ai] section of config.toml (Claude, OpenAI, " +
  "Gemini, or a free local model with Ollama), then restart Horu. The README has the details. " +
  "Dictionary lookups keep working without it.";

function api() {
  return window.pywebview && window.pywebview.api;
}

function el(tag, className, text) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text !== undefined) node.textContent = text;
  return node;
}

function button(className, text, onClick, title) {
  const node = el("button", className, text);
  node.type = "button";
  if (title) node.title = title;
  node.addEventListener("click", onClick);
  return node;
}

// [[text, reading|null], ...] -> nodes with <ruby> over the kanji.
function furiganaNodes(segments) {
  return segments.map(([text, reading]) => {
    if (!reading) return document.createTextNode(text);
    const ruby = el("ruby");
    ruby.append(text, el("rt", null, reading));
    return ruby;
  });
}

// A small, safe Markdown subset for AI replies: paragraphs, "- " and "1. " lists,
// **bold**, *italic*, and `code`. Everything is escaped first; only these tags are added.
function renderMarkdown(text) {
  const escape = (s) => s.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
  const inline = (s) =>
    escape(s)
      .replace(/\*\*(.+?)\*\*/g, "<strong>$1</strong>")
      .replace(/(^|[^*])\*([^*\s][^*]*?)\*(?!\*)/g, "$1<em>$2</em>")
      .replace(/`([^`]+)`/g, "<code>$1</code>");
  const html = [];
  let list = null; // "ul" | "ol" | null
  const closeList = () => {
    if (list) html.push(`</${list}>`);
    list = null;
  };
  for (const raw of text.split("\n")) {
    const line = raw.trimEnd();
    const bullet = line.match(/^\s*[-*•]\s+(.*)$/);
    const numbered = line.match(/^\s*\d+[.)]\s+(.*)$/);
    if (bullet || numbered) {
      const kind = bullet ? "ul" : "ol";
      if (list !== kind) {
        closeList();
        html.push(`<${kind}>`);
        list = kind;
      }
      html.push(`<li>${inline((bullet || numbered)[1])}</li>`);
    } else if (!line.trim()) {
      closeList();
    } else {
      closeList();
      html.push(`<p>${inline(line.replace(/^#+\s*/, ""))}</p>`);
    }
  }
  closeList();
  return html.join("");
}

// --- Captures and lines ----------------------------------------------------------------

// Opening brackets stick to the word after them; all other punctuation to the word before,
// so a line never starts with 、 or 。 (or ends with 「).
const OPENING = /^[「『（(【〈《［]/;

function renderWords(line, container) {
  container.replaceChildren();
  let unit = null; // a no-wrap group: one word plus the punctuation attached to it
  let openUnit = false;
  line.words.forEach((word, index) => {
    const symbol = word.pos === "symbol";
    const node = el(symbol ? "span" : "button", `word pos-${word.pos}`);
    if (!symbol) {
      node.type = "button";
      node.dataset.line = line.id;
      node.dataset.index = index;
    }
    node.append(...furiganaNodes(word.furigana));

    const opening = symbol && OPENING.test(word.surface);
    if (unit && (openUnit || (symbol && !opening))) {
      unit.append(node);
    } else {
      unit = el("span", "unit");
      unit.append(node);
      container.append(unit);
    }
    openUnit = opening || (openUnit && symbol);
  });
}

function renderLine(line) {
  const row = el("article", `line ${line.kind}`);
  row.dataset.id = line.id;

  const meta = el("div", "line-meta");
  if (line.speaker) meta.append(el("span", "speaker", line.speaker));
  if (KIND_LABEL[line.kind]) meta.append(el("span", "kind-label", KIND_LABEL[line.kind]));
  meta.append(
    el("span", "spacer"),
    button("line-btn", "Explain", () => explainLine(line), "Break this line down with AI"),
    button("line-btn", "Edit", () => startEdit(line, row), "Fix OCR mistakes in this line"),
  );

  const text = el("div", "text");
  text.lang = "ja";
  renderWords(line, text);
  row.append(meta, text);
  if (line.kind === "dialogue" && state.ai.enabled) row.append(translationRow(line));
  return row;
}

// One capture (one hotkey press) is one card; its lines are rows inside it, in screen order.
function renderCapture(capture) {
  const card = el("section", "capture");
  const head = el("header", "capture-head");
  const count = capture.lines.length === 1 ? "1 line" : `${capture.lines.length} lines`;
  head.append(el("span", "capture-num", `Capture ${capture.id}`), el("span", null, capture.time), el("span", null, count));
  card.append(head);
  for (const line of capture.lines) card.append(renderLine(line));
  return card;
}

function startEdit(line, row) {
  closePopover();
  const text = row.querySelector(".text");
  const editor = el("div", "editor");
  const area = el("textarea");
  area.lang = "ja";
  area.value = line.text;
  const actions = el("div", "editor-actions");
  const cancel = el("button", null, "Cancel");
  const save = el("button", "primary", "Save");
  actions.append(cancel, save);
  editor.append(area, actions);
  text.hidden = true;
  text.after(editor);
  area.focus();

  const finish = () => {
    editor.remove();
    text.hidden = false;
  };
  cancel.addEventListener("click", finish);
  save.addEventListener("click", async () => {
    const value = area.value.trim();
    if (value && value !== line.text && api()) {
      line.words = await api().update_line(line.id, value);
      line.text = value;
      renderWords(line, text);
      // The old translation no longer matches the text.
      const old = row.querySelector(".translation");
      if (old) old.replaceWith(translationRow(line, { auto: false }));
    }
    finish();
  });
  area.addEventListener("keydown", (e) => {
    if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); save.click(); }
    if (e.key === "Escape") finish();
  });
}

function isNearBottom() {
  return linesEl.scrollHeight - linesEl.scrollTop - linesEl.clientHeight < 120;
}

// --- Translations ----------------------------------------------------------------------

function translationRow(line, { auto = state.prefs.autoTranslate } = {}) {
  const row = el("div", "translation");
  row.dataset.line = line.id;
  if (auto) {
    // Python starts these itself as soon as the capture arrives.
    setTranslationState(row, "loading");
  } else {
    row.append(button("translate-btn", "Translate", () => requestTranslation(line.id, row)));
  }
  return row;
}

function requestTranslation(lineId, row) {
  row.dataset.reveal = "true"; // asked for explicitly, so don't blur it
  setTranslationState(row, "loading");
  api().translate(lineId);
}

function setTranslationState(row, status, message) {
  row.dataset.state = status;
  if (status === "loading") {
    row.replaceChildren(el("span", "translation-note", "Translating..."));
  } else if (status === "error") {
    row.replaceChildren(el("span", "translation-note error", message));
    row.append(button("translate-btn", "Retry", () => requestTranslation(Number(row.dataset.line), row)));
  }
}

function translationText(row) {
  let text = row.querySelector(".translation-text");
  if (!text) {
    text = el("span", "translation-text");
    text.title = "Click to show or hide";
    text.addEventListener("click", () => {
      row.dataset.reveal = String(row.dataset.reveal !== "true");
    });
    row.replaceChildren(text);
  }
  return text;
}

function translationRowFor(lineId) {
  return linesEl.querySelector(`.translation[data-line="${lineId}"]`);
}

// --- Tutor panel -----------------------------------------------------------------------

function openTutor(title) {
  closePopover();
  toggleSettings(false);
  tutor.hidden = false;
  $("#tutor-title").replaceChildren(...title);
  $("#tutor-log").replaceChildren();
  state.tutor = { session: null, answer: null, text: "", busy: false };
  if (!state.ai.enabled) {
    $("#tutor-log").append(el("p", "tutor-note", state.ai.error || AI_SETUP_HINT));
    setTutorBusy(true);
    return false;
  }
  startAnswer();
  return true;
}

function closeTutor() {
  tutor.hidden = true;
  state.tutor.session = null; // ignore anything still streaming
}

function startAnswer() {
  state.tutor.text = "";
  state.tutor.answer = el("div", "tutor-answer");
  state.tutor.answer.append(el("p", "tutor-note", "Thinking..."));
  $("#tutor-log").append(state.tutor.answer);
  setTutorBusy(true);
}

function setTutorBusy(busy) {
  state.tutor.busy = busy;
  $("#tutor-input").disabled = busy || !state.ai.enabled;
  $("#tutor-send").disabled = busy || !state.ai.enabled;
  if (!busy && state.ai.enabled) $("#tutor-input").focus();
}

function scrollTutor() {
  const log = $("#tutor-log");
  log.scrollTop = log.scrollHeight;
}

function linePreview(line) {
  return el("span", "tutor-line", line.speaker ? `${line.speaker}「${line.text}」` : `「${line.text}」`);
}

function explainWord(line, index) {
  const word = line.words[index];
  if (!openTutor([el("strong", "tutor-word", word.surface), " in ", linePreview(line)])) return;
  state.tutor.session = state.nextSession++;
  api().explain_word(line.id, index, state.tutor.session);
}

function explainLine(line) {
  if (!openTutor(["Breaking down ", linePreview(line)])) return;
  state.tutor.session = state.nextSession++;
  api().explain_line(line.id, state.tutor.session);
}

$("#tutor-form").addEventListener("submit", (e) => {
  e.preventDefault();
  const input = $("#tutor-input");
  const question = input.value.trim();
  if (!question || state.tutor.busy || state.tutor.session === null) return;
  input.value = "";
  $("#tutor-log").append(el("div", "tutor-question", question));
  startAnswer();
  scrollTutor();
  api().ask(state.tutor.session, question);
});
$("#tutor-close").addEventListener("click", closeTutor);

// --- Word popover ----------------------------------------------------------------------

function openPopover(wordEl) {
  const line = state.lines.get(Number(wordEl.dataset.line));
  const index = Number(wordEl.dataset.index);
  const word = line && line.words[index];
  if (!word) return;

  if (state.selected) state.selected.classList.remove("selected");
  state.selected = wordEl;
  wordEl.classList.add("selected");

  popover.replaceChildren(...popoverHeader(word));
  const body = el("div", "pop-body");
  body.append(el("p", "pop-note", "Looking up..."));
  const actions = el("div", "pop-actions");
  actions.append(button("pop-action", "Explain in context", () => explainWord(line, index)));
  popover.append(body, actions);
  popover.hidden = false;
  positionPopover(wordEl);

  const request = wordEl;
  Promise.resolve(api() ? api().lookup(word) : []).then((entries) => {
    if (state.selected !== request) return; // another word was clicked meanwhile
    body.replaceChildren(...popoverEntries(word, entries));
    positionPopover(wordEl);
  });
}

function popoverHeader(word) {
  const head = el("div", "pop-head");
  const surface = el("div", "pop-word");
  surface.lang = "ja";
  surface.append(...furiganaNodes(word.furigana));
  head.append(surface, button("pop-close", "×", closePopover, "Close"));
  const nodes = [head];

  if (word.dictionary_form && word.dictionary_form !== word.surface) {
    const base = el("div", "pop-base");
    base.append("Dictionary form: ", el("b", null, word.dictionary_form));
    if (word.dictionary_reading && word.dictionary_reading !== word.dictionary_form) {
      base.append(" ", el("span", "kana", `(${word.dictionary_reading})`));
    }
    nodes.push(base);
  }

  const chips = el("div", "chips");
  chips.append(el("span", `chip pos pos-${word.pos}`, POS_LABEL[word.pos] || word.pos));
  for (const note of word.grammar) chips.append(el("span", "chip grammar", note));
  nodes.push(chips);
  return nodes;
}

function popoverEntries(word, entries) {
  if (!entries.length) {
    const message = word.pos === "name" ? "A name; not in the dictionary." : "No dictionary entry found.";
    return [el("p", "pop-note", message)];
  }
  const [best, ...others] = entries;
  const nodes = [senseList(best.senses.slice(0, SENSES_SHOWN))];

  if (others.length) {
    const more = el("details", "more");
    more.append(el("summary", null, `Other entries (${others.length})`));
    for (const entry of others) {
      const box = el("div", "entry");
      const heading = entry.kanji.length ? `${entry.kanji[0]}【${entry.readings[0]}】` : entry.readings[0];
      box.append(el("div", "entry-head", heading), senseList(entry.senses.slice(0, 2)));
      more.append(box);
    }
    nodes.push(more);
  }
  return nodes;
}

function senseList(senses) {
  const list = el("ol", "senses");
  let lastPos = "";
  for (const sense of senses) {
    const item = el("li");
    const pos = sense.pos.join(" · ");
    if (pos && pos !== lastPos) item.append(el("span", "sense-pos", pos));
    lastPos = pos;
    item.append(sense.gloss.join("; "));
    if (sense.notes.length) item.append(" ", el("span", "sense-notes", `(${sense.notes.join(", ")})`));
    list.append(item);
  }
  return list;
}

function positionPopover(anchor) {
  const margin = 12;
  const rect = anchor.getBoundingClientRect();
  const width = popover.offsetWidth;
  const height = popover.offsetHeight;
  const left = Math.min(Math.max(margin, rect.left), window.innerWidth - width - margin);
  const below = rect.bottom + 8;
  const top = below + height + margin <= window.innerHeight ? below : Math.max(margin, rect.top - height - 8);
  popover.style.left = `${left}px`;
  popover.style.top = `${top}px`;
}

function closePopover() {
  popover.hidden = true;
  if (state.selected) state.selected.classList.remove("selected");
  state.selected = null;
}

// --- Preferences and toolbar -----------------------------------------------------------

function setPref(key, value) {
  state.prefs[key] = value;
  if (api()) api().set_pref(key, value);
}

function applyDisplay() {
  const { textSize, furiganaScale } = { ...DISPLAY_DEFAULTS, ...state.prefs };
  document.documentElement.style.setProperty("--text-size", `${textSize}px`);
  document.documentElement.style.setProperty("--rt-scale", furiganaScale);
  $("#text-size").value = textSize;
  $("#text-size-value").textContent = `${textSize}px`;
  $("#furigana-scale").value = furiganaScale;
  $("#furigana-scale-value").textContent = `${Math.round(textSize * furiganaScale)}px`;
  if (state.selected) positionPopover(state.selected);
}

// Toggle buttons: each mirrors one boolean preference: [button, default, apply].
const TOGGLES = {
  furigana: ["#toggle-furigana", true, (on) => document.body.classList.toggle("no-furigana", !on)],
  screenText: ["#toggle-screen", true, (on) => document.body.classList.toggle("hide-screen", !on)],
  onTop: ["#toggle-top", false, () => {}], // applied to the window by Python
};

function applyToggle(key) {
  const [selector, fallback, apply] = TOGGLES[key];
  const on = state.prefs[key] ?? fallback;
  $(selector).setAttribute("aria-pressed", String(on));
  apply(on);
}

for (const key of Object.keys(TOGGLES)) {
  $(TOGGLES[key][0]).addEventListener("click", (e) => {
    setPref(key, e.currentTarget.getAttribute("aria-pressed") !== "true");
    applyToggle(key);
  });
}

// Settings-panel checkboxes and text fields: [element, preference, default].
const FIELDS = [
  ["#auto-translate", "autoTranslate", false],
  ["#blur-translations", "blurTranslations", true],
  ["#learner-level", "learnerLevel", ""],
];

function applyFields() {
  for (const [selector, key, fallback] of FIELDS) {
    const input = $(selector);
    const value = state.prefs[key] ?? fallback;
    if (input.type === "checkbox") input.checked = value;
    else if (document.activeElement !== input) input.value = value;
  }
  document.body.classList.toggle("blur-translations", state.prefs.blurTranslations ?? true);
}

for (const [selector, key] of FIELDS) {
  const input = $(selector);
  input.addEventListener("change", () => {
    setPref(key, input.type === "checkbox" ? input.checked : input.value.trim());
    applyFields();
  });
}

function applyAI() {
  const status = $("#ai-status");
  if (state.ai.enabled) status.textContent = `Using ${state.ai.label}.`;
  else status.textContent = state.ai.error || "Off. Add a provider in config.toml (see the README).";
  status.classList.toggle("error", Boolean(state.ai.error));
  $("#ai-options").hidden = !state.ai.enabled;
}

function applyAllPrefs() {
  applyDisplay();
  applyFields();
  for (const key of Object.keys(TOGGLES)) applyToggle(key);
}

for (const [id, key] of [["#text-size", "textSize"], ["#furigana-scale", "furiganaScale"]]) {
  const input = $(id);
  // Apply live while dragging; save once the slider is released.
  input.addEventListener("input", () => {
    state.prefs[key] = Number(input.value);
    applyDisplay();
  });
  input.addEventListener("change", () => setPref(key, Number(input.value)));
}

$("#reset-display").addEventListener("click", () => {
  for (const [key, value] of Object.entries(DISPLAY_DEFAULTS)) setPref(key, value);
  applyDisplay();
});

function toggleSettings(open) {
  settings.hidden = !open;
  $("#open-settings").setAttribute("aria-pressed", String(open));
  if (open) closePopover();
}
$("#open-settings").addEventListener("click", () => toggleSettings(settings.hidden));

$("#clear").addEventListener("click", () => {
  closePopover();
  closeTutor();
  state.lines.clear();
  linesEl.querySelectorAll(".capture").forEach((node) => node.remove());
  $("#empty").hidden = false;
  if (api()) api().clear();
});

linesEl.addEventListener("click", (e) => {
  const word = e.target.closest("button.word");
  if (!word) return;
  if (word === state.selected) closePopover();
  else openPopover(word);
});

document.addEventListener("mousedown", (e) => {
  if (!popover.hidden && !popover.contains(e.target) && !e.target.closest("button.word")) closePopover();
  if (!settings.hidden && !settings.contains(e.target) && !e.target.closest("#open-settings")) toggleSettings(false);
});
document.addEventListener("keydown", (e) => {
  if (e.key === "Escape") {
    closePopover();
    toggleSettings(false);
  }
});
linesEl.addEventListener("scroll", () => {
  if (state.selected) positionPopover(state.selected);
});
window.addEventListener("resize", closePopover);

async function loadFromPython() {
  state.prefs = await api().get_prefs();
  applyAllPrefs();
  horu.setAI(await api().ai_info());
}

applyAllPrefs();
applyAI();
if (api()) loadFromPython();
else window.addEventListener("pywebviewready", loadFromPython, { once: true });

// --- Called from Python ----------------------------------------------------------------

window.horu = {
  setStatus({ state: status, message }) {
    $("#status").className = `status ${status}`;
    $("#status-text").textContent = message;
    $("#status").title = message;
  },

  setAI(info) {
    state.ai = info;
    applyAI();
  },

  addCapture(capture) {
    const stick = isNearBottom();
    $("#empty").hidden = true;
    for (const line of capture.lines) state.lines.set(line.id, line);
    const card = renderCapture(capture);
    linesEl.querySelectorAll(".capture.new").forEach((node) => node.classList.remove("new"));
    card.classList.add("new");
    linesEl.append(card);
    if (stick) card.scrollIntoView({ block: "end" });
    // Let the highlight fade out.
    setTimeout(() => card.classList.remove("new"), 2500);
  },

  aiChunk(msg) {
    if (msg.target === "translation") {
      const row = translationRowFor(msg.lineId);
      if (!row) return;
      if (row.dataset.state !== "streaming") {
        row.dataset.state = "streaming";
        translationText(row).textContent = "";
      }
      translationText(row).textContent += msg.text;
    } else if (msg.target === "tutor" && msg.session === state.tutor.session && state.tutor.answer) {
      const stick = $("#tutor-log").scrollHeight - $("#tutor-log").scrollTop - $("#tutor-log").clientHeight < 40;
      state.tutor.text += msg.text;
      state.tutor.answer.innerHTML = renderMarkdown(state.tutor.text);
      if (stick) scrollTutor();
    }
  },

  aiDone(msg) {
    if (msg.target === "translation") {
      const row = translationRowFor(msg.lineId);
      if (row) row.dataset.state = "done";
    } else if (msg.target === "tutor" && msg.session === state.tutor.session) {
      setTutorBusy(false);
    }
  },

  aiError(msg) {
    if (msg.target === "translation") {
      const row = translationRowFor(msg.lineId);
      if (row) setTranslationState(row, "error", msg.message);
    } else if (msg.target === "tutor" && msg.session === state.tutor.session && state.tutor.answer) {
      state.tutor.answer.replaceChildren(el("p", "tutor-note error", msg.message));
      setTutorBusy(false);
    }
  },
};
