# Copyright (c) Microsoft. All rights reserved.
# Licensed under the MIT license. See LICENSE.md file in the project root for full license information.

import logging
from datetime import datetime, timezone
from typing import List, Optional, Dict, Any

from azure.data.tables.aio import TableServiceClient
from azure.core.credentials_async import AsyncTokenCredential

logger = logging.getLogger("azureaiapp")

TABLE_NAME = "conversations"


class ConversationManager:
    """Manages chat conversation metadata in Azure Table Storage."""

    def __init__(self, endpoint_url: str, credential: AsyncTokenCredential):
        self.account_url = endpoint_url.rstrip("/")
        self.credential = credential
        self._table_client = None

    async def _get_table_client(self):
        if self._table_client is None:
            service = TableServiceClient(
                endpoint=self.account_url,
                credential=self.credential,
            )
            self._table_client = service.get_table_client(TABLE_NAME)
            # Auto-create table if it doesn't exist
            try:
                await self._table_client.create_table()
                logger.info(f"Created table '{TABLE_NAME}'")
            except Exception:
                # Table already exists or other non-critical error
                pass
        return self._table_client

    async def upsert_conversation(
        self,
        user_id: str,
        conversation_id: str,
        agent_id: str,
        title: str,
        preview: str = "",
        message_count: int = 0,
    ) -> None:
        """Create or update a conversation entry."""
        table_client = await self._get_table_client()
        now = datetime.now(timezone.utc).isoformat()
        entity = {
            "PartitionKey": user_id,
            "RowKey": conversation_id,
            "agentId": agent_id,
            "title": title[:100] if title else "",
            "preview": preview[:200] if preview else "",
            "messageCount": message_count,
            "updatedAt": now,
        }
        try:
            # Try to get existing entity to preserve createdAt
            existing = await table_client.get_entity(
                partition_key=user_id, row_key=conversation_id
            )
            entity["createdAt"] = existing.get("createdAt", now)
            entity["messageCount"] = existing.get("messageCount", 0) + 1
        except Exception:
            entity["createdAt"] = now
            entity["messageCount"] = max(message_count, 1)

        await table_client.upsert_entity(entity)
        logger.info(f"Upserted conversation {conversation_id} for user {user_id}")

    async def list_conversations(
        self, user_id: str, limit: int = 30
    ) -> List[Dict[str, Any]]:
        """List conversations for a user, ordered by most recent."""
        table_client = await self._get_table_client()
        query_filter = f"PartitionKey eq '{user_id}'"
        conversations = []
        async for entity in table_client.query_entities(
            query_filter=query_filter,
        ):
            conversations.append(
                {
                    "id": entity["RowKey"],
                    "title": entity.get("title", ""),
                    "preview": entity.get("preview", ""),
                    "messageCount": entity.get("messageCount", 0),
                    "createdAt": entity.get("createdAt", ""),
                    "updatedAt": entity.get("updatedAt", ""),
                    "agentId": entity.get("agentId", ""),
                }
            )
        # Sort by updatedAt descending
        conversations.sort(key=lambda c: c.get("updatedAt", ""), reverse=True)
        return conversations[:limit]

    async def get_conversation(
        self, user_id: str, conversation_id: str
    ) -> Optional[Dict[str, Any]]:
        """Get a specific conversation."""
        table_client = await self._get_table_client()
        try:
            entity = await table_client.get_entity(
                partition_key=user_id, row_key=conversation_id
            )
            return {
                "id": entity["RowKey"],
                "title": entity.get("title", ""),
                "preview": entity.get("preview", ""),
                "messageCount": entity.get("messageCount", 0),
                "createdAt": entity.get("createdAt", ""),
                "updatedAt": entity.get("updatedAt", ""),
                "agentId": entity.get("agentId", ""),
            }
        except Exception:
            return None

    async def delete_conversation(
        self, user_id: str, conversation_id: str
    ) -> bool:
        """Delete a conversation entry."""
        table_client = await self._get_table_client()
        try:
            await table_client.delete_entity(
                partition_key=user_id, row_key=conversation_id
            )
            logger.info(f"Deleted conversation {conversation_id} for user {user_id}")
            return True
        except Exception as e:
            logger.error(f"Error deleting conversation: {e}")
            return False

    async def close(self):
        """Close the table client."""
        if self._table_client:
            await self._table_client.close()
            self._table_client = None
