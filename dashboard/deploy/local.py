"""Запуск/остановка установленного в Ubuntu локального приложения из Windows."""

import argparse
import os
import subprocess
import time
import urllib.error
import urllib.request
import webbrowser
from pathlib import Path

RUNTIME = "/home/dima/SoMaA-local"
URL = "http://localhost:8501"


def healthy():
    try:
        with urllib.request.urlopen(URL + "/_stcore/health", timeout=2) as response:
            return response.status == 200
    except (OSError, urllib.error.URLError):
        return False


def wait_for_docker(timeout=180):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            result = subprocess.run(
                ["wsl.exe", "-d", "Ubuntu", "--exec", "docker", "info",
                 "--format", "{{.ServerVersion}}"],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=10,
            )
            if result.returncode == 0:
                return
        except subprocess.TimeoutExpired:
            pass
        time.sleep(2)
    raise SystemExit("Docker is not ready in Ubuntu. Check Docker Desktop WSL integration.")


def wait_for_dashboard(process, logfile, timeout=600):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if healthy():
            return
        code = process.poll()
        if code is not None:
            raise SystemExit(f"Startup process exited ({code}). See: {logfile}")
        time.sleep(1)
    raise SystemExit(f"Startup timed out. See: {logfile}")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("start", "stop", "status", "sync"))
    parser.add_argument("--no-browser", action="store_true")
    args = parser.parse_args()
    if args.command == "sync":
        root = Path(__file__).resolve().parents[2]
        source = "/mnt/" + root.drive[0].lower() + "/" + "/".join(root.parts[1:])
        subprocess.run(
            ["wsl.exe", "-d", "Ubuntu", "--exec", "python3.12",
             source + "/dashboard/deploy/prepare_runtime.py", source, RUNTIME], check=True,
        )
        print("Source copy synchronized. Restart the application to apply changes.")
        return
    if args.command == "status":
        print("Running: " + URL if healthy() else "Dashboard is stopped.")
        return
    if args.command == "stop":
        subprocess.run(["wsl.exe", "-d", "Ubuntu", "--cd", RUNTIME, "--exec", "bash",
                        "dashboard/deploy/stop.sh"], check=True)
        return
    if not healthy():
        docker = (Path(os.environ["LOCALAPPDATA"])
                  / "Programs/DockerDesktop/resources/bin/docker.exe")
        subprocess.run([str(docker), "desktop", "start", "--timeout", "180"], check=True)
        print("Waiting for Docker in Ubuntu...", flush=True)
        wait_for_docker()
        folder = Path(os.environ["LOCALAPPDATA"]) / "SoMaA-local"
        folder.mkdir(exist_ok=True)
        logfile = folder / "application.log"
        print("Starting SoMaA services; this may take a few minutes...", flush=True)
        with logfile.open("ab") as log:
            log.write(("\nStartup: " + time.strftime("%Y-%m-%d %H:%M:%S") + "\n").encode())
            log.flush()
            process = subprocess.Popen(
                ["wsl.exe", "-d", "Ubuntu", "--cd", RUNTIME, "--exec", "bash",
                 "dashboard/deploy/start.sh"], stdout=log, stderr=log, stdin=subprocess.DEVNULL,
                creationflags=subprocess.CREATE_NO_WINDOW | subprocess.CREATE_NEW_PROCESS_GROUP,
            )
        wait_for_dashboard(process, logfile)
    print("Running: " + URL)
    if not args.no_browser:
        webbrowser.open(URL)


if __name__ == "__main__":
    main()
