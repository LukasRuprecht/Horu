# Contributing to Horu

Thanks for wanting to help. Horu is a small project, so the most useful contributions are
often not code: a debug bundle from a game Horu reads badly is worth more than most patches.

## Reporting a screen Horu got wrong

1. In Horu, open the line's **⋯** menu (or **Settings → Help**) and choose **Save debug
   bundle**.
2. Open the zip and look at `capture.png`: it's your game window as it was. Don't share it if
   something private is in view.
3. Attach the zip to an [issue](https://github.com/LukasRuprecht/Horu/issues) and say what
   you expected Horu to show.

Bundles become regression tests (`horu add-screen`), so a fixed screen stays fixed.

## Setting up for development

You need Windows 10 or 11 and [uv](https://docs.astral.sh/uv/).

```sh
git clone https://github.com/LukasRuprecht/Horu.git
cd Horu
uv sync
uv run horu          # the app
uv run pytest        # the tests (the first run downloads the OCR models, about 100 MB)
```

Horu keeps its settings and data in the folder it runs from (`config.toml`, `data/`), so a
source checkout doesn't touch an installed Horu's data.

## Where things are

| Path | What |
|---|---|
| `src/horu/app.py` | The window's backend: captures, the line history, AI requests |
| `src/horu/text_sources/` | OCR (RapidOCR) and text hookers |
| `src/horu/layout.py` | Grouping OCR lines into dialogue, speakers, and other text |
| `src/horu/autocapture.py`, `reads.py` | Noticing new text, and the queue of reads |
| `src/horu/nlp/`, `dictionary.py` | Words, conjugations, furigana, JMdict lookups |
| `src/horu/llm/` | AI providers |
| `src/horu/ui/` | The page shown in the window (plain HTML, CSS, and JavaScript: no build step) |
| `tests/fixtures/*_screens.json` | Regression screens: OCR output of real game screens and what Horu should make of them |
| `evals/` | Scripts that measure OCR, word splitting, AI requests, and auto-capture on recordings |
| `packaging/` | The Windows build (PyInstaller, then Inno Setup) |

## Making a change

- Keep changes small and focused, and say in the pull request what you tested.
- Add or update a test for what you fixed. For a layout problem, that is usually a regression
  screen from a debug bundle.
- A change to how text is grouped or how auto-capture decides to read should be checked
  against all the regression screens (`uv run pytest tests/test_game_screens.py`), not only
  the game it was written for. Rules that only fit one game tend to break another.
- Run `uv run pytest` before opening the pull request.
- Don't commit game screenshots. Regression screens hold only the text OCR found and where
  it was (`tests/fixtures/*_screens.json`), and the pictures the auto-capture tests watch are
  drawn by `tests/fixtures/make_screens.py`. Never commit API keys, `config.toml`, or anything
  from `data/`.

## Building the installer

Install [Inno Setup 6](https://jrsoftware.org/isinfo.php) (`winget install JRSoftware.InnoSetup`),
then:

```powershell
powershell -ExecutionPolicy ByPass -File packaging\build.ps1
```

It writes `dist\Horu\` (run `Horu.exe` there to try the build) and
`dist\Horu-Setup-<version>.exe`. The version comes from `src/horu/__init__.py`; keep
`pyproject.toml` the same.

## Licence

By contributing you agree that your contribution is released under the [MIT licence](LICENSE).
