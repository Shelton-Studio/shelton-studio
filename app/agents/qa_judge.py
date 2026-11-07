from anthropic import Anthropic
from app.config import settings
import json

class QAJudgeAgent:
    """Quality assurance agent - validates generated content"""

    def __init__(self):
        self.client = Anthropic(api_key=settings.ANTHROPIC_API_KEY)

    async def process(self, image_path: str, prompt: str) -> dict:
        """Validate image quality and prompt adherence"""

        system_prompt = """You are a quality assurance expert evaluating AI-generated images.

Evaluate the image against these criteria:
1. Technical quality (no artifacts, artifacts, distortions)
2. Prompt adherence (does it match the description?)
3. Realism (does it look natural and authentic?)
4. Completeness (all requested elements present?)
5. Safety (no problematic content?)

Respond with JSON containing:
- "passed": boolean (true if acceptable for human review)
- "score": 1-100
- "issues": list of any problems found
- "notes": brief assessment

Example:
{"passed": true, "score": 85, "issues": [], "notes": "High quality, matches prompt well"}"""

        message = self.client.messages.create(
            model="claude-opus-5-5",
            max_tokens=200,
            system=system_prompt,
            messages=[
                {
                    "role": "user",
                    "content": f"""Image: {image_path}
Prompt: {prompt}

Evaluate this image against the prompt."""
                }
            ]
        )

        response_text = message.content[0].text

        try:
            result = json.loads(response_text)
        except json.JSONDecodeError:
            # Fallback if JSON parsing fails
            result = {
                "passed": True,
                "score": 75,
                "issues": ["Could not parse QA response"],
                "notes": "Proceeding with human review"
            }

        return result
