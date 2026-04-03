# Copyright (c) Microsoft. All rights reserved.
# Licensed under the MIT license. See LICENSE.md file in the project root for full license information.

import asyncio
import json
import os
import uuid
from datetime import datetime, timezone
from typing import AsyncGenerator, Mapping, Optional, Dict, List

import fastapi
from fastapi import Request, Depends, HTTPException, UploadFile, File, Form
from fastapi.responses import HTMLResponse, JSONResponse, StreamingResponse
from fastapi.templating import Jinja2Templates
from fastapi.responses import JSONResponse

import logging
from opentelemetry.trace.propagation.tracecontext import TraceContextTextMapPropagator
from azure.ai.projects.models import AgentVersionObject, AgentReference
from openai.types.conversations.message import Message
from openai.types.responses import ResponseOutputMessage
from openai.types.conversations import Conversation

from azure.ai.projects.aio import AIProjectClient

from util import encode_project_resource_id

from urllib.parse import quote


from openai import AsyncOpenAI

# Create a logger for this module
logger = logging.getLogger("azureaiapp")

# Set the log level for the azure HTTP logging policy to WARNING (or ERROR)
logging.getLogger("azure.core.pipeline.policies.http_logging_policy").setLevel(logging.WARNING)

from opentelemetry import trace

tracer = trace.get_tracer(__name__)

# Define the directory for your templates.
directory = os.path.join(os.path.dirname(__file__), "templates")
templates = Jinja2Templates(directory=directory)

# Create a new FastAPI router
router = fastapi.APIRouter()

from fastapi import FastAPI, Depends, HTTPException, status
from fastapi.security import HTTPBasic, HTTPBasicCredentials
from typing import Optional
import secrets

# --- Authentication ---
# Supports both Entra ID Easy Auth (via X-MS-CLIENT-PRINCIPAL headers)
# and legacy HTTP Basic Auth (via WEB_APP_USERNAME/WEB_APP_PASSWORD env vars)

security = HTTPBasic(auto_error=False)

username = os.getenv("WEB_APP_USERNAME")
password = os.getenv("WEB_APP_PASSWORD")
basic_auth = username and password
entra_auth_enabled = os.getenv("ENTRA_AUTH_ENABLED", "").lower() == "true"

ALLOWED_UPLOAD_TYPES = {
    "application/pdf", "text/plain", "text/csv", "text/markdown",
    "application/json", "text/html",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
}
ALLOWED_EXTENSIONS = {".pdf", ".txt", ".csv", ".md", ".json", ".html", ".docx", ".xlsx"}
MAX_UPLOAD_SIZE = 10 * 1024 * 1024  # 10MB


def get_user_id(request: Request) -> str:
    """Extract user ID from Easy Auth headers or return 'anonymous'."""
    # Easy Auth injects these headers after authentication
    principal_id = request.headers.get("X-MS-CLIENT-PRINCIPAL-ID")
    principal_name = request.headers.get("X-MS-CLIENT-PRINCIPAL-NAME", "")
    if principal_id:
        logger.info(f"Easy Auth user: id={principal_id}, name={principal_name}")
        return principal_id
    logger.info("No Easy Auth principal, using 'anonymous'")
    return "anonymous"


def get_user_name(request: Request) -> str:
    """Extract user display name from Easy Auth headers."""
    return request.headers.get("X-MS-CLIENT-PRINCIPAL-NAME", "")


def authenticate(
    request: Request,
    credentials: Optional[HTTPBasicCredentials] = Depends(security),
) -> None:
    """Authenticate the request using Entra ID Easy Auth or HTTP Basic Auth."""
    # If Entra ID Easy Auth is enabled, the platform handles auth before the request reaches us.
    # We just check that the principal header is present.
    if entra_auth_enabled:
        principal_id = request.headers.get("X-MS-CLIENT-PRINCIPAL-ID")
        if principal_id:
            return  # Authenticated via Easy Auth
        # In production with Easy Auth enabled, unauthenticated requests are redirected
        # by the platform. If we get here without a principal, it's likely a misconfiguration.
        logger.warning("Entra Auth enabled but no X-MS-CLIENT-PRINCIPAL-ID header found")

    # Fallback to HTTP Basic Auth
    if basic_auth:
        if not credentials:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Authentication required",
                headers={"WWW-Authenticate": "Basic"},
            )
        correct_username = secrets.compare_digest(credentials.username, username)
        correct_password = secrets.compare_digest(credentials.password, password)
        if not (correct_username and correct_password):
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid credentials",
                headers={"WWW-Authenticate": "Basic"},
            )
        return

    # No auth configured — allow access (development mode)
    if not entra_auth_enabled and not basic_auth:
        logger.info("Skipping authentication: no auth method configured.")
        return

    raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Authentication required")


