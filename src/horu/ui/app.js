"use strict";

// State shared between Python pushes (window.horu.*) and user interaction.
const state = {
  lines: new Map(), // id -> line object from Python
  selected: null, // the .word element whose popover is open
  prefs: {}, // display preferences, loaded from and saved through Python
};

const $ = (selector) => document.querySelector(selector);
const linesEl = $("#lines");
const popover = $("#popover");
const settings = $("#settings");

const SENSES_SHOWN = 5;
const KIND_LABEL = { screen: "Screen text" };
const POS_LABEL = { "na-adjective": "な-adjective", "pre-noun": "pre-noun adjectival" };
// Used until Python's saved preferences arrive, and by "Reset to defaults".
const DISPLAY_DEFAULTS = { textSize: 26, furiganaScale: 0.5 };

function api() {
  return window.pywebview && window.pywebview.api;
}

function el(tag, className, text) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text !== undefined) node.textContent = text;
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
  meta.append(el("span", "spacer"));
  const edit = el("button", "edit-btn", "Edit");
  edit.type = "button";
  edit.title = "Fix OCR mistakes in this line";
  edit.addEventListener("click", () => startEdit(line, row));
  meta.append(edit);

  const text = el("div", "text");
  text.lang = "ja";
  renderWords(line, text);
  row.append(meta, text);
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
  row.append(editor);
  area.focus();

  const finish = () => {
    editor.remove();
    text.hidden = false;
  };
  cancel.addEventListener("click", finish);
  save.addEventListener("click", async () => {
    const value = area.value.trim();
    if (value && value !== line.text && api()) {
      line.words = await api().reanalyze(value);
      line.text = value;
      renderWords(line, text);
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

// --- Word popover ----------------------------------------------------------------------

function openPopover(wordEl) {
  const line = state.lines.get(Number(wordEl.dataset.line));
  const word = line && line.words[Number(wordEl.dataset.index)];
  if (!word) return;

  if (state.selected) state.selected.classList.remove("selected");
  state.selected = wordEl;
  wordEl.classList.add("selected");

  popover.replaceChildren(...popoverHeader(word));
  const body = el("div", "pop-body");
  body.append(el("p", "pop-note", "Looking up..."));
  popover.append(body);
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
  const close = el("button", "pop-close", "×");
  close.type = "button";
  close.title = "Close";
  close.addEventListener("click", closePopover);
  head.append(surface, close);
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

function applyAllPrefs() {
  applyDisplay();
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

async function loadPrefs() {
  state.prefs = await api().get_prefs();
  applyAllPrefs();
}

applyAllPrefs();
if (api()) loadPrefs();
else window.addEventListener("pywebviewready", loadPrefs, { once: true });

// --- Called from Python ----------------------------------------------------------------

window.horu = {
  setStatus({ state: status, message }) {
    $("#status").className = `status ${status}`;
    $("#status-text").textContent = message;
    $("#status").title = message;
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
};
