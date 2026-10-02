# Horu

A Japanese study buddy for video games. Press a hotkey while you play, and Horu reads the
Japanese on screen and shows it in a side window, Language Reactor style: furigana over the
kanji, words colour-coded by part of speech, and a click on any word for its dictionary form,
grammar (conjugations like 〜たら or 〜てしまう), and meaning. Optionally, an AI model can
explain lines in context.

The goal is to cut lookup time so you can stay in the game.

> **Status:** early development. See the roadmap below.

## How it works

```
Hotkey ─► Capture the game window
            │
            ▼
        OCR: find every line of text on screen, read it, group it into blocks
            │
            ▼
        Local analysis: words, conjugations, furigana, JMdict lookups (instant, offline)
            │
            ▼
        Line history window (clickable words) ◄──► AI translations and explanations (optional, bring your own key)
            │
            ▼
        Optional: Anki export
```

## Roadmap

- [x] **Phase 1:** hotkey → capture region → OCR → print text
- [x] **Phase 2:** line-history window with furigana, clickable words, and JMdict popups
- [x] **Phase 3:** AI translations, explanations, and follow-up questions (Claude, OpenAI-compatible APIs, Ollama)
- [ ] **Phase 4:** auto-capture and more text sources (owocr, text hooking)
- [ ] **Phase 5:** Anki export
- [ ] **Phase 6:** first release

## Setup

