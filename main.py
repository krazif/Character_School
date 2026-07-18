"""Character School — main entry point."""
import os
from datetime import datetime, timezone
from pathlib import Path
from fastapi import FastAPI
from fastapi.responses import HTMLResponse, JSONResponse, FileResponse
from fastapi.staticfiles import StaticFiles
import db
from school import router as school_router
from rp import router as rp_router
from imagegen import router as imagegen_router

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


@app.get("/api/images")
async def api_list_images():
    """List all uploaded images with modification dates for gallery display."""
    images = []
    if db.UPLOAD_DIR.exists():
        for f in db.UPLOAD_DIR.iterdir():
            if f.is_file() and f.suffix.lower() in ('.png', '.jpg', '.jpeg', '.gif', '.webp'):
                stat = f.stat()
                images.append({
                    "filename": f.name,
                    "size": stat.st_size,
                    "mtime": stat.st_mtime,
                    "date": datetime.fromtimestamp(stat.st_mtime, tz=timezone.utc).strftime("%Y-%m-%d"),
                })
    images.sort(key=lambda x: x["mtime"], reverse=True)
    return JSONResponse({"images": images})


@app.delete("/api/images/{filename}")
async def api_delete_image(filename: str):
    """Delete an uploaded image from disk."""
    safe = Path(filename).name
    file = db.UPLOAD_DIR / safe
    if not file.exists():
        return JSONResponse({"error": "Image not found"}, status_code=404)
    try:
        file.unlink()
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
