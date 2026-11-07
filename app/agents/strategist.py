from anthropic import Anthropic
from app.config import settings
import json

class StrategistAgent:
    """Defines content strategy: style, platform, tone, aspect ratio"""

    def __init__(self):
        self.client = Anthropic(api_key=settings.ANTHROPIC_API_KEY)

    async def process(self, enriched_prompt: str) -> dict:
        """Analyze prompt and define content strategy"""

        system_prompt = """You are a content strategist expert in social media and photography.

Analyze the image prompt and define the optimal content strategy including:
1. Visual style (e.g., lifestyle photography, documentary, editorial, street photography)
2. Target platform (Instagram, TikTok, LinkedIn, Pinterest, etc.)
3. Tone/mood (aspirational, authentic, professional, casual, etc.)
4. Aspect ratio (1:1, 16:9, 4:5, 9:16, etc.) - prefer square (1:1) or vertical (4:5)
5. Recommended adjustments for quality (if any)

Output ONLY valid JSON with these exact keys: style, platform, tone, aspect_ratio, adjustments

Example:
{"style": "lifestyle photography", "platform": "Instagram", "tone": "authentic & aspirational", "aspect_ratio": "1:1", "adjustments": []}"""

        message = self.client.messages.create(
            model="claude-opus-5-5",
            max_tokens=200,
            system=system_prompt,
            messages=[
                {
                    "role": "user",
                    "content": f"Analyze this prompt and define strategy: {enriched_prompt}"
                }
            ]
        )

        response_text = message.content[0].text

        try:
            strategy = json.loads(response_text)
        except json.JSONDecodeError:
            strategy = {
                "style": "lifestyle photography",
                "platform": "Instagram",
                "tone": "authentic",
                "aspect_ratio": "1:1",
                "adjustments": []
            }

        return strategy
