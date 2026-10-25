"""Guards for a public repo: the hand-over folder and secrets must never be tracked."""
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def git_ls_files() -> list[str]:
    try:
        out = subprocess.run(["git", "ls-files"], cwd=ROOT, capture_output=True, text=True, check=True)
    except (OSError, subprocess.CalledProcessError):
        return []
    return [line for line in out.stdout.splitlines() if line]


def test_recieved_files_never_tracked():
    tracked = git_ls_files()
    assert not [p for p in tracked if p.startswith("recieved_files/")]
    assert [p for p in tracked if p.lower().endswith(".pdf")] in ([], ["docs/slides.pdf"])


def test_no_secrets_tracked():
    assert ".env" not in git_ls_files()


def test_gitignore_lists_private_paths():
    lines = (ROOT / ".gitignore").read_text().splitlines()
    for entry in ["recieved_files/", ".env", "var/", ".superpowers/", ".venv/"]:
        assert entry in lines
