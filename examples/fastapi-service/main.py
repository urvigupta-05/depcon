from fastapi import FastAPI, HTTPException
from dotenv import load_dotenv
from pydantic import BaseModel
import os
import time
import random

from opentelemetry import trace
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor
from opentelemetry.sdk.resources import Resource
from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
from opentelemetry.sdk._logs import LoggerProvider, LoggingHandler
from opentelemetry._logs import set_logger_provider
from opentelemetry.sdk._logs.export import BatchLogRecordProcessor
from opentelemetry.exporter.otlp.proto.http._log_exporter import OTLPLogExporter
import logging


load_dotenv()

CHAOS_MODE=os.getenv("CHAOS_MODE","off")
request_count=0

resource = Resource.create({
    "service.name": "depcon-target",
    "service.version": "0.1.0"
})


exporter = OTLPSpanExporter(
    endpoint=os.getenv("DT_OTLP_ENDPOINT") + "/v1/traces",
    headers={"Authorization": "Api-Token " + os.getenv("DT_API_TOKEN")}
)

provider = TracerProvider(resource=resource)
provider.add_span_processor(BatchSpanProcessor(exporter))
trace.set_tracer_provider(provider)
log_exporter = OTLPLogExporter(
    endpoint=os.getenv("DT_OTLP_ENDPOINT") + "/v1/logs",
    headers={"Authorization": "Api-Token " + os.getenv("DT_API_TOKEN")}
)
log_provider = LoggerProvider(resource=resource)
set_logger_provider(log_provider)
log_provider.add_log_record_processor(BatchLogRecordProcessor(log_exporter))

handler = LoggingHandler(logger_provider=log_provider)
logging.getLogger().addHandler(handler)
logger = logging.getLogger(__name__)


app=FastAPI()
FastAPIInstrumentor().instrument_app(app)

class RunRequest(BaseModel):
    input: str

@app.get("/health")
def health():
    return {"status": "ok"}

@app.post("/run")
def run(body: RunRequest):
    global request_count
    request_count += 1
    start_time = time.time()

    span = trace.get_current_span()
    span.set_attribute("http.method", "POST")
    span.set_attribute("http.route", "/run")

    if not body.input:
        span.set_attribute("http.status_code", 400)
        span.set_attribute("error", True)
        raise HTTPException(status_code=400, detail="input cannot be empty")

    if CHAOS_MODE == "latency":
        time.sleep(0.8)
    elif CHAOS_MODE == "error":
        if random.random() < 0.5:
            span.set_attribute("http.status_code", 500)
            span.set_attribute("error", True)
            latency = (time.time() - start_time) * 1000
            span.set_attribute("custom.latency_ms", latency)
            raise HTTPException(status_code=500, detail="chaos error")
    elif CHAOS_MODE == "panic":
        if request_count >= 3:
            span.set_attribute("error", True)
            raise RuntimeError("panic mode triggered")

    latency = (time.time() - start_time) * 1000
    span.set_attribute("custom.latency_ms", latency)
    span.set_attribute("http.status_code", 200)
    span.set_attribute("error", False)
    
    logger.info(f"run called with input: {body.input}")

    return {"result": body.input}

