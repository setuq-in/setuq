#!/usr/bin/env python3
"""Setuq project bootstrapper.

One command takes a fresh checkout to a running system:
  env wizard -> Splunk connectivity check -> extract + schema -> deps -> run commands.

Usage:
    python bootstrap.py up [flags]
"""


import sys
from dataclasses import dataclass
from pathlib import Path


_SECRET_MARKERS = ("PASSWORD", "SECRET", "_KEY", "TOKEN")


class ConnectivityError(Exception):
    """Raised when the Splunk connectivity check fails."""


@dataclass
class Field:
    name: str
    default: object
    secret: bool


def is_secret_field(name: str) -> bool:
    """True for env fields whose value should be masked when echoed."""
    upper = name.upper()
    return any(marker in upper for marker in _SECRET_MARKERS)


def fields_from_settings(settings_cls) -> list:
    """Enumerate (name, default, secret) for every pydantic Settings field.

    Reads `model_fields` so it stays in lockstep with config.py — no
    hand-maintained field list to drift.
    """
    return [
        Field(name=name, default=info.default, secret=is_secret_field(name))
        for name, info in settings_cls.model_fields.items()
    ]


def render_env_file(values: dict) -> str:
    """Render a dict to KEY=value .env lines (trailing newline)."""
    return "".join(f"{key}={_env_value(value)}\n" for key, value in values.items())


def _env_value(value) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    return str(value)


def splunk_subset(values: dict) -> dict:
    """Return only the SPLUNK_* keys — what splunk_pipeline.py reads."""
    return {k: v for k, v in values.items() if k.startswith("SPLUNK_")}


def should_skip_env_wizard(env_exists: bool, reconfigure: bool) -> bool:
    return env_exists and not reconfigure


def should_skip_extract(schema_exists: bool, refresh: bool, skip_extract: bool) -> bool:
    if skip_extract:
        return True
    if refresh:
        return False
    return schema_exists


def missing_fields(fields: list, existing: dict) -> list:
    """Fields whose name is absent from an existing .env (need backfilling)."""
    return [field for field in fields if field.name not in existing]


def merge_ordered(fields: list, values: dict) -> dict:
    """Re-key a value dict into Settings field order, keeping any extra keys."""
    ordered = {field.name: values.get(field.name, field.default) for field in fields}
    for key, value in values.items():
        ordered.setdefault(key, value)
    return ordered


def collect_values(fields: list, existing: dict, input_fn) -> dict:
    """Prompt for each field; blank input keeps the existing-or-default value."""
    values = {}
    for field in fields:
        current = existing.get(field.name, field.default)
        shown = "****" if field.secret and current else current
        answer = input_fn(f"{field.name} [{shown}]: ").strip()
        values[field.name] = answer if answer else current
    return values


def check_splunk(connect_fn, host, port, username, password, verify_ssl):
    """Connect to Splunk and return its server version. Raise ConnectivityError on failure."""
    try:
        service = connect_fn(
            host=host,
            port=port,
            username=username,
            password=password,
            verify=verify_ssl,
        )
        return service.info["version"]
    except Exception as exc:  # noqa: BLE001 - surfaced as ConnectivityError below
        raise ConnectivityError(str(exc)) from exc


# Engine MUST listen here: ui/vite.config.ts proxies /api -> 127.0.0.1:8001.
ENGINE_PORT = 8001
ENGINE_HOST = "127.0.0.1"
UI_PORT = 3000


def venv_python(engine_dir, platform=sys.platform):
    """Path to the engine venv's python interpreter for this platform."""
    engine_dir = Path(engine_dir)
    if platform.startswith("win"):
        return engine_dir / "venv" / "Scripts" / "python.exe"
    return engine_dir / "venv" / "bin" / "python"


def engine_launch_cmd(python_path):
    return [
        str(python_path),
        "-m",
        "uvicorn",
        "app.main:app",
        "--host",
        ENGINE_HOST,
        "--port",
        str(ENGINE_PORT),
    ]


def ui_launch_cmd(platform=sys.platform):
    npm = "npm.cmd" if platform.startswith("win") else "npm"
    return [npm, "run", "dev"]


def running_under_venv(executable, engine_dir, platform=sys.platform) -> bool:
    """True if `executable` is already the engine venv's python."""
    return Path(executable) == venv_python(engine_dir, platform)


_REEXEC_FLAGS = (
    ("reconfigure", "--reconfigure"),
    ("refresh_schema", "--refresh-schema"),
    ("skip_extract", "--skip-extract"),
    ("skip_launch", "--skip-launch"),
    ("with_field_stats", "--with-field-stats"),
)


