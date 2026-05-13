"""Context manager for the local GROBID Docker sidecar.

Long-lived container model
--------------------------
The container is *not* thrown away on context-manager exit. Each invocation
of EviTrace reuses an existing ``evi-grobid`` container when one is present,
and only creates a new container when none exists. This preserves the
60-second JVM + CRF model warmup across runs, which is the single largest
fixed cost of GROBID on native PDFs.

States the manager handles, in order of cost:

1. Container running and ``/api/isalive`` OK → reuse, no Docker calls.
2. Container exists but stopped → ``docker start <name>``, then poll.
3. Container does not exist → ``docker run -d --name <name> --restart
   unless-stopped ...``, then poll.

On context exit the container is left running by default. Pass
``stop_on_exit=true`` under ``quality_control.grobid`` in ``config.yaml``
(or set ``EVI_GROBID_STOP_ON_EXIT=1``) for CI environments that need a
clean shutdown.
"""

from __future__ import annotations

import logging
import os
import platform
import subprocess
import sys
import threading
import time

logger = logging.getLogger("pdf_extractor")

# Disable proxies on every localhost call. A stray HTTP_PROXY / HTTPS_PROXY env
# var (common on corporate-managed dev machines) otherwise routes loopback
# traffic through the corp proxy, which usually hangs or refuses.
_NO_PROXY: dict[str, str | None] = {"http": None, "https": None}


def _escape_pdf_text(text: str) -> str:
    return text.replace("\\", "\\\\").replace("(", r"\(").replace(")", r"\)")


def _build_tiny_real_pdf(title: str, text: str) -> bytes:
    title = title.strip() or "EviTrace warmup"
    text = text.strip() or "Warmup document for GROBID model loading."
    escaped_title = _escape_pdf_text(title)
    escaped_text = _escape_pdf_text(text)

    content_stream = (
        "BT\n"
        "/F1 18 Tf\n"
        "72 740 Td\n"
        f"({escaped_title}) Tj\n"
        "0 -28 Td\n"
        f"({escaped_text}) Tj\n"
        "ET\n"
    )
    stream_bytes = content_stream.encode("latin-1")

    objects = [
        "<< /Type /Catalog /Pages 2 0 R >>",
        "<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        "<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Resources << /Font << /F1 4 0 R >> >> /Contents 5 0 R >>",
        "<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
        f"<< /Length {len(stream_bytes)} >>\nstream\n{content_stream}endstream",
    ]

    parts: list[bytes] = [b"%PDF-1.4\n"]
    offsets = [0]
    for index, obj in enumerate(objects, start=1):
        offsets.append(sum(len(part) for part in parts))
        parts.append(f"{index} 0 obj\n{obj}\nendobj\n".encode("latin-1"))

    xref_offset = sum(len(part) for part in parts)
    xref_lines = [b"xref\n0 6\n", b"0000000000 65535 f \n"]
    for offset in offsets[1:]:
        xref_lines.append(f"{offset:010d} 00000 n \n".encode("latin-1"))
    trailer = (
        "trailer\n<< /Size 6 /Root 1 0 R >>\n"
        f"startxref\n{xref_offset}\n%%EOF\n"
    ).encode("latin-1")
    return b"".join(parts + xref_lines + [trailer])


def _build_synthetic_minimal_pdf() -> bytes:
    return (
        b"%PDF-1.0\n"
        b"1 0 obj<</Type/Catalog/Pages 2 0 R>>endobj\n"
        b"2 0 obj<</Type/Pages/Kids[3 0 R]/Count 1>>endobj\n"
        b"3 0 obj<</Type/Page/MediaBox[0 0 612 792]/Parent 2 0 R>>endobj\n"
        b"xref\n0 4\n"
        b"0000000000 65535 f \n"
        b"0000000009 00000 n \n"
        b"0000000058 00000 n \n"
        b"0000000115 00000 n \n"
        b"trailer<</Size 4/Root 1 0 R>>\n"
        b"startxref\n190\n%%EOF\n"
    )


