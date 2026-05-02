"""Messaging routes. Supabase Realtime handles live delivery on the frontend."""

from datetime import datetime, timezone
from uuid import uuid4

from fastapi import APIRouter, HTTPException, Query, status

from app.deps import UserClientDep, UserIdDep
from app.schemas.conversation import ConversationCreate, MessageCreate

router = APIRouter()


@router.get("")
async def list_conversations(user_id: UserIdDep, db: UserClientDep):
    """Return conversations where the authenticated user is a participant."""
    result = (
        db.table("conversations")
        .select("*")
        .contains("participant_ids", [user_id])
        .order("last_message_at", desc=True, nullsfirst=False)
        .execute()
    )
    return result.data or []


@router.post("", status_code=status.HTTP_201_CREATED)
async def start_conversation(body: ConversationCreate, user_id: UserIdDep, db: UserClientDep):
    """Start a new conversation. Exactly two participants for Phase 1."""
    if user_id in body.participant_ids:
        participant_ids = list(set([user_id] + body.participant_ids))
    else:
        participant_ids = [user_id] + body.participant_ids

    if len(participant_ids) != 2:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Conversations must have exactly 2 participants")

    # Check if conversation already exists between these two users
    existing = (
        db.table("conversations")
        .select("id")
        .contains("participant_ids", participant_ids)
        .execute()
    )
    if existing.data:
        return existing.data[0]

    conv_id = str(uuid4())
    now = datetime.now(timezone.utc).isoformat()
    result = db.table("conversations").insert(
        {
            "id": conv_id,
            "booking_id": body.booking_id,
            "participant_ids": participant_ids,
            "created_at": now,
        }
    ).execute()
    return result.data[0]


@router.get("/{conversation_id}/messages")
async def get_messages(
    conversation_id: str,
    user_id: UserIdDep,
    db: UserClientDep,
    limit: int = Query(50, ge=1, le=200),
    before: str = Query(None, description="ISO timestamp — return messages older than this"),
):
    """Fetch message history. RLS ensures user is a participant."""
    _assert_participant(conversation_id, user_id, db)

    query = (
        db.table("messages")
        .select("*,users!sender_id(full_name,avatar_url)")
        .eq("conversation_id", conversation_id)
        .order("created_at", desc=True)
    )
    if before:
        query = query.lt("created_at", before)

    result = query.limit(limit).execute()
    messages = result.data or []
    # Mark as read
    db.table("messages").update({"is_read": True}).eq("conversation_id", conversation_id).neq("sender_id", user_id).execute()
    return list(reversed(messages))


@router.post("/{conversation_id}/messages", status_code=status.HTTP_201_CREATED)
async def send_message(conversation_id: str, body: MessageCreate, user_id: UserIdDep, db: UserClientDep):
    """Send a message. Supabase Realtime broadcasts it to the recipient."""
    _assert_participant(conversation_id, user_id, db)

    content = body.content.strip()
    if not content:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="Message content cannot be empty")

    msg_id = str(uuid4())
    now = datetime.now(timezone.utc).isoformat()
    result = db.table("messages").insert(
        {
            "id": msg_id,
            "conversation_id": conversation_id,
            "sender_id": user_id,
            "content": content,
            "is_read": False,
            "created_at": now,
        }
    ).execute()

    # Update conversation.last_message_at for inbox ordering
    db.table("conversations").update({"last_message_at": now}).eq("id", conversation_id).execute()

    return result.data[0] if isinstance(result.data, list) else result.data


def _assert_participant(conversation_id: str, user_id: str, db) -> None:
    result = db.table("conversations").select("participant_ids").eq("id", conversation_id).single().execute()
    if not result.data or user_id not in result.data.get("participant_ids", []):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Not a participant in this conversation")
