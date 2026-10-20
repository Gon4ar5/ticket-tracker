from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from app.database import Base, engine
from app.models import AgentProject, AgentRun, AgentRunEvent, Notification, Ticket  # noqa: F401
from app.routers import api, web
from app.agent_api import router as agent_api


@asynccontextmanager
async def lifespan(_: FastAPI):
    Base.metadata.create_all(bind=engine)
    yield


app = FastAPI(
    title="Tiny Ticket Tracker",
    description="A small ticket board for practicing AI-enabled delivery workflows.",
    version="0.1.0",
    lifespan=lifespan,
)

static_dir = Path(__file__).parent / "static"
app.mount("/static", StaticFiles(directory=static_dir), name="static")
app.include_router(web.router)
app.include_router(api.router)
app.include_router(agent_api)


@app.get("/health", tags=["health"])
def health():
    return {"status": "ok"}
