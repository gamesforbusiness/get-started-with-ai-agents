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
from starlette.datastructures import UploadFile as StarletteUploadFile
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

# File extensions that need Azure Document Intelligence for text extraction
DOCUMENT_INTELLIGENCE_EXTENSIONS = {".pdf", ".docx", ".xlsx", ".pptx", ".png", ".jpg", ".jpeg", ".tiff", ".bmp"}
# File extensions that are plain text
PLAIN_TEXT_EXTENSIONS = {".txt", ".csv", ".md", ".json", ".html", ".xml", ".log", ".yaml", ".yml"}

async def extract_text_from_file(filename: str, file_data: bytes) -> str:
    """Extract maximum content from a file.

    Uses Azure Document Intelligence with markdown output for rich documents
    (preserves tables, headers, structure). Falls back to pypdf for PDF,
    direct decode for text files.
    """
    ext = os.path.splitext(filename)[1].lower()

    # Plain text files — decode directly, no processing needed
    if ext in PLAIN_TEXT_EXTENSIONS:
        text = file_data.decode("utf-8", errors="replace")
        logger.info(f"Plain text extracted: {filename}, {len(text)} chars")
        return text

    # Complex documents — Azure Document Intelligence (markdown output for max structure)
    if ext in DOCUMENT_INTELLIGENCE_EXTENSIONS:
        di_endpoint = os.getenv("AZURE_DOCUMENT_INTELLIGENCE_ENDPOINT", "")
        if di_endpoint:
            try:
                from azure.ai.documentintelligence.aio import DocumentIntelligenceClient
                from azure.ai.documentintelligence.models import AnalyzeDocumentRequest, DocumentAnalysisFeature, AnalyzeOutputOption
                from azure.identity.aio import DefaultAzureCredential as AsyncDefaultAzureCredential

                async with AsyncDefaultAzureCredential() as cred:
                    client = DocumentIntelligenceClient(endpoint=di_endpoint, credential=cred)
                    poller = await client.begin_analyze_document(
                        "prebuilt-layout",
                        body=AnalyzeDocumentRequest(bytes_source=file_data),
                        output_content_format="markdown",
                        features=[
                            DocumentAnalysisFeature.KEY_VALUE_PAIRS,
                            DocumentAnalysisFeature.LANGUAGES,
                        ],
                    )
                    result = await poller.result()

                    # Build rich output: markdown content + key-value pairs + tables metadata
                    parts = []

                    # Main content in markdown (tables, headers, paragraphs preserved)
                    if result.content:
                        parts.append(result.content)

                    # Extract key-value pairs (form fields, labels)
                    if result.key_value_pairs:
                        kv_lines = ["\n## Extracted Key-Value Pairs:"]
                        for kv in result.key_value_pairs:
                            key = kv.key.content if kv.key else ""
                            value = kv.value.content if kv.value else ""
                            confidence = kv.confidence or 0
                            if key and confidence > 0.5:
                                kv_lines.append(f"- **{key}**: {value}")
                        if len(kv_lines) > 1:
                            parts.append("\n".join(kv_lines))

                    # Page-level info
                    if result.pages:
                        page_info = f"\n[Document: {len(result.pages)} page(s)"
                        if result.languages:
                            langs = [l.locale for l in result.languages[:3]]
                            page_info += f", language(s): {', '.join(langs)}"
                        page_info += "]"
                        parts.append(page_info)

                    text = "\n\n".join(parts)
                    logger.info(f"Document Intelligence extracted: {filename}, {len(text)} chars, {len(result.pages or [])} pages")
                    await client.close()
                    return text
            except Exception as e:
                logger.error(f"Document Intelligence failed for {filename}: {e}")
                if ext == ".pdf":
                    return _extract_pdf_fallback(filename, file_data)
                return f"[Could not extract text from {filename}: {e}]"
        else:
            if ext == ".pdf":
                return _extract_pdf_fallback(filename, file_data)
            logger.warning(f"No AZURE_DOCUMENT_INTELLIGENCE_ENDPOINT set, cannot extract {filename}")
            return f"[Cannot extract text from {filename} — configure AZURE_DOCUMENT_INTELLIGENCE_ENDPOINT for {ext} support]"

    # Unknown extension — try text decode
    try:
        text = file_data.decode("utf-8", errors="replace")
        logger.info(f"Fallback text decode: {filename}, {len(text)} chars")
        return text
    except Exception:
        return f"[Binary file: {filename}, {len(file_data)} bytes]"