def reexec_cmd(python_path, script_path, args) -> list:
    """Command to re-run bootstrap under the venv python, with deps already done."""
    cmd = [str(python_path), str(script_path), "up", "--_bootstrapped"]
    for attr, flag in _REEXEC_FLAGS:
        if getattr(args, attr, False):
            cmd.append(flag)
    return cmd


# =========================================================
# ORCHESTRATION (integration layer — not unit-tested)
# =========================================================

import argparse
import shutil
import subprocess

ROOT = Path(__file__).resolve().parent
ENGINE_DIR = ROOT / "engine"
UI_DIR = ROOT / "ui"
ROOT_ENV = ROOT / ".env"
ENGINE_ENV = ENGINE_DIR / ".env"
SCHEMA_OVERRIDES = ENGINE_DIR / "schema_overrides.yaml"
SPLUNK_METADATA = ROOT / "splunk_metadata"
VENV_DIR = ENGINE_DIR / "venv"
PIPELINE = ROOT / "splunk_pipeline.py"


def _banner(num, name):
    print(f"\n==> Phase {num}: {name}")


def _as_bool(value) -> bool:
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in ("1", "true", "yes", "on")


def _confirm(prompt: str, input_fn=input) -> bool:
    """y/N confirm; blank or anything but y/yes = No (keep)."""
    return input_fn(f"{prompt} [y/N]: ").strip().lower() in ("y", "yes")


def _wipe_path(path) -> bool:
    """Delete a file or directory tree if present. Return True if removed."""
    path = Path(path)
    if not path.exists():
        return False
    if path.is_dir():
        shutil.rmtree(path)
    else:
        path.unlink()
    return True


def phase_wipe(input_fn=input) -> None:
    """Per-artifact prompt to wipe prior state. Default (blank) keeps everything."""
    venv_locked = running_under_venv(sys.executable, ENGINE_DIR)
    targets = [
        ("env files (.env + engine/.env)", [ROOT_ENV, ENGINE_ENV], False),
        ("engine venv", [VENV_DIR], venv_locked),
        ("splunk_metadata/", [SPLUNK_METADATA], False),
        ("schema_overrides.yaml", [SCHEMA_OVERRIDES], False),
    ]
    present = [(label, paths, locked) for label, paths, locked in targets
               if any(Path(p).exists() for p in paths)]
    if not present:
        return
    _banner(0, "Wipe existing state (optional)")
    for label, paths, locked in present:
        if locked:
            print(f"Skipping {label}: running under it, cannot wipe in place.")
            continue
        if _confirm(f"Wipe {label}?", input_fn):
            for path in paths:
                if _wipe_path(path):
                    print(f"  removed {path}")


def load_settings_cls():
    """Import the app's pydantic Settings (source of truth for env fields)."""
    if str(ENGINE_DIR) not in sys.path:
        sys.path.insert(0, str(ENGINE_DIR))
    from app.config import Settings

    return Settings


def parse_env_file(path) -> dict:
    """Read an existing .env into {KEY: value} (best-effort, ignores comments)."""
    path = Path(path)
    if not path.exists():
        return {}
    out = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        out[key.strip()] = value.strip()
    return out


def _write_env_files(values: dict) -> None:
    ENGINE_ENV.write_text(render_env_file(values), encoding="utf-8")
    ROOT_ENV.write_text(render_env_file(splunk_subset(values)), encoding="utf-8")


def phase_env_wizard(reconfigure: bool, input_fn=input) -> dict:
    _banner(2, "Environment")
    settings_cls = load_settings_cls()
    fields = fields_from_settings(settings_cls)

    if should_skip_env_wizard(ENGINE_ENV.exists(), reconfigure):
        existing = parse_env_file(ENGINE_ENV)
        missing = missing_fields(fields, existing)
        if not missing:
            print(f"{ENGINE_ENV} exists with all {len(fields)} params - keeping it "
                  f"(use --reconfigure to edit).")
            return existing
        print(f"{ENGINE_ENV} is missing {len(missing)} required param(s) - "
              f"prompting for those only (Enter keeps the default).\n")
        filled = collect_values(missing, existing, input_fn)
        values = merge_ordered(fields, {**existing, **filled})
        _write_env_files(values)
        print(f"\nBackfilled {ENGINE_ENV} and {ROOT_ENV}")
        return values

    existing = {**parse_env_file(ROOT_ENV), **parse_env_file(ENGINE_ENV)}
    print("Press Enter to keep the shown value.\n")
    values = collect_values(fields, existing, input_fn)
    _write_env_files(values)
    print(f"\nWrote {ENGINE_ENV} and {ROOT_ENV}")
    return values


