# Horu quick start

From download to your first flashcard. Each step says what you'll see in Horu's window. The
[README](../README.md) has the details, the costs of AI, and troubleshooting.

## 1. Install

1. On Windows 10 or 11, download **Horu-Setup-<version>.exe** from the
   [latest release](https://github.com/LukasRuprecht/Horu/releases/latest) and run it.
2. If Windows shows **"Windows protected your PC"**, click **More info**, then **Run anyway**.
   The installer isn't code-signed yet.
3. Finish the installer. It needs about 700 MB, asks for no administrator password, and adds
   Horu to the Start menu.

## 2. First start

1. Start Horu. The first time, it downloads its dictionary (about 25 MB). The status line at
   the top of the window shows the progress, then **Ready**.
2. A short welcome takes you through four steps. **Skip** closes it; everything in it is also
   in Settings.
   - **How do you want to study the words you save?** Choose **In Horu** (nothing else to
     set up) or **With Anki** (sign in to AnkiWeb right there; see
     [step 6](#6-make-anki-cards)).
   - **What's your Japanese level?** Only AI explanations use this.
   - **Capture your game** shows the capture hotkey.
   - **Add AI? (optional)** is last. Leave the provider on **Not now** and click **Finish** to
     skip it, or set it up there (see [step 5](#5-add-ai-optional)) and click **Finish**.
3. Put Horu's window beside your game: on a second monitor, or over the game with the pin
   button (**Keep window on top**) in the Read tab's toolbar.

## 3. Capture text from your game

1. Run the game in **windowed** or **borderless windowed** mode. Exclusive fullscreen often
   can't be captured.
2. With Japanese text on screen, press **Ctrl+Alt+J** in the game. The lines appear in the
   **Read** tab: dialogue large, with its speaker above it, and other text (menus, button
   hints) small.
3. The first press also tells Horu which window is your game. Its name shows at the right of
   the toolbar; click it to choose another window. After that the hotkey reads the game even
   while Horu is in front.

Useful next:

- **Another hotkey:** the settings button (sliders, top right) → **Capture** → **Change**,
  then press the keys you want: a letter, number, or F-key with Ctrl, Alt, or Shift.
- **Reading by itself:** the eye button in the toolbar turns on auto-capture, which reads new
  dialogue as it appears. Press the hotkey once first, so Horu knows your game. Expect lines a
  few seconds after the game shows them; the hotkey is still the quickest way to read one
  screen.
- **If auto-capture picks up menus or signs:** **Settings → Capture → Edit regions…** lets you
  draw a box around the dialogue area on your latest hotkey capture.
- **One bit of the screen:** the snip button (beside the eye) lets you drag a box around any
  text to read it once.

## 4. Look words up

1. Click any word in a line. A popup shows its reading, its dictionary form and how it's
   conjugated, its JLPT level when known, and its meanings.
2. Click **Save** to keep the word, with its line and a picture of the line. (If you chose
   Anki, the button says **Add to Anki**.)
3. Saved words are in the **Words** tab. The **Study** tab reviews them: **Start review**,
   **Show answer** (or Space), then rate how well you knew it.

This all works offline and needs no AI. The 漢 button in the toolbar turns furigana off and on.

## 5. Add AI (optional)

AI adds translations and explanations. You need either an API key from a cloud provider, which
bills each request to your own account, or [Ollama](https://ollama.com/download) running a
free model on your PC.

The welcome's last step has the same fields as below, with **Check it works** in place of
**Test**. Choosing Ollama or another server there takes you to Settings → AI to finish.

1. Open settings (the sliders button, top right) → **AI**.
2. Choose a **Provider**.
3. For a cloud provider: paste your key under **API key** and click **Save**. **Get a key**
   opens the provider's page for making one.
4. Fill in **Model for explanations** if it's empty: the model's name as the provider writes
   it. For Ollama, click **Download** on one of the suggested models, then **Use for
   explanations** (or **Use for translations**) once it's installed.
5. Click **Test**. A reply means it works.

With AI on:

- New dialogue is translated under each line. The translation stays blurred until you click
  it, so you can try reading first. To translate only on request, turn off **Translate new
  dialogue automatically**; each line then has a **Translate** button.
- **Explain** on a line breaks down its grammar. **Explain in context** in a word's popup
  explains that word in that line.

What is sent to the provider, and what it costs, is in the README under
[Privacy](../README.md#privacy) and [Costs](../README.md#costs).

## 6. Make Anki cards

1. Open settings → **Study** and set **Study the words you save** to **With Anki**.
2. Under **Anki**, enter your AnkiWeb email and password and click **Sign in**. No Anki app
   is needed. **Create a free AnkiWeb account** opens the sign-up page.
3. **Deck for new cards** is the deck cards go into (`Horu` unless you change it).
4. Click a word in the Read tab, then **Add to Anki**.

Review cards on AnkiWeb, in the AnkiDroid or AnkiMobile app, or in Anki on a computer once
it syncs. The card shows the word on the front with the line it came from under it. The back adds the
reading, meaning, grammar notes, the translation if there is one, and a screenshot of the
line. A word already in the deck isn't added twice.

With an AI provider that supports tool use, **Cards** on a line lets the AI pick the words
worth learning at your level and add them for you.

## If something goes wrong

- **Nothing happens when you press the hotkey:** another program may own the shortcut (Horu's
  status line says so): choose another in **Settings → Capture**. If the game runs as
  administrator, Horu must too.
- **Captures are black:** switch the game to borderless windowed.
- **A line is misread or filed wrongly:** **Edit** fixes the text; the line's **⋯** menu
  corrects how it was read and can save a debug bundle to attach to an
  [issue](https://github.com/LukasRuprecht/Horu/issues).
