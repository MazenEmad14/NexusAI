import asyncio
import os
from motor.motor_asyncio import AsyncIOMotorClient

async def backup_data():
    client = AsyncIOMotorClient(os.environ.get("MONGODB_URL", "mongodb://localhost:27017/"))
    db = client[os.environ.get("MONGODB_DATABASE", "depi")]

    # Backup tasks
    tasks = await db["tasks"].find({}).to_list(length=None)
    if tasks:
        await db["tasks_archive"].insert_many(tasks)
        print(f"Backed up {len(tasks)} tasks.")

    # Backup reminders
    reminders = await db["reminders"].find({}).to_list(length=None)
    if reminders:
        await db["reminders_archive"].insert_many(reminders)
        print(f"Backed up {len(reminders)} reminders.")

    print("Backup completed successfully.")

if __name__ == "__main__":
    asyncio.run(backup_data())
