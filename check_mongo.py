import asyncio
from motor.motor_asyncio import AsyncIOMotorClient

async def check_db():
    try:
        client = AsyncIOMotorClient("mongodb://admin:admin@localhost:27007/?authSource=admin", serverSelectionTimeoutMS=2000)
        db = client["depi"]
        collections = await db.list_collection_names()
        print(f"Connection Successful! Collections: {collections}")
    except Exception as e:
        print(f"Connection Failed: {e}")

if __name__ == "__main__":
    asyncio.run(check_db())