def phase_connectivity(values: dict) -> None:
    _banner(3, "Splunk connectivity")
    import splunklib.client as client

    version = check_splunk(
        client.connect,
        host=values.get("SPLUNK_HOST", "localhost"),
        port=int(values.get("SPLUNK_PORT", 8089)),
        username=values.get("SPLUNK_USERNAME"),
        password=values.get("SPLUNK_PASSWORD"),
        verify_ssl=_as_bool(values.get("SPLUNK_VERIFY_SSL", False)),
    )
    print(f"Connected to Splunk {version}")


def phase_extract(refresh: bool, skip_extract: bool, with_field_stats: bool) -> None:
    _banner(4, "Extract metadata + build schema_overrides.yaml")
    if should_skip_extract(SCHEMA_OVERRIDES.exists(), refresh, skip_extract):
        print(f"{SCHEMA_OVERRIDES.name} present - skipping extraction "
              f"(use --refresh-schema to rebuild).")
        return
    cmd = [sys.executable, str(PIPELINE), "--env-file", str(ROOT_ENV)]
    if with_field_stats:
        cmd.append("--with-field-stats")
    subprocess.run(cmd, cwd=str(ROOT), check=True)


def phase_deps() -> None:
    _banner(1, "Dependencies")
    py = venv_python(ENGINE_DIR)
    if not py.exists():
        print("Creating engine venv ...")
        subprocess.run([sys.executable, "-m", "venv", "venv"], cwd=str(ENGINE_DIR), check=True)
    print("Installing engine requirements ...")
    subprocess.run([str(py), "-m", "pip", "install", "-r", "requirements.txt"],
                   cwd=str(ENGINE_DIR), check=True)


def _join_cmd(parts) -> str:
    """Render an argv list as a copy-pasteable shell command (quote spaces)."""
    return " ".join(f'"{p}"' if " " in str(p) else str(p) for p in parts)


def phase_launch() -> None:
    _banner(5, "Run the app")
    py = venv_python(ENGINE_DIR)
    npm = "npm.cmd" if sys.platform.startswith("win") else "npm"

    print("Setup complete. Start each service in its own terminal:\n")
    print(f"  Engine  (cwd: {ENGINE_DIR})")
    print(f"    {_join_cmd(engine_launch_cmd(py))}\n")
    print(f"  UI      (cwd: {UI_DIR})")
    print(f"    {npm} install        # run first, installs UI deps")
    print(f"    {_join_cmd(ui_launch_cmd())}\n")
    print(f"Then open http://127.0.0.1:{UI_PORT}  "
          f"(proxies /api -> http://{ENGINE_HOST}:{ENGINE_PORT})")


def reexec_under_venv(args) -> None:
    """Re-run bootstrap under the venv python (so phases 1-3 have their imports)."""
    py = venv_python(ENGINE_DIR)
    cmd = reexec_cmd(py, Path(__file__).resolve(), args)
    print(f"\nRe-launching under {py} ...")
    result = subprocess.run(cmd)
    sys.exit(result.returncode)


def up(args) -> None:
    # Cold start: install deps FIRST, then re-exec under the venv python so the
    # env wizard (pydantic), connectivity + extraction (splunklib/dotenv/yaml)
    # all have their imports available regardless of which python invoked us.
    if not args._bootstrapped:
        phase_wipe()
        phase_deps()
        if not running_under_venv(sys.executable, ENGINE_DIR):
            reexec_under_venv(args)  # does not return
        # already running under the venv python — deps done, continue in-process

    values = phase_env_wizard(args.reconfigure)
    try:
        phase_connectivity(values)
    except ConnectivityError as exc:
        print(f"\nSplunk unreachable: {exc}\nAborting.")
        sys.exit(1)
    phase_extract(args.refresh_schema, args.skip_extract, args.with_field_stats)
    if args.skip_launch:
        print("\n--skip-launch set - setup done, run commands not printed.")
        return
    phase_launch()


def main() -> None:
    parser = argparse.ArgumentParser(description="Setuq project bootstrapper")
    sub = parser.add_subparsers(dest="command", required=True)
    up_parser = sub.add_parser("up", help="env -> check -> extract -> deps -> run commands")
    up_parser.add_argument("--reconfigure", action="store_true",
                           help="re-run the .env wizard even if engine/.env exists")
    up_parser.add_argument("--refresh-schema", action="store_true",
                           help="force re-extract even if schema_overrides.yaml exists")
    up_parser.add_argument("--skip-extract", action="store_true",
                           help="skip Splunk extraction; use existing metadata")
    up_parser.add_argument("--skip-launch", action="store_true",
                           help="setup only; do not print the run commands")
    up_parser.add_argument("--with-field-stats", action="store_true",
                           help="pass --with-field-stats to splunk_pipeline.py")
    up_parser.add_argument("--_bootstrapped", action="store_true",
                           help=argparse.SUPPRESS)  # internal: set on the re-exec'd run
    up_parser.set_defaults(func=up)

    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
