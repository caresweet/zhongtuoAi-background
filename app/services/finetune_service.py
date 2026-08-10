"""Fine-tuning service for report generation quality improvement.

Uses DeepSeek/OpenAI-compatible fine-tuning API via 胜算云 router.
Supports: dataset building, fine-tune job submission, model switching.
"""

import json, logging, os
from pathlib import Path
from typing import Dict, Optional, List
import httpx

logger = logging.getLogger(__name__)


class FinetuneService:
    """Manage fine-tuning of the report generation model."""

    def __init__(self):
        from app.config import settings
        self.api_key = settings.ANTHROPIC_API_KEY
        self.base_url = settings.LLM_BASE_URL.rstrip("/")
        self.model = settings.LLM_MODEL or "deepseek-chat"
        self._finetuned_model = None

    @property
    def is_available(self) -> bool:
        return bool(self.api_key)

    @property
    def finetuned_model(self) -> Optional[str]:
        """Return fine-tuned model name if available, else None."""
        if self._finetuned_model:
            return self._finetuned_model
        # Check config/env
        return os.environ.get("FINETUNED_MODEL") or None

    def use_finetuned(self, model_name: str):
        """Switch to use the fine-tuned model."""
        self._finetuned_model = model_name
        os.environ["FINETUNED_MODEL"] = model_name
        logger.info(f"Switched to fine-tuned model: {model_name}")

    def use_default(self):
        """Switch back to default model."""
        self._finetuned_model = None
        os.environ.pop("FINETUNED_MODEL", None)

    async def upload_dataset(self, file_path: str) -> Optional[str]:
        """Upload a JSONL dataset file for fine-tuning.

        Returns file_id if successful.
        """
        if not self.is_available:
            logger.error("API key not configured for fine-tuning")
            return None

        file_path = Path(file_path)
        if not file_path.exists():
            logger.error(f"Dataset file not found: {file_path}")
            return None

        try:
            async with httpx.AsyncClient(timeout=httpx.Timeout(60.0)) as client:
                with open(file_path, 'rb') as f:
                    response = await client.post(
                        f"{self.base_url}/files",
                        headers={"Authorization": f"Bearer {self.api_key}"},
                        files={"file": (file_path.name, f, "application/jsonl"),
                               "purpose": (None, "fine-tune")},
                    )
                if response.status_code == 200:
                    data = response.json()
                    file_id = data.get("id", "")
                    logger.info(f"Dataset uploaded: {file_id}")
                    return file_id
                else:
                    logger.error(f"Upload failed: {response.status_code} {response.text[:200]}")
                    return None
        except Exception as e:
            logger.error(f"Upload error: {e}")
            return None

    async def create_finetune_job(
        self, file_id: str, suffix: str = "stability-report",
        epochs: int = 3, learning_rate: float = 1e-5,
    ) -> Optional[str]:
        """Create a fine-tuning job.

        Returns job_id if successful.
        """
        if not self.is_available:
            return None

        try:
            body = {
                "model": self.model,
                "training_file": file_id,
                "hyperparameters": {
                    "n_epochs": epochs,
                    "learning_rate_multiplier": learning_rate,
                },
                "suffix": suffix,
            }

            async with httpx.AsyncClient(timeout=httpx.Timeout(30.0)) as client:
                response = await client.post(
                    f"{self.base_url}/fine_tuning/jobs",
                    headers={
                        "Authorization": f"Bearer {self.api_key}",
                        "Content-Type": "application/json",
                    },
                    json=body,
                )
                if response.status_code == 200:
                    data = response.json()
                    job_id = data.get("id", "")
                    logger.info(f"Fine-tune job created: {job_id}")
                    return job_id
                else:
                    logger.error(f"Job creation failed: {response.status_code} {response.text[:200]}")
                    return None
        except Exception as e:
            logger.error(f"Job creation error: {e}")
            return None

    async def get_job_status(self, job_id: str) -> Optional[Dict]:
        """Get fine-tuning job status."""
        if not self.is_available:
            return None

        try:
            async with httpx.AsyncClient(timeout=httpx.Timeout(15.0)) as client:
                response = await client.get(
                    f"{self.base_url}/fine_tuning/jobs/{job_id}",
                    headers={"Authorization": f"Bearer {self.api_key}"},
                )
                if response.status_code == 200:
                    return response.json()
                return None
        except Exception as e:
            logger.error(f"Status check error: {e}")
            return None

    async def wait_for_completion(self, job_id: str, poll_interval: int = 30) -> Optional[str]:
        """Poll until fine-tuning completes, return model name."""
        import asyncio
        while True:
            status = await self.get_job_status(job_id)
            if not status:
                return None

            state = status.get("status", "unknown")
            logger.info(f"Fine-tune job {job_id}: {state}")

            if state == "succeeded":
                model = status.get("fine_tuned_model", "")
                if model:
                    self.use_finetuned(model)
                return model
            elif state in ("failed", "cancelled"):
                logger.error(f"Fine-tune job failed: {status.get('error', 'unknown')}")
                return None

            await asyncio.sleep(poll_interval)

    def get_model_name(self) -> str:
        """Get the current model name (fine-tuned or default)."""
        return self.finetuned_model or self.model


# Module singleton
finetune_service = FinetuneService()
