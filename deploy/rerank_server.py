"""A minimal cross-encoder reranker with a TEI-compatible /rerank endpoint.

Run inside the TensorRT-LLM image, which already carries torch + transformers + CUDA:

    docker run --gpus all -p 8006:8006 \
      -v ~/.cache/huggingface:/root/.cache/huggingface \
      -v ./deploy:/deploy:ro --entrypoint python3 echomind-trtllm:1.2.0rc6 \
      /deploy/rerank_server.py

Exists because `RERANKER=bge` was a documented flag with nothing behind it. The wire
format matches text-embeddings-inference so the same code path works against TEI in a
real deployment — this server is the local stand-in, not a bespoke protocol.
"""

from __future__ import annotations

import os

import torch
import uvicorn
from fastapi import FastAPI
from pydantic import BaseModel
from transformers import AutoModelForSequenceClassification, AutoTokenizer

MODEL = os.environ.get("RERANK_MODEL", "BAAI/bge-reranker-large")
PORT = int(os.environ.get("RERANK_PORT", "8006"))
MAX_LEN = int(os.environ.get("RERANK_MAX_LEN", "512"))

device = "cuda" if torch.cuda.is_available() else "cpu"
dtype = torch.float16 if device == "cuda" else torch.float32

print(f"loading {MODEL} on {device} ({dtype})", flush=True)
tokenizer = AutoTokenizer.from_pretrained(MODEL)
model = AutoModelForSequenceClassification.from_pretrained(MODEL, torch_dtype=dtype).to(device)
model.eval()
print("reranker ready", flush=True)

app = FastAPI(title="echomind-reranker")


class RerankRequest(BaseModel):
    query: str
    texts: list[str]
    model: str | None = None
    raw_scores: bool = False


@app.get("/health")
def health() -> dict:
    return {"ok": True, "model": MODEL, "device": device}


@app.post("/rerank")
def rerank(req: RerankRequest) -> list[dict]:
    """TEI's shape: a list of {index, score}, highest first."""
    if not req.texts:
        return []
    pairs = [(req.query, t) for t in req.texts]
    with torch.no_grad():
        batch = tokenizer(
            [p[0] for p in pairs], [p[1] for p in pairs],
            padding=True, truncation=True, max_length=MAX_LEN, return_tensors="pt",
        ).to(device)
        logits = model(**batch).logits.view(-1).float()
    scored = sorted(
        ({"index": i, "score": float(s)} for i, s in enumerate(logits.tolist())),
        key=lambda d: d["score"],
        reverse=True,
    )
    return scored


if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=PORT, log_level="warning")