class GrobidServerManager:
    """Context manager to automate the local GROBID server lifecycle."""

    def __init__(self, config: dict):
        self.config = config.get("quality_control", {}).get("grobid", {})
        self.auto_start = self.config.get("auto_start", False)
        self.image = self.config.get("docker_image", "lfoppiano/grobid:0.8.2-crf")
        self.url = self.config.get("url", "http://localhost:8070")
        self.java_opts = str(self.config.get("java_opts", "") or "").strip()
        self.cpus = str(self.config.get("cpus", "") or "").strip()
        self.concurrency = int(self.config.get("concurrency", 0) or 0)
        self.container_name = (
            str(self.config.get("container_name", "evi-grobid") or "evi-grobid").strip()
            or "evi-grobid"
        )
        env_stop = os.environ.get("EVI_GROBID_STOP_ON_EXIT", "").strip().lower()
        self.stop_on_exit = (
            env_stop in {"1", "true", "yes"}
            if env_stop
            else bool(self.config.get("stop_on_exit", False))
        )
        self.container_id: str | None = None
        self._started_by_us = False
        logger.debug(
            "GrobidServerManager init: auto_start=%s, image=%s, url=%s, "
            "container_name=%s, java_opts=%r, concurrency=%s, cpus=%r, "
            "stop_on_exit=%s",
            self.auto_start, self.image, self.url, self.container_name,
            self.java_opts, self.concurrency, self.cpus, self.stop_on_exit,
        )

    # ------------------------------------------------------------------
    # Context manager protocol
    # ------------------------------------------------------------------

    def __enter__(self):
        if not self.auto_start:
            logger.debug("auto_start disabled; assuming external GROBID server")
            return self

        # 1. Fast path — server already answering.
        if self._is_server_alive():
            logger.info("GROBID server is already running on %s; reusing.", self.url)
            # Container was not started by this invocation; do not touch it on exit.
            return self

        logger.debug("GROBID server not responding on %s; checking Docker...", self.url)
        if not self._ensure_docker_running():
            sys.exit(1)

        # 2. Existing named container — just start it.
        state = self._container_state(self.container_name)
        if state == "running":
            # Container is up but /api/isalive was not yet ready — just poll.
            logger.info("GROBID container %r already running; waiting for readiness.", self.container_name)
            self.container_id = self.container_name
            self._started_by_us = False
        elif state == "exited":
            logger.info("GROBID container %r exists but is stopped; restarting.", self.container_name)
            try:
                subprocess.run(
                    ["docker", "start", self.container_name],
                    capture_output=True, text=True, check=True,
                )
                self.container_id = self.container_name
                self._started_by_us = True
            except subprocess.CalledProcessError as exc:
                logger.warning(
                    "docker start %s failed (%s); removing and recreating.",
                    self.container_name, exc.stderr.strip() if exc.stderr else exc,
                )
                self._remove_container(self.container_name)
                self._create_new_container()
        else:
            # No container with this name — create a fresh one.
            self._create_new_container()

        # 3. Poll until responsive.
        print("Waiting for GROBID server to become ready (this may take a few minutes on first run)...")
        logger.debug("Polling %s/api/isalive (up to 180s)...", self.url)
        ready = self._poll_until_alive(timeout=180, label="GROBID")
        if not ready:
            # Container is running but unresponsive — likely a stale/crashed
            # JVM from a previous session. Remove it and start fresh.
            print()
            logger.warning(
                "GROBID container %r is running but not responding after 180s; "
                "removing stale container and recreating.",
                self.container_name,
            )
            print("GROBID container is unresponsive; recreating...")
            self._remove_container(self.container_name)
            self._create_new_container()
            ready = self._poll_until_alive(timeout=300, label="GROBID (fresh)")
            if not ready:
                print("\nGROBID server failed to start in time.")
                logger.error("GROBID did not report healthy within 300s (fresh container).")
                sys.exit(1)

        # 4. Warm up the CRF models by sending a trivial PDF.
        # /api/isalive returns before the CRF models are fully loaded into
        # memory — the first real processFulltextDocument request pays the
        # ~120-300s model-load penalty. We absorb that cost here with a
        # synthetic minimal PDF so the user's real PDF processes quickly.
        self._warmup_models()

        print("GROBID server is ready.")
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        if not self.stop_on_exit:
            # Persistent-sidecar model: leave the container alive so the
            # next run reuses it. This is the hot path.
            logger.debug(
                "Leaving GROBID container %r running (stop_on_exit=False).",
                self.container_name,
            )
            return

        if not self._started_by_us and self.container_id is None:
            return

        target = self.container_id or self.container_name
        logger.info("Stopping GROBID container %r (stop_on_exit=True).", target)
        try:
            subprocess.run(
                ["docker", "stop", target],
                capture_output=True, check=True,
            )
        except subprocess.CalledProcessError:
            pass
        self.container_id = None

    # ------------------------------------------------------------------
    # Container management helpers
    # ------------------------------------------------------------------

    def _ensure_image_local(self) -> None:
        """Pre-pull the GROBID image with streamed progress.

        ``docker run`` will silently pull a missing image, capturing the
        progress output behind ``subprocess.run(capture_output=True)`` so the
        user just sees "Starting GROBID container..." and a multi-minute
        silence on first run. We detect the missing-image case explicitly and
        stream ``docker pull`` so progress is visible.
        """
        try:
            inspect = subprocess.run(
                ["docker", "image", "inspect", self.image],
                capture_output=True, text=True,
            )
        except FileNotFoundError:
            # Docker CLI missing; let the caller's docker-run path surface it.
            return
        if inspect.returncode == 0:
            return
        print(f"GROBID image {self.image!r} not present locally; pulling (one-time, ~1.5GB)...")
        logger.info("docker pull %s (image not local)", self.image)
        try:
            # Stream stdout/stderr directly so the user sees pull progress.
            pull = subprocess.run(["docker", "pull", self.image])
        except FileNotFoundError:
            return
        if pull.returncode != 0:
            print(f"docker pull {self.image} returned {pull.returncode}; will retry via docker run.")
            logger.warning("docker pull %s failed (rc=%d); falling back to implicit pull in docker run", self.image, pull.returncode)

    def _create_new_container(self) -> None:
        """Create a new named, persistent GROBID container."""
        # Surface image pulls explicitly so first-run users don't think the
        # warmup is hung when Docker is actually pulling ~1.5GB silently.
        self._ensure_image_local()
        print(f"Starting GROBID container {self.container_name!r} ({self.image})...")
        cmd: list[str] = [
            "docker", "run", "-d",
            "--name", self.container_name,
            "--restart", "unless-stopped",
        ]
        if self.cpus:
            cmd += ["--cpus", self.cpus]
        if self.java_opts:
            cmd += ["-e", f"JAVA_OPTS={self.java_opts}"]
        if self.concurrency:
            cmd += ["-e", f"GROBID_NB_THREADS={self.concurrency}"]
        cmd += ["-p", "8070:8070", self.image]
        logger.debug("docker run command: %s", " ".join(cmd))
        try:
            result = subprocess.run(cmd, capture_output=True, text=True, check=True)
        except subprocess.CalledProcessError as exc:
            stderr = (exc.stderr or "").strip()
            # Race condition: another process created the container between
            # state check and run. Fall back to start.
            if "is already in use" in stderr.lower() or "conflict" in stderr.lower():
                logger.warning(
                    "docker run race for %r; falling back to docker start.",
                    self.container_name,
                )
                try:
                    subprocess.run(
                        ["docker", "start", self.container_name],
                        capture_output=True, text=True, check=True,
                    )
                    self.container_id = self.container_name
                    self._started_by_us = True
                    return
                except subprocess.CalledProcessError as exc2:
                    print(f"Failed to start GROBID container: {exc2.stderr}")
                    sys.exit(1)
            print(f"Failed to start GROBID container: {stderr}")
            sys.exit(1)
        self.container_id = result.stdout.strip()
        self._started_by_us = True
        logger.debug("GROBID container id=%s", self.container_id)

    def _warmup_models(self) -> None:
        """Send a configurable warmup PDF to GROBID to trigger CRF model loading.

        The /api/isalive endpoint returns 200 before GROBID's CRF models
        are fully loaded. The first processFulltextDocument request pays a
        heavy penalty (~120-300s) for model deserialization and JIT warmup.
        We absorb that cost here so the user's actual documents process at
        normal speed (~10-60s).
        """
        import requests  # noqa: PLC0415 — lazy
        import io

        warmup_cfg = self.config.get("warmup", {}) if isinstance(self.config, dict) else {}
        enabled = bool(warmup_cfg.get("enabled", True))
        mode = str(warmup_cfg.get("mode", "tiny_real_pdf") or "tiny_real_pdf").strip()
        timeout = int(warmup_cfg.get("timeout", 600) or 600)
        title = str(warmup_cfg.get("title", "EviTrace warmup") or "EviTrace warmup")
        text = str(warmup_cfg.get("text", "Warmup document for GROBID model loading.") or "Warmup document for GROBID model loading.")

        if not enabled or mode == "disabled":
            logger.info("GROBID warmup disabled by config; skipping model warmup.")
            print("GROBID warmup disabled; proceeding.")
            return

        if mode == "synthetic_minimal_pdf":
            warmup_pdf = _build_synthetic_minimal_pdf()
        elif mode == "tiny_real_pdf":
            warmup_pdf = _build_tiny_real_pdf(title=title, text=text)
        else:
            logger.warning(
                "Unknown GROBID warmup mode %r; falling back to tiny_real_pdf.",
                mode,
            )
            warmup_pdf = _build_tiny_real_pdf(title=title, text=text)

        endpoint = self.url.rstrip("/") + "/api/processFulltextDocument"
        print(f"Warming up GROBID models (mode={mode}, timeout={timeout}s)...")
        logger.debug(
            "Sending warmup PDF to %s (mode=%s, timeout=%ds, title=%r, text_len=%d)",
            endpoint, mode, timeout, title, len(text),
        )
        t_start = time.time()

        result: dict = {}

        def _do_post() -> None:
            try:
                resp = requests.post(
                    endpoint,
                    files={"input": ("warmup.pdf", io.BytesIO(warmup_pdf), "application/pdf")},
                    data={"consolidateHeader": "0", "consolidateCitations": "0"},
                    timeout=timeout,
                    proxies=_NO_PROXY,
                )
                result["status"] = resp.status_code
            except requests.exceptions.Timeout:
                result["error"] = "timeout"
            except requests.exceptions.RequestException as exc:
                result["error"] = str(exc)

        thread = threading.Thread(target=_do_post, daemon=True)
        thread.start()
        # Enforce the wall-clock deadline ourselves: requests' timeout is a
        # read-gap timeout, not a total wall-clock cap, and there are pathological
        # combinations (slow streaming, proxy interception, hung TLS) where the
        # daemon thread never exits. Add a 5s grace so a request that timed out
        # right at the edge still gets to record its result.
        deadline = t_start + timeout + 5
        while thread.is_alive() and time.time() < deadline:
            elapsed = int(time.time() - t_start)
            remaining = max(0, timeout - elapsed)
            print(f"\r  Warming up GROBID... {elapsed}s / {timeout}s (timeout in {remaining}s)", end="", flush=True)
            thread.join(timeout=1)
        print()

        dt = time.time() - t_start
        if thread.is_alive():
            # Deadline tripped before the request returned. Leave the daemon
            # thread to die with the process and move on; real extraction has
            # its own working timeout.
            logger.warning(
                "GROBID warmup watchdog tripped after %.1fs (timeout=%ds); "
                "abandoning warmup and proceeding.", dt, timeout,
            )
            print(f"GROBID warmup did not complete in {timeout}s; proceeding without confirmation.")
        elif "error" in result:
            if result["error"] == "timeout":
                logger.warning("GROBID warmup timed out after %.1fs; proceeding anyway.", dt)
                print(f"GROBID warmup timed out ({dt:.0f}s); proceeding.")
            else:
                logger.warning("GROBID warmup request failed: %s; proceeding anyway.", result["error"])
                print("GROBID warmup failed; proceeding.")
        else:
            status = result.get("status", 0)
            if status >= 400 and mode == "tiny_real_pdf":
                logger.warning(
                    "GROBID warmup returned HTTP %d in %.1fs; proceeding.",
                    status, dt,
                )
                print(f"GROBID warmup returned {status} ({dt:.0f}s); proceeding.")
            else:
                logger.info("GROBID warmup completed in %.1fs (status=%d).", dt, status)
                print(f"GROBID models loaded ({dt:.0f}s).")

    def _poll_until_alive(self, timeout: int, label: str = "GROBID") -> bool:
        """Poll /api/isalive until ready or *timeout* wall-clock seconds elapse."""
        t_start = time.time()
        deadline = t_start + timeout
        while time.time() < deadline:
            if self._is_server_alive():
                elapsed = int(time.time() - t_start)
                logger.info("%s became ready after %ds.", label, elapsed)
                print(f"\r{label} ready ({elapsed}s).          ")
                return True
            elapsed = int(time.time() - t_start)
            remaining = max(0, int(deadline - time.time()))
            print(f"\r  Waiting for {label}... {elapsed}s (timeout in {remaining}s)", end="", flush=True)
            time.sleep(1)
        return False

    @staticmethod
    def _container_state(name: str) -> str:
        """Return 'running', 'exited', 'other', or 'missing' for container *name*."""
        try:
            result = subprocess.run(
                ["docker", "inspect", "-f", "{{.State.Status}}", name],
                capture_output=True, text=True, check=True,
            )
        except subprocess.CalledProcessError:
            return "missing"
        except FileNotFoundError:
            return "missing"
        status = (result.stdout or "").strip().lower()
        if status == "running":
            return "running"
        if status in {"exited", "created", "dead"}:
            return "exited"
        return "other" if status else "missing"

    @staticmethod
    def _remove_container(name: str) -> None:
        try:
            subprocess.run(
                ["docker", "rm", "-f", name],
                capture_output=True, text=True, check=True,
            )
        except subprocess.CalledProcessError:
            pass

    def _ensure_docker_running(self) -> bool:
        """Ensure the Docker daemon is reachable, prompting the user when possible."""
        if self._is_docker_running():
            return True

        # Non-interactive environment: don't prompt, just fail clearly.
        if not sys.stdin.isatty():
            logger.error(
                "Docker daemon is not reachable and this session is non-interactive."
            )
            print(
                "Docker daemon is not running. Start Docker Desktop and re-run, "
                "or set quality_control.grobid.auto_start: false and run GROBID externally."
            )
            return False

        print("\nPlease start Docker Desktop to use GROBID extraction.")
        try:
            ans = input(
                "Would you like EviTrace to automatically launch Docker Desktop for you? [Y/n]: "
            ).strip()
        except EOFError:
            return False

        if ans.lower() == "n":
            return False

        if not self._launch_docker_desktop():
            print("\nCould not automatically launch Docker Desktop from standard locations.")
            try:
                path = input(
                    "Please input the exact path to your Docker executable to launch it, "
                    "or press Enter to cancel and launch it manually: "
                ).strip()
            except EOFError:
                return False
            if not path:
                print("Please start Docker manually and re-run EviTrace.")
                return False
            if not self._launch_custom(path):
                print("Failed to launch from the provided path. Please start Docker manually and re-run.")
                return False

        print("Waiting for Docker daemon to start...")
        for _ in range(30):
            if self._is_docker_running():
                return True
            time.sleep(2)
        print(
            "Docker daemon did not start within the expected time. "
            "Please ensure it's fully running and try again."
        )
        return False

    def _is_server_alive(self) -> bool:
        import requests  # noqa: PLC0415 — lazy
        try:
            resp = requests.get(
                f"{self.url.rstrip('/')}/api/isalive",
                timeout=2,
                proxies=_NO_PROXY,
            )
            return resp.status_code == 200
        except requests.exceptions.RequestException:
            return False

    @staticmethod
    def _is_docker_running() -> bool:
        try:
            subprocess.run(["docker", "info"], capture_output=True, check=True)
            return True
        except (subprocess.CalledProcessError, FileNotFoundError):
            return False

    @staticmethod
    def _launch_docker_desktop() -> bool:
        system = platform.system()
        try:
            if system == "Windows":
                path = r"C:\Program Files\Docker\Docker\Docker Desktop.exe"
                if os.path.exists(path):
                    os.startfile(path)  # type: ignore[attr-defined]
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

    @staticmethod
    def _launch_custom(path: str) -> bool:
        path = path.strip('"\'')
        try:
            if platform.system() == "Windows":
                os.startfile(path)  # type: ignore[attr-defined]
            else:
                subprocess.Popen([path])
            return True
        except Exception:
            return False
