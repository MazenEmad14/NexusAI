import asyncio
import os
from motor.motor_asyncio import AsyncIOMotorClient

async def backup_data():
    client = AsyncIOMotorClient("mongodb://admin:admin@localhost:27007/?authSource=admin")
    db = client["depi"]

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
