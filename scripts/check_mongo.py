import os
import asyncio
from motor.motor_asyncio import AsyncIOMotorClient

async def check_db():
    try:
        client = AsyncIOMotorClient(os.environ.get("MONGODB_URL", "mongodb://localhost:27017/"), serverSelectionTimeoutMS=2000)
        db = client[os.environ.get("MONGODB_DATABASE", "depi")]
        collections = await db.list_collection_names()
        print(f"Connection Successful! Collections: {collections}")
    except Exception as e:
        print(f"Connection Failed: {e}")

if __name__ == "__main__":
    asyncio.run(check_db())
