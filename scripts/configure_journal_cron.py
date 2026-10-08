"""Enable or disable the legacy OpenClaw journal job without changing other jobs."""
import argparse
import shlex
import subprocess
from datetime import datetime
from pathlib import Path


def updated_crontab(current, repo, enabled=True):
    script = repo / "scripts" / "openclaw_publish.sh"
    log = repo / "logs" / "openclaw_publish.log"
    lines = [line for line in current.splitlines()
             if not (str(repo) in line and "scripts/openclaw_publish.sh" in line and not line.lstrip().startswith("#"))]
    if not enabled:
        return "\n".join(lines).rstrip() + "\n"
    # Use the existing global zone, or set it just for this final entry.
    if not lines or next((line for line in reversed(lines) if line.startswith("CRON_TZ=")), "") != "CRON_TZ=America/New_York":
        lines.append("CRON_TZ=America/New_York")
    lines.append(f"15 9 * * 5 cd {shlex.quote(str(repo))} && bash {shlex.quote(str(script))} >> {shlex.quote(str(log))} 2>&1")
    return "\n".join(lines).rstrip() + "\n"


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--repo", type=Path, default=Path(__file__).resolve().parents[1])
    action = parser.add_mutually_exclusive_group()
    action.add_argument("--install", action="store_true")
    action.add_argument("--disable", action="store_true", help="Remove only this repository's OpenClaw journal cron entry.")
    args = parser.parse_args()
    repo = args.repo.resolve()
    if not (repo / "scripts" / "openclaw_publish.sh").is_file():
        parser.error("Repository does not contain the OpenClaw publisher.")
    result = subprocess.run(["crontab", "-l"], text=True, capture_output=True)
    if result.returncode and "no crontab" not in result.stderr:
        raise RuntimeError("Could not inspect the user's crontab.")
    updated = updated_crontab(result.stdout, repo, enabled=not args.disable)
    if not args.install and not args.disable:
        print(updated, end="")
        return
    backup_dir = repo / ".git" / "cron-backups"
    backup_dir.mkdir(parents=True, exist_ok=True)
    (backup_dir / f"before-{datetime.now():%Y%m%d-%H%M%S}.txt").write_text(result.stdout, encoding="utf-8")
    (repo / "logs").mkdir(exist_ok=True)
    subprocess.run(["crontab", "-"], input=updated, text=True, check=True)
    if args.disable:
        print("OpenClaw journal cron disabled; other jobs preserved. Previous crontab backed up.")
    else:
        print("OpenClaw journal publishing scheduled for Friday at 9:15 AM America/New_York; other jobs preserved.")


if __name__ == "__main__":
    main()
