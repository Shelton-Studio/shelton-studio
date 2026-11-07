"""Post-producción de video local con ffmpeg (incluido vía imageio-ffmpeg).

- clip del motor (WebP animado de ComfyUI o MP4 de la nube) -> MP4 H.264 vertical listo para redes
- interpolación de movimiento (16 -> 30 fps) para que se vea fluido
- póster (primer cuadro) y hoja de contacto (3 cuadros) para que el juez revise
- unir clips en un reel
"""
import io
import subprocess
import tempfile
from pathlib import Path

from PIL import Image, ImageSequence

try:
    import imageio_ffmpeg
    FFMPEG = imageio_ffmpeg.get_ffmpeg_exe()
except Exception:  # pragma: no cover
    FFMPEG = "ffmpeg"


def _run(args: list[str], stdin: bytes | None = None) -> None:
    p = subprocess.run([FFMPEG, "-hide_banner", "-loglevel", "error", "-y", *args], input=stdin, capture_output=True)
    if p.returncode != 0:
        raise RuntimeError(f"ffmpeg: {p.stderr.decode(errors='ignore')[-600:]}")


def _vf(width: int, height: int, src_fps: float, out_fps: int, interpolate: bool) -> str:
    filters = []
    if interpolate and out_fps > src_fps:
        filters.append(f"minterpolate=fps={out_fps}:mi_mode=mci:mc_mode=aobmc:me_mode=bidir:vsbmc=1")
    filters.append(f"scale={width}:{height}:force_original_aspect_ratio=increase:flags=lanczos")
    filters.append(f"crop={width}:{height}")
    filters.append("format=yuv420p")
    return ",".join(filters)


_ENCODE = ["-c:v", "libx264", "-preset", "slow", "-crf", "17", "-profile:v", "high", "-movflags", "+faststart", "-an"]


def is_webp(data: bytes) -> bool:
    return data[:4] == b"RIFF" and data[8:12] == b"WEBP"


def clip_to_mp4(data: bytes, src_fps: float, out_path: str, width: int, height: int,
                out_fps: int = 30, interpolate: bool = True) -> str:
    """Convierte la salida del motor en un MP4 del tamaño exacto de la red."""
    vf = _vf(width, height, src_fps, out_fps, interpolate)
    if is_webp(data) or data[:4] == b"GIF8":
        frames = [f.convert("RGB") for f in ImageSequence.Iterator(Image.open(io.BytesIO(data)))]
        if not frames:
            raise RuntimeError("El clip no tiene cuadros")
        w, h = frames[0].size
        raw = b"".join(f.resize((w, h)).tobytes() for f in frames)
        _run(["-f", "rawvideo", "-pix_fmt", "rgb24", "-s", f"{w}x{h}", "-r", str(src_fps), "-i", "-",
              "-vf", vf, "-r", str(out_fps), *_ENCODE, out_path], stdin=raw)
    else:  # MP4/MOV/WebM de motores en la nube
        with tempfile.NamedTemporaryFile(suffix=".bin", delete=False) as tmp:
            tmp.write(data)
        try:
            _run(["-i", tmp.name, "-vf", vf, "-r", str(out_fps), *_ENCODE, out_path])
        finally:
            Path(tmp.name).unlink(missing_ok=True)
    return out_path


def frame_at(video_path: str, out_path: str, at: str = "0") -> str:
    _run(["-ss", at, "-i", video_path, "-frames:v", "1", "-q:v", "2", out_path])
    return out_path


def duration(video_path: str) -> float:
    p = subprocess.run([FFMPEG, "-hide_banner", "-i", video_path], capture_output=True)
    for line in p.stderr.decode(errors="ignore").splitlines():
        if "Duration:" in line:
            h, m, s = line.split("Duration:")[1].split(",")[0].strip().split(":")
            return int(h) * 3600 + int(m) * 60 + float(s)
    return 0.0


def contact_sheet(video_path: str, out_path: str) -> str:
    """3 cuadros (inicio, medio, final) lado a lado para que el juez revise el movimiento."""
    d = max(duration(video_path), 0.1)
    frames = []
    with tempfile.TemporaryDirectory() as td:
        for i, t in enumerate((0.0, d / 2, max(d - 0.1, 0))):
            f = Path(td) / f"f{i}.jpg"
            frame_at(video_path, str(f), f"{t:.2f}")
            frames.append(Image.open(f).convert("RGB"))
        w, h = frames[0].size
        sheet = Image.new("RGB", (w * 3, h))
        for i, fr in enumerate(frames):
            sheet.paste(fr.resize((w, h)), (i * w, 0))
        sheet.thumbnail((2400, 2400))
        sheet.save(out_path, "JPEG", quality=88)
    return out_path


def concat(paths: list[str], out_path: str, width: int, height: int, fps: int = 30) -> str:
    """Une clips en un reel (re-codifica para que todo encaje: tamaño, fps, transición limpia)."""
    if not paths:
        raise ValueError("No hay clips para unir")
    inputs, chains = [], []
    for i, p in enumerate(paths):
        inputs += ["-i", p]
        chains.append(f"[{i}:v]scale={width}:{height}:force_original_aspect_ratio=increase,crop={width}:{height},"
                      f"fps={fps},format=yuv420p,setsar=1[v{i}]")
    graph = ";".join(chains) + ";" + "".join(f"[v{i}]" for i in range(len(paths))) + f"concat=n={len(paths)}:v=1:a=0[out]"
    _run([*inputs, "-filter_complex", graph, "-map", "[out]", *_ENCODE, out_path])
    return out_path
