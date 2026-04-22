"""Start the FastAPI server with AWS credentials loaded from SSO profile."""
import os
import sys
import json
import subprocess

# Add project root to Python path
PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)


def load_credentials(profile_name: str = "default") -> dict:
    """Load AWS credentials via `aws configure export-credentials`."""
    try:
        result = subprocess.run(
            ["aws", "configure", "export-credentials", "--profile", profile_name, "--format", "env-no-export"],
            capture_output=True, text=True, timeout=10,
        )
        if result.returncode != 0:
            print(f"[start_server] SSO credential export failed: {result.stderr}", file=sys.stderr)
            return {}

        creds = {}
        for line in result.stdout.strip().split("\n"):
            if "=" in line:
                key, val = line.split("=", 1)
                creds[key.strip()] = val.strip()
        return creds
    except Exception as e:
        print(f"[start_server] Error loading credentials: {e}", file=sys.stderr)
        return {}


def main():
    # Activate venv if available
    venv_path = os.path.join(os.path.dirname(__file__), '..', 'ai_engine', '.venv')
    venv_site = os.path.join(venv_path, 'lib')
    if os.path.isdir(venv_path):
        # Add venv site-packages to path
        import glob
        site_dirs = glob.glob(os.path.join(venv_site, 'python*', 'site-packages'))
        for sd in site_dirs:
            if sd not in sys.path:
                sys.path.insert(0, sd)

    # Load settings — check multiple locations
    settings_path_candidates = [
        os.path.expanduser("~/.ai-editor/settings/settings.json"),
        os.path.join(os.path.expanduser("~"), "Library", "Application Support", "ai-editor", "settings", "settings.json"),
    ]
    profile = "default"
    for settings_path in settings_path_candidates:
        if os.path.isfile(settings_path):
            with open(settings_path) as f:
                settings = json.load(f)
            profile = settings.get("awsProfile", "default")
            break

    # If profile not set, try to auto-detect bedrockuser-* profile
    if profile == "default":
        import subprocess as _sp
        try:
            # Check /fsx/home path first
            username = os.environ.get("USER", os.environ.get("USERNAME", ""))
            fsx_config = f"/fsx/home/{username}/.aws/config"
            config_path = fsx_config if os.path.isfile(fsx_config) else os.path.expanduser("~/.aws/config")
            if os.path.isfile(config_path):
                with open(config_path) as cf:
                    import re
                    for m in re.finditer(r'\[profile\s+(bedrockuser-\S+)\]', cf.read()):
                        profile = m.group(1)
                        break
        except Exception:
            pass

    # Export credentials to env
    creds = load_credentials(profile)
    for k, v in creds.items():
        os.environ[k] = v

    # Start uvicorn
    import uvicorn
    uvicorn.run(
        "ai_engine.server:app",
        host="0.0.0.0",
        port=8765,
        reload=True,
        log_level="info",
    )


if __name__ == "__main__":
    main()
