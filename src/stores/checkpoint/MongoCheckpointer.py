"""MongoDB-backed LangGraph checkpointer (async, Motor).

Keeps pending approvals and chat history across restarts and across workers.
The official langgraph-checkpoint-mongodb package would force a major upgrade of
langchain/langgraph, so this implements just the async saver API the app uses.
Sync methods are intentionally not implemented: use graph.ainvoke / aget_state /
aupdate_state.
"""
import logging
from collections.abc import AsyncIterator, Sequence
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

from langchain_core.runnables import RunnableConfig
from langgraph.checkpoint.base import (
    WRITES_IDX_MAP,
    BaseCheckpointSaver,
    ChannelVersions,
    Checkpoint,
    CheckpointMetadata,
    CheckpointTuple,
    SerializerProtocol,
    get_checkpoint_id,
    get_checkpoint_metadata,
)
from pymongo import ASCENDING, DESCENDING, ReplaceOne

logger = logging.getLogger("uvicorn.error")


class MongoCheckpointer(BaseCheckpointSaver[str]):
    def __init__(self, db, *, prefix: str = "lg_", serde: Optional[SerializerProtocol] = None):
        super().__init__(serde=serde)
        self.checkpoints = db[f"{prefix}checkpoints"]
        self.blobs = db[f"{prefix}checkpoint_blobs"]
        self.writes = db[f"{prefix}checkpoint_writes"]

    async def setup(self) -> None:
        """Create indexes (idempotent). Call once at startup."""
        await self.checkpoints.create_index(
            [("thread_id", ASCENDING), ("checkpoint_ns", ASCENDING), ("checkpoint_id", DESCENDING)],
            unique=True,
        )
        await self.checkpoints.create_index([("updated_at", ASCENDING)])
        await self.blobs.create_index(
            [("thread_id", ASCENDING), ("checkpoint_ns", ASCENDING), ("channel", ASCENDING), ("version", ASCENDING)],
            unique=True,
        )
        await self.writes.create_index(
            [
                ("thread_id", ASCENDING),
                ("checkpoint_ns", ASCENDING),
                ("checkpoint_id", ASCENDING),
                ("task_id", ASCENDING),
                ("idx", ASCENDING),
            ],
            unique=True,
        )

    # ── helpers ──────────────────────────────────────────────────────────────
    @staticmethod
    def _config(thread_id: str, ns: str, checkpoint_id: str) -> RunnableConfig:
        return {"configurable": {"thread_id": thread_id, "checkpoint_ns": ns, "checkpoint_id": checkpoint_id}}

    async def _load_blobs(self, thread_id: str, ns: str, versions: ChannelVersions) -> dict[str, Any]:
        if not versions:
            return {}
        wanted = {(channel, str(version)) for channel, version in versions.items()}
        cursor = self.blobs.find(
            {
                "thread_id": thread_id,
                "checkpoint_ns": ns,
                "$or": [{"channel": c, "version": v} for c, v in wanted],
            }
        )
        values: dict[str, Any] = {}
        async for doc in cursor:
            if doc["type"] != "empty":
                values[doc["channel"]] = self.serde.loads_typed((doc["type"], bytes(doc["blob"])))
        return values

    async def _to_tuple(self, doc: dict, config: Optional[RunnableConfig] = None) -> CheckpointTuple:
        thread_id, ns, checkpoint_id = doc["thread_id"], doc["checkpoint_ns"], doc["checkpoint_id"]
        checkpoint: Checkpoint = self.serde.loads_typed((doc["type"], bytes(doc["checkpoint"])))
        metadata = self.serde.loads_typed((doc["metadata_type"], bytes(doc["metadata"])))
        writes = [
            (w["task_id"], w["channel"], self.serde.loads_typed((w["type"], bytes(w["value"]))))
            async for w in self.writes.find(
                {"thread_id": thread_id, "checkpoint_ns": ns, "checkpoint_id": checkpoint_id}
            ).sort([("task_id", ASCENDING), ("idx", ASCENDING)])
        ]
        parent = doc.get("parent_checkpoint_id")
        return CheckpointTuple(
            config=config or self._config(thread_id, ns, checkpoint_id),
            checkpoint={
                **checkpoint,
                "channel_values": await self._load_blobs(thread_id, ns, checkpoint["channel_versions"]),
            },
            metadata=metadata,
            parent_config=self._config(thread_id, ns, parent) if parent else None,
            pending_writes=writes,
        )

    # ── BaseCheckpointSaver async API ────────────────────────────────────────
    async def aget_tuple(self, config: RunnableConfig) -> Optional[CheckpointTuple]:
        thread_id = config["configurable"]["thread_id"]
        ns = config["configurable"].get("checkpoint_ns", "")
        query = {"thread_id": thread_id, "checkpoint_ns": ns}
        if checkpoint_id := get_checkpoint_id(config):
            doc = await self.checkpoints.find_one({**query, "checkpoint_id": checkpoint_id})
            return await self._to_tuple(doc, config) if doc else None
        doc = await self.checkpoints.find_one(query, sort=[("checkpoint_id", DESCENDING)])
        return await self._to_tuple(doc) if doc else None

    async def alist(
        self,
        config: Optional[RunnableConfig],
        *,
        filter: Optional[dict[str, Any]] = None,
        before: Optional[RunnableConfig] = None,
        limit: Optional[int] = None,
    ) -> AsyncIterator[CheckpointTuple]:
        query: dict[str, Any] = {}
        if config:
            query["thread_id"] = config["configurable"]["thread_id"]
            if (ns := config["configurable"].get("checkpoint_ns")) is not None:
                query["checkpoint_ns"] = ns
            if checkpoint_id := get_checkpoint_id(config):
                query["checkpoint_id"] = checkpoint_id
        if before and (before_id := get_checkpoint_id(before)):
            query["checkpoint_id"] = {**query.get("checkpoint_id", {}), "$lt": before_id} if isinstance(
                query.get("checkpoint_id"), dict
            ) else {"$lt": before_id}

        remaining = limit
        async for doc in self.checkpoints.find(query).sort([("checkpoint_id", DESCENDING)]):
            if remaining is not None and remaining <= 0:
                break
            if filter:
                metadata = self.serde.loads_typed((doc["metadata_type"], bytes(doc["metadata"])))
                if not all(metadata.get(k) == v for k, v in filter.items()):
                    continue
            if remaining is not None:
                remaining -= 1
            yield await self._to_tuple(doc)

    async def aput(
        self,
        config: RunnableConfig,
        checkpoint: Checkpoint,
        metadata: CheckpointMetadata,
        new_versions: ChannelVersions,
    ) -> RunnableConfig:
        thread_id = config["configurable"]["thread_id"]
        ns = config["configurable"].get("checkpoint_ns", "")
        parent_id = config["configurable"].get("checkpoint_id")

        stored = checkpoint.copy()
        values: dict[str, Any] = stored.pop("channel_values")  # type: ignore[misc]

        blob_ops = []
        for channel, version in new_versions.items():
            type_, blob = self.serde.dumps_typed(values[channel]) if channel in values else ("empty", b"")
            key = {"thread_id": thread_id, "checkpoint_ns": ns, "channel": channel, "version": str(version)}
            blob_ops.append(ReplaceOne(key, {**key, "type": type_, "blob": blob}, upsert=True))
        if blob_ops:
            await self.blobs.bulk_write(blob_ops, ordered=False)

        cp_type, cp_bytes = self.serde.dumps_typed(stored)
        md_type, md_bytes = self.serde.dumps_typed(get_checkpoint_metadata(config, metadata))
        key = {"thread_id": thread_id, "checkpoint_ns": ns, "checkpoint_id": checkpoint["id"]}
        await self.checkpoints.replace_one(
            key,
            {
                **key,
                "parent_checkpoint_id": parent_id,
                "type": cp_type,
                "checkpoint": cp_bytes,
                "metadata_type": md_type,
                "metadata": md_bytes,
                "updated_at": datetime.now(timezone.utc),
            },
            upsert=True,
        )
        return self._config(thread_id, ns, checkpoint["id"])

    async def aput_writes(
        self,
        config: RunnableConfig,
        writes: Sequence[tuple[str, Any]],
        task_id: str,
        task_path: str = "",
    ) -> None:
        thread_id = config["configurable"]["thread_id"]
        ns = config["configurable"].get("checkpoint_ns", "")
        checkpoint_id = config["configurable"]["checkpoint_id"]

        for idx, (channel, value) in enumerate(writes):
            inner_idx = WRITES_IDX_MAP.get(channel, idx)
            type_, blob = self.serde.dumps_typed(value)
            key = {
                "thread_id": thread_id,
                "checkpoint_ns": ns,
                "checkpoint_id": checkpoint_id,
                "task_id": task_id,
                "idx": inner_idx,
            }
            doc = {**key, "channel": channel, "type": type_, "value": blob, "task_path": task_path}
            if inner_idx >= 0:
                # Regular writes are written once; a retried task must not duplicate them.
                await self.writes.update_one(key, {"$setOnInsert": doc}, upsert=True)
            else:
                # Special channels (errors, interrupts) overwrite.
                await self.writes.replace_one(key, doc, upsert=True)

    async def adelete_thread(self, thread_id: str) -> None:
        for collection in (self.checkpoints, self.blobs, self.writes):
            await collection.delete_many({"thread_id": thread_id})

    # ── maintenance ──────────────────────────────────────────────────────────
    async def purge_stale_threads(self, older_than_days: int) -> int:
        """Delete threads whose newest checkpoint is older than the cutoff. Returns threads removed."""
        cutoff = datetime.now(timezone.utc) - timedelta(days=older_than_days)
        pipeline = [
            {"$group": {"_id": "$thread_id", "last": {"$max": "$updated_at"}}},
            {"$match": {"last": {"$lt": cutoff}}},
        ]
        stale = [doc["_id"] async for doc in self.checkpoints.aggregate(pipeline)]
        for thread_id in stale:
            await self.adelete_thread(thread_id)
        if stale:
            logger.info(f"Purged {len(stale)} stale agent threads")
        return len(stale)
