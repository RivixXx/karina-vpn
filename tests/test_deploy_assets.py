from pathlib import Path
import os
import re
import shutil
import subprocess


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
ORIGINAL_POPEN = subprocess.Popen


def source(name):
    return (SCRIPTS / name).read_text(encoding="utf-8")


def run_local(command):
    process = ORIGINAL_POPEN(
        command, cwd=ROOT, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
    )
    stdout, stderr = process.communicate()
    assert process.returncode == 0, stderr or stdout
    return stdout


def test_deployment_text_assets_have_no_utf8_bom():
    assets = [ROOT / "README.md", ROOT / "requirements.txt"]
    assets.extend(path for path in (ROOT / "deploy").rglob("*") if path.is_file())
    assets.extend(SCRIPTS.glob("*.sh"))
    assert assets
    assert all(not path.read_bytes().startswith(b"\xef\xbb\xbf") for path in assets)


def test_shell_scripts_have_shebang_executable_git_mode_and_valid_syntax():
    scripts = sorted(SCRIPTS.glob("*.sh"))
    assert scripts and all(path.read_bytes().startswith(b"#!") for path in scripts)

    modes = {
        line.split(maxsplit=3)[3]: line.split(maxsplit=1)[0]
        for line in run_local(["git", "ls-files", "--stage", "--", "scripts"]).splitlines()
    }
    assert modes == {path.relative_to(ROOT).as_posix(): "100755" for path in scripts}

    if os.name == "nt":
        bash = Path(os.environ["ProgramFiles"]) / "Git" / "bin" / "bash.exe"
        assert bash.is_file()
        executable = str(bash)
    else:
        executable = shutil.which("bash")
        assert executable
    run_local([executable, "-n", *[path.relative_to(ROOT).as_posix() for path in scripts]])


def test_runtime_requirements_are_minimal_and_exclude_pytest():
    requirements = (ROOT / "requirements.txt").read_text(encoding="utf-8").splitlines()
    assert requirements == [
        "python-telegram-bot==20.8", "qrcode==7.4.2", "Pillow==10.2.0",
    ]
    assert all("pytest" not in line.lower() for line in requirements)


def test_deploy_has_no_source_control_or_data_migration_actions():
    deploy = source("deploy.sh")
    forbidden = (
        "git pull", "git push", "migrate-mobile", "x-ui.db", "sqlite3",
        "UPDATE ", "DELETE ", "INSERT ", "crypto.happ.su", "migrate-mobile --apply",
    )
    assert all(value not in deploy for value in forbidden)
    assert "set -Eeuo pipefail" in deploy
    assert "src.karina_issue \"$@\"" in deploy
    assert "-m src.karina_issue --help" not in deploy
    assert "issue_subscription(" not in deploy and "qrcode.QRCode" not in deploy


def test_smoke_imports_direct_issuer_and_checks_help_without_issuance():
    smoke = source("smoke-check.sh")
    assert "import qrcode" in smoke
    assert "src.integrations.subscription" in smoke
    assert "-m src.karina_issue --help" in smoke
    assert "crypto.happ.su" not in smoke


def test_repository_tracks_no_crypt5_artifacts():
    assert not list(ROOT.rglob("*.crypt5"))


def test_preflight_checks_only_tracked_secret_paths():
    preflight = source("preflight.sh")
    assert "git ls-files" in preflight
    assert "*.env" not in preflight
    assert "--with-xui" in preflight
    assert "migrate-mobile" not in preflight


def test_backup_copies_without_printing_secret_contents():
    backup = source("backup-production.sh")
    assert "cp -a" in backup
    assert "chmod 600" in backup and "-m 700" in backup
    assert not re.search(r"\b(cat|head|tail|less|more)\b", backup)
    assert "letsencrypt" not in backup.lower()


def test_rollback_is_code_only():
    rollback = source("rollback-code.sh")
    assert "git checkout --detach" in rollback
    assert "deploy.sh" in rollback
    assert not re.search(r"\b(rm|sqlite3|cp|mv)\b", rollback)


def test_systemd_module_entrypoints_and_schedule():
    units = ROOT / "deploy" / "systemd"
    bot = (units / "karina-bot.service").read_text(encoding="utf-8")
    notifier = (units / "karina-notifier.service").read_text(encoding="utf-8")
    timer = (units / "karina-notifier.timer").read_text(encoding="utf-8")
    assert "WorkingDirectory=/opt/karina-vpn" in bot
    assert "python -m src.bot" in bot and "Restart=on-failure" in bot
    assert "Type=oneshot" in notifier and "python -m src.notifier" in notifier
    assert "OnCalendar=daily" in timer and "Persistent=true" in timer


def test_scripts_contain_no_hardcoded_credentials():
    combined = "\n".join(path.read_text(encoding="utf-8")
                           for path in SCRIPTS.glob("*.sh"))
    assert not re.search(r"(?i)(password|token|api_key)=['\"][^$][^'\"]+", combined)
