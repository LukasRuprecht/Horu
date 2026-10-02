"use strict";

// State shared between Python pushes (window.horu.*) and user interaction.
const state = {
  lines: new Map(), // id -> line object from Python
  selected: null, // the .word element whose popover is open
};

const $ = (selector) => document.querySelector(selector);
const linesEl = $("#lines");
const popover = $("#popover");

const SENSES_SHOWN = 5;
const KIND_LABEL = { screen: "Screen text" };
const POS_LABEL = { "na-adjective": "な-adjective", "pre-noun": "pre-noun adjectival" };

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

// --- Lines -----------------------------------------------------------------------------

function renderWords(line, container) {
  container.replaceChildren();
  line.words.forEach((word, index) => {
    const clickable = word.pos !== "symbol";
    const node = el(clickable ? "button" : "span", `word pos-${word.pos}`);
    if (clickable) {
      node.type = "button";
      node.dataset.line = line.id;
      node.dataset.index = index;
    }
    node.append(...furiganaNodes(word.furigana));
    container.append(node);
  });
}

function renderLine(line) {
  const card = el("article", `line ${line.kind}`);
  card.dataset.id = line.id;

  const meta = el("div", "line-meta");
  if (line.speaker) meta.append(el("span", "speaker", line.speaker));
  if (KIND_LABEL[line.kind]) meta.append(el("span", "kind-label", KIND_LABEL[line.kind]));
  meta.append(el("span", "spacer"), el("span", "time", line.time));
  const edit = el("button", "edit-btn", "Edit");
  edit.type = "button";
  edit.title = "Fix OCR mistakes in this line";
  edit.addEventListener("click", () => startEdit(line, card));
  meta.append(edit);

  const text = el("div", "text");
  text.lang = "ja";
  renderWords(line, text);
  card.append(meta, text);
  return card;
}

function startEdit(line, card) {
  closePopover();
  const text = card.querySelector(".text");
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
  card.append(editor);
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

// --- Toolbar ---------------------------------------------------------------------------

function loadPref(key, fallback) {
  try {
    const value = localStorage.getItem(`horu.${key}`);
    return value === null ? fallback : value === "true";
  } catch {
    return fallback;
  }
}

function savePref(key, value) {
  try {
    localStorage.setItem(`horu.${key}`, String(value));
  } catch {
    // Preferences are a convenience; ignore storage failures.
  }
}

function setupToggle(id, key, fallback, apply) {
  const button = $(id);
  const set = (on) => {
    button.setAttribute("aria-pressed", String(on));
    apply(on);
    savePref(key, on);
  };
  set(loadPref(key, fallback));
  button.addEventListener("click", () => set(button.getAttribute("aria-pressed") !== "true"));
}

setupToggle("#toggle-furigana", "furigana", true, (on) => document.body.classList.toggle("no-furigana", !on));
setupToggle("#toggle-screen", "screenText", true, (on) => document.body.classList.toggle("hide-screen", !on));
// On-top is applied through Python, which may not be connected yet when the page loads.
$("#toggle-top").addEventListener("click", (e) => {
  const on = e.currentTarget.getAttribute("aria-pressed") !== "true";
  e.currentTarget.setAttribute("aria-pressed", String(on));
  if (api()) api().set_on_top(on);
});

$("#clear").addEventListener("click", () => {
  closePopover();
  state.lines.clear();
  linesEl.querySelectorAll(".line").forEach((node) => node.remove());
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
});
document.addEventListener("keydown", (e) => {
  if (e.key === "Escape") closePopover();
});
linesEl.addEventListener("scroll", () => {
  if (state.selected) positionPopover(state.selected);
});
window.addEventListener("resize", closePopover);

// --- Called from Python ----------------------------------------------------------------

window.horu = {
  setStatus({ state: status, message }) {
    $("#status").className = `status ${status}`;
    $("#status-text").textContent = message;
    $("#status").title = message;
  },

  addLines(lines) {
    const stick = isNearBottom();
    $("#empty").hidden = true;
    linesEl.querySelectorAll(".line.new").forEach((node) => node.classList.remove("new"));
    let last;
    for (const line of lines) {
      state.lines.set(line.id, line);
      last = renderLine(line);
      last.classList.add("new");
      linesEl.append(last);
    }
    if (stick && last) last.scrollIntoView({ block: "end" });
    // Let the highlight fade out.
    setTimeout(() => linesEl.querySelectorAll(".line.new").forEach((n) => n.classList.remove("new")), 2500);
  },
};
