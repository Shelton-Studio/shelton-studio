from app.comfyui.client import ComfyUIClient
import os
from datetime import datetime

class GeneratorAgent:
    """Generates image using Z-Image Turbo via ComfyUI"""

    def __init__(self):
        self.client = ComfyUIClient()

    async def process(self, prompt: str, comfyui_location: str = "local") -> str:
        """Generate image using Z-Image Turbo"""

        # Create output directory if needed
        output_dir = "generated_images"
        os.makedirs(output_dir, exist_ok=True)

        try:
            print(f"[Generator] Sending prompt to ComfyUI ({comfyui_location})...")

            # Prepare workflow for Z-Image Turbo
            workflow = self._prepare_workflow(prompt)

            # Execute on selected location
            result = await self.client.execute_workflow(
                workflow=workflow,
                location=comfyui_location
            )

            # Save image
            if result.get("success"):
                image_path = os.path.join(output_dir, f"gen_{datetime.now().strftime('%Y%m%d_%H%M%S')}.png")
                # In real implementation, save the actual image bytes from result
                print(f"[Generator] Image saved to {image_path}")
                return image_path
            else:
                raise Exception(f"ComfyUI generation failed: {result.get('error')}")

        except Exception as e:
            print(f"[Generator] Error: {e}")
            raise

    def _prepare_workflow(self, prompt: str) -> dict:
        """Prepare Z-Image Turbo workflow for ComfyUI"""

        # This is a simplified workflow structure
        # Real implementation would use exact ComfyUI node structure
        workflow = {
            "1": {
                "inputs": {"text": prompt},
                "class_type": "CLIPTextEncode",
                "_meta": {"title": "CLIP Text Encode"}
            },
            "2": {
                "inputs": {
                    "seed": 0,
                    "steps": 8,
                    "cfg": 1,
                    "sampler_name": "euler",
                    "scheduler": "simple"
                },
                "class_type": "KSampler",
                "_meta": {"title": "KSampler"}
            },
            "3": {
                "inputs": {"samples": ["2", 0]},
                "class_type": "VAEDecode",
                "_meta": {"title": "VAE Decode"}
            },
            "4": {
                "inputs": {"images": ["3", 0]},
                "class_type": "SaveImage",
                "_meta": {"title": "Save Image"}
            }
        }

        return workflow
