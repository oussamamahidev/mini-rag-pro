"""Initialize MongoDB indexes and an idempotent admin tenant."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src import database as database_module
from src.auth.key_generator import pwd_context, verify_api_key
from src.config import get_settings
from src.database import close_connections, create_indexes, initialize_connections
from src.models.tenant import PLAN_LIMITS, Tenant, TenantPlan


ADMIN_EMAIL = "admin@mini-rag.local"
ADMIN_NAME = "mini-rag admin"


async def main() -> None:
    """Create indexes and ensure the configured admin tenant exists."""
    settings = get_settings()
    await initialize_connections(settings)

    try:
        await create_indexes()
        print("MongoDB indexes are ready.")

        db = database_module.mongo_database
        if db is None:
            raise RuntimeError("MongoDB database is not initialized")

        admin_key = settings.admin_api_key.strip()
        admin_prefix = admin_key[:10]
        now = datetime.now(UTC)

        existing = await db.tenants.find_one(
            {
                "$or": [
                    {"email": ADMIN_EMAIL},
                    {"metadata.is_admin": True},
                ]
            }
        )

        if existing is None:
            tenant = Tenant(
                name=ADMIN_NAME,
                email=ADMIN_EMAIL,
                api_key_hash=pwd_context.hash(admin_key),
                api_key_prefix=admin_prefix,
                plan=TenantPlan.ENTERPRISE,
                rate_limit_per_hour=PLAN_LIMITS[TenantPlan.ENTERPRISE.value]["rate_limit_per_hour"],
                max_projects=PLAN_LIMITS[TenantPlan.ENTERPRISE.value]["max_projects"],
                max_documents_per_project=PLAN_LIMITS[TenantPlan.ENTERPRISE.value]["max_documents_per_project"],
                max_file_size_mb=PLAN_LIMITS[TenantPlan.ENTERPRISE.value]["max_file_size_mb"],
                created_by="init_db",
                updated_by="init_db",
                metadata={
                    "is_admin": True,
                    "created_by_script": "scripts/init_db.py",
                },
            )
            await db.tenants.insert_one(tenant.model_dump(mode="python"))
            print(f"Created admin tenant: {tenant.id}")
        else:
            updates = {
                "name": existing.get("name") or ADMIN_NAME,
                "email": existing.get("email") or ADMIN_EMAIL,
                "api_key_prefix": admin_prefix,
                "plan": TenantPlan.ENTERPRISE.value,
                "rate_limit_per_hour": PLAN_LIMITS[TenantPlan.ENTERPRISE.value]["rate_limit_per_hour"],
                "max_projects": PLAN_LIMITS[TenantPlan.ENTERPRISE.value]["max_projects"],
                "max_documents_per_project": PLAN_LIMITS[TenantPlan.ENTERPRISE.value]["max_documents_per_project"],
                "max_file_size_mb": PLAN_LIMITS[TenantPlan.ENTERPRISE.value]["max_file_size_mb"],
                "is_active": True,
                "updated_at": now,
                "updated_by": "init_db",
                "metadata.is_admin": True,
                "metadata.updated_by_script": "scripts/init_db.py",
            }
            if not verify_api_key(admin_key, str(existing.get("api_key_hash") or "")):
                updates["api_key_hash"] = pwd_context.hash(admin_key)

            await db.tenants.update_one({"_id": existing["_id"]}, {"$set": updates})
            print(f"Admin tenant already exists: {existing.get('id')}")

        print(f"Admin API key: {admin_key}")
    finally:
        await close_connections()


if __name__ == "__main__":
    asyncio.run(main())