auth_dependency = Depends(authenticate)

def cleanup_created_at_metadata(metadata: Mapping[str, str]) -> None:
    """Remove oldest created_at timestamp entries to keep metadata under 16 items limit."""
    if not metadata:
        return

    # metadata go to be up to 16 items.  If there is more than that, remove the one ended with _created_at key with smallest value
    while len(metadata) > 16:
        created_at_keys = [k for k in metadata if k.endswith("_created_at")]
        if not created_at_keys:
            break  # No more _created_at keys to remove
        min_key = min(created_at_keys, key=metadata.get)
        del metadata[min_key]

def get_project_client(request: Request) -> AIProjectClient:
    return request.app.state.ai_project

def get_agent_version_obj(request: Request) -> AgentVersionObject:
    return request.app.state.agent_version_obj

def get_openai_client(request: Request) -> AsyncOpenAI:
    return get_project_client(request).get_openai_client()

def get_conversation_manager(request: Request):
    return request.app.state.conversation_manager

def get_created_at_label(message_id: str) -> str:
    return f"{message_id}_created_at"

def serialize_sse_event(data: Dict) -> str:
    return f"data: {json.dumps(data)}\n\n"

async def get_or_create_conversation(
    openai_client: AsyncOpenAI,
    conversation_id: Optional[str],
    agent_id: Optional[str],
    current_agent_id: str
) -> Conversation:
    """
    Get an existing conversation or create a new one.
    Returns the conversation_id.
    """
    conversation: Optional[Conversation] = None

    # Attempt to get an existing conversation if we have matching agent and conversation IDs
    if conversation_id and agent_id == current_agent_id:
        try:
            logger.info(f"Using existing conversation with ID {conversation_id}")
            conversation = await openai_client.conversations.retrieve(conversation_id=conversation_id)
            logger.info(f"Retrieved conversation: {conversation.id}")
        except Exception as e:
            logger.error(f"Error retrieving conversation: {e}")

    # Create a new conversation if we don't have one
    if not conversation:
        try:
            logger.info("Creating a new conversation")
            conversation = await openai_client.conversations.create()
            logger.info(f"Generated new conversation ID: {conversation.id}")
        except Exception as e:
            logger.error(f"Error creating conversation: {e}")
            raise HTTPException(status_code=400, detail=f"Error handling conversation: {e}")

    return conversation

async def get_message_and_annotations(event: Message | ResponseOutputMessage) -> Dict:
    annotations = []
    # Get file annotations for the file search.
    text = ""
    content = event.content[0]
    if content.type == "output_text" or content.type == "input_text":
        text = content.text
    if content.type == "output_text":
        for annotation in content.annotations:
            if annotation.type == "file_citation":
                ann = {
                    'label': annotation.filename,
                    "index": annotation.index
                }
                annotations.append(ann)
            elif annotation.type == "url_citation":
                ann = {
                    'label': annotation.title,
                    "index": annotation.start_index
                }
                annotations.append(ann)

    return {
        'content': text,
        'annotations': annotations
    }


@router.get("/", response_class=HTMLResponse)
async def index(request: Request, _ = auth_dependency):
    return templates.TemplateResponse(
        "index.html",
        {
            "request": request,
        }
    )

async def save_user_message_created_at(openai_client: AsyncOpenAI, conversation: Conversation,  input_created_at: float):
    conversation.metadata = conversation.metadata  or {}
    try:
        logger.info(f"Saving created_at.")
        messages = await openai_client.conversations.items.list(conversation_id=conversation.id, order="desc")
        last_input_message = None
        async for message in messages:
            if isinstance(message, Message) and message.role == "user":
                last_input_message = message
                break
        if last_input_message:
            conversation.metadata[get_created_at_label(last_input_message.id)] = str(input_created_at)
        cleanup_created_at_metadata(conversation.metadata)

        await openai_client.conversations.update(conversation.id, metadata=conversation.metadata)

        logger.info(f"Successfully saved created_at for user message")
        return  # Success, exit the retry loop

    except Exception as e:
        logger.error(f"Error updating message created_at.")



