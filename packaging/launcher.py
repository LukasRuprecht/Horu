r"""The entry point of Horu.exe (the installed app, built by packaging/build.ps1).

Horu keeps its settings and data next to where it runs (config.toml, data\, captures\). The
installed program folder is replaced on every update, so the app runs in a folder of its own
instead: %LOCALAPPDATA%\Horu, the same one install.ps1 uses, so an earlier install's
settings, dictionary, and learned names carry over. HORU_HOME picks another folder.
"""

import os
import shutil
import sys
from pathlib import Path


def main() -> None:
    home = Path(os.environ.get("HORU_HOME") or Path(os.environ["LOCALAPPDATA"]) / "Horu")
    home.mkdir(parents=True, exist_ok=True)
    os.chdir(home)
    # The template config.toml is made from on first start (and a reference for editing it).
    shutil.copyfile(Path(sys._MEIPASS) / "config.example.toml", home / "config.example.toml")

    from horu import cli

    cli.gui()


if __name__ == "__main__":
    main()
