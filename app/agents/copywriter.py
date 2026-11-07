from anthropic import Anthropic
from app.config import settings

class CopywriterAgent:
    """Enriches user prompt into detailed image generation prompt"""

    def __init__(self):
        self.client = Anthropic(api_key=settings.ANTHROPIC_API_KEY)

    async def process(self, user_prompt: str) -> str:
        """Transform simple user request into detailed Z-Image Turbo prompt"""

        system_prompt = """You are an expert copywriter specialized in creating detailed, evocative prompts for AI image generation with Z-Image Turbo.

Your task is to transform a simple user idea into a comprehensive, detailed prompt that will generate photorealistic images of people in everyday situations.

Guidelines:
- Focus on natural, candid moments
- Include specific details about:
  * Physical appearance (age range, body type, clothing, expression)
  * Lighting (natural, golden hour, overcast, etc.)
  * Camera/photography style (phone camera, professional, cinematic)
  * Location and environment details
  * Textures and materials
- Write in natural language, not as a tag list
- Make it vivid and descriptive but concise
- Keep it realistic - avoid fantasy or unrealistic scenarios

Output ONLY the enriched prompt, nothing else."""

        message = self.client.messages.create(
            model="claude-opus-5-5",
            max_tokens=300,
            system=system_prompt,
            messages=[
                {
                    "role": "user",
                    "content": f"Transform this into a detailed image prompt: {user_prompt}"
                }
            ]
        )

        return message.content[0].text