async def get_result(
    agent: AgentVersionObject,
    conversation: Conversation,
    user_message: str,
    project_client: AIProjectClient,
    carrier: Dict[str, str],
    file_contents: Optional[List[Dict[str, str]]] = None,
) -> AsyncGenerator[str, None]:
    ctx = TraceContextTextMapPropagator().extract(carrier=carrier)
    with tracer.start_as_current_span('get_result', context=ctx):
        async with project_client.get_openai_client() as openai_client:
            logger.info(f"get_result invoked for conversation={conversation.id}")
            input_created_at = datetime.now(timezone.utc).timestamp()

            # Build input: user message + optional file contents
            if file_contents:
                file_parts = []
                for fc in file_contents:
                    file_parts.append(f"--- START OF UPLOADED FILE: {fc['name']} ---\n{fc['content']}\n--- END OF UPLOADED FILE: {fc['name']} ---")
                full_input = (
                    f"The user has uploaded {len(file_contents)} file(s). "
                    f"Please analyze the UPLOADED file content below and answer the user's question based on it.\n\n"
                    + "\n\n".join(file_parts)
                    + f"\n\nUser's message: {user_message}"
                )
            else:
                full_input = user_message

            logger.info(f"get_result: full_input length={len(full_input)}, has_files={file_contents is not None}")
            try:
                response = await openai_client.responses.create(
                    conversation=conversation.id,
                    input=full_input,
                    extra_body={
                            "agent_reference": {
                                "name": agent.name,
                                "type": "agent_reference",
                            }
                        },
                    stream=True
                )
                logger.info("Successfully created stream; starting to process events")
                async for event in response:
                    if event.type == "response.created":
                        logger.info(f"Stream response created with ID: {event.response.id}")
                    elif event.type == "response.output_text.delta":
                        logger.info(f"Delta: {event.delta}")
                        stream_data = {'content': event.delta, 'type': "message"}
                        yield serialize_sse_event(stream_data)
                    elif event.type == "response.output_item.done" and event.item.type == "message":
                        stream_data = await get_message_and_annotations(event.item)
                        stream_data['type'] = "completed_message"
                        yield serialize_sse_event(stream_data)
                    elif event.type == "response.completed":
                        logger.info(f"Response completed with full message: {event.response.output_text}")

            except Exception as e:
                logger.exception(f"Exception in get_result: {e}")
                error_data = {
                    'content': str(e),
                    'annotations': [],
                    'type': "completed_message"
                }
                yield serialize_sse_event(error_data)
            finally:
                stream_data = {'type': "stream_end"}
                await save_user_message_created_at(openai_client, conversation, input_created_at)
                yield serialize_sse_event(stream_data)



@router.get("/chat/history")
async def history(
    request: Request,
    agent: AgentVersionObject = Depends(get_agent_version_obj),
    openai_client : AsyncOpenAI = Depends(get_openai_client),
	_ = auth_dependency
):
    with tracer.start_as_current_span("chat_history"):
        async with openai_client:
            conversation_id = request.cookies.get('conversation_id')
            agent_id = request.cookies.get('agent_id')

            # Get or create conversation using the reusable function
            conversation = await get_or_create_conversation(
                openai_client, conversation_id, agent_id, agent.id
            )
            agent_id = agent.id
            # Create a new message from the user's input.
            try:
                content = []
                items = await openai_client.conversations.items.list(conversation_id=conversation.id, order="desc", limit=16)
                async for item in items:
                    if item.type == "message":
                        formatteded_message = await get_message_and_annotations(item)
                        formatteded_message['role'] = item.role
                        formatteded_message['created_at'] = conversation.metadata.get(get_created_at_label(item.id), "")
                        content.append(formatteded_message)


                logger.info(f"List message, conversation ID: {conversation_id}")
                response = JSONResponse(content=content)

                # Update cookies to persist the conversation IDs.
                response.set_cookie("conversation_id", conversation.id, httponly=True, samesite="strict")
                response.set_cookie("agent_id", agent_id, httponly=True, samesite="strict")
                return response
            except Exception as e:
                logger.error(f"Error listing message: {e}")
                raise HTTPException(status_code=500, detail=f"Error list message: {e}")

@router.get("/agent")
async def get_chat_agent(
    agent: AgentVersionObject = Depends(get_agent_version_obj),
):
    wsid = os.environ.get("AZURE_EXISTING_AIPROJECT_RESOURCE_ID")
    agent_id = os.environ.get("AZURE_EXISTING_AGENT_ID")
    agent_name = agent_id.split(":")[0]
    agent_version = agent_id.split(":")[1]
    agent_playground_url = f"https://ai.azure.com/nextgen/r/{encode_project_resource_id(wsid)}/build/agents/{quote(agent_name)}/build?version={agent_version}"
    return JSONResponse(content={"name": agent.name, "metadata": agent.metadata, "agentPlaygroundUrl": agent_playground_url})


