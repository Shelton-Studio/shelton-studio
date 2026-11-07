from sqlalchemy import Column, String, Text, DateTime, Boolean, JSON, Integer
from sqlalchemy.ext.declarative import declarative_base
from datetime import datetime
import uuid

Base = declarative_base()

class Job(Base):
    __tablename__ = "jobs"

    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    user_prompt = Column(Text, nullable=False)
    comfyui_location = Column(String, default="local")  # "local" o "cloud"
    status = Column(String, default="pending")  # pending, processing, completed, failed
    error_message = Column(Text, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

class Content(Base):
    __tablename__ = "content"

    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    job_id = Column(String, nullable=False)
    original_prompt = Column(Text, nullable=False)
    enriched_prompt = Column(Text, nullable=False)
    image_path = Column(String, nullable=True)
    metadata = Column(JSON, nullable=True)
    qa_passed = Column(Boolean, default=False)
    qa_notes = Column(Text, nullable=True)
    human_approved = Column(Boolean, default=False)
    approved_by = Column(String, nullable=True)
    approved_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)

class ProcessingLog(Base):
    __tablename__ = "processing_logs"

    id = Column(Integer, primary_key=True)
    job_id = Column(String, nullable=False)
    agent = Column(String, nullable=False)  # copywriter, strategist, generator, editor, qa
    step = Column(String, nullable=False)
    input_data = Column(JSON, nullable=True)
    output_data = Column(JSON, nullable=True)
    status = Column(String, default="completed")  # completed, failed
    error = Column(Text, nullable=True)
    duration_seconds = Column(Integer, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)
