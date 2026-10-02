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

The first run creates `config.toml` from `config.example.toml` and downloads the manga-ocr
model (~450 MB).

### Setting the capture region

1. `uv run horu monitors` lists your monitors and their resolutions.
2. Edit the `[profiles.*]` region in `config.toml` so it covers the game's dialogue box.
   Coordinates are relative to the chosen monitor's top-left corner.
3. `uv run horu snap` saves the region to `captures/` so you can check it lines up.

Then run `uv run horu` and press the hotkey (default `Ctrl+Alt+J`) whenever text appears.

### Troubleshooting

- **Captures are black:** switch the game to borderless windowed; exclusive fullscreen
  often can't be captured.
- **Hotkey does nothing in-game:** if the game runs as administrator, Horu must too.
- **Garbled OCR:** make the region hug the dialogue text and leave out name tags and icons.
  `crop_to_text` (on by default) trims empty space, since manga-ocr struggles with
  wide, mostly empty images.

## AI providers

Horu never ships with API keys. You bring your own (put them in `.env`, see `.env.example`),
or run a free local model with [Ollama](https://ollama.com). Dictionary lookups work without
any AI provider at all.

## License

MIT. See [LICENSE](LICENSE).