@router.post("/chat")
async def chat(
    request: Request,
    project_client: AIProjectClient = Depends(get_project_client),
    agent: AgentVersionObject = Depends(get_agent_version_obj),
    conversation_mgr = Depends(get_conversation_manager),
	_ = auth_dependency
):
    user_id = get_user_id(request)
    # Retrieve the conversation ID from the cookies (if available).
    conversation_id = request.cookies.get('conversation_id')
    agent_id = request.cookies.get('agent_id')

    carrier = {}
    TraceContextTextMapPropagator().inject(carrier)

    # Determine content type for file upload support
    content_type = request.headers.get("content-type", "")
    user_message_text = ""
    file_contents: List[Dict[str, str]] = []
    uploaded_file_names: List[str] = []

    logger.info(f"POST /chat: content_type={content_type[:80]}")
    if "multipart/form-data" in content_type:
        # Handle file upload via multipart form data
        form = await request.form()
        user_message_text = form.get("message", "")
        files = form.getlist("files")
        logger.info(f"Multipart upload: message='{user_message_text[:50]}', files_count={len(files)}")
        for uploaded_file in files:
            if not isinstance(uploaded_file, UploadFile):
                continue
            # Validate file extension
            filename = uploaded_file.filename or "unknown"
            ext = os.path.splitext(filename)[1].lower()
            if ext not in ALLOWED_EXTENSIONS:
                continue  # Skip unsupported files
            # Read file content (streaming, up to MAX_UPLOAD_SIZE)
            file_data = await uploaded_file.read(MAX_UPLOAD_SIZE + 1)
            if len(file_data) > MAX_UPLOAD_SIZE:
                continue  # Skip files that exceed size limit
            try:
                text_content = file_data.decode("utf-8", errors="replace")
            except Exception:
                text_content = f"[Binary file: {filename}, size: {len(file_data)} bytes]"
            file_contents.append({"name": filename, "content": text_content})
            uploaded_file_names.append(filename)
            logger.info(f"File extracted: {filename}, size={len(file_data)}, text_len={len(text_content)}")
        # Store uploaded files to blob storage if available
        storage_account = os.getenv("AZURE_STORAGE_ACCOUNT_NAME")
        if storage_account and uploaded_file_names:
            try:
                from azure.storage.blob.aio import BlobServiceClient
                from azure.identity.aio import DefaultAzureCredential as AsyncDefaultAzureCredential
                async with AsyncDefaultAzureCredential() as cred:
                    blob_service = BlobServiceClient(
                        account_url=f"https://{storage_account}.blob.core.windows.net",
                        credential=cred,
                    )
                    container_client = blob_service.get_container_client("user-uploads")
                    for uploaded_file in files:
                        if not isinstance(uploaded_file, UploadFile):
                            continue
                        filename = uploaded_file.filename or "unknown"
                        ext = os.path.splitext(filename)[1].lower()
                        if ext not in ALLOWED_EXTENSIONS:
                            continue
                        await uploaded_file.seek(0)
                        blob_data = await uploaded_file.read()
                        if len(blob_data) > MAX_UPLOAD_SIZE:
                            continue
                        blob_name = f"{user_id}/{conversation_id or 'new'}/{uuid.uuid4()}{ext}"
                        blob_client = container_client.get_blob_client(blob_name)
                        await blob_client.upload_blob(blob_data, overwrite=True)
                        logger.info(f"Uploaded file to blob: {blob_name}")
                    await blob_service.close()
            except Exception as e:
                logger.error(f"Error uploading to blob storage: {e}")
    else:
        # Standard JSON body
        try:
            body = await request.json()
            user_message_text = body.get('message', '')
        except Exception as e:
            logger.error(f"Invalid JSON in request: {e}")
            raise HTTPException(status_code=400, detail=f"Invalid JSON in request: {e}")

    with tracer.start_as_current_span("chat_request"):
        async with project_client.get_openai_client() as openai_client:
            # if the connection no longer exist or agent is changed, create a new one
            conversation = await get_or_create_conversation(
                openai_client, conversation_id, agent_id, agent.id
            )
            conversation_id = conversation.id
            agent_id = agent.id

    # Set the Server-Sent Events (SSE) response headers.
    headers = {
        "Cache-Control": "no-cache",
        "Connection": "keep-alive",
        "Content-Type": "text/event-stream"
    }
    logger.info(f"Starting streaming response for conversation ID {conversation_id}")

    # Create the streaming response using the generator.
    response = StreamingResponse(
        get_result(agent, conversation, user_message_text, project_client, carrier, file_contents if file_contents else None),
        headers=headers,
    )

    # Update cookies to persist the conversation and agent IDs.
    response.set_cookie("conversation_id", conversation_id, httponly=True, samesite="strict")
    response.set_cookie("agent_id", agent_id, httponly=True, samesite="strict")

    # Upsert conversation in Table Storage for chat history sidebar
    logger.info(f"Chat history upsert: user_id={user_id}, conversation_id={conversation_id}, conversation_mgr={conversation_mgr is not None}")
    if conversation_mgr:
        try:
            title = user_message_text[:50] if user_message_text else "New conversation"
            preview = user_message_text[:200] if user_message_text else ""
            if uploaded_file_names:
                file_suffix = f" [{', '.join(uploaded_file_names)}]"
                title = (title + file_suffix)[:100]
            await conversation_mgr.upsert_conversation(
                user_id=user_id,
                conversation_id=conversation_id,
                agent_id=agent_id,
                title=title,
                preview=preview,
            )
            logger.info(f"Chat history upsert SUCCESS for conversation {conversation_id}")
        except Exception as e:
            logger.error(f"Chat history upsert FAILED: {e}", exc_info=True)
    else:
        logger.warning("Chat history: conversation_mgr is None, skipping upsert")

    return response


