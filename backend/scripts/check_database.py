import asyncio

from sqlalchemy import text

from app.db.session import engine


async def check_database() -> None:
    """Verify the real database connection and required vector extension."""
    try:
        async with engine.connect() as connection:
            database_name = await connection.scalar(text("SELECT current_database()"))
            vector_version = await connection.scalar(
                text("SELECT extversion FROM pg_extension WHERE extname = 'vector'")
            )
    finally:
        await engine.dispose()

    print(f"database: {database_name}")
    print(f"vector extension: {vector_version}")


if __name__ == "__main__":
    asyncio.run(check_database())
