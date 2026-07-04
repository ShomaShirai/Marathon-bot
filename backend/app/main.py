from fastapi import FastAPI

from backend.app.controllers import health_controller

app = FastAPI(title="Marathon Deadline Bot API")

app.include_router(health_controller.router)
