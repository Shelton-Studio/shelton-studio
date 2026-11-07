"""Motor de prueba sin GPU: dibuja imágenes de ejemplo para probar toda la cadena y la interfaz."""
import asyncio
import hashlib
import io
import textwrap

from PIL import Image, ImageDraw, ImageFilter, ImageFont, ImageOps

from app.engines import GPU_LOCK, Progress


def _font(size: int):
    for name in ("DejaVuSans-Bold.ttf", "Arial.ttf", "arial.ttf"):
        try:
            return ImageFont.truetype(name, size)
        except OSError:
            pass
    return ImageFont.load_default()


class MockEngine:
    name = "mock"

    async def generate(self, prompt, width, height, seed, on_progress: Progress = None) -> bytes:
        async with GPU_LOCK:
            if on_progress:
                await on_progress("simulando GPU")
            await asyncio.sleep(0.5)
            h = hashlib.sha256(f"{prompt}{seed}".encode()).digest()
            c1, c2 = (h[0], h[1], h[2]), (h[3], h[4], h[5])
            img = Image.new("RGB", (width, height))
            d = ImageDraw.Draw(img)
            for y in range(height):
                t = y / max(height - 1, 1)
                d.line([(0, y), (width, y)], fill=tuple(int(a + (b - a) * t) for a, b in zip(c1, c2)))
            f = _font(max(18, width // 28))
            text = "\n".join(textwrap.wrap(prompt[:300], 34))
            d.multiline_text((width * 0.07, height * 0.1), "MOCK · " + text, font=f, fill="white", spacing=8)
            buf = io.BytesIO()
            img.save(buf, "PNG")
            return buf.getvalue()

    async def edit(self, image, instruction, seed, on_progress: Progress = None) -> bytes:
        async with GPU_LOCK:
            await asyncio.sleep(0.3)
            img = Image.open(io.BytesIO(image)).convert("RGB")
            img = ImageOps.autocontrast(img.filter(ImageFilter.SMOOTH_MORE))
            d = ImageDraw.Draw(img)
            d.rectangle([0, img.height - 80, img.width, img.height], fill=(0, 0, 0))
            d.text((20, img.height - 60), f"EDIT: {instruction[:60]}", font=_font(28), fill="yellow")
            buf = io.BytesIO()
            img.save(buf, "PNG")
            return buf.getvalue()

    async def generate_with_refs(self, refs, prompt, width, height, seed, on_progress: Progress = None) -> bytes:
        base = await self.generate(prompt, width, height, seed, on_progress)
        img = Image.open(io.BytesIO(base)).convert("RGB")
        ref = Image.open(io.BytesIO(refs[0])).convert("RGB")
        ref.thumbnail((width // 3, height // 3))
        img.paste(ref, (width - ref.width - 20, height - ref.height - 20))
        buf = io.BytesIO()
        img.save(buf, "PNG")
        return buf.getvalue()

    async def health(self) -> dict:
        return {"ok": True, "engine": self.name, "note": "Modo prueba: no usa GPU ni modelos reales."}
