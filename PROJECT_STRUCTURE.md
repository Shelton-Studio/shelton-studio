# Project Structure - Shelton

Complete directory structure and file descriptions.

```
alligator AI/
├── README.md                      # Main documentation
├── PROJECT_STRUCTURE.md          # This file
├── SETUP_COMFYUI.md              # ComfyUI setup guide
├── EXAMPLES.md                   # Usage examples
├── requirements.txt              # Python dependencies
├── .env.example                  # Environment variables template
├── start.sh                      # Quick start script
├── .claude/
│   └── plans/
│       └── mellow-swimming-bee.md # Implementation plan
│
├── app/                          # Main application
│   ├── __init__.py
│   ├── main.py                   # FastAPI app & routes
│   ├── config.py                 # Configuration settings
│   │
│   ├── api/
│   │   ├── __init__.py
│   │   └── routes.py             # API endpoints (in main.py)
│   │
│   ├── agents/                   # AI agents
│   │   ├── __init__.py
│   │   ├── coordinator.py        # Shelton - main orchestrator
│   │   ├── copywriter.py         # Prompt enrichment
│   │   ├── strategist.py         # Strategy definition
│   │   ├── generator.py          # Image generation
│   │   ├── editor.py             # Image editing
│   │   └── qa_judge.py           # Quality assurance
│   │
│   ├── comfyui/                  # ComfyUI integration
│   │   ├── __init__.py
│   │   ├── client.py             # ComfyUI API client
│   │   ├── downloader.py         # Model downloader (stub)
│   │   └── workflows/            # ComfyUI workflow files
│   │       ├── generate.json     # Z-Image workflow
│   │       └── edit.json         # Qwen-Edit workflow
│   │
│   ├── database/                 # Data layer
│   │   ├── __init__.py
│   │   ├── models.py             # SQLAlchemy ORM models
│   │   └── db.py                 # Database connection
│   │
│   └── utils/                    # Utilities
│       ├── logger.py             # Logging (stub)
│       └── config.py             # Config utils (stub)
│
├── web/                          # Frontend dashboard
│   ├── index.html                # Main HTML page
│   ├── style.css                 # Styling
│   └── app.js                    # JavaScript logic
│
├── cli/                          # Command-line interface
│   ├── __init__.py
│   └── shelton_cli.py            # CLI commands
│
├── generated_images/             # Output directory (auto-created)
│   └── *.png                     # Generated images
│
└── shelton.db                    # SQLite database (auto-created)
```

## Key Files Explained

### Core Application Files

**app/main.py**
- FastAPI application setup
- Route definitions:
  - `POST /api/generate` - Start generation job
  - `GET /api/status/{job_id}` - Check job status
  - `GET /api/content/{content_id}` - Get content details
  - `POST /api/approve` - Approve/reject content
  - `GET /api/history` - Get content history
  - `GET /` - Serve web dashboard

**app/config.py**
- Environment variable loading
- Settings object with all configuration

### Agent Files

**app/agents/coordinator.py (Shelton)**
- Main orchestration logic
- Sequential pipeline execution
- Error handling and logging
- Database updates

**app/agents/copywriter.py**
- Claude API calls
- Prompt enrichment
- Focuses on details, lighting, camera, textures

**app/agents/strategist.py**
- Strategy definition
- Returns JSON with: style, platform, tone, aspect_ratio

**app/agents/generator.py**
- Z-Image Turbo integration
- Workflow preparation
- ComfyUI client interaction

**app/agents/editor.py**
- Qwen-Image-Edit integration
- Image refinement logic
- Conditional editing based on strategy

**app/agents/qa_judge.py**
- Quality validation
- Claude evaluation
- Returns: passed, score, issues, notes

### Database Files

**app/database/models.py**
```
Tables:
- Job: Processing job tracking
- Content: Generated content metadata
- ProcessingLog: Agent execution history
```

**app/database/db.py**
- SQLAlchemy engine setup
- Session management
- Database initialization

### Frontend Files

**web/index.html**
- Form for content generation
- Status display
- Results section with image and prompts
- History grid
- Approval buttons