# --- Chat History Sidebar Endpoints ---

@router.get("/conversations")
async def list_conversations(
    request: Request,
    conversation_mgr = Depends(get_conversation_manager),
    _ = auth_dependency,
):
    """List all conversations for the authenticated user."""
    user_id = get_user_id(request)
    logger.info(f"GET /conversations: user_id={user_id}, conversation_mgr={conversation_mgr is not None}")
    if not conversation_mgr:
        logger.warning("GET /conversations: conversation_mgr is None")
        return JSONResponse(content=[])
    try:
        conversations = await conversation_mgr.list_conversations(user_id)
        logger.info(f"GET /conversations: found {len(conversations)} conversations for user {user_id}")
        return JSONResponse(content=conversations)
    except Exception as e:
        logger.error(f"GET /conversations FAILED: {e}", exc_info=True)
        return JSONResponse(content=[])


@router.post("/conversations/{conversation_id}/load")
async def load_conversation(
    request: Request,
    conversation_id: str,
    agent: AgentVersionObject = Depends(get_agent_version_obj),
    openai_client: AsyncOpenAI = Depends(get_openai_client),
    conversation_mgr = Depends(get_conversation_manager),
    _ = auth_dependency,
):
    """Load a specific conversation and set cookies for it."""
    user_id = get_user_id(request)
    # Verify the conversation belongs to the user
    if conversation_mgr:
        conv = await conversation_mgr.get_conversation(user_id, conversation_id)
        if not conv:
            raise HTTPException(status_code=404, detail="Conversation not found")
        agent_id = conv.get("agentId", agent.id)
    else:
        agent_id = agent.id

    # Return the messages from this conversation
    async with openai_client:
        try:
            content = []
            items = await openai_client.conversations.items.list(
                conversation_id=conversation_id, order="desc", limit=16
            )
            conversation = await openai_client.conversations.retrieve(conversation_id=conversation_id)
            async for item in items:
                if item.type == "message":
                    formatted_msg = await get_message_and_annotations(item)
                    formatted_msg["role"] = item.role
                    formatted_msg["created_at"] = conversation.metadata.get(
                        get_created_at_label(item.id), ""
                    ) if conversation.metadata else ""
                    content.append(formatted_msg)

            response = JSONResponse(content=content)
            response.set_cookie("conversation_id", conversation_id, httponly=True, samesite="strict")
            response.set_cookie("agent_id", agent_id, httponly=True, samesite="strict")
            return response
        except Exception as e:
            logger.error(f"Error loading conversation: {e}")
            raise HTTPException(status_code=500, detail=f"Error loading conversation: {e}")


@router.delete("/conversations/{conversation_id}")
async def delete_conversation(
    request: Request,
    conversation_id: str,
    conversation_mgr = Depends(get_conversation_manager),
    _ = auth_dependency,
):
    """Delete a conversation from chat history."""
    user_id = get_user_id(request)
    if not conversation_mgr:
        raise HTTPException(status_code=500, detail="Conversation manager not available")
    success = await conversation_mgr.delete_conversation(user_id, conversation_id)
    if success:
        return JSONResponse(content={"status": "deleted"})
    raise HTTPException(status_code=404, detail="Conversation not found")
