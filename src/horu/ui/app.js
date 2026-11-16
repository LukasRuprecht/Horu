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
  "one under AI: a cloud provider with your own API key, or Ollama to run a free model " +
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
    if (line.speaker.guessed) meta.append(guessTag(line));
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
    button("line-btn foldable", "Explain", () => explainLine(line), "Break this line down with AI"),
    button("line-btn foldable", "Cards", () => makeCards(line), "Let AI pick words worth learning and add them to Anki"),
    button("line-btn foldable", "Edit", () => startEdit(line, row), "Fix OCR mistakes in this line"),
    button("line-btn", "⋯", (e) => openLineMenu(e.currentTarget, line, row), "Correct how this line was read"),
  );

  const text = el("div", "text");
  text.lang = "ja";
  renderWords(line, text);
  row.append(meta, text);
  if (state.ai.enabled && line.snip) {
    // A snip is translated whole, other text too: Python starts it, and it's not blurred.
    const translation = translationRow(line, { auto: true });
    translation.dataset.reveal = "true";
    row.append(translation);
  } else if (state.ai.enabled) {
    // Repeats were translated (or skipped) when first shown, and other text (menus, signs)
    // isn't translated by itself: those only on request.
    row.append(translationRow(line, line.repeat || line.kind !== "dialogue" ? { auto: false } : undefined));
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

// A name tag OCR was unsure of, matched to a name the AI confirmed earlier. Quiet unless the
// learner thinks it's wrong.
function guessTag(line) {
  const tag = button("corrected-tag speaker-guess", "wrong name?", (e) => openGuessMenu(e.currentTarget, line));
  tag.title = `OCR read 「${line.speaker.was}」; Horu took it for ${line.speaker.text}, a name it checked earlier. Click if that's wrong.`;
  return tag;
}

function openGuessMenu(anchor, line) {
  const id = line.id;
  const items = [];
  if (state.ai.enabled) {
    items.push(["Check the name with AI", async () => {
      horu.setStatus({ state: "busy", message: "Checking the name..." });
      const result = await api().check_speaker(id);
      horu.setStatus({ state: result.ok ? "ready" : "error", message: result.message });
    }]);
  }
  items.push([`It's 「${line.speaker.was}」, as OCR read it`, () => api().fix_speaker(id, line.speaker.was)]);
  items.push(["Type the name…", () => {
    const name = window.prompt(`Speaker's name, as the game shows it (OCR read 「${line.speaker.was}」):`, line.speaker.text);
    if (name !== null && name.trim()) api().fix_speaker(id, name.trim());
  }]);
  showMenu(anchor, items, `Remembered for ${line.game}.`);
}

// --- The game window this session captures ------------------------------------------------

$("#game-chip").addEventListener("click", async (e) => {
  const anchor = e.currentTarget;
  const { windows, current } = await api().game_windows();
  const items = windows.map((w) => {
    const label = `${w.id === current ? "✓ " : ""}${w.title}`;
    return [label, () => api().set_game_window(w.id), w.process];
  });
  items.push(["Choose automatically", () => api().set_game_window(null), "The window in front when you press the hotkey"]);
  showMenu(anchor, items, "Horu captures this window even when another one is in front.");
});

// Captures are numbered as they're shown: Horu's own ids also count auto-capture reads that
// found nothing new, which left gaps (Capture 1, 2, 5, 9).
let capturesShown = 0;

// One capture (one hotkey press) is one card; its lines are rows inside it, in screen order.
function renderCapture(capture) {
  const card = el("section", "capture");
  const head = el("header", "capture-head");
  const count = capture.lines.length === 1 ? "1 line" : `${capture.lines.length} lines`;
  const label = capture.hooked ? "Hooked" : capture.snip ? "Snip" : `Capture ${++capturesShown}`;
  head.append(el("span", "capture-num", label), el("span", null, capture.time), el("span", null, count));
  if (capture.snip) card.classList.add("show-other"); // asked for: nothing folded away
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
  const fold = button("other-fold", "", () => {
    card.classList.toggle("show-other");
    refreshFold(card);
  });
  card.append(fold);
  refreshFold(card);
  return card;
}

// The row other text is folded behind (when it's folded away, see the toolbar toggle).
function refreshFold(card) {
  const fold = card.querySelector(".other-fold");
  if (!fold) return;
  const count = card.querySelectorAll(".line.other").length;
  const what = count === 1 ? "1 line of other text" : `${count} lines of other text`;
  fold.textContent = card.classList.contains("show-other") ? `▾ Fold ${what}` : `▸ ${what}`;
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
  if (state.ai.enabled && line.kind === "dialogue" && !line.hooked) {
    items.unshift(["Re-read with AI", () => reread(line, row)]);
  }
  if (line.capture) {
    items.push(["Set auto-read regions…", () => openRegionEditor(line)]);
  }
  // Buttons folded away in a narrow window come first.
  const folded = [...row.querySelectorAll(".line-btn.foldable")].filter((b) => getComputedStyle(b).display === "none");
  items.unshift(...folded.map((b) => [b.textContent, () => b.click()]));
  if (line.captureId) {
    items.push(["Save debug bundle…", () => openBundleDialog(line)]);
  }
  showMenu(anchor, items, `Remembered for ${line.game}.`);
}

function showMenu(anchor, items, note) {
  closePopover();
  lineMenu.replaceChildren(...items.map(([label, action, detail]) => {
    const item = button("menu-item", label, () => {
      closeLineMenu();
      action();
    });
    if (detail) item.append(el("small", null, detail));
    return item;
  }));
  lineMenu.append(el("p", "menu-note", note));
  lineMenu.hidden = false;
  const rect = anchor.getBoundingClientRect();
  lineMenu.style.top = `${Math.min(rect.bottom + 4, window.innerHeight - lineMenu.offsetHeight - 8)}px`;
  lineMenu.style.left = `${Math.max(8, rect.right - lineMenu.offsetWidth)}px`;
}

function closeLineMenu() {
  lineMenu.hidden = true;
}

// --- Auto-read regions: the parts of a game's picture auto-capture watches --------------

const regionEditor = $("#region-editor");
const REGION_COLORS = ["#f2c14e", "#3fb8a5", "#ef6f8e", "#6aa0ff", "#b48ef0"];
const MIN_REGION = 0.02; // smallest region side, as a fraction of the picture

async function openRegionEditor(line) {
  closePopover();
  const result = await api().region_editor(line.id);
  if (!result.ok) {
    horu.setStatus({ state: "error", message: result.message });
    return;
  }
  const editor = {
    line,
    regions: result.regions.map((r) => ({ name: r.name, box: [...r.box] })),
    proposed: result.proposed,
    selected: null,
  };
  if (!editor.regions.length) {
    editor.regions.push({ name: "Dialogue", box: [...result.proposed] });
  }
  editor.selected = editor.regions.length - 1;

  $("#region-game").textContent = result.game;
  const stage = $("#region-stage");
  const img = el("img");
  img.src = result.image;
  img.alt = `Capture from ${result.game}`;
  img.draggable = false;
  stage.replaceChildren(img);
  stage.style.aspectRatio = String(result.aspect);
  editor.stage = stage;
  regionEditor.editor = editor;
  renderRegions(editor);
  regionEditor.hidden = false;
}

function closeRegionEditor() {
  regionEditor.hidden = true;
  regionEditor.editor = null;
}

function renderRegions(editor) {
  const stage = editor.stage;
  stage.querySelectorAll(".region").forEach((node) => node.remove());
  editor.regions.forEach((region, index) => {
    const color = REGION_COLORS[index % REGION_COLORS.length];
    const node = el("div", "region" + (index === editor.selected ? " selected" : ""));
    node.style.setProperty("--region", color);
    node.dataset.index = index;
    placeRegion(node, region.box);
    node.append(el("span", "region-label", region.name || `Region ${index + 1}`));
    for (const corner of ["nw", "ne", "sw", "se"]) {
      const handle = el("span", `region-handle ${corner}`);
      handle.dataset.corner = corner;
      node.append(handle);
    }
    stage.append(node);
  });

  const list = $("#region-list");
  list.replaceChildren(...editor.regions.map((region, index) => {
    const item = el("li", "region-item" + (index === editor.selected ? " selected" : ""));
    const swatch = el("span", "region-swatch");
    swatch.style.background = REGION_COLORS[index % REGION_COLORS.length];
    const name = el("input");
    name.type = "text";
    name.value = region.name;
    name.placeholder = `Region ${index + 1}`;
    name.addEventListener("focus", () => selectRegion(editor, index));
    name.addEventListener("input", () => {
      region.name = name.value;
      const label = stage.querySelector(`.region[data-index="${index}"] .region-label`);
      if (label) label.textContent = name.value || `Region ${index + 1}`;
    });
    const remove = button("line-btn", "Remove", () => {
      editor.regions.splice(index, 1);
      editor.selected = editor.regions.length ? Math.min(index, editor.regions.length - 1) : null;
      renderRegions(editor);
    });
    item.append(swatch, name, remove);
    return item;
  }));
  $("#region-empty").hidden = editor.regions.length > 0;
}

function placeRegion(node, [left, top, right, bottom]) {
  node.style.left = `${left * 100}%`;
  node.style.top = `${top * 100}%`;
  node.style.width = `${(right - left) * 100}%`;
  node.style.height = `${(bottom - top) * 100}%`;
}

function selectRegion(editor, index) {
  if (editor.selected === index) return;
  editor.selected = index;
  editor.stage.querySelectorAll(".region").forEach((node) => node.classList.toggle("selected", Number(node.dataset.index) === index));
  $("#region-list").querySelectorAll(".region-item").forEach((node, i) => node.classList.toggle("selected", i === index));
}

// Dragging: a region's body moves it, a corner resizes it, empty picture draws a new one.
$("#region-stage").addEventListener("pointerdown", (e) => {
  const editor = regionEditor.editor;
  if (!editor || e.button !== 0) return;
  e.preventDefault();
  const stage = editor.stage;
  const rect = stage.getBoundingClientRect();
  const at = (event) => [
    Math.min(Math.max((event.clientX - rect.left) / rect.width, 0), 1),
    Math.min(Math.max((event.clientY - rect.top) / rect.height, 0), 1),
  ];
  const [x0, y0] = at(e);
  const regionNode = e.target.closest(".region");
  let index, corner;
  if (regionNode) {
    index = Number(regionNode.dataset.index);
    corner = e.target.dataset.corner || null;
  } else {
    editor.regions.push({ name: "", box: [x0, y0, x0, y0] });
    index = editor.regions.length - 1;
    corner = "se";
    editor.selected = index;
    renderRegions(editor);
  }
  selectRegion(editor, index);
  const region = editor.regions[index];
  const start = [...region.box];
  const node = () => stage.querySelector(`.region[data-index="${index}"]`);

  const move = (event) => {
    const [x, y] = at(event);
    const dx = x - x0, dy = y - y0;
    let [left, top, right, bottom] = start;
    if (!corner) {
      const w = right - left, h = bottom - top;
      left = Math.min(Math.max(left + dx, 0), 1 - w);
      top = Math.min(Math.max(top + dy, 0), 1 - h);
      right = left + w;
      bottom = top + h;
    } else {
      if (corner.includes("w")) left = Math.min(left + dx, right - MIN_REGION);
      if (corner.includes("e")) right = Math.max(right + dx, left + MIN_REGION);
      if (corner.includes("n")) top = Math.min(top + dy, bottom - MIN_REGION);
      if (corner.includes("s")) bottom = Math.max(bottom + dy, top + MIN_REGION);
      [left, top, right, bottom] = [Math.max(left, 0), Math.max(top, 0), Math.min(right, 1), Math.min(bottom, 1)];
    }
    region.box = [left, top, right, bottom];
    placeRegion(node(), region.box);
  };
  const up = () => {
    stage.removeEventListener("pointermove", move);
    stage.removeEventListener("pointerup", up);
    stage.removeEventListener("pointercancel", up);
    const [left, top, right, bottom] = region.box;
    if (!regionNode && (right - left < 0.03 || bottom - top < 0.03)) {
      // A click on the picture, not a drag: no new region.
      editor.regions.splice(index, 1);
      editor.selected = editor.regions.length ? editor.regions.length - 1 : null;
    }
    renderRegions(editor);
  };
  try {
    stage.setPointerCapture(e.pointerId); // keeps the drag going outside the picture
  } catch {
    // not a real pointer (a test); the drag still works inside the picture
  }
  stage.addEventListener("pointermove", move);
  stage.addEventListener("pointerup", up);
  stage.addEventListener("pointercancel", up);
});

$("#region-add").addEventListener("click", () => {
  const editor = regionEditor.editor;
  if (!editor) return;
  editor.regions.push({ name: "", box: [...editor.proposed] });
  editor.selected = editor.regions.length - 1;
  renderRegions(editor);
});

$("#region-cancel").addEventListener("click", closeRegionEditor);
$("#region-close").addEventListener("click", closeRegionEditor);
regionEditor.addEventListener("click", (e) => {
  if (e.target === regionEditor) closeRegionEditor();
});

$("#region-save").addEventListener("click", async () => {
  const editor = regionEditor.editor;
  if (!editor) return;
  const regions = editor.regions.map((r, i) => ({ name: r.name.trim() || `Region ${i + 1}`, box: r.box }));
  const result = await api().save_regions(editor.line.id, regions);
  horu.setStatus({ state: result.ok ? "ready" : "error", message: result.message });
  if (result.ok) closeRegionEditor();
});

// --- The capture hotkey: recorded from the next key combination pressed ------------------

let recordingHotkey = false;

function showHotkey(info, message, ok = true) {
  $("#hotkey-name").textContent = info.name;
  $("#hotkey-chip").textContent = info.error ? "" : info.name;
  $("#welcome-hotkey").textContent = info.name;
  const status = $("#hotkey-status");
  status.textContent = message || (info.error ? `${info.error} Choose another.` : "Works while your game is in front.");
  status.classList.toggle("error", !ok || (!message && Boolean(info.error)));
}

// A KeyboardEvent as a hotkey spec ("<ctrl>+<alt>+j"), or null for a lone modifier.
function hotkeySpec(e) {
  let key = null;
  if (/^Key[A-Z]$/.test(e.code)) key = e.code.slice(3).toLowerCase();
  else if (/^Digit\d$/.test(e.code)) key = e.code.slice(5);
  else if (/^F([1-9]|1\d|2[0-4])$/.test(e.code)) key = e.code.toLowerCase();
  else key = { Space: "space", Insert: "insert", Delete: "delete", Home: "home", End: "end",
    PageUp: "page_up", PageDown: "page_down", Pause: "pause" }[e.code] || null;
  if (!key) return null;
  const mods = [e.ctrlKey && "<ctrl>", e.altKey && "<alt>", e.shiftKey && "<shift>", e.metaKey && "<win>"].filter(Boolean);
  return [...mods, key.length > 1 ? `<${key}>` : key].join("+");
}

function stopRecordingHotkey() {
  recordingHotkey = false;
  $("#hotkey-name").classList.remove("recording");
  $("#hotkey-change").textContent = "Change";
}

$("#hotkey-change").addEventListener("click", async () => {
  if (recordingHotkey) {
    stopRecordingHotkey();
    showHotkey(await api().hotkey_info());
    return;
  }
  recordingHotkey = true;
  $("#hotkey-name").classList.add("recording");
  $("#hotkey-name").textContent = "Press the keys…";
  $("#hotkey-change").textContent = "Cancel";
  $("#hotkey-status").textContent = "A letter, number, or F-key, with Ctrl, Alt, or Shift (F-keys work alone).";
  $("#hotkey-status").classList.remove("error");
});

document.addEventListener("keydown", async (e) => {
  if (!recordingHotkey) return;
  e.preventDefault();
  e.stopImmediatePropagation();
  if (e.key === "Escape") {
    stopRecordingHotkey();
    showHotkey(await api().hotkey_info());
    return;
  }
  const spec = hotkeySpec(e);
  // Ignore a lone modifier, and a key that's needed for typing or playing (J, Space) alone.
  if (!spec || !(/^<f\d+>$/.test(spec) || /<(ctrl|alt|shift|win)>/.test(spec))) return;
  stopRecordingHotkey();
  const result = await api().set_hotkey(spec);
  showHotkey(result, result.message, result.ok);
}, true);

// --- Debug bundles: a capture and what Horu made of it, for bug reports ------------------

const bundleDialog = $("#bundle-dialog");

function openBundleDialog(line) {
  closePopover();
  toggleSettings(false);
  bundleDialog.lineId = line ? line.id : null;
  $("#bundle-what").textContent = line
    ? `Saves the capture with 「${line.text.slice(0, 20)}${line.text.length > 20 ? "…" : ""}」 in it.`
    : "Saves your last capture, even one where Horu found no text.";
  $("#bundle-note").value = "";
  bundleDialog.hidden = false;
  $("#bundle-note").focus();
}

function closeBundleDialog() {
  bundleDialog.hidden = true;
}

$("#bundle-last").addEventListener("click", () => openBundleDialog(null));
// Recording lasts only while Horu runs (it's never on at startup), so the button holds the state.
function setRecording(on) {
  $("#record").dataset.on = on ? "1" : "";
  $("#record").textContent = on ? "Stop recording" : "Record auto-capture";
}
$("#record").addEventListener("click", async () => {
  const result = await api().record(!$("#record").dataset.on);
  setRecording(result.recording);
  if (result.message) horu.setStatus({ state: "ready", message: result.message });
});
$("#bundle-cancel").addEventListener("click", closeBundleDialog);
$("#bundle-close").addEventListener("click", closeBundleDialog);
bundleDialog.addEventListener("click", (e) => {
  if (e.target === bundleDialog) closeBundleDialog();
});
$("#bundle-save").addEventListener("click", async () => {
  const result = await api().save_debug_bundle(bundleDialog.lineId, $("#bundle-note").value);
  horu.setStatus({ state: result.ok ? "ready" : "error", message: result.message });
  closeBundleDialog();
});

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
    refreshFold(row.closest(".capture"));
    const kindLabel = row.querySelector(".kind-label");
    if (kindLabel) kindLabel.remove();
    if (KIND_LABEL[line.kind]) row.querySelector(".spacer").before(el("span", "kind-label", KIND_LABEL[line.kind]));
    if (state.ai.enabled && !row.querySelector(".translation")) {
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
  delete line.translation; // a repeat's copied translation is of the old text too
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
// The suggestion against what was read, character by character: what OCR read that the
// suggestion drops is struck out in red, what it adds is green. Spacing is ignored.
function diffNodes(before, text) {
  const a = [...before.replace(/[\s　]/g, "")];
  const b = [...text.replace(/[\s　]/g, "")];
  // Longest common subsequence table, filled from the end.
  const lcs = Array.from({ length: a.length + 1 }, () => new Array(b.length + 1).fill(0));
  for (let i = a.length - 1; i >= 0; i--) {
    for (let j = b.length - 1; j >= 0; j--) {
      lcs[i][j] = a[i] === b[j] ? lcs[i + 1][j + 1] + 1 : Math.max(lcs[i + 1][j], lcs[i][j + 1]);
    }
  }
  const nodes = [];
  const push = (kind, char) => {
    const last = nodes[nodes.length - 1];
    if (last && last.kind === kind) last.text += char;
    else nodes.push({ kind, text: char });
  };
  let i = 0;
  let j = 0;
  while (i < a.length || j < b.length) {
    if (i < a.length && j < b.length && a[i] === b[j]) { push("same", a[i]); i++; j++; }
    else if (j < b.length && (i === a.length || lcs[i][j + 1] >= lcs[i + 1][j])) push("add", b[j++]);
    else push("drop", a[i++]);
  }
  return nodes.map(({ kind, text: chars }) =>
    kind === "same" ? chars : el(kind === "add" ? "ins" : "del", `fix-${kind}`, chars));
}

function showSuggestion(line, row, text) {
  const old = row.querySelector(".fix-chip");
  if (old) old.remove();
  const chip = el("div", "fix-chip");
  const quote = el("span", "fix-text");
  quote.lang = "ja";
  quote.append("「", ...diffNodes(line.text, text), "」?");
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
  if (line.translation) {
    // A repeat of a line already translated.
    row.dataset.state = "done";
    translationText(row).textContent = line.translation;
  } else if (auto) {
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
  actions.append(button("pop-action secondary", "Explain in context", () => explainWord(line, index, part)));
  actions.append(saveControl(line, index, part, word, body, wordEl));
  popover.append(body, actions);
  popover.hidden = false;
  positionPopover(wordEl);

  showLookup(word, body, wordEl);
}

// Save keeps the word in Horu for the Study tab. Anki users get "Add to Anki", with a ▾ to
// save it in Horu instead.
function saveControl(line, index, part, word, body, wordEl) {
  const run = async (target, label, busy, done, call) => {
    target.disabled = true;
    target.textContent = state.ai.enabled ? "Writing card..." : busy;
    const result = await call();
    target.textContent = result.ok ? done : label;
    target.disabled = result.ok;
    target.classList.toggle("done", result.ok);
    if (!result.ok) body.append(el("p", "pop-note error", result.message));
    else horu.setStatus({ state: "ready", message: result.message });
    positionPopover(wordEl);
    return result;
  };
  const saveInHoru = (target, label) => run(target, label, "Saving...", "Saved", async () => {
    const result = await api().save_word(line.id, index, part);
    if (result.ok) refreshCounts();
    return result;
  });
  if (state.prefs.studyWith !== "anki") {
    const save = button("pop-action primary", "Save", () => saveInHoru(save, "Save"), "Keep this word, with this line and screenshot, for the Study tab");
    return save;
  }
  const split = el("div", "split");
  const add = button("pop-action primary", "Add to Anki", () => run(add, "Add to Anki", "Adding...", "Added", async () => {
    const result = await api().save_card(line.id, index, part);
    if (result.ok) horu.cardsAdded({ lineId: line.id, words: [word.dictionary_form] });
    return result;
  }));
  const menu = el("div", "split-menu");
  menu.hidden = true;
  const more = button("pop-action primary more-btn", "▾", () => { menu.hidden = !menu.hidden; }, "More ways to save");
  menu.append(button("menu-item", "Save in Horu instead", () => {
    menu.hidden = true;
    saveInHoru(add, "Add to Anki");
    more.disabled = true;
  }));
  split.append(add, more, menu);
  return split;
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
  if (word.jlpt) {
    const level = el("span", "chip jlpt", `N${word.jlpt}`);
    level.title = `On the JLPT N${word.jlpt} vocabulary list`;
    chips.append(level);
  }
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
    const on = e.currentTarget.getAttribute("aria-pressed") !== "true";
    setPref(key, on);
    applyToggle(key);
    if (key === "autoCapture" && on && !state.prefs.autoTipSeen) $("#auto-tip").hidden = false;
  });
}

// Shown once, the first time auto-capture is turned on.
function closeAutoTip() {
  $("#auto-tip").hidden = true;
  setPref("autoTipSeen", true);
}
$("#auto-tip-close").addEventListener("click", closeAutoTip);
$("#auto-tip-regions").addEventListener("click", () => {
  closeAutoTip();
  openLatestRegions();
});

$("#snip").addEventListener("click", async () => {
  const result = await api().snip();
  if (!result.ok) horu.setStatus({ state: "error", message: result.message });
});

// Auto-read regions are drawn on the latest hotkey capture (auto-capture doesn't keep the
// picture), preferring one with dialogue, whose box is the suggested region.
function openLatestRegions() {
  const lines = [...state.lines.values()].filter((l) => l.capture).reverse();
  const line = lines.find((l) => l.kind === "dialogue") || lines[0];
  if (!line) {
    horu.setStatus({ state: "error", message: `Regions are drawn on a capture: press ${$("#hotkey-chip").textContent || "the hotkey"} in your game first, then click this again.` });
    return;
  }
  openRegionEditor(line);
}
$("#edit-regions").addEventListener("click", () => {
  toggleSettings(false);
  openLatestRegions();
});

// Settings-panel checkboxes and text fields: [element, preference, default].
const FIELDS = [
  ["#anki-deck", "ankiDeck", "Horu"],
  ["#auto-cards", "autoCards", false],
  ["#hide-from-capture", "hideFromCapture", false],
  ["#minimize-to-tray", "minimizeToTray", false],
  ["#auto-translate", "autoTranslate", true],
  ["#ai-checks", "aiChecks", true],
  ["#apply-ai-readings", "applyAiReadings", true],
  ["#blur-translations", "blurTranslations", true],
  ["#learner-level", "learnerLevel", ""],
  ["#hook-source", "hookSource", ""],
  ["#hook-url", "hookUrl", "ws://127.0.0.1:6677"],
  ["#study-with", "studyWith", "horu"],
  ["#new-per-day", "newPerDay", 10],
];

function applyFields() {
  for (const [selector, key, fallback] of FIELDS) {
    const input = $(selector);
    const value = state.prefs[key] ?? fallback;
    if (input.type === "checkbox") input.checked = value;
    else if (document.activeElement !== input) input.value = value;
  }
  document.body.classList.toggle("blur-translations", state.prefs.blurTranslations ?? true);
  $("#hook-url-row").hidden = state.prefs.hookSource !== "websocket";
  $("#study-with").value = state.prefs.studyWith || "horu"; // "": not chosen yet, which works like Horu
}

for (const [selector, key] of FIELDS) {
  const input = $(selector);
  input.addEventListener("change", () => {
    setPref(key, input.type === "checkbox" ? input.checked : input.type === "number" ? Number(input.value) : input.value.trim());
    applyFields();
    if (key === "studyWith" || key === "newPerDay") refreshCounts();
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
  applyTheme();
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
  if (open) showView("settings");
  else if (state.view === "settings") showView(state.lastView);
}
$("#open-settings").addEventListener("click", () => toggleSettings(state.view !== "settings"));

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
  if (!e.target.closest(".split")) popover.querySelectorAll(".split-menu").forEach((m) => { m.hidden = true; });
});
document.addEventListener("keydown", (e) => {
  if (e.key === "Escape") {
    const open = !popover.hidden || !lineMenu.hidden || !regionEditor.hidden || !bundleDialog.hidden;
    closePopover();
    closeLineMenu();
    closeRegionEditor();
    closeBundleDialog();
    if (!open) toggleSettings(false);
  }
});
linesEl.addEventListener("scroll", () => {
  if (state.selected) positionPopover(state.selected);
});
window.addEventListener("resize", closePopover);

// --- Anki setup: cards go to AnkiWeb, no Anki app needed -------------------------------------
// The same controls in the welcome screen and in Settings → Study.

const ANKI_SETUP = `
  <div class="via-web">
    <p class="check-hint flush">Cards go to your AnkiWeb account: review them at ankiweb.net, on your phone, or in
      Anki on any computer once it syncs.</p>
    <div class="web-account" hidden>
      <span class="web-who"></span>
      <div class="test-row"><button type="button" class="web-sync">Sync now</button><button type="button" class="web-out">Sign out</button></div>
    </div>
    <form class="web-sign-in">
      <label class="field"><span>AnkiWeb email</span><input class="web-email" type="email" autocomplete="username" spellcheck="false"></label>
      <label class="field"><span>Password</span><input class="web-password" type="password" autocomplete="current-password"></label>
      <div class="test-row"><button type="submit">Sign in</button><button type="button" class="link web-signup">Create a free AnkiWeb account</button></div>
    </form>
    <p class="web-status test-result"></p>
    <p class="check-hint flush">Your password goes only to AnkiWeb; Horu keeps the sign-in key AnkiWeb returns in
      Windows Credential Manager. Cards sync as you add them.</p>
  </div>`;

function ankiSetup(root) {
  root.innerHTML = ANKI_SETUP;
  const find = (selector) => root.querySelector(selector);
  const show = (status) => {
    if (!status) return;
    const signedIn = Boolean(status.account);
    find(".web-account").hidden = !signedIn;
    find(".web-sign-in").hidden = signedIn;
    find(".web-who").textContent = signedIn ? `Signed in as ${status.account}.` : "";
    const result = find(".web-status");
    result.className = `web-status test-result ${status.error && signedIn ? "error" : signedIn ? "ok" : ""}`;
    result.textContent = signedIn ? (status.error || `Cards go to the "${status.deck}" deck on AnkiWeb.`) : "";
  };
  const refresh = async () => show(await api().anki_status());
  find(".web-sign-in").addEventListener("submit", async (e) => {
    e.preventDefault();
    const email = find(".web-email").value.trim();
    const password = find(".web-password").value;
    if (!email || !password) return;
    const result = find(".web-status");
    result.className = "web-status test-result";
    result.textContent = "Signing in and downloading your collection...";
    const reply = await api().anki_sign_in(email, password);
    find(".web-password").value = "";
    if (reply.ok) refreshAnkiSetups();
    else {
      result.className = "web-status test-result error";
      result.textContent = reply.message;
    }
  });
  find(".web-signup").addEventListener("click", () => openExternal("https://ankiweb.net/account/signup"));
  find(".web-sync").addEventListener("click", async () => {
    find(".web-status").textContent = "Syncing...";
    const reply = await api().anki_sync();
    show(reply.ok ? { ...reply, error: null } : { ...(await api().anki_status()), error: reply.message });
    if (reply.ok) find(".web-status").textContent = "Synced with AnkiWeb.";
  });
  find(".web-out").addEventListener("click", async () => show(await api().anki_sign_out()));
  root.refresh = refresh;
  return root;
}

const ankiSetups = [ankiSetup($("#settings-anki-setup")), ankiSetup($("#welcome-anki-setup"))];

function refreshAnkiSetups() {
  for (const setup of ankiSetups) if (!setup.closest("[hidden]")) setup.refresh();
}

function checkAnki() {
  refreshAnkiSetups();
}

async function loadFromPython() {
  state.prefs = await api().get_prefs();
  horu.setHookState(await api().hook_state());
  showHotkey(await api().hotkey_info());
  applyAllPrefs();
  if (!state.prefs.welcomeDone) openWelcome();
  horu.setAI(await api().ai_info());
  await loadAISettings();
  refreshCounts();
}


// --- Look: colours and light or dark ---------------------------------------------------

const systemLight = window.matchMedia("(prefers-color-scheme: light)");

function applyTheme() {
  const palette = state.prefs.theme || "horu";
  const mode = state.prefs.colorMode || "system";
  const root = document.documentElement;
  root.dataset.palette = palette;
  root.dataset.mode = mode === "system" ? (systemLight.matches ? "light" : "dark") : mode;
  for (const b of document.querySelectorAll("#theme-picker [data-theme]")) b.setAttribute("aria-checked", String(b.dataset.theme === palette));
  for (const b of document.querySelectorAll("#mode-picker [data-mode]")) b.setAttribute("aria-checked", String(b.dataset.mode === mode));
}
systemLight.addEventListener("change", applyTheme);
for (const b of document.querySelectorAll("#theme-picker [data-theme]")) {
  b.addEventListener("click", () => { setPref("theme", b.dataset.theme); applyTheme(); });
}
for (const b of document.querySelectorAll("#mode-picker [data-mode]")) {
  b.addEventListener("click", () => { setPref("colorMode", b.dataset.mode); applyTheme(); });
}

// --- Tabs: Read, Words, Study, and the Settings page ------------------------------------

const VIEWS = { read: "#view-read", words: "#view-words", study: "#view-study", settings: "#settings" };
state.view = "read";
state.lastView = "read"; // the tab to go back to when Settings closes

function showView(name) {
  if (name !== "settings") state.lastView = name;
  state.view = name;
  for (const [view, selector] of Object.entries(VIEWS)) $(selector).hidden = view !== name;
  for (const tab of document.querySelectorAll(".tab")) tab.setAttribute("aria-selected", String(tab.dataset.view === name));
  $("#open-settings").setAttribute("aria-pressed", String(name === "settings"));
  closePopover();
  closeLineMenu();
  if (name !== "read") closeTutor();
  if (name === "words") loadWords();
  if (name === "study") loadStudy();
  if (name === "settings" && api()) checkAnki();
}
for (const tab of document.querySelectorAll(".tab")) tab.addEventListener("click", () => showView(tab.dataset.view));

for (const b of document.querySelectorAll(".settings-nav [data-pane]")) {
  b.addEventListener("click", () => showPane(b.dataset.pane));
}
function showPane(name) {
  for (const b of document.querySelectorAll(".settings-nav [data-pane]")) b.setAttribute("aria-current", String(b.dataset.pane === name));
  for (const pane of document.querySelectorAll(".pane")) pane.hidden = pane.dataset.pane !== name;
  $(".settings-panes").scrollTop = 0;
  if (name === "study") refreshAnkiSetups();
}

// The Study tab's count: reviews due plus new words left today.
async function refreshCounts() {
  if (!api()) return;
  const s = await api().study_summary();
  const count = s.due + s.new;
  $("#due-count").textContent = count;
  $("#due-count").hidden = count === 0;
  if (state.view === "study" && $("#review").hidden && $("#review-done").hidden) renderStudy(s);
}

// --- Words: everything saved, with the line it came from ---------------------------------

const wordFilter = { query: "", game: "", status: "" };
let wordSearchTimer = null;

$("#word-search").addEventListener("input", () => {
  clearTimeout(wordSearchTimer);
  wordSearchTimer = setTimeout(() => { wordFilter.query = $("#word-search").value; loadWords(); }, 200);
});

async function loadWords() {
  if (!api()) return;
  const { words, games } = await api().saved_words(wordFilter.query, wordFilter.game, wordFilter.status);
  renderWordFilters(games);
  const list = $("#word-list");
  list.replaceChildren(...words.map(wordRow));
  const filtered = wordFilter.query || wordFilter.game || wordFilter.status;
  $("#words-empty").hidden = words.length > 0 || filtered;
  $("#words-total").textContent = words.length === 1 ? "1 word" : `${words.length} words`;
  if (!words.length && filtered) $("#words-total").textContent = "No words match.";
  const toAnki = $("#words-to-anki");
  toAnki.hidden = state.prefs.studyWith !== "anki" || !words.length;
  toAnki.textContent = filtered ? "Add these to Anki" : "Add all to Anki";
  toAnki.disabled = false;
  toAnki.onclick = async () => {
    toAnki.disabled = true;
    toAnki.textContent = "Adding...";
    const result = await api().words_to_anki(words.map((w) => w.id));
    toAnki.textContent = result.ok ? "Added" : "Add to Anki";
    horu.setStatus({ state: result.ok ? "ready" : "error", message: result.message });
  };
}

function renderWordFilters(games) {
  const box = $("#word-filters");
  const chip = (label, key, value) => {
    const b = button(null, label, () => { wordFilter[key] = value; loadWords(); });
    b.setAttribute("aria-pressed", String(wordFilter[key] === value));
    return b;
  };
  const nodes = [chip("All games", "game", "")];
  if (games.length > 1) nodes.push(...games.map((g) => chip(gameName(g), "game", g)));
  nodes.push(el("span", "divider"), chip("Any", "status", ""), chip("New", "status", "new"), chip("Learning", "status", "learning"), chip("Known", "status", "known"));
  box.replaceChildren(...nodes);
}

function gameName(game) {
  return (game || "Unknown game").replace(/\.exe$/i, "");
}

const STATUS_LABEL = { new: "New", learning: "Learning", known: "Known" };

function wordRow(w) {
  const row = el("div", "word-row");
  const head = el("div", "word-row-head");
  head.lang = "ja";
  head.append(el("b", null, w.word));
  if (w.reading && w.reading !== w.word) head.append(el("span", "kana", w.reading));
  if (w.jlpt) head.append(el("span", "jlpt-tag", `N${w.jlpt}`));
  const side = el("div", "word-side");
  side.append(el("span", `status-pill ${w.status}`, STATUS_LABEL[w.status]));
  const remove = button("word-remove", "Remove", () => {
    if (remove.dataset.armed) {
      api().remove_word(w.id).then(() => { loadWords(); refreshCounts(); });
      return;
    }
    remove.dataset.armed = "1";
    remove.textContent = "Remove for good?";
    setTimeout(() => { delete remove.dataset.armed; remove.textContent = "Remove"; }, 3000);
  }, "Remove this word and its review history");
  side.append(remove);
  row.append(head, side, el("div", "meaning", w.meaning || ""));
  if (w.sentence) {
    const context = el("div", "context");
    context.lang = "ja";
    if (w.speaker) context.append(`${w.speaker} · `);
    context.append(...markedSentence(w.sentence, w.surface || w.word), ` · ${gameName(w.game)}`);
    row.append(context);
  }
  return row;
}

// The sentence with the word marked (its first appearance).
function markedSentence(sentence, surface) {
  const at = surface ? sentence.indexOf(surface) : -1;
  if (at < 0) return [sentence];
  return [sentence.slice(0, at), el("mark", null, surface), sentence.slice(at + surface.length)];
}

// --- Study: today, and reviews ------------------------------------------------------------

const review = { card: null, done: 0, total: 0 };

async function loadStudy() {
  if (!api()) return;
  $("#study-home").hidden = false;
  $("#review").hidden = true;
  $("#review-done").hidden = true;
  renderStudy(await api().study_summary());
}

function renderStudy(s) {
  $("#num-due").textContent = s.due;
  $("#num-new").textContent = s.new;
  $("#num-saved").textContent = s.saved;
  const streak = $("#streak");
  streak.replaceChildren();
  if (s.streak) streak.append(el("span", "streak-dot", s.streak), el("span", null, "day streak"));
  $("#num-due").parentElement.classList.toggle("due", s.due > 0);
  const most = Math.max(1, ...s.week.map((d) => d.reviews));
  $("#week").replaceChildren(...s.week.map((d, i) => {
    const day = el("div", i === 6 ? "day is-today" : "day");
    const bar = el("i", "week-bar");
    bar.style.height = `${Math.max(3, Math.round((d.reviews / most) * 52))}px`;
    day.title = `${d.reviews} ${d.reviews === 1 ? "review" : "reviews"}`;
    day.append(bar, d.day.slice(0, 2));
    return day;
  }));
  const cards = s.due + s.new;
  const start = $("#start-review");
  start.disabled = cards === 0;
  start.textContent = cards ? `Start review · ${cards} ${cards === 1 ? "card" : "cards"}` : "Nothing to review";
  review.total = cards;
  const note = $("#study-note");
  if (!s.saved) note.textContent = "Save words from the Read tab (click a word, then Save) to study them here.";
  else if (!cards) note.textContent = s.reviewedToday ? `All done for today: ${s.reviewedToday} ${s.reviewedToday === 1 ? "review" : "reviews"}.` : "Nothing due today.";
  else note.textContent = "";

  const anki = s.anki;
  $("#anki-study").hidden = !anki;
  if (anki) {
    $("#anki-due").textContent = anki.connected ? `${anki.due} due, ${anki.new} new in ${anki.deck}` : anki.error;
    $("#review-in-anki").textContent = "Review on AnkiWeb";
    $("#review-in-anki").disabled = !anki.connected;
  }

  $("#study-games").replaceChildren(...s.games.map((g) => {
    const row = el("div", "game");
    const icon = el("div", "game-icon", gameName(g.game).slice(0, 2).toUpperCase());
    const info = el("div");
    const bar = el("div", "progress");
    const fill = el("i");
    fill.style.width = `${Math.round((100 * g.known) / Math.max(1, g.words))}%`;
    bar.append(fill);
    info.append(gameName(g.game), el("small", null, `${g.words} ${g.words === 1 ? "word" : "words"} · ${g.known} known`), bar);
    row.append(icon, info, el("span", "due-chip", `${g.due} due`));
    return row;
  }));
}

$("#start-review").addEventListener("click", async () => {
  review.done = 0;
  showCard(await api().next_review());
});
$("#review-in-anki").addEventListener("click", async () => {
  const result = await api().review_in_anki();
  if (!result.ok) horu.setStatus({ state: "error", message: result.message });
});
$("#end-review").addEventListener("click", loadStudy);
$("#review-back").addEventListener("click", loadStudy);
$("#show-answer").addEventListener("click", showAnswer);
for (const b of document.querySelectorAll("#grades [data-rating]")) {
  b.addEventListener("click", () => answerCard(b.dataset.rating));
}
document.addEventListener("keydown", (e) => {
  if (state.view !== "study" || $("#review").hidden || (e.target instanceof Element && e.target.closest("input, textarea, select"))) return;
  if ((e.key === " " || e.key === "Enter") && !$("#show-answer").hidden) {
    e.preventDefault();
    showAnswer();
  } else if (!$("#grades").hidden && ["1", "2", "3", "4"].includes(e.key)) {
    answerCard(["again", "hard", "good", "easy"][Number(e.key) - 1]);
  } else if (!$("#grades").hidden && e.key === " ") {
    e.preventDefault();
    answerCard("good");
  }
});

function showCard(card) {
  review.card = card;
  $("#study-home").hidden = true;
  if (!card) {
    $("#review").hidden = true;
    $("#review-done").hidden = false;
    $("#review-done-text").textContent = review.done
      ? `${review.done} ${review.done === 1 ? "review" : "reviews"} done. New words and reviews come back as they're due.`
      : "Nothing is due right now.";
    refreshCounts();
    return;
  }
  $("#review").hidden = false;
  $("#review-done").hidden = true;
  $("#review-left").textContent = review.total ? `${Math.min(review.done + 1, review.total)} of ${review.total}` : `${review.done + 1}`;
  $("#review-meta").textContent = [card.speaker, gameName(card.game)].filter(Boolean).join(" · ");
  $("#review-sentence").replaceChildren(...markedSentence(card.sentence || card.word, card.surface || card.word));
  $("#review-word").textContent = card.word;
  $("#review-reading").textContent = card.reading !== card.word ? card.reading : "";
  $("#review-jlpt").hidden = !card.jlpt;
  $("#review-jlpt").textContent = card.jlpt ? `N${card.jlpt}` : "";
  $("#review-meaning").textContent = card.meaning;
  $("#review-notes").textContent = card.notes;
  $("#review-translation").textContent = card.translation;
  for (const b of document.querySelectorAll("#grades [data-rating]")) {
    b.querySelector("small").textContent = interval(card.intervals[b.dataset.rating]);
  }
  $("#review-answer").hidden = true;
  $("#grades").hidden = true;
  $("#show-answer").hidden = false;
  const shot = $("#review-shot");
  shot.hidden = true;
  shot.removeAttribute("src");
  if (card.image) {
    api().word_image(card.id).then((src) => {
      if (src && review.card === card) {
        shot.src = src;
        shot.hidden = false;
      }
    });
  }
}

function showAnswer() {
  $("#review-answer").hidden = false;
  $("#grades").hidden = false;
  $("#show-answer").hidden = true;
}

async function answerCard(rating) {
  if (!review.card) return;
  const id = review.card.id;
  review.card = null; // ignore a second key press while this one is saved
  review.done++;
  showCard(await api().answer_review(id, rating));
}

// Seconds until the next review, as "1 min", "10 min", "3 days", "2 mo".
function interval(seconds) {
  const minutes = Math.round(seconds / 60);
  if (minutes < 60) return `${Math.max(1, minutes)} min`;
  const hours = Math.round(minutes / 60);
  if (hours < 24) return `${hours} h`;
  const days = Math.round(hours / 24);
  if (days < 31) return days === 1 ? "1 day" : `${days} days`;
  const months = Math.round(days / 30);
  if (months < 12) return `${months} mo`;
  return `${(days / 365).toFixed(1)} yr`;
}

// --- Welcome: the first start ------------------------------------------------------------

const LEVELS = [
  "Just starting (learning kana)",
  "Beginner (studying with Genki I)",
  "Upper beginner (Genki II, around N4)",
  "Intermediate (around N3)",
  "Upper intermediate (around N2)",
  "Advanced (N1 and up)",
];
const welcome = { step: 0 };

function openWelcome() {
  welcome.step = 0;
  welcome.study = state.prefs.studyWith || "";
  $("#welcome-deck").value = state.prefs.ankiDeck || "Horu";
  $("#welcome").hidden = false;
  renderWelcome();
}

function closeWelcome() {
  $("#welcome").hidden = true;
  setPref("welcomeDone", true);
  refreshCounts();
}

function renderWelcome() {
  for (const step of document.querySelectorAll(".welcome-step")) step.hidden = Number(step.dataset.step) !== welcome.step;
  [...document.querySelectorAll("#welcome-steps i")].forEach((bar, i) => bar.classList.toggle("on", i <= welcome.step));
  for (const choice of document.querySelectorAll(".choice[data-study]")) choice.setAttribute("aria-pressed", String(choice.dataset.study === welcome.study));
  const ankiWasHidden = $("#welcome-anki").hidden;
  $("#welcome-anki").hidden = welcome.study !== "anki";
  if (ankiWasHidden && welcome.study === "anki") $("#welcome-anki-setup").refresh();
  $("#welcome-back").hidden = welcome.step === 0;
  $("#welcome-next").textContent = welcome.step === 3 ? "Finish" : "Continue";
  $("#welcome-next").disabled = welcome.step === 0 && !welcome.study;
  if (welcome.step === 1) {
    const current = state.prefs.learnerLevel || LEVELS[1];
    $("#welcome-levels").replaceChildren(...LEVELS.map((level) => {
      const b = button("choice", level, () => {
        setPref("learnerLevel", level);
        applyFields();
        renderWelcome();
      });
      b.setAttribute("aria-pressed", String(level === current));
      return b;
    }));
  }
  if (welcome.step === 3) renderWelcomeAI();
}

for (const choice of document.querySelectorAll(".choice[data-study]")) {
  choice.addEventListener("click", () => {
    welcome.study = choice.dataset.study;
    setPref("studyWith", welcome.study);
    applyFields();
    renderWelcome();
  });
}

$("#welcome-deck").addEventListener("change", () => {
  setPref("ankiDeck", $("#welcome-deck").value.trim() || "Horu");
  applyFields();
  refreshAnkiSetups();
});

// The last step: a provider, its key, and a model, set up right here. Ollama (models to
// download) and other servers (an address to fill in) are finished in Settings → AI.
const NEEDS_SETTINGS = new Set(["ollama", "custom"]);

async function renderWelcomeAI() {
  if (!aiSetup.settings) await loadAISettings();
  const s = aiSetup.settings;
  const select = $("#welcome-ai-provider");
  if (!select.options.length) {
    select.replaceChildren(...Object.entries(s.presets).map(([id, p]) => {
      const option = el("option", null, id === "none" ? "Not now" : p.label);
      option.value = id;
      return option;
    }));
    select.value = state.ai.enabled ? s.provider || "none" : "none";
    $("#welcome-ai-model").value = state.ai.enabled ? s.model : "";
  }
  const id = select.value;
  const preset = s.presets[id] || {};
  const on = id !== "none";
  const needsKey = Boolean(preset.key_env);
  $("#welcome-ai-key-row").hidden = !needsKey;
  $("#welcome-key-link").hidden = !preset.key_url;
  $("#welcome-key").placeholder = s.savedKeys.includes(id) ? "Key saved. Paste a new one to replace it" : "Paste your key";
  $("#welcome-ai-model-row").hidden = !on || NEEDS_SETTINGS.has(id);
  $("#welcome-ai-check-row").hidden = !on || NEEDS_SETTINGS.has(id);
  $("#welcome-ai-note").textContent = NEEDS_SETTINGS.has(id)
    ? `${preset.label}: Finish opens Settings → AI to ${id === "ollama" ? "download a model" : "fill in the server's address and model"}.`
    : on ? "Requests are billed to your own account with the provider. You can change this any time in Settings → AI."
      : "You can set up AI any time in Settings → AI.";
}

$("#welcome-ai-provider").addEventListener("change", () => {
  const s = aiSetup.settings;
  const id = $("#welcome-ai-provider").value;
  $("#welcome-ai-model").value = (s.presets[id] || {}).model || (id === s.provider ? s.model : "");
  $("#welcome-ai-status").textContent = "";
  renderWelcomeAI();
});

// Saves what's filled in. With `check`, also sends a tiny request. True when AI works.
async function applyWelcomeAI(check) {
  const id = $("#welcome-ai-provider").value;
  const status = $("#welcome-ai-status");
  const key = $("#welcome-key").value.trim();
  if (key) {
    await api().save_api_key(id, key);
    $("#welcome-key").value = "";
  }
  const changes = { aiProvider: id };
  if (!NEEDS_SETTINGS.has(id)) changes.aiModel = $("#welcome-ai-model").value.trim();
  const info = await api().set_ai(changes);
  horu.setAI(info);
  await loadAISettings();
  renderWelcomeAI();
  if (!info.enabled) {
    status.className = "test-result error";
    status.textContent = info.error || "That didn't work.";
    return false;
  }
  if (!check) return true;
  status.className = "test-result";
  status.textContent = "Checking...";
  const result = await api().test_ai();
  status.className = `test-result ${result.ok ? "ok" : "error"}`;
  status.textContent = result.ok ? `Working: ${info.label}.` : result.message;
  return result.ok;
}

$("#welcome-ai-check").addEventListener("click", () => applyWelcomeAI(true));
$("#welcome-key").addEventListener("keydown", (e) => {
  if (e.key === "Enter") applyWelcomeAI(true);
});
$("#welcome-key-link").addEventListener("click", () => {
  const preset = aiSetup.settings.presets[$("#welcome-ai-provider").value];
  if (preset && preset.key_url) openExternal(preset.key_url);
});

async function finishWelcome() {
  const id = $("#welcome-ai-provider").value;
  if (id === "none") {
    closeWelcome();
    showView("read");
    return;
  }
  if (NEEDS_SETTINGS.has(id)) {
    await applyWelcomeAI(false);
    closeWelcome();
    showView("settings");
    showPane("ai");
    return;
  }
  // A problem with the key or model is shown here, to fix (or Skip) before leaving.
  if (await applyWelcomeAI(false)) {
    closeWelcome();
    showView("read");
  }
}
$("#welcome-back").addEventListener("click", () => { welcome.step = Math.max(0, welcome.step - 1); renderWelcome(); });
$("#welcome-next").addEventListener("click", () => {
  if (welcome.step === 3) {
    finishWelcome();
    return;
  }
  welcome.step++;
  renderWelcome();
});
$("#welcome-skip").addEventListener("click", closeWelcome);
$("#show-welcome").addEventListener("click", openWelcome);

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

  // The AI read a doubtful name tag differently (or the learner undid that).
  speakerCorrected({ id, speaker }) {
    const line = state.lines.get(id);
    if (!line) return;
    line.speaker = speaker;
    const row = rowFor(id);
    if (!row) return;
    renderWords(line, row.querySelector(".speaker"), "speaker");
    const english = row.querySelector(".speaker-en");
    english.dataset.speaker = speaker.text;
    english.textContent = speaker.english || "";
    const guess = row.querySelector(".speaker-guess");
    if (guess) guess.remove();
    let tag = row.querySelector(".speaker-fixed");
    if (!speaker.was) {
      if (tag) tag.remove();
      return;
    }
    if (!tag) {
      tag = button("corrected-tag speaker-fixed", "corrected", () => api().set_speaker(id, line.speaker.was));
      english.after(tag);
    }
    tag.title = `Horu fixed a likely misread of the speaker's name. It was:\n${speaker.was}\n\nClick to undo.`;
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
    setTimeout(() => {
      card.classList.add("fading");
      card.classList.remove("new");
      setTimeout(() => card.classList.remove("fading"), 1600);
    }, 2500);
  },

  // `translated`: the fix came with the translation of the fixed text (already shown).
  lineCorrected({ id, text, words, was, translated }) {
    const line = state.lines.get(id);
    const row = rowFor(id);
    if (!line || !row) return;
    setLineText(line, row, text, words);
    markCorrected(line, row, was);
    if (!translated) refreshTranslation(line, row);
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

  // Hotkey captures keep finding dialogue in the same place: offer to auto-read it.
  showHotkey(info) {
    if (!recordingHotkey) showHotkey(info);
  },

  setHookState({ state: hookState, message }) {
    const status = $("#hook-status");
    status.hidden = hookState === "off";
    status.textContent = message;
    status.classList.toggle("ok", hookState === "listening");
    status.classList.toggle("error", hookState === "error");
  },

  // The game window this session captures (null: the next one the hotkey is pressed in).
  setGame(game) {
    $("#game-name").textContent = game ? game.label : "Choose game";
    $("#game-chip").classList.toggle("set", Boolean(game));
    $("#game-chip").title = game ? `Capturing ${game.title} (${game.game}). Click to change.` : "Choose the game window Horu captures";
  },

  setRecording,

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

// Start: show saved settings, then load the rest from Python.
applyAllPrefs();
applyAI();
if (api()) loadFromPython();
else window.addEventListener("pywebviewready", loadFromPython, { once: true });