**web/style.css**
- Dark theme with purple gradient
- Responsive design
- Grid layouts for history

**web/app.js**
- API communication
- Job polling
- Form handling
- History display
- Approval logic

### CLI Files

**cli/shelton_cli.py**
- Typer CLI framework
- Commands:
  - `generate` - Create content
  - `status` - Check job
  - `content` - View details
  - `history` - Show history
  - `approve` - Approve/reject
  - `health` - API health check

## Data Flow

### Request → Response

```
1. User Input (Web/CLI)
2. API receives request
3. Create Job record in DB
4. Start async processing
5. Coordinator orchestrates agents sequentially:
   - Copywriter enriches prompt
   - Strategist defines strategy
   - Generator creates image
   - Editor refines image
   - QA validates
6. Save Content record in DB
7. Update Job status
8. User polls for status
9. Display results in UI
10. Human approves/rejects
11. Update approval status in DB
```

## Database Schema

### Jobs Table
```
id (UUID) - Job ID
user_prompt (Text) - Original user input
comfyui_location (String) - local or cloud
status (String) - pending, processing, completed, failed
error_message (Text)
created_at (DateTime)
updated_at (DateTime)
```

### Content Table
```
id (UUID) - Content ID
job_id (String) - Reference to Job
original_prompt (Text)
enriched_prompt (Text)
image_path (String)
metadata (JSON) - Strategy info
qa_passed (Boolean)
qa_notes (Text)
human_approved (Boolean)
approved_by (String)
approved_at (DateTime)
created_at (DateTime)
```

### ProcessingLogs Table
```
id (Integer)
job_id (String)
agent (String) - Agent name
step (String)
input_data (JSON)
output_data (JSON)
status (String)
error (Text)
duration_seconds (Integer)
created_at (DateTime)
```

## Environment Variables

See `.env.example` for all variables:
- `ANTHROPIC_API_KEY` - Claude API key
- `COMFYUI_LOCAL_URL` - Local ComfyUI endpoint
- `COMFYUI_CLOUD_URL` - Cloud ComfyUI endpoint (optional)
- `COMFYUI_CLOUD_TOKEN` - Cloud auth token
- `DATABASE_URL` - Database connection string
- `APP_PORT` - FastAPI port
- `DEBUG` - Debug mode

## Running the Application

### Development

```bash
# 1. Install dependencies
pip install -r requirements.txt

# 2. Set up environment
cp .env.example .env
# Edit .env with your settings

# 3. Run server
python app/main.py

# 4. Open dashboard
# http://localhost:8000

# 5. Use CLI
python cli/shelton_cli.py "Your prompt"
```

### Production

```bash
# Use uvicorn with production settings
uvicorn app.main:app --host 0.0.0.0 --port 8000 --workers 4

# Or use supervisor/systemd for process management
```

## Testing the Setup

```bash
# Health check
curl http://localhost:8000/api/health

# Generate content
curl -X POST http://localhost:8000/api/generate \
  -H "Content-Type: application/json" \
  -d '{"prompt": "Test prompt", "comfyui_location": "local"}'

# Check job status
curl http://localhost:8000/api/status/{job_id}
```

## Future Enhancements

- [ ] Additional agents (hashtag generator, caption writer)
- [ ] Social media integration (post to Instagram, TikTok)
- [ ] Advanced image curation (filter by aesthetic)
- [ ] Batch processing
- [ ] Analytics dashboard
- [ ] LoRA training integration
- [ ] Multi-checkpoint support
- [ ] WebSocket real-time updates
- [ ] Redis caching
- [ ] Rate limiting

## Performance Metrics

- Generation time: 30-60s (depends on ComfyUI location)
- API response time: <100ms
- Database query time: <10ms
- Concurrent jobs: Limited by GPU VRAM

## Security Considerations

- API keys stored in environment variables
- Input validation on all endpoints
- SQL injection protection (SQLAlchemy ORM)
- CORS not enabled (single domain deployment)
- Authentication can be added per deployment needs

## Monitoring & Logging

- Processing logs in database
- Console output for debug mode
- Job status tracking
- Error messages stored in Job records

---

**Created with Claude Code**
