"""Character School — main entry point."""
import os
from fastapi import FastAPI
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
import db
from school import router as school_router
from rp import router as rp_router

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


app.include_router(school_router)
app.include_router(rp_router)

if db.STATIC_DIR.exists():
    app.mount("/static", StaticFiles(directory=str(db.STATIC_DIR)), name="static")


if __name__ == "__main__":
    import uvicorn
    _srv = db._cfg.get("server", {})
    _host = os.environ.get("CHARACTERSCHOOL_HOST", _srv.get("host", "0.0.0.0"))
    _port = int(os.environ.get("CHARACTERSCHOOL_PORT", _srv.get("port", 7862)))
    uvicorn.run(app, host=_host, port=_port)
