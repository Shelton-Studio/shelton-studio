#!/usr/bin/env python3
"""Shelton CLI - Command-line interface for content generation"""

import typer
import asyncio
import httpx
import json
from typing import Optional
import time

app = typer.Typer(help="Shelton - Multi-Agent Content Generation Platform")

BASE_URL = "http://localhost:8000/api"

@app.command()
def generate(
    prompt: str = typer.Argument(..., help="Content generation prompt"),
    location: str = typer.Option("local", "--location", "-l", help="ComfyUI location: local or cloud"),
    wait: bool = typer.Option(True, "--wait/--no-wait", help="Wait for completion")
):
    """Generate content from a prompt"""

    typer.echo(f"🤖 Shelton - Generating content...\n")
    typer.echo(f"📝 Prompt: {prompt}")
    typer.echo(f"📍 Location: {location}\n")

    try:
        # Send generation request
        with httpx.Client() as client:
            response = client.post(
                f"{BASE_URL}/generate",
                json={"prompt": prompt, "comfyui_location": location}
            )
            response.raise_for_status()
            data = response.json()
            job_id = data["job_id"]

        typer.echo(f"✓ Job created: {job_id}")

        if not wait:
            typer.echo(f"✓ Processing started. Check status with: shelton status {job_id}")
            return

        # Poll for completion
        typer.echo("\n⏳ Processing...\n")
        _poll_job(job_id)

    except Exception as e:
        typer.echo(f"❌ Error: {e}", err=True)
        raise typer.Exit(code=1)

@app.command()
def status(job_id: str = typer.Argument(..., help="Job ID")):
    """Check job status"""

    try:
        with httpx.Client() as client:
            response = client.get(f"{BASE_URL}/status/{job_id}")
            response.raise_for_status()
            job = response.json()

        typer.echo(f"\n📊 Job Status: {job_id}\n")
        typer.echo(f"Status: {job['status']}")

        if job.get("error"):
            typer.echo(f"❌ Error: {job['error']}")

        if job.get("image_path"):
            typer.echo(f"✓ Image: {job['image_path']}")
            if job.get("qa_passed"):
                typer.echo("✓ QA Passed")
            else:
                typer.echo("⚠ QA Issues")

    except Exception as e:
        typer.echo(f"❌ Error: {e}", err=True)
        raise typer.Exit(code=1)

@app.command()
def content(content_id: str = typer.Argument(..., help="Content ID")):
    """View content details"""

    try:
        with httpx.Client() as client:
            response = client.get(f"{BASE_URL}/content/{content_id}")
            response.raise_for_status()
            content = response.json()

        typer.echo(f"\n📋 Content: {content_id}\n")
        typer.echo(f"Original Prompt: {content['original_prompt']}")
        typer.echo(f"\nEnriched Prompt: {content['enriched_prompt']}")
        typer.echo(f"\nImage: {content.get('image_path', 'N/A')}")

        if content.get("metadata"):
            typer.echo(f"\nMetadata:")
            for key, value in content["metadata"].items():
                typer.echo(f"  {key}: {value}")

        typer.echo(f"\nQA Passed: {'✓' if content['qa_passed'] else '✗'}")
        typer.echo(f"Human Approved: {'✓' if content['human_approved'] else '✗'}")

    except Exception as e:
        typer.echo(f"❌ Error: {e}", err=True)
        raise typer.Exit(code=1)

@app.command()
def history(limit: int = typer.Option(10, "--limit", "-n", help="Number of items to show")):
    """View content history"""

    try:
        with httpx.Client() as client:
            response = client.get(f"{BASE_URL}/history?limit={limit}")
            response.raise_for_status()
            items = response.json()

        if not items:
            typer.echo("No content history")
            return

        typer.echo(f"\n📚 Content History (last {limit})\n")

        for i, item in enumerate(items, 1):
            status_icon = "✓" if item.get("human_approved") else "⏳"
            typer.echo(f"{i}. {status_icon} {item['original_prompt'][:60]}...")
            typer.echo(f"   ID: {item['id']}")
            typer.echo()

    except Exception as e:
        typer.echo(f"❌ Error: {e}", err=True)
        raise typer.Exit(code=1)

@app.command()
def approve(
    content_id: str = typer.Argument(..., help="Content ID"),
    reject: bool = typer.Option(False, "--reject", help="Reject instead of approve")
):
    """Approve or reject content"""

    try:
        approved = not reject
        action = "reject" if reject else "approve"

        with httpx.Client() as client:
            response = client.post(
                f"{BASE_URL}/approve",
                json={
                    "content_id": content_id,
                    "approved": approved,
                    "notes": f"Decision made via CLI"
                }
            )
            response.raise_for_status()
            result = response.json()

        icon = "✓" if approved else "✗"
        typer.echo(f"\n{icon} Content {action}ed: {content_id}")
        typer.echo(result.get("message", "Done"))

    except Exception as e:
        typer.echo(f"❌ Error: {e}", err=True)
        raise typer.Exit(code=1)

@app.command()
def health():
    """Check API health"""

    try:
        with httpx.Client() as client:
            response = client.get(f"{BASE_URL}/health")
            response.raise_for_status()
            data = response.json()

        typer.echo(f"\n✓ {data['app']} is healthy")

    except Exception as e:
        typer.echo(f"❌ API is down: {e}", err=True)
        raise typer.Exit(code=1)

def _poll_job(job_id: str, timeout: int = 300):
    """Poll job status until completion"""

    steps = [
        "Copywriter enriching prompt...",
        "Strategist analyzing content...",
        "Generator creating image...",
        "Editor refining image...",
        "QA validating content...",
        "Ready for human review!"
    ]

    start_time = time.time()
    step = 0

    with httpx.Client() as client:
        while time.time() - start_time < timeout:
            try:
                response = client.get(f"{BASE_URL}/status/{job_id}")
                response.raise_for_status()
                job = response.json()

                if job["status"] == "completed":
                    typer.echo("✓ Processing complete!")
                    typer.echo(f"Content ID: {job['content_id']}")
                    typer.echo(f"Image: {job.get('image_path', 'N/A')}")
                    return

                elif job["status"] == "failed":
                    typer.echo(f"\n❌ Processing failed: {job.get('error')}")
                    raise typer.Exit(code=1)

                else:
                    # Show progress
                    step = min(step + 1, len(steps) - 1)
                    typer.echo(steps[step % len(steps)], nl=False)
                    typer.echo("\r" + steps[step % len(steps)], nl=False)

                time.sleep(1)

            except httpx.HTTPError as e:
                typer.echo(f"❌ API error: {e}", err=True)
                raise typer.Exit(code=1)

    typer.echo(f"\n❌ Job timeout after {timeout}s")
    raise typer.Exit(code=1)

if __name__ == "__main__":
    app()
