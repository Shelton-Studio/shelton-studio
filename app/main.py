from fastapi import FastAPI, HTTPException, Depends
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from sqlalchemy.orm import Session
from pydantic import BaseModel
import uuid
import asyncio
from datetime import datetime

from app.config import settings
from app.database.db import init_db, get_db
from app.database.models import Job, Content
from app.agents.coordinator import SheltonCoordinator

# Initialize
app = FastAPI(title=settings.APP_TITLE)
init_db()

# Mount static files for web frontend
app.mount("/static", StaticFiles(directory="web"), name="static")

# Pydantic models
class GenerateRequest(BaseModel):
    prompt: str
    comfyui_location: str = "local"

class ApprovalRequest(BaseModel):
    content_id: str
    approved: bool
    notes: str = ""

class JobStatus(BaseModel):
    job_id: str
    status: str
    content_id: str | None = None
    error: str | None = None

# Routes
@app.get("/")
async def root():
    """Serve web dashboard"""
    return FileResponse("web/index.html")

@app.post("/api/generate")
async def generate(request: GenerateRequest, db: Session = Depends(get_db)):
    """Start content generation job"""

    job_id = str(uuid.uuid4())

    # Create job record
    job = Job(
        id=job_id,
        user_prompt=request.prompt,
        comfyui_location=request.comfyui_location,
        status="processing"
    )
    db.add(job)
    db.commit()

    # Start async processing
    asyncio.create_task(
        _process_job(job_id, request.prompt, request.comfyui_location, db)
    )

    return {"job_id": job_id, "status": "processing"}

@app.get("/api/status/{job_id}")
async def get_status(job_id: str, db: Session = Depends(get_db)):
    """Get job status"""

    job = db.query(Job).filter(Job.id == job_id).first()
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")

    content = db.query(Content).filter(Content.job_id == job_id).first()

    return {
        "job_id": job_id,
        "status": job.status,
        "content_id": content.id if content else None,
        "error": job.error_message,
        "image_path": content.image_path if content else None,
        "qa_passed": content.qa_passed if content else False
    }

@app.post("/api/approve")
async def approve_content(request: ApprovalRequest, db: Session = Depends(get_db)):
    """Human approval of generated content"""

    content = db.query(Content).filter(Content.id == request.content_id).first()
    if not content:
        raise HTTPException(status_code=404, detail="Content not found")

    content.human_approved = request.approved
    if request.approved:
        content.approved_at = datetime.utcnow()
        content.approved_by = "human_reviewer"

    db.commit()

    return {
        "content_id": request.content_id,
        "approved": request.approved,
        "message": "Content approved" if request.approved else "Content rejected"
    }

@app.get("/api/content/{content_id}")
async def get_content(content_id: str, db: Session = Depends(get_db)):
    """Get content details"""

    content = db.query(Content).filter(Content.id == content_id).first()
    if not content:
        raise HTTPException(status_code=404, detail="Content not found")

    return {
        "id": content.id,
        "job_id": content.job_id,
        "original_prompt": content.original_prompt,
        "enriched_prompt": content.enriched_prompt,
        "image_path": content.image_path,
        "metadata": content.metadata,
        "qa_passed": content.qa_passed,
        "qa_notes": content.qa_notes,
        "human_approved": content.human_approved,
        "created_at": content.created_at
    }

@app.get("/api/history")
async def get_history(skip: int = 0, limit: int = 20, db: Session = Depends(get_db)):
    """Get content history"""

    contents = db.query(Content)\
        .order_by(Content.created_at.desc())\
        .offset(skip)\
        .limit(limit)\
        .all()

    return [
        {
            "id": c.id,
            "job_id": c.job_id,
            "original_prompt": c.original_prompt,
            "image_path": c.image_path,
            "qa_passed": c.qa_passed,
            "human_approved": c.human_approved,
            "created_at": c.created_at
        }
        for c in contents
    ]

@app.get("/api/health")
async def health():
    """Health check"""
    return {"status": "healthy", "app": settings.APP_TITLE}

# Internal processing function
async def _process_job(job_id: str, prompt: str, location: str, db: Session):
    """Internal function to process job asynchronously"""
    coordinator = SheltonCoordinator(db)
    await coordinator.process_request(job_id, prompt, location)

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=settings.APP_PORT)
