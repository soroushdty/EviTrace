import subprocess
import time
import logging
import sys
import os
import platform

logger = logging.getLogger("pdf_extractor")

class GrobidServerManager:
    """Context manager to automate the local GROBID server lifecycle."""
    
    def __init__(self, config: dict):
        self.config = config.get("quality_control", {}).get("grobid", {})
        self.auto_start = self.config.get("auto_start", False)
        self.image = self.config.get("docker_image", "lfoppiano/grobid:0.8.0")
        self.url = self.config.get("url", "http://localhost:8070")
        self.container_id = None

    def __enter__(self):
        if not self.auto_start:
            return self

        if self._is_server_alive():
            logger.debug("GROBID server is already running.")
            return self

        if not self._is_docker_running():
            print("\nPlease start Docker Desktop to use GROBID extraction.")
            ans = input("Would you like EviTrace to automatically launch Docker Desktop for you? [Y/n]: ").strip()
            if ans.lower() != 'n':
                if not self._launch_docker_desktop():
                    print("\nCould not automatically launch Docker Desktop from standard locations.")
                    path = input("Please input the exact path to your Docker executable to launch it, or press Enter to cancel and launch it manually: ").strip()
                    if path:
                        if not self._launch_custom(path):
                            print("Failed to launch from the provided path. Please start Docker manually and re-run.")
                            sys.exit(1)
                    else:
                        print("Please start Docker manually and re-run EviTrace.")
                        sys.exit(1)
                
                print("Waiting for Docker daemon to start...")
                # Poll for up to 60 seconds
                started = False
                for _ in range(30):
                    if self._is_docker_running():
                        started = True
                        break
                    time.sleep(2)
                
                if not started:
                    print("Docker daemon did not start within the expected time. Please ensure it's fully running and try again.")
                    sys.exit(1)
            else:
                sys.exit(1)

        print(f"Starting temporary GROBID server ({self.image})...")
        try:
            # GROBID defaults to port 8070.
            cmd = ["docker", "run", "--rm", "-d", "-p", "8070:8070", self.image]
            result = subprocess.run(cmd, capture_output=True, text=True, check=True)
            self.container_id = result.stdout.strip()
            
            # Poll /api/isalive
            print("Waiting for GROBID server to become ready (this may take a few minutes on first run)...")
            ready = False
            for i in range(300): # up to 300 seconds
                if self._is_server_alive():
                    ready = True
                    print()  # newline after dots
                    break
                print(".", end="", flush=True)
                time.sleep(1)
            
            if not ready:
                print("GROBID server failed to start in time.")
                self.__exit__(None, None, None)
                sys.exit(1)
                
            print("GROBID server is ready.")
        except subprocess.CalledProcessError as e:
            print(f"Failed to start GROBID container: {e.stderr}")
            sys.exit(1)

        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        if self.container_id:
            print("\nStopping temporary GROBID server...")
            try:
                subprocess.run(["docker", "stop", self.container_id], capture_output=True, check=True)
            except subprocess.CalledProcessError:
                pass
            self.container_id = None

    def _is_server_alive(self):
        import requests
        try:
            resp = requests.get(f"{self.url.rstrip('/')}/api/isalive", timeout=2)
            return resp.status_code == 200
        except requests.exceptions.RequestException:
            return False

    def _is_docker_running(self):
        try:
            # Use docker info to reliably check if daemon is responsive
            subprocess.run(["docker", "info"], capture_output=True, check=True)
            return True
        except (subprocess.CalledProcessError, FileNotFoundError):
            return False

    def _launch_docker_desktop(self):
        system = platform.system()
        try:
            if system == "Windows":
                path = r"C:\Program Files\Docker\Docker\Docker Desktop.exe"
                if os.path.exists(path):
                    os.startfile(path)
                    return True
            elif system == "Darwin":
                subprocess.Popen(["open", "-a", "Docker"])
                return True
            elif system == "Linux":
                subprocess.Popen(["systemctl", "--user", "start", "docker-desktop"])
                return True
        except Exception:
            pass
        return False

    def _launch_custom(self, path):
        path = path.strip('"\'')
        try:
            if platform.system() == "Windows":
                os.startfile(path)
            else:
                subprocess.Popen([path])
            return True
        except Exception:
            return False
