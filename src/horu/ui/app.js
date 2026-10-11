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
const KIND_LABEL = { other: "Other text" };
const POS_LABEL = { "na-adjective": "な-adjective", "pre-noun": "pre-noun adjectival" };
// Used until Python's saved preferences arrive, and by "Reset to defaults".
const DISPLAY_DEFAULTS = { textSize: 26, furiganaScale: 0.5 };
const AI_SETUP_HINT =
  "AI explanations need a provider. Open settings (the sliders button, top right) and choose " +
  "one under AI: Claude, OpenAI or Gemini with your own API key, or Ollama to run a free model " +
  "on this PC. Dictionary lookups keep working without it.";

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

function renderWords(line, container, part = "text") {
  container.replaceChildren();
  const words = part === "speaker" ? line.speaker.words : line.words;
  let unit = null; // a no-wrap group: one word plus the punctuation attached to it
  let openUnit = false;
  words.forEach((word, index) => {
    const symbol = word.pos === "symbol";
    const node = el(symbol ? "span" : "button", `word pos-${word.pos}`);
    if (!symbol) {
      node.type = "button";
      node.dataset.line = line.id;
      node.dataset.index = index;
      node.dataset.part = part;
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
  if (line.speaker) {
    // The speaker is words too: names and phrases like 少年の声 can be looked up.
    const speaker = el("span", "speaker");
    speaker.lang = "ja";
    renderWords(line, speaker, "speaker");
    meta.append(speaker, speakerEnglish(line));
  }
  if (KIND_LABEL[line.kind]) meta.append(el("span", "kind-label", KIND_LABEL[line.kind]));
  if (line.uncertain && line.kind === "dialogue") {
    const flag = el("span", "uncertain-flag", "?");
    flag.title = "Horu wasn't sure it read this line correctly. Check it against the game, or fix it with Edit.";
    if (line.alternatives && line.alternatives.length) {
      flag.title += "\n\nOther possible readings:\n" + line.alternatives.join("\n");
    }
    meta.append(flag);
  }
  meta.append(
    el("span", "spacer"),
    button("line-btn", "Explain", () => explainLine(line), "Break this line down with AI"),
    button("line-btn", "Cards", () => makeCards(line), "Let AI pick words worth learning and add them to Anki"),
    button("line-btn", "Edit", () => startEdit(line, row), "Fix OCR mistakes in this line"),
    button("line-btn", "⋯", (e) => openLineMenu(e.currentTarget, line, row), "Correct how this line was read"),
  );

  const text = el("div", "text");
  text.lang = "ja";
  renderWords(line, text);
  row.append(meta, text);
  if (line.kind === "dialogue" && state.ai.enabled) {
    // Repeats were translated (or skipped) when first shown; translate them only on request.
    row.append(translationRow(line, line.repeat ? { auto: false } : undefined));
  }
  return row;
}

// The speaker's English, e.g. 謎の通信 · Mysterious transmission. Filled in later when it comes
// from the AI; every row with the same speaker updates together.
function speakerEnglish(line) {
  const node = el("span", "speaker-en", line.speaker.english || "");
  node.dataset.game = line.game;
  node.dataset.speaker = line.speaker.text;
  node.title = "Speaker in English";
  return node;
}

// One capture (one hotkey press) is one card; its lines are rows inside it, in screen order.
function renderCapture(capture) {
  const card = el("section", "capture");
  const head = el("header", "capture-head");
  const count = capture.lines.length === 1 ? "1 line" : `${capture.lines.length} lines`;
  head.append(el("span", "capture-num", `Capture ${capture.id}`), el("span", null, capture.time), el("span", null, count));
  card.append(head);
  // Runs of lines that were already shown are collapsed, so a re-captured backlog page shows
  // what's new without anything silently disappearing.
  let repeats = null;
  for (const line of capture.lines) {
    if (line.repeat) {
      if (!repeats) {
        repeats = el("details", "repeats");
        repeats.append(el("summary"));
        card.append(repeats);
      }
      repeats.append(renderLine(line));
      const count = repeats.querySelectorAll(".line").length;
      repeats.querySelector("summary").textContent = count === 1 ? "1 line shown earlier" : `${count} lines shown earlier`;
    } else {
      repeats = null;
      card.append(renderLine(line));
    }
  }
  return card;
}

// --- Corrections ("this is dialogue", "this is a speaker name"), remembered per game --------

const lineMenu = $("#line-menu");

function openLineMenu(anchor, line, row) {
  closePopover();
  const items = [];
  if (line.kind === "dialogue") {
    items.push(["Treat as other text", () => mark(line, row, "other")]);
  } else {
    items.push(["Treat as dialogue", () => mark(line, row, "dialogue")]);
  }
  if (line.speaker) {
    items.push(["Edit the speaker's English…", () => editSpeakerEnglish(line)]);
    items.push([`「${line.speaker.text}」 isn't a speaker`, () => mark(line, row, "not_speaker", "speaker")]);
  } else if (line.text.length <= 10) {
    items.push([`「${line.text}」 is a speaker name`, () => mark(line, row, "speaker")]);
  }
  if (state.ai.enabled && line.kind === "dialogue") {
    items.unshift(["Re-read with AI", () => reread(line, row)]);
  }
  if (line.box && line.kind === "dialogue") {
    items.push(["Use as the dialogue box for auto-capture", async () => {
      const result = await api().set_dialogue_box(line.id);
      horu.setStatus({ state: result.ok ? "ready" : "error", message: result.message });
    }]);
  }
  lineMenu.replaceChildren(...items.map(([label, action]) => button("menu-item", label, () => {
    closeLineMenu();
    action();
  })));
  lineMenu.append(el("p", "menu-note", `Remembered for ${line.game}.`));
  lineMenu.hidden = false;
  const rect = anchor.getBoundingClientRect();
  lineMenu.style.top = `${Math.min(rect.bottom + 4, window.innerHeight - lineMenu.offsetHeight - 8)}px`;
  lineMenu.style.left = `${Math.max(8, rect.right - lineMenu.offsetWidth)}px`;
}

function closeLineMenu() {
  lineMenu.hidden = true;
}

function editSpeakerEnglish(line) {
  const current = line.speaker.english || "";
  const english = window.prompt(`English for 「${line.speaker.text}」 in ${line.game}:`, current);
  if (english !== null && english.trim() && english.trim() !== current) {
    api().set_speaker_english(line.game, line.speaker.text, english.trim());
  }
}

async function mark(line, row, label, part = "text") {
  const result = await api().mark(line.id, label, part);
  if (part === "text" && (label === "dialogue" || label === "other")) {
    line.kind = result.kind;
    row.classList.remove("dialogue", "other");
    row.classList.add(line.kind);
    const kindLabel = row.querySelector(".kind-label");
    if (kindLabel) kindLabel.remove();
    if (KIND_LABEL[line.kind]) row.querySelector(".spacer").before(el("span", "kind-label", KIND_LABEL[line.kind]));
    if (line.kind === "dialogue" && state.ai.enabled && !row.querySelector(".translation")) {
      row.append(translationRow(line, { auto: false }));
    }
    horu.setStatus({ state: "ready", message: `Saved: "${line.text}" will be read as ${line.kind === "other" ? "other text" : "dialogue"} in ${line.game}.` });
  } else {
    horu.setStatus({ state: "ready", message: `Saved for ${line.game}; applies from the next capture.` });
  }
}

// A vision model reads the line again from its screenshot; its reading opens in the editor
// for the user to check and save.
async function reread(line, row) {
  horu.setStatus({ state: "loading", message: "Re-reading the line with AI…" });
  const result = await api().reread(line.id);
  if (!result.ok) {
    horu.setStatus({ state: "error", message: result.message });
    return;
  }
  if (result.text === line.text) {
    horu.setStatus({ state: "ready", message: "The AI read the same text." });
    return;
  }
  horu.setStatus({ state: "ready", message: "The AI's reading is in the editor: check it against the game, then Save." });
  startEdit(line, row, result.text);
}

function startEdit(line, row, suggestion = null) {
  closePopover();
  const text = row.querySelector(".text");
  const editor = el("div", "editor");
  const area = el("textarea");
  area.lang = "ja";
  area.value = suggestion ?? line.text;
  if (suggestion !== null) editor.append(el("p", "editor-note", `Was: ${line.text}`));
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
      await setLineText(line, row, value, await api().update_line(line.id, value));
      refreshTranslation(line, row, { auto: false });
    }
    finish();
  });
  area.addEventListener("keydown", (e) => {
    if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); save.click(); }
    if (e.key === "Escape") finish();
  });
}

