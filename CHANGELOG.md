# Changelog

## 0.1.0

The first public release, for Windows 10 and 11.

- Capture a game's window with a hotkey and read the Japanese on it (on-device OCR), grouped
  into dialogue with its speaker and other text.
- Line history with furigana, words coloured by part of speech, and offline dictionary popups
  (JMdict, conjugation and grammar notes, JLPT levels).
- Auto-capture: reads new dialogue by itself, optionally limited to regions you draw. Reads
  are done one at a time; when the game is faster than Horu, older screens are skipped rather
  than shown ever later, and the hotkey always goes first.
- Snip: read and translate one bit of the screen.
- AI with your own key (Google Gemini has a free tier): translations, explanations,
  follow-up questions, checks of lines OCR was unsure of, and cards.
- Study saved words in Horu (FSRS reviews) or send them to Anki through AnkiWeb (no Anki
  app needed).
  Cards show the word, with the line it came from under it, and a screenshot of the scene on the back.
- Debug bundles for reporting a misread screen, and recordings for measuring auto-capture.
- A Windows installer (per user, no administrator prompt).
