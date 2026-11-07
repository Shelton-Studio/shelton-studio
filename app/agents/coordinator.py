import asyncio
from typing import Dict, Any
from sqlalchemy.orm import Session
from app.agents.copywriter import CopywriterAgent
from app.agents.strategist import StrategistAgent
from app.agents.generator import GeneratorAgent
from app.agents.editor import EditorAgent
from app.agents.qa_judge import QAJudgeAgent
from app.database.models import Job, Content, ProcessingLog
import time

class SheltonCoordinator:
    """Main orchestrator agent - Shelton"""

    def __init__(self, db: Session):
        self.db = db
        self.copywriter = CopywriterAgent()
        self.strategist = StrategistAgent()
        self.generator = GeneratorAgent()
        self.editor = EditorAgent()
        self.qa_judge = QAJudgeAgent()

    async def process_request(
        self,
        job_id: str,
        user_prompt: str,
        comfyui_location: str = "local"
    ) -> Dict[str, Any]:
        """Main processing pipeline - sequential execution"""

        job = self.db.query(Job).filter(Job.id == job_id).first()

        try:
            # Step 1: Copywriter enriches prompt
            print(f"[{job_id}] Starting Copywriter agent...")
            start_time = time.time()
            enriched_prompt = await self.copywriter.process(user_prompt)
            self._log_step(job_id, "copywriter", user_prompt, enriched_prompt, time.time() - start_time)

            # Step 2: Strategist defines strategy
            print(f"[{job_id}] Starting Strategist agent...")
            start_time = time.time()
            strategy = await self.strategist.process(enriched_prompt)
            self._log_step(job_id, "strategist", enriched_prompt, strategy, time.time() - start_time)

            # Step 3: Generator creates image
            print(f"[{job_id}] Starting Generator agent...")
            start_time = time.time()
            image_path = await self.generator.process(enriched_prompt, comfyui_location)
            self._log_step(job_id, "generator", {"prompt": enriched_prompt, "location": comfyui_location},
                          {"image_path": image_path}, time.time() - start_time)

            # Step 4: Editor refines image
            print(f"[{job_id}] Starting Editor agent...")
            start_time = time.time()
            edited_image_path = await self.editor.process(image_path, strategy)
            self._log_step(job_id, "editor", {"original": image_path, "strategy": strategy},
                          {"edited_path": edited_image_path}, time.time() - start_time)

            # Step 5: QA validates
            print(f"[{job_id}] Starting QA Judge agent...")
            start_time = time.time()
            qa_result = await self.qa_judge.process(edited_image_path, enriched_prompt)
            self._log_step(job_id, "qa_judge", {"image": edited_image_path, "prompt": enriched_prompt},
                          qa_result, time.time() - start_time)

            # Save content to DB
            content = Content(
                job_id=job_id,
                original_prompt=user_prompt,
                enriched_prompt=enriched_prompt,
                image_path=edited_image_path,
                metadata=strategy,
                qa_passed=qa_result.get("passed", False),
                qa_notes=qa_result.get("notes", "")
            )
            self.db.add(content)

            # Update job status
            job.status = "completed"
            self.db.commit()

            return {
                "job_id": job_id,
                "status": "completed",
                "image_path": edited_image_path,
                "original_prompt": user_prompt,
                "enriched_prompt": enriched_prompt,
                "metadata": strategy,
                "qa_passed": qa_result.get("passed", False),
                "qa_notes": qa_result.get("notes", ""),
                "ready_for_human_review": True
            }

        except Exception as e:
            job.status = "failed"
            job.error_message = str(e)
            self.db.commit()
            print(f"[{job_id}] Error: {e}")
            return {
                "job_id": job_id,
                "status": "failed",
                "error": str(e)
            }

    def _log_step(self, job_id: str, agent: str, input_data: Any, output_data: Any, duration: float):
        """Log processing step"""
        log = ProcessingLog(
            job_id=job_id,
            agent=agent,
            step=f"{agent}_execution",
            input_data=input_data if isinstance(input_data, dict) else {"data": str(input_data)},
            output_data=output_data if isinstance(output_data, dict) else {"data": str(output_data)},
            duration_seconds=int(duration)
        )
        self.db.add(log)
        self.db.commit()