// --- Fixing misread lines ----------------------------------------------------------------

function rowFor(lineId) {
  return linesEl.querySelector(`.line[data-id="${lineId}"]`);
}

function setLineText(line, row, text, words) {
  line.text = text;
  line.words = words;
  renderWords(line, row.querySelector(".text"));
  const chip = row.querySelector(".fix-chip");
  if (chip) chip.remove();
}

// The old translation no longer matches the text: translate again, or offer to.
function refreshTranslation(line, row, { auto = state.prefs.autoTranslate } = {}) {
  const old = row.querySelector(".translation");
  if (!old) return;
  const fresh = translationRow(line, { auto });
  // The new translation is the next one Python numbers; ignore the old one's chunks.
  fresh.dataset.gen = Number(old.dataset.gen || 0) + 1;
  old.replaceWith(fresh);
  if (auto) api().translate(line.id);
}

// Shows that the AI fixed this line; clicking puts the original back.
function markCorrected(line, row, was) {
  let tag = row.querySelector(".corrected-tag");
  if (!tag) {
    tag = button("corrected-tag", "corrected", async () => {
      await setLineText(line, row, was, await api().update_line(line.id, was));
      tag.remove();
      refreshTranslation(line, row);
    });
    row.querySelector(".spacer").before(tag);
  }
  tag.title = `Horu fixed a likely OCR misread. It was:\n${was}\n\nClick to undo.`;
  const flag = row.querySelector(".uncertain-flag");
  if (flag) flag.remove();
}

