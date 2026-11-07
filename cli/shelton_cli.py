#!/usr/bin/env python3
"""CLI de Shelton. Habla con el servidor (python -m app.main debe estar corriendo).

  python cli/shelton_cli.py chat                      # conversación con Shelton en la terminal
  python cli/shelton_cli.py generate "idea" -p tiktok -n 3
  python cli/shelton_cli.py campaign "brief" -n 50
  python cli/shelton_cli.py status
  python cli/shelton_cli.py export --status approved -o posts.zip
"""
import json
import os

import httpx
import typer

app = typer.Typer(help="Shelton — estudio de contenido multiagente")
BASE = os.getenv("SHELTON_URL", "http://127.0.0.1:8000")


def _chat_once(message: str, conv: str | None) -> str | None:
    with httpx.stream("POST", f"{BASE}/api/chat", json={"message": message, "conversation_id": conv},
                      timeout=None) as r:
        r.raise_for_status()
        for line in r.iter_lines():
            if not line.startswith("data: "):
                continue
            ev = json.loads(line[6:])
            t = ev["type"]
            if t == "conversation":
                conv = ev["id"]
            elif t == "status":
                typer.secho(f"  · {ev['text']}", fg="bright_black")
            elif t == "message":
                typer.secho(f"\nShelton: {ev['text']}", fg="cyan")
                for pid in ev.get("post_ids", []):
                    typer.echo(f"  post {pid}: {BASE}/api/posts/{pid}")
            elif t == "error":
                typer.secho(f"Error: {ev['text']}", fg="red")
    return conv


@app.command()
def chat():
    """Conversación interactiva con Shelton."""
    conv = None
    typer.secho("Shelton listo. Escribe 'salir' para terminar.", fg="green")
    while True:
        msg = typer.prompt("\nTú")
        if msg.strip().lower() in ("salir", "exit", "quit"):
            break
        conv = _chat_once(msg, conv)


@app.command()
def generate(idea: str, platform: str = typer.Option("instagram_feed", "-p"), count: int = typer.Option(1, "-n"),
             subject: list[str] = typer.Option([], "-s", help="Modelos del banco por nombre")):
    """Encola posts directamente (sin chat)."""
    r = httpx.post(f"{BASE}/api/posts", json={"idea": idea, "platform": platform, "count": count, "subjects": subject})
    r.raise_for_status()
    typer.echo(json.dumps(r.json(), indent=2, ensure_ascii=False))


@app.command()
def campaign(brief: str, count: int = typer.Option(50, "-n"), platform: list[str] = typer.Option([], "-p")):
    """Planifica y encola un lote grande."""
    r = httpx.post(f"{BASE}/api/campaigns", json={"brief": brief, "count": count, "platforms": platform})
    r.raise_for_status()
    typer.echo(f"Campaña {r.json()['id']} lanzada con {count} posts.")


@app.command()
def status():
    """Estado de Claude, motor de imagen y cola."""
    typer.echo(json.dumps(httpx.get(f"{BASE}/api/health", timeout=30).json(), indent=2, ensure_ascii=False))


@app.command()
def export(status: str = "approved", out: str = typer.Option("shelton_posts.zip", "-o")):
    """Descarga un ZIP con imágenes + textos + CSV."""
    r = httpx.get(f"{BASE}/api/export", params={"status": status}, timeout=120)
    r.raise_for_status()
    open(out, "wb").write(r.content)
    typer.echo(f"Guardado en {out}")


if __name__ == "__main__":
    app()
