"""Independent HTTP process. It has no core database or model dependencies."""
import os
from typing import Annotated

from fastapi import FastAPI, Path, Query

from app.measurement.service import Measurement


def create_app(directory=None):
    measurement = Measurement(directory or os.getenv("MEASUREMENT_DATA_DIR", "/measurement-data"))
    app = FastAPI(title="主体自律指数测度服务", version="1.0.0")

    @app.get("/health/live")
    def live():
        return {"status": "ok"}

    @app.get("/internal/v1/measurement/status")
    def status():
        return measurement.status()

    @app.get("/internal/v1/measurement/results/{stock_code}")
    def result(stock_code: Annotated[str, Path(pattern=r"^[0-9]{6}$")],
               report_year: Annotated[int, Query(ge=1990, le=2100)]):
        return measurement.result(stock_code, report_year)

    return app


app = create_app()
