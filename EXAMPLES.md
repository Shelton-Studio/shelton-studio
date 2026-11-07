# Shelton Usage Examples

## Web Dashboard Examples

### Example 1: Lifestyle Photography

**Prompt:**
```
A woman in her 30s cooking fresh pasta in a modern, minimalist kitchen. 
Warm afternoon sunlight streams through a large window, casting soft 
shadows on natural wood countertops. She's wearing casual linen clothing, 
with a gentle, focused expression. Shot with a full-frame camera, 
shallow depth of field, professional food photography style.
```

**Expected Output:**
- Style: Lifestyle Photography
- Platform: Instagram
- Aspect Ratio: 1:1 (square)
- QA Score: 85+

### Example 2: Product Photography

**Prompt:**
```
Close-up of a luxury coffee mug on a white marble surface. Golden hour 
lighting with warm, soft shadows. The mug contains steaming coffee, with 
visible steam wisps. Minimalist composition, shallow depth of field, 
professional product photography, shot on 50mm lens.
```

**Expected Output:**
- Style: Product Photography
- Platform: Pinterest
- Aspect Ratio: 4:5 (vertical)
- QA Score: 80+

### Example 3: Candid Lifestyle

**Prompt:**
```
A man in his 40s taking a morning walk in a city park. Golden sunlight, 
autumn leaves, natural candid expression. He's wearing casual athletic wear. 
Shot with smartphone camera, natural lighting, documentary photography style.
```

**Expected Output:**
- Style: Documentary Photography
- Platform: TikTok
- Aspect Ratio: 9:16 (vertical)
- QA Score: 75+

## CLI Examples

### Generate and Wait

```bash
python cli/shelton_cli.py "Young professional woman at laptop in cozy home office"
```

### Generate Without Waiting

```bash
python cli/shelton_cli.py "Person meditating in yoga studio" --no-wait
# Returns job ID for later checking
```

### Check Specific Job

```bash
python cli/shelton_cli.py status a1b2c3d4-e5f6-7890-abcd-ef1234567890
```

### View and Approve Content

```bash
# View details
python cli/shelton_cli.py content xyz123

# Approve it
python cli/shelton_cli.py approve xyz123

# Or reject it
python cli/shelton_cli.py approve xyz123 --reject
```

### Check Recent History

```bash
python cli/shelton_cli.py history --limit 20
```

## API Usage Examples

### Using cURL

```bash
# Generate content
curl -X POST http://localhost:8000/api/generate \
  -H "Content-Type: application/json" \
  -d '{
    "prompt": "Person jogging in park at sunrise",
    "comfyui_location": "local"
  }'

# Check status
curl http://localhost:8000/api/status/job-id-here

# Approve content
curl -X POST http://localhost:8000/api/approve \
  -H "Content-Type: application/json" \
  -d '{
    "content_id": "content-id-here",
    "approved": true
  }'
```

### Using Python

```python
import httpx
import json
import time

BASE_URL = "http://localhost:8000/api"

# Generate
client = httpx.Client()
response = client.post(
    f"{BASE_URL}/generate",
    json={
        "prompt": "Family having dinner at rustic table",
        "comfyui_location": "local"
    }
)
job = response.json()
job_id = job["job_id"]

# Poll for completion
while True:
    status = client.get(f"{BASE_URL}/status/{job_id}").json()
    if status["status"] == "completed":
        content_id = status["content_id"]
        break
    time.sleep(2)

# Get content
content = client.get(f"{BASE_URL}/content/{content_id}").json()
print(f"Image: {content['image_path']}")
print(f"QA Passed: {content['qa_passed']}")

# Approve
client.post(
    f"{BASE_URL}/approve",
    json={"content_id": content_id, "approved": True}
)
```

## Advanced Workflows

### Batch Generation

```bash
#!/bin/bash

prompts=(
  "Woman cooking breakfast in bright kitchen"
  "Man working at desk in home office"
  "Family walking in park on sunny day"
  "Person doing yoga in calm studio"
)

for prompt in "${prompts[@]}"; do
  echo "Generating: $prompt"
  python cli/shelton_cli.py "$prompt"
  sleep 2
done
```

### Approval Script

```python
import httpx
from app.database.db import SessionLocal
from app.database.models import Content

db = SessionLocal()

# Get all unapproved content
unapproved = db.query(Content).filter(
    Content.human_approved == False,
    Content.qa_passed == True
).all()

client = httpx.Client()

for content in unapproved:
    print(f"Reviewing: {content.original_prompt[:50]}...")
    
    # You would review the image here
    # For demo, approve all
    
    client.post(
        "http://localhost:8000/api/approve",
        json={
            "content_id": content.id,
            "approved": True
        }
    )
    print("Approved ✓")
```

## Common Prompt Patterns

### Lifestyle
```
[Person description] [Action] in [Location] with [Lighting] 
shot with [Camera], [Photography style]
```

### Product
```
[Product] on [Surface] with [Lighting] [Details about product] 
shot on [Lens], [Photography style]
```

### Environmental
```
[Location description] [Time of day] lighting, [Mood/feeling]
shot with [Camera], [Photography style]
```

### Candid
```
[Person] [Natural action] in [Location] [Natural lighting]
shot with [Camera] natural style
```

## Monitoring Performance

### Database Queries

```python
from app.database.db import SessionLocal
from app.database.models import Job, Content

db = SessionLocal()

# Check processing times
jobs = db.query(Job).all()
for job in jobs:
    duration = (job.updated_at - job.created_at).total_seconds()
    print(f"{job.id}: {duration}s")

# Check approval rate
total = db.query(Content).count()
approved = db.query(Content).filter(Content.human_approved == True).count()
print(f"Approval Rate: {approved}/{total}")
```

### API Health Check

```bash
# Quick health check
curl http://localhost:8000/api/health

# Sample response
{"status": "healthy", "app": "Shelton - Content Generation Platform"}
```

## Tips for Best Results

1. **Be Specific**: More detail in prompts = better results
2. **Describe Lighting**: Important for realism
3. **Include Camera Style**: Affects the look significantly
4. **Use Natural Phrases**: Z-Image understands conversational language
5. **Mention Textures**: "natural skin with pores", "soft fabric"
6. **Avoid Tags**: Don't use "masterpiece, best quality" lists
7. **Consider Platform**: Different platforms need different aspect ratios

## Troubleshooting Common Issues

### Generation Too Slow
- Check if local ComfyUI is running
- Try switching to cloud if available
- Check system resources (GPU VRAM)

### Low Quality Output
- More details in prompt
- Ensure Z-Image Turbo models are correctly installed
- Try different lighting descriptions

### Approval Hanging
- Check if human reviewer interface is loaded
- Verify database connection
- Check browser console for errors

### Job Timeout
- Increase timeout in polling logic
- Check ComfyUI is responsive
- Verify network connection
