# Security

If you find a security problem in Horu (for example a way for text on screen or an AI reply
to run code or read files, or a leak of an API key), please report it privately
rather than in a public issue: use **Report a vulnerability** under the repository's
[Security tab](https://github.com/LukasRuprecht/Horu/security/advisories/new).

Please include the Horu version (the installer's file name, or Details in Horu.exe's Properties), what you did, and
what happened. Only the latest release is supported.

What Horu handles that is worth protecting:

- **API keys** and the AnkiWeb sign-in key, stored in Windows Credential Manager.
- **Screenshots of your game window**, kept in memory, and on disk only in saved words'
  pictures, debug bundles, and recordings you ask for.
