"""Start the FastAPI server with AWS credentials loaded from SSO profile."""
import os
import sys
import json

# Add project root to Python path
PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)


def load_credentials(profile_name: str = "default") -> dict:
    """Pre-load AWS credentials via boto3 (dev-only convenience).

    Uses ``boto3.Session(profile_name=...).get_credentials()`` and maps the
    resolved credentials onto the same env-var dict keys the previous
    ``aws configure export-credentials`` shell-out produced. This is a
    development convenience only; the real credential path is the app's
    ``/api/reset-cache`` injection at runtime. On any failure this returns an
    empty dict so that server startup can continue.
    """
    try:
        import boto3

        session = boto3.Session(profile_name=profile_name)
        frozen = session.get_credentials().get_frozen_credentials()
        creds = {
            "AWS_ACCESS_KEY_ID": frozen.access_key,
            "AWS_SECRET_ACCESS_KEY": frozen.secret_key,
        }
        if frozen.token:
            creds["AWS_SESSION_TOKEN"] = frozen.token
        return creds
    except Exception as e:
        # DEV-ENTRY convenience only — never crash startup. The real credential
        # path is the app's /api/reset-cache runtime injection.
        print(f"[start_server] credential pre-load skipped: {e}", file=sys.stderr)
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
    # reload=True는 개발 편의지만, 파일 수정/생성 감지 시 진행 중인 SSE 스트림(특히
    # 멀티-에이전트 오케스트레이션)을 끊어 "network error"를 유발한다. 따라서 기본 OFF.
    # 개발 중 핫리로드가 필요하면 AE_DEV_RELOAD=1로 명시적 opt-in. (NO_RELOAD=1도 계속 존중)
    use_reload = (os.environ.get("AE_DEV_RELOAD", "") == "1") and (os.environ.get("NO_RELOAD", "") != "1")
    import uvicorn
    uvicorn.run(
        "ai_engine.server:app",
        host="0.0.0.0",
        port=8765,
        reload=use_reload,
        reload_dirs=["ai_engine"] if use_reload else None,
        log_level="info",
    )


if __name__ == "__main__":
    main()