// `text` with the part that differs from `before` marked, so a one-kana change (のお → のぉ)
// stands out.
function highlightChange(before, text) {
  let start = 0;
  while (start < before.length && start < text.length && before[start] === text[start]) start++;
  let end = 0;
  while (end < before.length - start && end < text.length - start
         && before[before.length - 1 - end] === text[text.length - 1 - end]) end++;
  const changed = text.slice(start, text.length - end);
  if (!changed) return [text];
  return [text.slice(0, start), el("mark", "fix-change", changed), text.slice(text.length - end)];
}

function showSuggestion(line, row, text) {
  const old = row.querySelector(".fix-chip");
  if (old) old.remove();
  const chip = el("div", "fix-chip");
  const quote = el("span", "fix-text");
  quote.lang = "ja";
  quote.append("「", ...highlightChange(line.text, text), "」?");
  chip.append(
    el("span", null, "Possible misread. Did you mean "),
    quote,
    button("fix-use", "Use this", async () => {
      const was = line.text;
      await setLineText(line, row, text, await api().update_line(line.id, text));
      markCorrected(line, row, was);
      refreshTranslation(line, row);
    }, "Replace the line with this reading"),
    button("fix-dismiss", "×", () => chip.remove(), "Keep the line as it is"),
  );
  row.querySelector(".text").after(chip);
}

// A model that suspects a misread starts its reply with "OCR: <fixed line>". Python acts on
// it; the page shows it as a short note (tutor) or hides it (translations).
const OCR_LINE = /^\s*OCR:\s*([^\n]*)\n?/;

function withoutOcrLine(text) {
  return text.replace(OCR_LINE, "");
}

