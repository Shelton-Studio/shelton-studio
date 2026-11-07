from app.comfyui.client import ComfyUIClient
import os
from datetime import datetime

class EditorAgent:
    """Edits generated images using Qwen-Image-Edit"""

    def __init__(self):
        self.client = ComfyUIClient()

    async def process(self, image_path: str, strategy: dict) -> str:
        """Refine image based on strategy"""

        try:
            print(f"[Editor] Refining image from {image_path}...")

            # Check if refinement is needed
            if not self._needs_refinement(strategy):
                print("[Editor] No refinement needed, returning original")
                return image_path

            # Prepare refinement instructions based on strategy
            refinement_prompt = self._prepare_refinement_prompt(strategy)

            # Prepare workflow for Qwen-Image-Edit
            workflow = self._prepare_workflow(image_path, refinement_prompt)

            # Execute refinement (using local by default for quick edits)
            result = await self.client.execute_workflow(
                workflow=workflow,
                location="local"
            )

            if result.get("success"):
                edited_path = os.path.join(
                    os.path.dirname(image_path),
                    f"edited_{os.path.basename(image_path)}"
                )
                print(f"[Editor] Image refined and saved to {edited_path}")
                return edited_path
            else:
                print(f"[Editor] Refinement skipped, returning original")
                return image_path

        except Exception as e:
            print(f"[Editor] Error during refinement: {e}, returning original")
            return image_path

    def _needs_refinement(self, strategy: dict) -> bool:
        """Check if refinement is needed"""
        adjustments = strategy.get("adjustments", [])
        return len(adjustments) > 0

    def _prepare_refinement_prompt(self, strategy: dict) -> str:
        """Create refinement prompt from strategy"""

        adjustments = strategy.get("adjustments", [])
        if not adjustments:
            return "Minor enhancement and polishing"

        return f"Apply these refinements: {', '.join(adjustments)}"

    def _prepare_workflow(self, image_path: str, refinement_prompt: str) -> dict:
        """Prepare Qwen-Image-Edit workflow"""

        # Simplified workflow structure
        workflow = {
            "1": {
                "inputs": {"image": image_path},
                "class_type": "LoadImage",
                "_meta": {"title": "Load Image"}
            },
            "2": {
                "inputs": {"text": refinement_prompt},
                "class_type": "CLIPTextEncode",
                "_meta": {"title": "Refinement Prompt"}
            },
            "3": {
                "inputs": {
                    "image": ["1", 0],
                    "text": ["2", 0],
                    "steps": 4,
                    "cfg": 1
                },
                "class_type": "QwenImageEdit",
                "_meta": {"title": "Qwen Image Edit"}
            },
            "4": {
                "inputs": {"images": ["3", 0]},
                "class_type": "SaveImage",
                "_meta": {"title": "Save Edited Image"}
            }
        }

        return workflow
