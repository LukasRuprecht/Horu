# Horu: status and what's next

Last updated 14 October 2026 (commit `8fe8443`). The README's [roadmap](../README.md#roadmap)
has the short version; this page has the detail.

## Where things stand

Horu works end to end on Windows. Press a hotkey in a game, or let auto-capture watch it, and
Horu reads the Japanese with on-device OCR. It shows the lines with furigana and clickable
words, and you can look them up, translate them with AI, and save words to study in Horu or
in Anki. There's a Windows installer, built and tested, but no published release yet.

**Checks:**

| Check | Result |
|---|---|
| Tests | 228 pass (`uv run pytest`) |
| OCR eval | 1.8% error, 49/51 screens (`evals/eval_ocr.py`) |
| AI eval (DeepSeek) | 76 requests for a session that took 133 before; median time to translation 2.1 s (was 3.2 s); no lines made worse |

## Done

### Reading the game (phases 1, 2 and 4)
- **Capture:** a hotkey (Ctrl+Alt+J, changeable in settings) captures the game window. The
  first press picks the game for the session, so later presses capture it even while Horu
  has focus. Parts covered by other windows are blanked out.
- **OCR:** RapidOCR finds and reads every line. Lines it isn't sure of are read again by two
  more models, which vote on the result. It also fixes small kana, the ー/一 mix-up, and
  spaces between phrases.
- **Layout:**
  - Groups lines into dialogue, speaker names and other text, including P5R's slanted name
    tags, choices, tutorials, signs and thought bubbles.
  - Tilted choices aren't read as speaker names.
  - Regression screens from Zelda (Ocarina of Time) and P5R are in
    `tests/fixtures/*_screens.json`.
- **Auto-capture:** reads new text once it stops changing and waits for the whole box to
  finish typing. It matches repeats despite OCR noise, and works on regions you draw for each
  game. It keeps going while the game is partly covered and pauses only when more than half
  is covered.
- **Text hooking:** lines from Textractor, LunaHook and others, by clipboard or WebSocket.

### Language tools (phase 2)
- Sudachi (full dictionary) splits sentences into words, and Horu joins split words back.
- Furigana, words coloured by part of speech, and offline JMdict and JMnedict lookups. The
  word popup shows the parts of compounds, conjugations and JLPT levels.
- Speaker names are remembered for each game. A "wrong name?" menu lets you check the name
  with AI, keep the OCR reading, or type the name.

### AI (phase 3)
- **Providers:** Claude, any OpenAI-compatible API (OpenAI, Gemini, DeepSeek) or a local
  Ollama model, chosen in settings. Nothing is tied to one provider. You're on DeepSeek
  `deepseek-flash`.
- **Features:** translations, explanations, follow-up questions, rereading a line from its
  screenshot, and AI-made cards.
- **Fewer requests:**
  - one request per line;
  - lines that appear together are batched;
  - the screenshot is sent only when OCR is unsure;
  - names are guessed from ones already checked;
  - a translation memory;
  - a job queue that does the newest line first;
  - a 30-second timeout with a Retry button.

### Studying (phases 5 and 7)
- Saved words go to the Words tab, with the screenshot each came from. The Study tab reviews
  them with FSRS scheduling.
- **Anki:** through AnkiWeb with fastanki (no Anki app needed, set up in the welcome screen or
  settings) or AnkiConnect with Anki open on this PC. Screenshots are stored inside the cards.
  Cards sync after you add them.
- JLPT tags.

### App and release (phase 6)
- **Layout:** Read, Words and Study tabs, a welcome screen, themes (Horu, Indigo, Matcha, each
  light or dark), and the DM Sans font.
- **Tray:** a ほ icon in the tray (show, capture, auto-capture on/off, quit). Minimizing hides
  Horu to the tray.
- **Narrow windows:** speaker names stay on one line, and Explain, Cards and Edit fold into ⋯.
- **Debug bundles** for bug reports, and `horu reset-learned`.
- **Installer:**
  - `dist\Horu-Setup-<version>.exe`, built by `packaging\build.ps1` (PyInstaller, then Inno
    Setup).
  - It installs per user with no admin prompt, and keeps data in `%LOCALAPPDATA%\Horu`.
  - Tested: installing, upgrading while Horu runs, and uninstalling.
  - The script install (`install.ps1`) still works too.
- **Docs:** README with install, getting started, privacy, costs and troubleshooting.

## What's next

### Before the first release
1. **Your tweaks.** You mentioned more tweaks before the release; send them when you're ready.
2. **Try the installer yourself:** run `dist\Horu-Setup-0.1.0.exe`. To keep your words and
   learned names, copy `C:\LukeDev\Horu\data` to `%LOCALAPPDATA%\Horu\data`.
3. **Sign in to AnkiWeb with a real account.** So far it has only been tested against Anki's
   own sync server.
4. **Publish:** push the commits, then a tag (`git tag v0.1.0 && git push --tags`). The GitHub
   workflow builds the installer into a draft release. It hasn't run yet, so check its first
   run.
5. **Decide on code signing.** Without it, Windows SmartScreen warns about the installer. The
   README explains how to get past the warning.

### Open items
- **Regression set:** add a visual novel, a modern 16:9 game, and a game with small subtitles.
  This needs captures from you.
- **Text hooking:** not yet tried with a real Textractor or LunaHook setup.
- **Welcome screen:** it still offers a Gemini key step. Decide whether to suggest DeepSeek or
  stay neutral about the provider.
- **Gemini rate limits:** if you switch back to Gemini, re-add the pause after 429 errors
  (written but never applied).
- **Narrow windows:** if the dialogue text still feels cramped, scale it down below a set
  width.
- **Installer size:** 292 MB, mostly the full Sudachi dictionary. A smaller dictionary would
  save around 250 MB unpacked, but it changes how words are split, so check it against the
  word-split eval first.

### Later
- **Phase 4, put off for now:** make auto-capture sturdier, and add owocr as an option.
- **Phase 7:** frequency tags and word recommendations.
- **Longer term:** accounts, a fuller study experience, and AI by subscription, while the core
  stays free (like Anki).
