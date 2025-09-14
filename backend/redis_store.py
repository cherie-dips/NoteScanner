import os
import redis
import numpy as np
from redis.commands.search.field import VectorField, TextField
from redis.commands.search.index_definition import IndexDefinition, IndexType

# Use redis-stack if running inside Docker Compose, otherwise localhost works on host
REDIS_HOST = os.getenv("REDIS_HOST", "redis-stack")
REDIS_PORT = int(os.getenv("REDIS_PORT", 6379))

print(f"🔌 Connecting to Redis at {REDIS_HOST}:{REDIS_PORT}")

r = redis.Redis(host=REDIS_HOST, port=REDIS_PORT, decode_responses=False)

INDEX_NAME = "notes_idx"

def create_index():
    schema = (
        TextField("content"),
        VectorField(
            "embedding",
            "FLAT",
            {"TYPE": "FLOAT32", "DIM": 1536, "DISTANCE_METRIC": "COSINE"},
        ),
    )
    definition = IndexDefinition(prefix=["note:"], index_type=IndexType.HASH)
    try:
        r.ft(INDEX_NAME).create_index(schema, definition=definition)
    except Exception:
        pass

def insert_embeddings(chunks):
    for i, (text, vector) in enumerate(chunks):
        key = f"note:{i}"
        r.hset(
            key,
            mapping={
                "content": text,
                "embedding": np.array(vector, dtype=np.float32).tobytes(),
            },
        )

if __name__ == "__main__":
    create_index()