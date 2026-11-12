#!/usr/bin/env python3
"""Trabajador de Shelton: corre en cada tarjeta alquilada (RunPod, Vast…) u otra PC con GPU.

Shelton le manda entrenamientos de LoRA (fotos + configuración), el trabajador los corre con ai-toolkit en una GPU
libre, informa el avance y devuelve la LoRA. Al terminar, Shelton borra el trabajo (las fotos no se quedan acá).
Solo usa la librería estándar de Python. Escucha con un token: úsalo detrás de Tailscale (nada abierto a internet).

Variables (todas opcionales salvo el token):
  WORKER_TOKEN   clave que también se pone en Shelton (Conexiones → Nodo de GPU)      [obligatoria]
  WORKER_PORT    puerto (8299)
  WORKER_HOST    dirección donde escucha (0.0.0.0; detrás de Tailscale)
  WORKER_GPUS    lugares de entrenamiento: "0" (una GPU), "0,1" (dos GPU), o los UUID de MIG
                 "MIG-aaa,MIG-bbb,MIG-ccc" (una RTX PRO 6000 dividida en 3). Por defecto: una por GPU detectada.
  AITK_DIR       carpeta de ai-toolkit (/workspace/ai-toolkit)
  WORKER_DIR     carpeta de trabajos (/workspace/shelton_jobs)
  COMFY_LORA_DIR carpeta de LoRAs del ComfyUI de esta máquina (si también genera fotos)
"""
import json
import os
import re
import shutil
import subprocess
import threading
import time
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

VERSION = "1.0"
TOKEN = os.environ.get("WORKER_TOKEN", "")
PORT = int(os.environ.get("WORKER_PORT", "8299"))
HOST = os.environ.get("WORKER_HOST", "0.0.0.0")
AITK = Path(os.environ.get("AITK_DIR", "/workspace/ai-toolkit")).expanduser()
JOBS = Path(os.environ.get("WORKER_DIR", "/workspace/shelton_jobs")).expanduser()
LORA_DIR = os.environ.get("COMFY_LORA_DIR", "")
STEP_RE = re.compile(r"(\d+)\s*/\s*(\d+)\s*\[")
SAFE = re.compile(r"^[A-Za-z0-9_.\-/]+$")

_jobs: dict = {}
_lock = threading.Lock()


def _gpus_detected() -> list:
    try:
        out = subprocess.run(["nvidia-smi", "--query-gpu=index", "--format=csv,noheader"], capture_output=True,
                             text=True, timeout=10).stdout
        return [x.strip() for x in out.splitlines() if x.strip()] or ["0"]
    except Exception:  # noqa: BLE001
        return ["0"]


SLOTS = [x.strip() for x in os.environ.get("WORKER_GPUS", "").split(",") if x.strip()] or _gpus_detected()


def _gpu_info() -> list:
    try:
        out = subprocess.run(["nvidia-smi", "--query-gpu=index,name,utilization.gpu,memory.used,memory.total,"
                              "power.draw,temperature.gpu", "--format=csv,noheader,nounits"],
                             capture_output=True, text=True, timeout=10).stdout
    except Exception:  # noqa: BLE001
        return []
    gpus = []
    for line in out.splitlines():
        c = [x.strip() for x in line.split(",")]
        if len(c) >= 7:
            def num(v):
                try:
                    return float(v)
                except ValueError:
                    return None
            gpus.append({"index": c[0], "name": c[1], "util": num(c[2]), "mem_used_mb": num(c[3]),
                         "mem_total_mb": num(c[4]), "power_w": num(c[5]), "temp_c": num(c[6])})
    return gpus


def _python() -> str:
    for p in (AITK / "venv" / "bin" / "python", AITK / ".venv" / "bin" / "python"):
        if p.exists():
            return str(p)
    return "python3"


def _read_step(path: Path) -> int:
    try:
        with open(path, "rb") as f:
            n = int.from_bytes(f.read(8), "little")
            if n <= 0 or n > 50_000_000:
                return 0
            header = json.loads(f.read(n))
        info = (header.get("__metadata__") or {}).get("training_info")
        info = json.loads(info) if isinstance(info, str) else (info or {})
        return int(info.get("step") or 0)
    except Exception:  # noqa: BLE001
        return 0