Horu uses [uv](https://docs.astral.sh/uv/) to manage Python and dependencies.

```sh
git clone https://github.com/LukasRuprecht/Horu.git
cd Horu
uv sync
uv run horu
```

The first run creates `config.toml` from `config.example.toml`, downloads the OCR models
(~100 MB), and builds the dictionary (~70 MB, a few seconds).

### Usage

`uv run horu` opens the Horu window; put it on a second monitor, or pin it on top. Switch
to your game and press the hotkey (default `Ctrl+Alt+J`) whenever text appears. Horu captures
the focused window, finds every line of Japanese on screen, and adds it to the history, so
there's no capture region to set up.

- Click a word for its dictionary form, reading, grammar notes, and meanings.
- Dialogue is shown large, with its speaker above it; speakers are found from the text
  (names, 〜の声), a different text style, or a different size. Other text such as button
  legends, menus, and the HUD is shown small, and can be hidden. Backlog screens (like
  Persona 5's Log & Voice) are split into one entry per speaker.
- Lines already shown in an earlier capture are collapsed rather than repeated.
- If OCR gets a character wrong, hover the line and click **Edit** to fix it.
- If Horu files something wrongly, use the line's **⋯** menu: treat it as dialogue or other
  text, or mark a speaker name. Corrections are remembered per game.
- With an AI provider set up (below): click **Translate** under a line (or turn on automatic
  translation in settings; translations stay blurred until you click them), **Explain** on a
  line for a grammar breakdown, or **Explain in context** in a word's popup. Ask follow-up
  questions in the panel that opens.

Other commands:

- `uv run horu console` prints captured text to the terminal instead of opening a window.
- `uv run horu read <image>` reads the Japanese text in a screenshot.
- `uv run horu snap` saves a capture to `captures/`.
- `uv run horu monitors` lists monitors, for the `monitor` and `region` capture modes.
- `uv run horu setup` re-downloads and rebuilds the dictionary.

### Capture modes

Set `capture.mode` in `config.toml`:

- `window` (default): the focused window. Windows only; elsewhere it falls back to `monitor`.
- `monitor`: a whole monitor.
- `region`: a fixed box. Useful for a capture-card preview, or to ignore everything but the
  dialogue box.

### OCR engines

- `rapidocr` (default): PaddleOCR's PP-OCRv5 models on ONNX Runtime. Finds and reads text
  anywhere on screen, and runs on CPU in well under a second.
- `manga_ocr`: reads a single block of text, so it needs a tight `region`. Install it with
  `uv sync --extra manga-ocr` (pulls in PyTorch, ~2 GB).

### Troubleshooting

- **Captures are black:** switch the game to borderless windowed; exclusive fullscreen
  often can't be captured.
- **"Hotkey is already used by another program":** something else (e.g. GPU or peripheral
  software) owns that shortcut. Choose another one in `config.toml`.
- **Hotkey does nothing in-game:** if the game runs as administrator, Horu must too.
- **Text is misread:** set `debug.save_captures = true` and open an issue with the PNG.

## AI providers

AI features are optional; everything else works without them. Horu never ships with API
keys: you bring your own, or run a free local model.

Open settings (the sliders button, top right) and pick a provider under **AI**:

| Provider | What you need |
|---|---|
| **Claude** | An API key from the [Anthropic Console](https://console.anthropic.com/settings/keys). Default model `claude-opus-5-5`. |
| **OpenAI** | An API key from the [OpenAI dashboard](https://platform.openai.com/api-keys) and a model name. |
| **Google Gemini** | An API key from [Google AI Studio](https://aistudio.google.com/apikey) and a model name. |
| **Ollama** | Free and runs on your PC: install [Ollama](https://ollama.com/download), then download a model from inside Horu. No key. |
| **Other** | Any OpenAI-compatible server (OpenRouter, LM Studio, ...): its address, model, and key if it needs one. |

Keys are saved in the Windows Credential Manager (the system keychain elsewhere), not in a
file. **Test** sends a tiny request to check everything works. You can also pick a separate,
smaller model just for translations.

<details>
<summary>Prefer config files?</summary>

If no provider is chosen in the app, Horu reads the `[ai]` section of `config.toml` (see the
comments in `config.example.toml`) and API keys from `.env` (copy `.env.example`).
</details>

### Local models with Ollama

Install [Ollama](https://ollama.com/download), choose **Ollama** in Horu's settings, and use
the **Download** buttons there, or run `ollama pull <name>` yourself. Suggestions, tested with
Horu's prompts on game dialogue:

| Use | Model | Download | Notes |
|---|---|---|---|
| Translations | [`7shi/gemma-2-jpn-translate:2b-instruct-q8_0`](https://ollama.com/7shi/gemma-2-jpn-translate) | 3.3 GB | Google's Japanese⇄English Gemma fine-tune. Fast (about 0.2 s a line on a GPU) and good for its size. Translation only. |
| Explanations, 12 GB+ graphics card | [`qwen3:14b`](https://ollama.com/library/qwen3) | 9.3 GB | Strong Japanese. Thinks before answering, so replies start a few seconds later. |
| Explanations, most PCs | [`gemma3:4b`](https://ollama.com/library/gemma3) or [`qwen3:4b`](https://ollama.com/library/qwen3) | 2.5 to 3.3 GB | Quick, readable answers, but gemma3:4b often got grammar wrong in testing. |
| Explanations, low-end PCs | [`gemma3:1b`](https://ollama.com/library/gemma3) | 0.8 GB | Runs almost anywhere; basic and sometimes wrong. |
| Not recommended | [`llama3.2:1b`](https://ollama.com/library/llama3.2) | 1.3 GB | Japanese isn't one of its supported languages; explanations came out garbled. |

A good setup on modest hardware is the translation model for translations plus a 4B model for
explanations, treating the explanations with some caution. Small models' grammar explanations
can be confidently wrong; Horu's own word popups (dictionary form and grammar notes) don't use
AI and are reliable. For trustworthy explanations, use a larger local model or a cloud provider.

**Cost:** Ollama is free. With a cloud provider, each translation, explanation, and follow-up
is one request billed to your account. Automatic translation makes one request per new dialogue line, so it's off by
default. With Claude, Horu uses low effort for fast replies and turns on Anthropic's
server-side refusal fallback, so the rare request a model declines is retried on another
model instead of failing.

Horu sends the provider the line's text, the few dialogue lines before it (for context), and
your level. No screenshots are sent.

## License and credits

Horu is MIT licensed. See [LICENSE](LICENSE).

- Dictionary data: [JMdict](https://www.edrdg.org/jmdict/j_jmdict.html) and
  [JMnedict](https://www.edrdg.org/enamdict/enamdict_doc.html) (names), property of the
  [Electronic Dictionary Research and Development Group](https://www.edrdg.org/), used under
  [CC BY-SA 4.0](https://www.edrdg.org/edrdg/licence.html). Downloaded on first run, not
  bundled.
- Word analysis: [fugashi](https://github.com/polm/fugashi) with
  [UniDic](https://clrd.ninjal.ac.jp/unidic/) (unidic-lite).
- OCR: [RapidOCR](https://github.com/RapidAI/RapidOCR) running
  [PaddleOCR](https://github.com/PaddlePaddle/PaddleOCR) models.
