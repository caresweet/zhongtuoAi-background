#!/usr/bin/env python3
"""Fine-tuning management CLI.

Usage:
  python3 scripts/manage_finetune.py build          # Build dataset
  python3 scripts/manage_finetune.py upload         # Upload dataset to API
  python3 scripts/manage_finetune.py train          # Start fine-tuning job
  python3 scripts/manage_finetune.py status [JOB_ID] # Check job status
  python3 scripts/manage_finetune.py use MODEL_NAME # Switch to fine-tuned model
  python3 scripts/manage_finetune.py reset          # Switch back to default model
"""

import asyncio, sys, os, json
from pathlib import Path

backend_dir = Path(__file__).resolve().parent.parent
os.chdir(str(backend_dir))
sys.path.insert(0, str(backend_dir))


async def cmd_build():
    """Build fine-tuning dataset."""
    from scripts.build_finetune_dataset import build_dataset, main
    main()


async def cmd_upload():
    """Upload dataset to API."""
    from app.services.finetune_service import finetune_service

    dataset_path = backend_dir / "data" / "finetune" / "stability_report_finetune.jsonl"
    if not dataset_path.exists():
        print(f"Dataset not found. Run 'build' first.")
        print(f"Expected: {dataset_path}")
        return

    print(f"Uploading {dataset_path} ({dataset_path.stat().st_size / 1024:.1f} KB)...")
    file_id = await finetune_service.upload_dataset(str(dataset_path))
    if file_id:
        print(f"✅ Uploaded: {file_id}")
        # Save for later use
        info_file = backend_dir / "data" / "finetune" / "job_info.json"
        info = {"file_id": file_id}
        info_file.write_text(json.dumps(info, indent=2))
    else:
        print("❌ Upload failed")


async def cmd_train():
    """Start fine-tuning job."""
    from app.services.finetune_service import finetune_service

    info_file = backend_dir / "data" / "finetune" / "job_info.json"
    if not info_file.exists():
        print("No uploaded file. Run 'upload' first.")
        return

    info = json.loads(info_file.read_text())
    file_id = info.get("file_id")
    if not file_id:
        print("No file_id found. Run 'upload' first.")
        return

    print(f"Starting fine-tune job for file: {file_id}")
    job_id = await finetune_service.create_finetune_job(file_id)
    if job_id:
        print(f"✅ Job created: {job_id}")
        info["job_id"] = job_id
        info_file.write_text(json.dumps(info, indent=2))

        print("Waiting for completion (this may take 10-30 minutes)...")
        model = await finetune_service.wait_for_completion(job_id)
        if model:
            print(f"✅ Fine-tuned model ready: {model}")
            info["model"] = model
            info_file.write_text(json.dumps(info, indent=2))
        else:
            print("❌ Fine-tuning failed or timed out")
    else:
        print("❌ Job creation failed")


async def cmd_status(job_id=None):
    """Check fine-tuning job status."""
    from app.services.finetune_service import finetune_service

    if not job_id:
        info_file = backend_dir / "data" / "finetune" / "job_info.json"
        if info_file.exists():
            info = json.loads(info_file.read_text())
            job_id = info.get("job_id")

    if not job_id:
        print("No job ID. Run 'train' first or pass job_id.")
        return

    status = await finetune_service.get_job_status(job_id)
    if status:
        print(json.dumps(status, indent=2, ensure_ascii=False))
    else:
        print("Failed to get status")


def cmd_use(model_name=None):
    """Switch to fine-tuned model."""
    from app.services.finetune_service import finetune_service

    if not model_name:
        info_file = backend_dir / "data" / "finetune" / "job_info.json"
        if info_file.exists():
            info = json.loads(info_file.read_text())
            model_name = info.get("model")

    if not model_name:
        print("No model name. Pass it or run 'train' first.")
        return

    finetune_service.use_finetuned(model_name)
    print(f"✅ Using fine-tuned model: {model_name}")
    print("Restart the backend for changes to take effect.")


def cmd_reset():
    """Switch back to default model."""
    from app.services.finetune_service import finetune_service
    finetune_service.use_default()
    print("✅ Switched back to default model. Restart backend.")


COMMANDS = {
    "build": cmd_build,
    "upload": cmd_upload,
    "train": cmd_train,
    "status": lambda: cmd_status(sys.argv[2] if len(sys.argv) > 2 else None),
    "use": lambda: cmd_use(sys.argv[2] if len(sys.argv) > 2 else None),
    "reset": cmd_reset,
}


def main():
    if len(sys.argv) < 2:
        print(__doc__)
        print("\nAvailable commands:", ", ".join(COMMANDS.keys()))
        return

    cmd = sys.argv[1]
    handler = COMMANDS.get(cmd)
    if not handler:
        print(f"Unknown command: {cmd}")
        print("Available:", ", ".join(COMMANDS.keys()))
        return

    if asyncio.iscoroutinefunction(handler):
        asyncio.run(handler())
    else:
        handler()


if __name__ == "__main__":
    main()