def _busy_slots() -> set:
    return {j["slot"] for j in _jobs.values() if j.get("status") in ("running",) and j.get("slot") is not None}


def _run_job(jid: str) -> None:
    job = _jobs[jid]
    d = JOBS / jid
    cfg_text = json.dumps(job["config"]).replace("__JOB__", str(d))
    (d / "config.json").write_text(cfg_text, encoding="utf-8")
    env = {**os.environ, "PYTHONUNBUFFERED": "1", "CUDA_VISIBLE_DEVICES": str(job["slot"])}
    job["phase"] = "Cargando el modelo"
    with open(d / "train.log", "a", encoding="utf-8") as log:
        proc = subprocess.Popen([_python(), "-u", "run.py", str(d / "config.json")], cwd=str(AITK), env=env,
                                stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
        job["pid"] = proc.pid
        buf = b""
        while True:
            chunk = proc.stdout.read1(4096) if hasattr(proc.stdout, "read1") else proc.stdout.read(4096)
            if not chunk:
                break
            buf += chunk
            parts = re.split(rb"[\r\n]", buf)
            buf = parts.pop()
            for raw in parts:
                line = raw.decode(errors="ignore").strip()
                if not line:
                    continue
                log.write(line + "\n")
                log.flush()
                job["tail"] = (job.get("tail", []) + [line])[-30:]
                m = STEP_RE.search(line)
                if m:
                    job["step"], job["total"] = int(m.group(1)), int(m.group(2))
                    job["phase"] = "Entrenando"
                elif "download" in line.lower() or "fetching" in line.lower():
                    job["phase"] = "Bajando el modelo (solo la primera vez)"
                elif "caching" in line.lower() or "latent" in line.lower():
                    job["phase"] = "Preparando las fotos"
        rc = proc.wait()
    if job.get("status") == "canceled":
        return
    if rc != 0:
        tail = job.get("tail", [])
        err = next((ln for ln in reversed(tail) if "error" in ln.lower()), tail[-1] if tail else "sin detalle")
        if "out of memory" in " ".join(tail).lower():
            err = "La tarjeta se quedó sin memoria: baja los entrenamientos a la vez (WORKER_GPUS) o usa el perfil estándar."
        job.update(status="failed", error=err[-400:])
        return
    job.update(status="done", phase="Terminado")


def _result_file(jid: str):
    d = JOBS / jid / "output"
    files = list(d.rglob("*.safetensors")) if d.exists() else []
    return max(files, key=lambda p: (_read_step(p), p.stat().st_mtime)) if files else None


class Handler(BaseHTTPRequestHandler):
    server_version = "SheltonWorker/" + VERSION

    def log_message(self, *a):  # silencioso
        pass

    def _send(self, code: int, data=None, raw: bytes | None = None):
        body = raw if raw is not None else json.dumps(data if data is not None else {}).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/octet-stream" if raw is not None else "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _auth(self) -> bool:
        if TOKEN and self.headers.get("Authorization", "") == f"Bearer {TOKEN}":
            return True
        self._send(401, {"error": "Token inválido"})
        return False

    def _body(self) -> bytes:
        n = int(self.headers.get("Content-Length") or 0)
        return self.rfile.read(n) if n else b""

    def _parts(self):
        u = urlparse(self.path)
        return [p for p in u.path.split("/") if p], parse_qs(u.query)

    def do_GET(self):
        if not self._auth():
            return
        parts, _ = self._parts()
        if parts == ["health"]:
            busy = _busy_slots()
            return self._send(200, {"ok": True, "version": VERSION, "gpus": _gpu_info(), "slots": len(SLOTS),
                                    "free": len([s for s in SLOTS if s not in busy]), "trainer": (AITK / "run.py").exists(),
                                    "running": [{"id": k, "name": v.get("name"), "step": v.get("step"), "total": v.get("total")}
                                                for k, v in _jobs.items() if v.get("status") == "running"]})
        if len(parts) == 2 and parts[0] == "jobs" and parts[1] in _jobs:
            j = _jobs[parts[1]]
            return self._send(200, {k: j.get(k) for k in ("status", "phase", "step", "total", "tail", "error", "name")})
        if len(parts) == 3 and parts[0] == "jobs" and parts[2] == "result" and parts[1] in _jobs:
            f = _result_file(parts[1])
            if not f:
                return self._send(404, {"error": "Todavía no hay LoRA"})
            return self._send(200, raw=f.read_bytes())
        self._send(404, {"error": "No existe"})

    def do_POST(self):
        if not self._auth():
            return
        parts, q = self._parts()
        if parts == ["jobs"]:
            data = json.loads(self._body() or b"{}")
            jid = uuid.uuid4().hex[:12]
            (JOBS / jid / "dataset").mkdir(parents=True, exist_ok=True)
            with _lock:
                _jobs[jid] = {"status": "created", "name": data.get("name", ""), "config": data.get("config") or {},
                              "step": 0, "total": 0, "tail": [], "slot": None, "created": time.time()}
            return self._send(200, {"id": jid})
        if len(parts) == 3 and parts[0] == "jobs" and parts[1] in _jobs:
            jid, action = parts[1], parts[2]
            if action == "file":
                rel = (q.get("path") or [""])[0]
                if not SAFE.match(rel) or ".." in rel:
                    return self._send(400, {"error": "Ruta inválida"})
                dest = JOBS / jid / rel
                dest.parent.mkdir(parents=True, exist_ok=True)
                dest.write_bytes(self._body())
                return self._send(200, {"ok": True})
            if action == "start":
                body = self._body()
                if body:
                    try:
                        cfg = json.loads(body).get("config")
                        if cfg:
                            _jobs[jid]["config"] = cfg
                    except ValueError:
                        return self._send(400, {"error": "Configuración inválida"})
                with _lock:
                    free = [s for s in SLOTS if s not in _busy_slots()]
                    if not free:
                        return self._send(409, {"error": "Sin lugar libre en esta máquina"})
                    _jobs[jid].update(status="running", slot=free[0], phase="Arrancando")
                threading.Thread(target=self._guard, args=(jid,), daemon=True).start()
                return self._send(200, {"ok": True, "slot": free[0]})
            if action == "cancel":
                j = _jobs[jid]
                j["status"] = "canceled"
                if j.get("pid"):
                    try:
                        os.kill(j["pid"], 15)
                    except OSError:
                        pass
                return self._send(200, {"ok": True})
        if parts == ["loras"]:
            name = (q.get("name") or [""])[0]
            if not LORA_DIR or not SAFE.match(name) or "/" in name or not name.endswith(".safetensors"):
                return self._send(400, {"error": "Sin COMFY_LORA_DIR o nombre inválido"})
            Path(LORA_DIR).mkdir(parents=True, exist_ok=True)
            (Path(LORA_DIR) / name).write_bytes(self._body())
            return self._send(200, {"ok": True})
        self._send(404, {"error": "No existe"})

    @staticmethod
    def _guard(jid):
        try:
            _run_job(jid)
        except Exception as e:  # noqa: BLE001
            _jobs[jid].update(status="failed", error=str(e)[:300])

    def do_DELETE(self):
        if not self._auth():
            return
        parts, _ = self._parts()
        if len(parts) == 2 and parts[0] == "jobs":
            j = _jobs.pop(parts[1], None)
            if j and j.get("pid") and j.get("status") == "running":
                try:
                    os.kill(j["pid"], 15)
                except OSError:
                    pass
            shutil.rmtree(JOBS / parts[1], ignore_errors=True)     # las fotos no quedan en esta máquina
            return self._send(200, {"ok": True})
        self._send(404, {"error": "No existe"})


def main():
    if not TOKEN:
        raise SystemExit("Falta WORKER_TOKEN (la misma clave que pones en Shelton)")
    JOBS.mkdir(parents=True, exist_ok=True)
    print(f"Trabajador de Shelton {VERSION} en {HOST}:{PORT} · lugares: {', '.join(SLOTS)} · ai-toolkit: {AITK}", flush=True)
    ThreadingHTTPServer((HOST, PORT), Handler).serve_forever()


if __name__ == "__main__":
    main()