function withOcrNote(text) {
  return text.replace(OCR_LINE, (_, fixed) => `*Likely OCR misread. The line probably reads 「${fixed.replace(/^「|」$/g, "")}」.*\n\n`);
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

// A translation of a line's earlier text, still streaming after the line was fixed and
// translated again.
function isStale(row, msg) {
  return msg.gen < Number(row.dataset.gen || 0);
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
  state.tutor = { session: null, answer: null, text: "", busy: false, agent: false };
  $("#tutor-input").placeholder = "Ask a follow-up, e.g. why は and not が here?";
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
  const closed = busy || !state.ai.enabled || Boolean(state.tutor.agent);
  $("#tutor-input").disabled = closed;
  $("#tutor-send").disabled = closed;
  if (!busy && state.ai.enabled) $("#tutor-input").focus();
}

function scrollTutor() {
  const log = $("#tutor-log");
  log.scrollTop = log.scrollHeight;
}

function linePreview(line) {
  return el("span", "tutor-line", line.speaker ? `${line.speaker.text}「${line.text}」` : `「${line.text}」`);
}

function explainWord(line, index, part = "text") {
  const word = (part === "speaker" ? line.speaker.words : line.words)[index];
  if (!openTutor([el("strong", "tutor-word", word.surface), " in ", linePreview(line)])) return;
  state.tutor.session = state.nextSession++;
  api().explain_word(line.id, index, state.tutor.session, part);
}

function makeCards(line) {
  if (!openTutor(["Making flashcards from ", linePreview(line)])) return;
  state.tutor.agent = true;
  state.tutor.answer.replaceChildren(el("p", "tutor-note", "Choosing words..."));
  $("#tutor-input").placeholder = "Follow-up questions aren't available for flashcard making.";
  state.tutor.session = state.nextSession++;
  api().make_cards(line.id, state.tutor.session);
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
  const part = wordEl.dataset.part || "text";
  const word = line && (part === "speaker" ? line.speaker.words : line.words)[index];
  if (!word) return;

  if (state.selected) state.selected.classList.remove("selected");
  state.selected = wordEl;
  wordEl.classList.add("selected");

  popover.replaceChildren(...popoverHeader(word, wordEl));
  const body = el("div", "pop-body");
  body.append(el("p", "pop-note", "Looking up..."));
  const actions = el("div", "pop-actions");
  actions.append(button("pop-action", "Explain in context", () => explainWord(line, index, part)));
  const save = button("pop-action secondary", "Add to Anki", async () => {
    save.disabled = true;
    save.textContent = state.ai.enabled ? "Writing card..." : "Adding...";
    const result = await api().save_card(line.id, index, part);
    save.textContent = result.ok ? "Added" : "Add to Anki";
    save.disabled = result.ok;
    if (!result.ok) body.append(el("p", "pop-note error", result.message));
    else {
      horu.setStatus({ state: "ready", message: result.message });
      horu.cardsAdded({ lineId: line.id, words: [word.dictionary_form] });
    }
    positionPopover(wordEl);
  });
  actions.append(save);
  popover.append(body, actions);
  popover.hidden = false;
  positionPopover(wordEl);

  showLookup(word, body, wordEl);
}

function showLookup(word, body, wordEl) {
  const request = wordEl;
  const view = popover.dataset.view = String(Number(popover.dataset.view || 0) + 1);
  Promise.resolve(api() ? api().lookup(word) : { entries: [], names: [] }).then((result) => {
    // Another word (or part) was opened meanwhile.
    if (state.selected !== request || popover.dataset.view !== view) return;
    body.replaceChildren(...nameEntries(result.names), ...popoverEntries(word, result.entries, result.names.length > 0));
    positionPopover(wordEl);
  });
}

// A closer look at one part of a compound (語 in 日本語), with a way back to the whole word.
function openPart(wordEl, part) {
  const back = button("pop-back", "← Back", () => openPopover(wordEl));
  const body = el("div", "pop-body");
  body.append(el("p", "pop-note", "Looking up..."));
  popover.replaceChildren(back, ...popoverHeader(part, wordEl), body);
  positionPopover(wordEl);
  showLookup(part, body, wordEl);
}

function popoverHeader(word, wordEl) {
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

  if (word.parts && word.parts.length) {
    const parts = el("div", "pop-parts");
    parts.append(el("span", "pop-parts-label", "Made of"));
    for (const part of word.parts) {
      const chip = button("chip part", part.surface, () => openPart(wordEl, part), `Look up ${part.surface}`);
      chip.lang = "ja";
      parts.append(chip);
    }
    nodes.push(parts);
  }
  return nodes;
}

// Name readings from JMnedict, e.g. 順平【じゅんぺい】 Junpei (given name).
function nameEntries(names) {
  if (!names.length) return [];
  const list = el("ul", "names");
  for (const entry of names) {
    const item = el("li");
    const reading = entry.readings.join("・");
    item.append(el("span", "name-reading", reading));
    for (const n of entry.names) {
      item.append(" ", el("span", "name-det", n.det.join(", ")), " ", el("span", "sense-notes", `(${n.types.join(", ")})`));
    }
    list.append(item);
  }
  return [el("div", "names-title", "Name"), list];
}

function popoverEntries(word, entries, hasNames = false) {
  if (!entries.length) {
    if (hasNames) return [];
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
  autoCapture: ["#toggle-auto", false, () => {}], // the watching is done by Python
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
  ["#anki-deck", "ankiDeck", "Horu"],
  ["#auto-cards", "autoCards", false],
  ["#hide-from-capture", "hideFromCapture", true],
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
  if (state.ai.enabled) {
    const translator = state.ai.translator ? `; translations: ${state.ai.translator}` : "";
    status.textContent = `Active: ${state.ai.label}${translator}.`;
  } else {
    status.textContent = state.ai.error || "AI is off. Dictionary lookups still work.";
  }
  status.classList.toggle("error", Boolean(state.ai.error));
  status.classList.toggle("ok", state.ai.enabled);
  $("#ai-options").hidden = !state.ai.enabled;
  $("#ai-test-row").hidden = !state.ai.enabled && !state.ai.error;
}

// --- AI provider setup -----------------------------------------------------------------

const aiSetup = { settings: null, ollama: { running: false, models: [] }, pulls: {} };

// Translation-only models (such as the Japanese-English Gemma fine-tune) can't explain grammar.
function isTranslationOnly(name) {
  const known = (aiSetup.settings ? aiSetup.settings.ollamaSuggestions : []).some((s) => s.name === name && s.use === "translation");
  return known || /translat/i.test(name);
}

function openExternal(url) {
  if (api()) api().open_url(url);
}

async function loadAISettings() {
  const s = await api().ai_settings();
  aiSetup.settings = s;
  const select = $("#ai-provider");
  select.replaceChildren(...Object.entries(s.presets).map(([id, p]) => {
    const option = el("option", null, p.label);
    option.value = id;
    return option;
  }));
  select.value = s.provider || "none";
  $("#ai-model").value = s.model;
  $("#ai-translation-model").value = s.translationModel;
  $("#ai-baseurl").value = s.baseUrl;
  renderAIForm();
}

function renderAIForm() {
  const s = aiSetup.settings;
  if (!s) return;
  const id = $("#ai-provider").value;
  const preset = s.presets[id] || {};
  const on = id !== "none";
  const needsKey = Boolean(preset.key_env || preset.needs_key);
  $("#ai-key-row").hidden = !needsKey;
  if (needsKey) {
    const saved = s.savedKeys.includes(id);
    $("#ai-key-status").textContent = saved ? "Key saved" : preset.needs_key === "optional" ? "Only if your server needs one" : "No key saved yet";
    $("#ai-key-status").className = saved ? "saved" : "";
    $("#ai-key").placeholder = saved ? "Paste a new key to replace it" : "Paste your key";
    $("#ai-key-link").hidden = !preset.key_url;
  }
  $("#ai-model-warning").hidden = !on || !isTranslationOnly($("#ai-model").value);
  $("#ai-baseurl-row").hidden = id !== "custom";
  $("#ai-model-row").hidden = !on;
  $("#ai-translation-row").hidden = !on;
  $("#ollama-box").hidden = id !== "ollama";
  if (id === "ollama") refreshOllama();
}

function saveAI(changes) {
  if (!api()) return;
  api().set_ai(changes).then((info) => horu.setAI(info));
  $("#ai-test-result").textContent = "";
}

$("#ai-provider").addEventListener("change", async () => {
  const id = $("#ai-provider").value;
  const preset = aiSetup.settings.presets[id] || {};
  let model = preset.model || "";
  let translationModel = "";
  if (id === "ollama") {
    // Pick an installed general model for explanations, and an installed translation-only
    // model (if any) for translations.
    await refreshOllama();
    const names = aiSetup.ollama.models.map((m) => m.name);
    model = names.find((n) => !isTranslationOnly(n)) || "";
    translationModel = names.find(isTranslationOnly) || "";
  }
  $("#ai-model").value = model;
  $("#ai-translation-model").value = translationModel;
  $("#ai-baseurl").value = "";
  renderAIForm();
  saveAI({ aiProvider: id, aiModel: model, translationModel, aiBaseUrl: "" });
});

for (const [selector, key] of [["#ai-model", "aiModel"], ["#ai-translation-model", "translationModel"], ["#ai-baseurl", "aiBaseUrl"]]) {
  $(selector).addEventListener("change", (e) => {
    saveAI({ aiProvider: $("#ai-provider").value, [key]: e.target.value.trim() });
    renderAIForm();
  });
}

$("#ai-key-save").addEventListener("click", async () => {
  const input = $("#ai-key");
  const id = $("#ai-provider").value;
  const info = await api().save_api_key(id, input.value);
  input.value = "";
  // Make sure the provider choice is saved too, then refresh what's shown.
  saveAI({ aiProvider: id });
  horu.setAI(info);
  await loadAISettings();
});
$("#ai-key").addEventListener("keydown", (e) => {
  if (e.key === "Enter") $("#ai-key-save").click();
});
$("#ai-key-link").addEventListener("click", () => {
  const preset = aiSetup.settings.presets[$("#ai-provider").value];
  if (preset && preset.key_url) openExternal(preset.key_url);
});

$("#ai-test").addEventListener("click", async () => {
  const result = $("#ai-test-result");
  result.className = "test-result";
  result.textContent = "Testing...";
  $("#ai-test").disabled = true;
  try {
    const outcome = await api().test_ai();
    result.className = `test-result ${outcome.ok ? "ok" : "error"}`;
    result.textContent = outcome.message;
  } finally {
    $("#ai-test").disabled = false;
  }
});

async function refreshOllama() {
  aiSetup.ollama = await api().ollama_models();
  const { running, models, download } = aiSetup.ollama;
  const status = $("#ollama-status");
  status.replaceChildren();
  if (!running) {
    status.append(el("span", "error", "Ollama isn't running. "), button("link", "Download Ollama", () => openExternal(download)));
  } else {
    status.textContent = models.length ? `Installed models: ${models.length}` : "Ollama is running, but has no models yet.";
  }
  $("#ai-model-list").replaceChildren(...models.map((m) => {
    const option = el("option");
    option.value = m.name;
    return option;
  }));
  renderSuggestions();
}
$("#ollama-refresh").addEventListener("click", refreshOllama);

function renderSuggestions() {
  const box = $("#ollama-suggestions");
  box.replaceChildren(el("div", "suggestions-title", "Suggested models"));
  const installed = new Set(aiSetup.ollama.models.map((m) => m.name));
  for (const s of aiSetup.settings.ollamaSuggestions) {
    const row = el("div", "suggestion");
    row.dataset.name = s.name;
    const head = el("div", "suggestion-head");
    head.append(el("span", "suggestion-name", s.name), el("span", "suggestion-size", s.size));
    const actions = el("div", "suggestion-actions");
    const pull = aiSetup.pulls[s.name];
    if (installed.has(s.name)) {
      actions.append(el("span", "installed", "Installed"));
      const field = s.use === "translation" ? "#ai-translation-model" : "#ai-model";
      if ($(field).value === s.name) {
        actions.append(el("span", "in-use", s.use === "translation" ? "Used for translations" : "Used for explanations"));
      } else {
        const label = s.use === "translation" ? "Use for translations" : "Use for explanations";
        actions.append(button(null, label, () => {
          $(field).value = s.name;
          $(field).dispatchEvent(new Event("change"));
        }));
      }
    } else if (pull && !pull.done) {
      actions.append(el("span", "progress", pullText(pull)));
    } else if (aiSetup.ollama.running) {
      if (pull && pull.error) actions.append(el("span", "error", pull.error));
      actions.append(button(null, "Download", () => startPull(s.name)));
    }
    actions.append(button("link", "About", () => openExternal(s.url)));
    row.append(head, el("div", "suggestion-note", s.note), actions);
    box.append(row);
  }
}

function pullText(pull) {
  if (pull.total) {
    const pct = Math.floor((100 * pull.completed) / pull.total);
    return `Downloading ${pct}% (${(pull.completed / 1e9).toFixed(1)} of ${(pull.total / 1e9).toFixed(1)} GB)`;
  }
  return pull.status ? `${pull.status[0].toUpperCase()}${pull.status.slice(1)}...` : "Starting...";
}

function startPull(name) {
  aiSetup.pulls[name] = { status: "starting", completed: 0, total: 0, done: false };
  renderSuggestions();
  api().ollama_pull(name);
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
  if (open) {
    closePopover();
    if (api()) checkAnki();
  }
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
  if (!lineMenu.hidden && !lineMenu.contains(e.target)) closeLineMenu();
  if (!settings.hidden && !settings.contains(e.target) && !e.target.closest("#open-settings")) toggleSettings(false);
});
document.addEventListener("keydown", (e) => {
  if (e.key === "Escape") {
    closePopover();
    closeLineMenu();
    toggleSettings(false);
  }
});
linesEl.addEventListener("scroll", () => {
  if (state.selected) positionPopover(state.selected);
});
window.addEventListener("resize", closePopover);

async function checkAnki() {
  const status = $("#anki-status");
  status.className = "ai-status";
  status.textContent = "Checking...";
  const result = await api().anki_status();
  status.classList.toggle("ok", result.connected);
  status.classList.toggle("error", !result.connected);
  status.textContent = result.connected
    ? `Connected. Cards go to the "${result.deck}" deck (created if needed).`
    : result.error;
}
$("#anki-check").addEventListener("click", checkAnki);

async function loadFromPython() {
  state.prefs = await api().get_prefs();
  applyAllPrefs();
  horu.setAI(await api().ai_info());
  await loadAISettings();
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

  pullProgress(event) {
    aiSetup.pulls[event.name] = { ...(aiSetup.pulls[event.name] || {}), ...event };
    if (event.done && !event.error) refreshOllama();
    else if (!$("#ollama-box").hidden) {
      // Update just the progress text, so buttons under the cursor don't jump around.
      const row = $(`#ollama-suggestions .suggestion[data-name="${CSS.escape(event.name)}"] .progress`);
      if (row) row.textContent = pullText(aiSetup.pulls[event.name]);
      else renderSuggestions();
    }
  },

  speakerEnglish({ game, text, english }) {
    for (const line of state.lines.values()) {
      if (line.speaker && line.game === game && line.speaker.text === text) line.speaker.english = english;
    }
    for (const node of linesEl.querySelectorAll(".speaker-en")) {
      if (node.dataset.game === game && node.dataset.speaker === text) node.textContent = english;
    }
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

  lineCorrected({ id, text, words, was, retranslate }) {
    const line = state.lines.get(id);
    const row = rowFor(id);
    if (!line || !row) return;
    setLineText(line, row, text, words);
    markCorrected(line, row, was);
    if (retranslate) refreshTranslation(line, row);
  },

  // A line auto-capture read while it was still typing out, now read in full.
  lineUpdated({ id, text, words, uncertain }) {
    const line = state.lines.get(id);
    const row = rowFor(id);
    if (!line || !row) return;
    setLineText(line, row, text, words);
    if (!uncertain) horu.lineVerified({ id });
    refreshTranslation(line, row);
  },

  // The AI read the same text as OCR, so the "?" no longer applies.
  lineVerified({ id }) {
    const line = state.lines.get(id);
    if (line) line.uncertain = false;
    const flag = rowFor(id)?.querySelector(".uncertain-flag");
    if (flag) flag.remove();
  },

  setAutoState({ state: autoState, message }) {
    const toggle = $("#toggle-auto");
    toggle.dataset.state = autoState;
    toggle.title = `Auto-capture: ${message}`;
  },

  lineSuggestion({ id, text }) {
    const line = state.lines.get(id);
    const row = rowFor(id);
    if (line && row) showSuggestion(line, row, text);
  },

  cardsAdded({ lineId, words }) {
    const row = linesEl.querySelector(`.line[data-id="${lineId}"]`);
    if (!row) return;
    let tag = row.querySelector(".cards-tag");
    if (!tag) {
      tag = el("span", "cards-tag");
      row.querySelector(".spacer").before(tag);
    }
    const all = [...new Set([...(tag.dataset.words ? tag.dataset.words.split("、") : []), ...words])];
    tag.dataset.words = all.join("、");
    tag.textContent = all.length === 1 ? "+1 card" : `+${all.length} cards`;
    tag.title = `Added to Anki: ${tag.dataset.words}`;
  },

  agentStep(msg) {
    if (msg.session !== state.tutor.session || !state.tutor.answer) return;
    // Progress lines sit above the answer, which arrives at the end.
    if (!state.tutor.steps) {
      state.tutor.steps = el("ul", "agent-steps");
      state.tutor.answer.before(state.tutor.steps);
    }
    state.tutor.steps.append(el("li", null, `${msg.text}...`));
    scrollTutor();
  },

  aiChunk(msg) {
    if (msg.target === "translation") {
      const row = translationRowFor(msg.lineId);
      if (!row || isStale(row, msg)) return;
      if (row.dataset.state !== "streaming" || msg.gen > Number(row.dataset.gen || 0)) {
        row.dataset.state = "streaming";
        row.dataset.gen = msg.gen;
        row.raw = "";
      }
      row.raw += msg.text;
      translationText(row).textContent = withoutOcrLine(row.raw);
    } else if (msg.target === "tutor" && msg.session === state.tutor.session && state.tutor.answer) {
      const stick = $("#tutor-log").scrollHeight - $("#tutor-log").scrollTop - $("#tutor-log").clientHeight < 40;
      state.tutor.text += msg.text;
      state.tutor.answer.innerHTML = renderMarkdown(withOcrNote(state.tutor.text));
      if (stick) scrollTutor();
    }
  },

  aiDone(msg) {
    if (msg.target === "translation") {
      const row = translationRowFor(msg.lineId);
      if (row && !isStale(row, msg)) row.dataset.state = "done";
    } else if (msg.target === "tutor" && msg.session === state.tutor.session) {
      setTutorBusy(false);
    }
  },

  aiError(msg) {
    if (msg.target === "translation") {
      const row = translationRowFor(msg.lineId);
      if (row && !isStale(row, msg)) setTranslationState(row, "error", msg.message);
    } else if (msg.target === "tutor" && msg.session === state.tutor.session && state.tutor.answer) {
      state.tutor.answer.replaceChildren(el("p", "tutor-note error", msg.message));
      setTutorBusy(false);
    }
  },
};
