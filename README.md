# Horu

A Japanese study buddy for video games. Press a hotkey while you play, and Horu reads the
dialogue on screen, breaks it into words with instant dictionary lookups, and (optionally)
asks an AI model to explain the kanji, vocabulary, and grammar at your level.

The goal is to cut lookup time so you can stay in the game.

> **Status:** early development. See the roadmap below.

## How it works

```
Hotkey ─► Capture (screen region per game)
            │
            ▼
        Text source:  game text hook ─► OCR (manga-ocr, owocr) ─► vision LLM fallback
            │
            ▼
        Local parsing:  tokenizer + JMdict lookups (instant, offline)
            │
            ▼
        Overlay UI (clickable words, "Explain", follow-up chat) ◄──► LLM (bring your own key)
            │
            ▼
        Optional: Anki export
```

## Roadmap

- [x] **Phase 1:** hotkey → capture region → OCR → print text
- [ ] **Phase 2:** overlay with clickable tokens and JMdict popups
- [ ] **Phase 3:** LLM explanations and follow-up chat (Claude, OpenAI-compatible APIs, Ollama)
- [ ] **Phase 4:** more text sources (owocr, text hooking, vision fallback) and a region picker
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

The first run creates `config.toml` from `config.example.toml` and downloads the OCR models
(~100 MB).

### Usage

Start Horu, switch to your game, and press the hotkey (default `Ctrl+Alt+J`) whenever text
appears. Horu captures the focused window, finds every line of Japanese on screen, and
groups them into blocks, so there's no capture region to set up:

```
[0.41s] 2 block(s):
  4/7火 放課後
  順平: おいおい、転校初日から遅刻かよ？まあ、オレも人のこと言えねーけどな。
```

Other commands:

- `uv run horu read <image>` reads the Japanese text in a screenshot.
- `uv run horu snap` saves a capture to `captures/`.
- `uv run horu monitors` lists monitors, for the `monitor` and `region` capture modes.

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

Horu never ships with API keys. You bring your own (put them in `.env`, see `.env.example`),
or run a free local model with [Ollama](https://ollama.com). Dictionary lookups work without
any AI provider at all.

## License

MIT. See [LICENSE](LICENSE).
