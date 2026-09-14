"""Admin media uploads to Cloudinary."""
from __future__ import annotations

from fastapi import APIRouter, File, Form, HTTPException, UploadFile

from ...config import settings
from ...services import cloudinary_service

router = APIRouter(prefix="/uploads", tags=["admin:uploads"])


@router.get("/status")
def status() -> dict:
    return {
        "configured": cloudinary_service.is_configured(),
        "folder": settings.cloudinary_folder,
        "cloud_name": settings.cloudinary_cloud_name or None,
    }


@router.post("", status_code=201)
async def upload(
    file: UploadFile = File(...),
    folder: str = Form(default="uploads"),
) -> dict:
    if not cloudinary_service.is_configured():
        raise HTTPException(
            status_code=503,
            detail="Cloudinary is not configured. Set CLOUDINARY_* environment variables.",
        )
    data = await file.read()
    if not data:
        raise HTTPException(status_code=400, detail="Empty file.")

    resource_type = "image" if (file.content_type or "").startswith("image") else "auto"
    result = cloudinary_service.upload_bytes(
        data, folder=folder, resource_type=resource_type, public_id=None
    )
    if result is None:
        raise HTTPException(status_code=502, detail="Upload to Cloudinary failed.")
    return {
        "ok": True,
        "url": result.get("secure_url") or result.get("url"),
        "public_id": result.get("public_id"),
        "format": result.get("format"),
        "bytes": result.get("bytes"),
        "resource_type": result.get("resource_type"),
    }
