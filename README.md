# 🤖 Shelton - Multi-Agent Content Generation Platform

A complete ecosystem for generating high-quality social media content using AI. Shelton coordinates multiple specialized agents to transform your ideas into polished, ready-to-publish images.

## ✨ Features

- **Multi-Agent Architecture**: Copywriter, Strategist, Generator, Editor, QA Judge
- **Sequential Processing**: Each agent builds on the previous one's output
- **Z-Image Turbo**: Fast, realistic image generation
- **Qwen-Image-Edit**: Intelligent image refinement
- **Dual Interface**: Web dashboard + CLI
- **ComfyUI Integration**: Works with both local and cloud instances
- **Human Review**: Final approval before publishing
- **Content History**: Track and manage all generated content

## 🚀 Quick Start

### 1. Install Dependencies

```bash
pip install -r requirements.txt
```

### 2. Configure Environment

```bash
cp .env.example .env
# Edit .env with your settings:
# - ANTHROPIC_API_KEY: Your Claude API key
# - COMFYUI_LOCAL_URL: Your local ComfyUI instance
# - COMFYUI_CLOUD_URL: Optional cloud instance
```

### 3. Ensure ComfyUI is Running

Make sure ComfyUI is running on your specified local URL (default: http://localhost:8188)

### 4. Start the Server

```bash
python app/main.py
```

The web dashboard will be available at: `http://localhost:8000`

## 📝 Usage

### Web Dashboard

1. Open `http://localhost:8000` in your browser
2. Enter your content description
3. Choose ComfyUI location (local or cloud)
4. Click "Generate Content"
5. Wait for processing to complete
6. Review results and approve or reject

### Command Line Interface

```bash
# Generate content
python cli/shelton_cli.py "Woman cooking pasta in modern kitchen with natural light"

# Check job status
python cli/shelton_cli.py status <job_id>

# View content details
python cli/shelton_cli.py content <content_id>

# View history
python cli/shelton_cli.py history --limit 10

# Approve content
python cli/shelton_cli.py approve <content_id>

# Reject content
python cli/shelton_cli.py approve <content_id> --reject

# Check API health
python cli/shelton_cli.py health
```

## 🏗️ Architecture

### Processing Pipeline

```
User Input
    ↓
[Copywriter Agent] → Enrich prompt with details
    ↓
[Strategist Agent] → Define style, platform, tone
    ↓
[Generator Agent] → Create image with Z-Image Turbo
    ↓
[Editor Agent] → Refine with Qwen-Image-Edit
    ↓
[QA Judge Agent] → Validate quality
    ↓
[Human Reviewer] → Final approval
    ↓
Published Content
```

### Database

SQLite (development) / PostgreSQL (production)

Tables:
- `jobs` - Processing jobs
- `content` - Generated content
- `processing_logs` - Agent execution history

## 🔑 API Endpoints

### Generate Content
```
POST /api/generate
{
  "prompt": "Woman cooking in kitchen",
  "comfyui_location": "local"  // or "cloud"
}
```

### Check Status
```
GET /api/status/{job_id}
```

### Get Content
```
GET /api/content/{content_id}
```

### Approve Content
```
POST /api/approve
{
  "content_id": "...",
  "approved": true,
  "notes": "..."
}
```

### View History
```
GET /api/history?skip=0&limit=20
```

## 🎨 Agents Explained

### Copywriter Agent
Transforms simple user requests into detailed, evocative prompts for Z-Image Turbo. Focuses on:
- Physical appearance details
- Lighting and atmosphere
- Camera/photography style
- Textures and materials

### Strategist Agent
Analyzes the enriched prompt and defines:
- Visual style (lifestyle, editorial, documentary, etc.)
- Target platform (Instagram, TikTok, LinkedIn, etc.)
- Tone and mood
- Optimal aspect ratio
- Any needed adjustments

### Generator Agent
Creates images using Z-Image Turbo via ComfyUI:
- Configured for 8 steps (optimized for quality/speed)
- Supports custom prompts
- Works with both local and cloud ComfyUI

### Editor Agent
Refines images using Qwen-Image-Edit:
- Adjusts lighting and shadows
- Applies strategic improvements
- Maintains image consistency

### QA Judge Agent
Validates quality:
- Technical assessment (artifacts, distortions)
- Prompt adherence checking
- Realism evaluation
- Safety validation

## 🔧 Configuration

### Models
The system uses:
- **Z-Image Turbo** (6B parameters): Fast, realistic image generation
- **Qwen-Image-Edit** (20B parameters): Intelligent image refinement
- **Claude API**: Agent reasoning and decision-making

### ComfyUI Setup

Download and install ComfyUI, then:

1. **Install Z-Image Turbo models:**
   - Download `z_image_turbo_bf16.safetensors` to `models/diffusion_models/`
   - Download `qwen_3_4b` to `models/text_encoders/`

2. **Install Qwen-Image-Edit models:**
   - Download `qwen_image_edit_2511.safetensors` to `models/diffusion_models/`
   - Download `qwen_2.5_vl_7b_fp8_scaled` to `models/text_encoders/`

## 📊 Performance

- **Average generation time**: 30-60 seconds (depending on ComfyUI location)
- **Database queries**: Optimized with indexed job_id and created_at
- **Concurrent jobs**: Limited by available VRAM

## 🐛 Troubleshooting

### ComfyUI Connection Issues
- Verify ComfyUI is running on the configured URL
- Check firewall settings
- Try local first, then cloud if available

### Model Missing Errors
- Ensure all required models are downloaded to ComfyUI
- Check model file names match exactly
- Restart ComfyUI after adding new models

### Generation Timeout
- Increase timeout in ComfyUI settings
- Check system resources (VRAM, CPU)
- Try cloud ComfyUI if available

## 📄 License

This project is open source and available under the MIT License.

## 🤝 Contributing

Contributions are welcome! Areas for improvement:
- Additional specialized agents
- Advanced image curation workflows
- Social media posting integration
- Analytics and performance metrics

## 📧 Support

For issues or questions, please create an issue in the repository.

---

**Powered by Claude AI, ComfyUI, and open-source AI models**
