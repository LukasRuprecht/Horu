# Quick start

From download to reading your first line, in about five minutes. The
[README](../README.md) has more detail on every feature.

## 1. Install

1. Download **Horu-Setup-0.1.0.exe** from the
   [latest release](https://github.com/LukasRuprecht/Horu/releases/latest) and run it.
2. If Windows says **"Windows protected your PC"**, click **More info**, then **Run anyway**.
   (The installer isn't code-signed yet.)
3. Click through the installer. It doesn't need an administrator password.

## 2. Get a free AI key (recommended)

Horu works without AI, but it's much better with it: every line gets a translation, and you
can ask what a line means and why. Google's Gemini API has a free tier:

1. Open [Google AI Studio](https://aistudio.google.com/apikey) and sign in with a Google
   account.
2. Click **Create API key** and copy the key.

Keep it handy for the last step of the welcome. (On the free tier there are daily limits, and
Google may use what you send to improve its products. Turning on billing for the key changes
both. Claude, OpenAI and DeepSeek keys work too.)

## 3. The welcome

Start Horu. The first time, it downloads its dictionary (about 25 MB; the line at the top of
the window shows the progress). Then a short welcome asks:

1. **How do you want to study the words you save?** **In Horu** needs nothing else.
   **With Anki** asks you to sign in to [AnkiWeb](https://ankiweb.net) (free); no Anki app is
   needed.
2. **What's your Japanese level?** Explanations are pitched to it.
3. **Capture your game** shows the capture hotkey, **Ctrl+Alt+J**.
4. **Horu is much better with AI:** paste your Gemini key and click **Finish**. Horu checks
   the key and picks Google's newest Flash model. To use another provider, click **Use Claude,
   OpenAI or DeepSeek instead**. No key yet? Click **Finish** anyway; you can add one later in
   **Settings → AI**.

## 4. Read your game

1. Run the game in **windowed** or **borderless windowed** mode.
2. Put Horu beside it: on a second monitor, or on top of the game with the pin button in
   Horu's toolbar.
3. When there's Japanese on screen, press **Ctrl+Alt+J** in the game.

The lines appear in Horu: dialogue large, with the speaker above it, and other text (menus,
button hints) small. With AI, a translation appears under each line, blurred until you click
it, so you can try reading first.

The first press also tells Horu which window is your game. From then on, the eye button in
the toolbar turns on **auto-capture**, which reads new dialogue by itself.

## 5. Look words up and save them

- **Click any word** for its reading, dictionary form, conjugation and meanings.
- **Save** keeps the word with its line and a picture of it. (With Anki the button says
  **Add to Anki** and makes a card in your AnkiWeb deck: the word with its line, and on the
  back the meaning, translation and screenshot.)
- **Explain** on a line (with AI) breaks it down; ask follow-up questions in the panel that
  opens.
- The **Study** tab reviews what you saved: **Start review**, **Show answer** (or Space), then
  rate how well you knew it.

## If something isn't working

- **Nothing happens when you press the hotkey:** another program may own the shortcut (Horu's
  status line says so). Choose another in **Settings → Capture**. If the game runs as
  administrator, Horu must too.
- **Captures are black:** switch the game to borderless windowed.
- **The AI step says the key didn't work:** check it was copied whole, and that your PC is
  online. **Settings → AI → Test** shows the provider's own message.
- **A line is misread:** **Edit** on the line fixes it. To report it, the line's **⋯** menu
  saves a debug bundle you can attach to an
  [issue](https://github.com/LukasRuprecht/Horu/issues).
