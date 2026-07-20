"""Character School — main entry point."""
import os
import json
import uuid
from datetime import datetime, timezone
from pathlib import Path
from fastapi import FastAPI, UploadFile, File
from fastapi.responses import HTMLResponse, JSONResponse, FileResponse
from fastapi.staticfiles import StaticFiles
import db
from school import router as school_router
from rp import router as rp_router
from imagegen import router as imagegen_router

ALLOWED_BG_TYPES = {"image/png", "image/jpeg", "image/gif", "image/webp"}
MAX_BG_SIZE = 10 * 1024 * 1024  # 10 MB

app = FastAPI(title="Character School")


@app.get("/")
async def index():
    """Serve the main HTML page with cache-busting headers."""
    html_path = db.STATIC_DIR / "index.html"
    return HTMLResponse(
        html_path.read_text(encoding="utf-8"),
        headers={"Cache-Control": "no-cache, no-store, must-revalidate",
                 "Pragma": "no-cache", "Expires": "0"}
    )


@app.post("/api/bg-upload")
async def api_bg_upload(file: UploadFile = File(...)):
    """Upload a background image to the server, return a URL.
    Replaces localStorage data-URL storage that was limited to ~5MB total."""
    if not file.content_type or file.content_type.lower() not in ALLOWED_BG_TYPES:
        return JSONResponse({"error": "Unsupported file type"}, status_code=400)
    contents = await file.read()
    if len(contents) > MAX_BG_SIZE:
        return JSONResponse({"error": "File too large (max 10MB)"}, status_code=400)
    ext = {"image/png": ".png", "image/jpeg": ".jpg", "image/gif": ".gif", "image/webp": ".webp"}.get(file.content_type.lower(), ".bin")
    filename = f"bg_{uuid.uuid4().hex}{ext}"
    dest = db.UPLOAD_DIR / filename
    dest.write_bytes(contents)
    return JSONResponse({"url": f"/api/bg/{filename}"})


@app.get("/api/bg/{filename}")
async def api_bg_serve(filename: str):
    """Serve a background image from the uploads directory."""
    safe = Path(filename).name
    file = db.UPLOAD_DIR / safe
    if not file.exists():
        return JSONResponse({"error": "Image not found"}, status_code=404)
    return FileResponse(file)


@app.delete("/api/bg/{filename}")
async def api_bg_delete(filename: str):
    """Delete a background image from disk."""
    safe = Path(filename).name
    file = db.UPLOAD_DIR / safe
    if not file.exists():
        return JSONResponse({"error": "Image not found"}, status_code=404)
    try:
        file.unlink()
        return JSONResponse({"deleted": True, "filename": safe})
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)


@app.post("/api/images/{filename}/to-background")
async def api_image_to_background(filename: str):
    """Move a generated image to the background pool by renaming it with bg_ prefix.
    Also renames the sidecar JSON metadata. Returns the new bg URL."""
    safe = Path(filename).name
    src = db.UPLOAD_DIR / safe
    if not src.exists():
        return JSONResponse({"error": "Image not found"}, status_code=404)
    ext = src.suffix or ".png"
    new_name = f"bg_{uuid.uuid4().hex}{ext}"
    dest = db.UPLOAD_DIR / new_name
    try:
        src.rename(dest)
        # Rename sidecar JSON if it exists
        meta_src = db.UPLOAD_DIR / (safe + ".json")
        if meta_src.exists():
            meta_dest = db.UPLOAD_DIR / (new_name + ".json")
            meta_src.rename(meta_dest)
        return JSONResponse({"url": f"/api/bg/{new_name}", "filename": new_name})
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)


@app.get("/api/images")
async def api_list_images():
    """List all uploaded images with metadata from sidecar JSON files."""
    images = []
    if db.UPLOAD_DIR.exists():
        for f in db.UPLOAD_DIR.iterdir():
            if f.is_file() and f.suffix.lower() in ('.png', '.jpg', '.jpeg', '.gif', '.webp') and not f.name.startswith('bg_'):
                stat = f.stat()
                img = {
                    "filename": f.name,
                    "size": stat.st_size,
                    "mtime": stat.st_mtime,
                    "date": datetime.fromtimestamp(stat.st_mtime, tz=timezone.utc).strftime("%Y-%m-%d"),
                    "prompt": "",
                    "seed": None,
                    "negative_prompt": "",
                    "width": None,
                    "height": None,
                }
                # Read sidecar metadata if it exists
                meta_file = db.UPLOAD_DIR / (f.name + ".json")
                if meta_file.exists():
                    try:
                        meta = json.loads(meta_file.read_text())
                        img.update({
                            "prompt": meta.get("prompt", ""),
                            "seed": meta.get("seed"),
                            "negative_prompt": meta.get("negative_prompt", ""),
                            "width": meta.get("width"),
                            "height": meta.get("height"),
                        })
                    except Exception:
                        pass
                images.append(img)
    images.sort(key=lambda x: x["mtime"], reverse=True)
    return JSONResponse({"images": images})


@app.delete("/api/images/{filename}")
async def api_delete_image(filename: str):
    """Delete an uploaded image and its sidecar metadata from disk."""
    safe = Path(filename).name
    file = db.UPLOAD_DIR / safe
    if not file.exists():
        return JSONResponse({"error": "Image not found"}, status_code=404)
    try:
        file.unlink()
        # Also delete sidecar JSON if it exists
        meta_file = db.UPLOAD_DIR / (safe + ".json")
        if meta_file.exists():
            meta_file.unlink()
        return JSONResponse({"deleted": True, "filename": safe})
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)


app.include_router(school_router)
app.include_router(rp_router)
app.include_router(imagegen_router)

if db.STATIC_DIR.exists():
    app.mount("/static", StaticFiles(directory=str(db.STATIC_DIR)), name="static")


if __name__ == "__main__":
    import uvicorn
    _srv = db._cfg.get("server", {})
    _host = os.environ.get("CHARACTERSCHOOL_HOST", _srv.get("host", "0.0.0.0"))
    _port = int(os.environ.get("CHARACTERSCHOOL_PORT", _srv.get("port", 7862)))
    uvicorn.run(app, host=_host, port=_port, ws_max_size=16 * 1024 * 1024)  # 16MB WS limit
