"""
paths.py — locate the MARK L project that EDIT WEB syncs with / borrows the face
engine from. Works in BOTH layouts:

  • nested:  <ai22>/edit_web/paths.py   → main project is the parent (<ai22>)
  • sibling: <home>/edit-web/paths.py   → main project is ../ai22

Override with the EDITWEB_MAIN environment variable if needed.
"""

import os
from pathlib import Path

HERE = Path(__file__).resolve().parent


def main_project_dir() -> Path:
    env = os.environ.get("EDITWEB_MAIN")
    if env and (Path(env) / "actions" / "face_vault.py").exists():
        return Path(env).resolve()

    candidates = [
        HERE.parent,                       # nested:  edit_web/ inside the project
        HERE.parent / "ai22",              # sibling: ../ai22
        HERE.parent / "Mark-L",
        HERE.parent / "mark-l",
    ]
    for c in candidates:
        if (c / "actions" / "face_vault.py").exists():
            return c.resolve()
    # sensible fallback so error messages still resolve a path
    return (HERE.parent / "ai22").resolve()