def _extract_pdf_fallback(filename: str, file_data: bytes) -> str:
    """Fallback PDF extraction using pypdf — gets text but loses table structure."""
    try:
        import io
        from pypdf import PdfReader
        reader = PdfReader(io.BytesIO(file_data))
        pages = []
        for i, page in enumerate(reader.pages):
            page_text = page.extract_text() or ""
            if page_text.strip():
                pages.append(f"--- Page {i+1} ---\n{page_text}")
        text = "\n\n".join(pages)
        logger.info(f"pypdf fallback extracted: {filename}, {len(text)} chars, {len(reader.pages)} pages")
        return text
    except Exception as e:
        logger.error(f"pypdf fallback failed for {filename}: {e}")
        return f"[Could not extract text from PDF: {filename}]"


ALLOWED_UPLOAD_TYPES = {
    "application/pdf", "text/plain", "text/csv", "text/markdown",
    "application/json", "text/html",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
}
ALLOWED_EXTENSIONS = {
    ".pdf", ".docx", ".xlsx", ".pptx",  # Document Intelligence
    ".png", ".jpg", ".jpeg", ".tiff", ".bmp",  # Images (Document Intelligence OCR)
    ".txt", ".csv", ".md", ".json", ".html", ".xml", ".log", ".yaml", ".yml",  # Plain text
}
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
    uploaded_files: Optional[List[tuple]] = None,
) -> AsyncGenerator[str, None]:
    ctx = TraceContextTextMapPropagator().extract(carrier=carrier)
    with tracer.start_as_current_span('get_result', context=ctx):
        async with project_client.get_openai_client() as openai_client:
            logger.info(f"get_result invoked for conversation={conversation.id}")
            input_created_at = datetime.now(timezone.utc).timestamp()

            # Build input: extract text from files and inject into message
            if uploaded_files:
                file_parts = []
                for filename, file_data in uploaded_files:
                    text_content = await extract_text_from_file(filename, file_data)
                    file_parts.append(
                        f"=== UPLOADED FILE: {filename} ===\n{text_content}\n=== END OF FILE ==="
                    )

                file_list = ", ".join(f[0] for f in uploaded_files)
                input_content = (
                    f"[SYSTEM: The user has uploaded {len(uploaded_files)} file(s): {file_list}. "
                    f"The full extracted content of each file is provided below. "
                    f"Use this content as your PRIMARY source to answer the user's question. "
                    f"Reference specific parts of the uploaded file(s) in your answer.]\n\n"
                    + "\n\n".join(file_parts)
                    + f"\n\n---\nUser's message: {user_message}"
                )
                logger.info(f"get_result: message with {len(uploaded_files)} file(s) injected, total length={len(input_content)}")
            else:
                input_content = user_message
                logger.info(f"get_result: text-only message, length={len(user_message)}")

            try:
                response = await openai_client.responses.create(
                    conversation=conversation.id,
                    input=input_content,
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
    uploaded_files: List[tuple] = []  # List of (filename, bytes)
    uploaded_file_names: List[str] = []

    logger.info(f"POST /chat: content_type={content_type[:80]}")
    if "multipart/form-data" in content_type:
        # Handle file upload via multipart form data
        form = await request.form()
        user_message_text = form.get("message", "")
        files = form.getlist("files")
        logger.info(f"Multipart upload: message='{user_message_text[:50]}', files_count={len(files)}")
        for i, uploaded_file in enumerate(files):
            if not isinstance(uploaded_file, (UploadFile, StarletteUploadFile)):
                logger.warning(f"File {i} skipped: not UploadFile, type={type(uploaded_file)}")
                continue
            filename = uploaded_file.filename or "unknown"
            ext = os.path.splitext(filename)[1].lower()
            if ext not in ALLOWED_EXTENSIONS:
                logger.warning(f"File {i} skipped: extension '{ext}' not allowed")
                continue
            file_data = await uploaded_file.read(MAX_UPLOAD_SIZE + 1)
            if len(file_data) > MAX_UPLOAD_SIZE:
                logger.warning(f"File {i} skipped: too large ({len(file_data)} bytes)")
                continue
            uploaded_files.append((filename, file_data))
            uploaded_file_names.append(filename)
            logger.info(f"File {i} accepted: {filename}, {len(file_data)} bytes")
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
        get_result(agent, conversation, user_message_text, project_client, carrier, uploaded_files if uploaded_files else None),
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


# --- Session Management ---

@router.post("/chat/new")
async def new_chat(
    request: Request,
    _ = auth_dependency,
):
    """Clear conversation cookies to start a new chat."""
    response = JSONResponse(content={"status": "new_chat"})
    response.delete_cookie("conversation_id", path="/")
    response.delete_cookie("agent_id", path="/")
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
