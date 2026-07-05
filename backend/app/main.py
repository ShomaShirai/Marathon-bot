import logging

from fastapi import FastAPI

from backend.app.controllers import dev_controller, health_controller, slack_controller
from backend.app.core.config import is_local_env

logging.basicConfig(level=logging.INFO if is_local_env() else logging.WARNING)

app = FastAPI(title="Marathon Deadline Bot API")

app.include_router(health_controller.router)
app.include_router(slack_controller.router)
app.include_router(dev_controller.router)
