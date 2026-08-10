"""A minimal reranker with a TEI-compatible /rerank endpoint.

Run inside the TensorRT-LLM image, which already carries torch + transformers + CUDA:

    docker run --gpus all -p 8006:8006 \
      -v ~/.cache/huggingface:/root/.cache/huggingface \
      -v ./deploy:/deploy:ro --entrypoint python3 echomind-trtllm:1.2.0rc6 \
      /deploy/rerank_server.py

Exists because `RERANKER=bge` was a documented flag with nothing behind it. The wire
format matches text-embeddings-inference so the same code path works against TEI in a
real deployment — this server is the local stand-in, not a bespoke protocol.

Two model families are supported behind the same endpoint:

  cross-encoder  BAAI/bge-reranker-large and friends — a sequence classifier whose
                 single logit is the score.
  qwen3          Qwen/Qwen3-Reranker-* — a causal LM asked to answer yes or no, scored
                 on the log-odds of those two tokens.

Both return an unbounded log-odds-shaped score, so the client's relative cut (keep what
is within N of the best) means the same thing either way and does not need recalibrating
per model.
"""

from __future__ import annotations

import os

import torch
import uvicorn
from fastapi import FastAPI
from pydantic import BaseModel
from transformers import AutoModelForCausalLM, AutoModelForSequenceClassification, AutoTokenizer

MODEL = os.environ.get("RERANK_MODEL", "BAAI/bge-reranker-large")
PORT = int(os.environ.get("RERANK_PORT", "8006"))
MAX_LEN = int(os.environ.get("RERANK_MAX_LEN", "1024"))
BATCH = int(os.environ.get("RERANK_BATCH", "8"))
# What the query is FOR. Qwen3-Reranker is instruction-following, and the default
# "find related passages" rewards anything on the topic — which is the failure being
# fixed: a consumables page mentioning "the monthly invoice" ranked alongside the one
# document that says when invoices go out.
INSTRUCT = os.environ.get(
    "RERANK_INSTRUCT",
    "Given a user's question, judge whether the document states the answer to it. "
    "Answer yes only if the document contains the specific fact the question asks for, "
    "not merely related discussion of the same topic.",
)

KIND = os.environ.get("RERANK_KIND", "auto")
if KIND == "auto":
    KIND = "qwen3" if "qwen3-reranker" in MODEL.lower() else "cross-encoder"

device = "cuda" if torch.cuda.is_available() else "cpu"
dtype = torch.float16 if device == "cuda" else torch.float32

print(f"loading {MODEL} as {KIND} on {device} ({dtype})", flush=True)

if KIND == "qwen3":
    # Left padding: the score is read from the final position, so right padding would
    # read the logits of a pad token.
    tokenizer = AutoTokenizer.from_pretrained(MODEL, padding_side="left")
    model = AutoModelForCausalLM.from_pretrained(MODEL, torch_dtype=dtype).to(device)
    YES_ID = tokenizer.convert_tokens_to_ids("yes")
    NO_ID = tokenizer.convert_tokens_to_ids("no")
    PREFIX = (
        "<|im_start|>system\nJudge whether the Document meets the requirements based on "
        'the Query and the Instruct provided. Note that the answer can only be "yes" or '
        '"no".<|im_end|>\n<|im_start|>user\n'
    )
    SUFFIX = "<|im_end|>\n<|im_start|>assistant\n<think>\n\n</think>\n\n"
else:
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
    return {"ok": True, "model": MODEL, "kind": KIND, "device": device}


def _cross_encoder_scores(query: str, texts: list[str]) -> list[float]:
    out: list[float] = []
    for start in range(0, len(texts), BATCH):
        window = texts[start : start + BATCH]
        batch = tokenizer(
            [query] * len(window), window,
            padding=True, truncation=True, max_length=MAX_LEN, return_tensors="pt",
        ).to(device)
        out.extend(model(**batch).logits.view(-1).float().tolist())
    return out


def _qwen3_scores(query: str, texts: list[str]) -> list[float]:
    """Log-odds of "yes" over "no" at the final position."""
    out: list[float] = []
    for start in range(0, len(texts), BATCH):
        window = texts[start : start + BATCH]
        prompts = [
            f"{PREFIX}<Instruct>: {INSTRUCT}\n<Query>: {query}\n<Document>: {doc}{SUFFIX}"
            for doc in window
        ]
        batch = tokenizer(
            prompts, padding=True, truncation=True, max_length=MAX_LEN, return_tensors="pt"
        ).to(device)
        logits = model(**batch).logits[:, -1, :].float()
        pair = torch.stack([logits[:, NO_ID], logits[:, YES_ID]], dim=1)
        log_probs = torch.nn.functional.log_softmax(pair, dim=1)
        # yes minus no in log space: unbounded, positive when the answer is yes.
        out.extend((log_probs[:, 1] - log_probs[:, 0]).tolist())
    return out


@app.post("/rerank")
def rerank(req: RerankRequest) -> list[dict]:
    """TEI's shape: a list of {index, score}, highest first."""
    if not req.texts:
        return []
    with torch.no_grad():
        scores = (
            _qwen3_scores(req.query, req.texts)
            if KIND == "qwen3"
            else _cross_encoder_scores(req.query, req.texts)
        )
    return sorted(
        ({"index": i, "score": float(s)} for i, s in enumerate(scores)),
        key=lambda d: d["score"],
        reverse=True,
    )


if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=PORT, log_level="warning")
