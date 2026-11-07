import httpx
import asyncio
import json
from typing import Dict, Any
from app.config import settings

class ComfyUIClient:
    """Client for ComfyUI API - handles both local and cloud connections"""

    def __init__(self):
        self.local_url = settings.COMFYUI_LOCAL_URL
        self.cloud_url = settings.COMFYUI_CLOUD_URL
        self.cloud_token = settings.COMFYUI_CLOUD_TOKEN

    async def execute_workflow(
        self,
        workflow: Dict[str, Any],
        location: str = "local"
    ) -> Dict[str, Any]:
        """Execute workflow on selected ComfyUI instance"""

        if location == "local":
            return await self._execute_local(workflow)
        elif location == "cloud":
            return await self._execute_cloud(workflow)
        else:
            raise ValueError(f"Unknown location: {location}")

    async def _execute_local(self, workflow: Dict[str, Any]) -> Dict[str, Any]:
        """Execute workflow on local ComfyUI instance"""

        try:
            async with httpx.AsyncClient(timeout=300.0) as client:
                # Prompt endpoint (queues the workflow)
                response = await client.post(
                    f"{self.local_url}/prompt",
                    json={"prompt": workflow}
                )

                if response.status_code != 200:
                    return {
                        "success": False,
                        "error": f"ComfyUI error: {response.text}"
                    }

                result = response.json()
                prompt_id = result.get("prompt_id")

                # Poll for completion
                if prompt_id:
                    return await self._wait_for_completion(prompt_id, "local")
                else:
                    return {"success": False, "error": "No prompt_id returned"}

        except Exception as e:
            return {"success": False, "error": str(e)}

    async def _execute_cloud(self, workflow: Dict[str, Any]) -> Dict[str, Any]:
        """Execute workflow on cloud ComfyUI instance"""

        if not self.cloud_url or not self.cloud_token:
            return {
                "success": False,
                "error": "Cloud ComfyUI not configured"
            }

        try:
            headers = {"Authorization": f"Bearer {self.cloud_token}"}

            async with httpx.AsyncClient(timeout=300.0) as client:
                response = await client.post(
                    f"{self.cloud_url}/prompt",
                    json={"prompt": workflow},
                    headers=headers
                )

                if response.status_code != 200:
                    return {
                        "success": False,
                        "error": f"Cloud API error: {response.text}"
                    }

                result = response.json()
                prompt_id = result.get("prompt_id")

                if prompt_id:
                    return await self._wait_for_completion(prompt_id, "cloud")
                else:
                    return {"success": False, "error": "No prompt_id returned"}

        except Exception as e:
            return {"success": False, "error": str(e)}

    async def _wait_for_completion(
        self,
        prompt_id: str,
        location: str,
        timeout: int = 300
    ) -> Dict[str, Any]:
        """Wait for workflow to complete"""

        base_url = self.local_url if location == "local" else self.cloud_url
        headers = {"Authorization": f"Bearer {self.cloud_token}"} if location == "cloud" else {}

        start_time = asyncio.get_event_loop().time()

        async with httpx.AsyncClient(timeout=10.0) as client:
            while asyncio.get_event_loop().time() - start_time < timeout:
                try:
                    response = await client.get(
                        f"{base_url}/history/{prompt_id}",
                        headers=headers
                    )

                    if response.status_code == 200:
                        history = response.json()
                        if prompt_id in history:
                            # Workflow completed
                            return {
                                "success": True,
                                "prompt_id": prompt_id,
                                "output": history[prompt_id]
                            }

                    await asyncio.sleep(1)  # Check every second

                except Exception as e:
                    print(f"Error checking status: {e}")
                    await asyncio.sleep(2)

        return {"success": False, "error": "Workflow execution timeout"}
