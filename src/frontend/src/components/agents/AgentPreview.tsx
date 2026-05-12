import { ReactNode, useState, useMemo, useEffect, useCallback } from "react";
import {
  Body1,
  Button,
  Caption1,
  Spinner,
  Title3,
} from "@fluentui/react-components";
import {
  ChatRegular,
  MoreHorizontalRegular,
  NavigationRegular,
} from "@fluentui/react-icons";
import clsx from "clsx";

import { AgentIcon } from "./AgentIcon";
import { SettingsPanel } from "../core/SettingsPanel";
import { AgentPreviewChatBot } from "./AgentPreviewChatBot";
import { ChatHistorySidebar } from "./ChatHistorySidebar";
import { MenuButton } from "../core/MenuButton/MenuButton";
import { IChatItem } from "./chatbot/types";
import { Waves } from "./Waves";
import { BuiltWithBadge } from "./BuiltWithBadge";

import styles from "./AgentPreview.module.css";

interface IAgent {
  id: string;
  object: string;
  created_at: number;
  name: string;
  description?: string | null;
  model: string;
  instructions?: string;
  tools?: Array<{ type: string }>;
  top_p?: number;
  temperature?: number;
  tool_resources?: {
    file_search?: {
      vector_store_ids?: string[];
    };
    [key: string]: any;
  };
  metadata?: Record<string, any>;
  response_format?: "auto" | string;
  agentPlaygroundUrl: string;
}

interface IAgentPreviewProps {
  resourceId: string;
  agentDetails: IAgent;
}

interface IAnnotation {
  label: string;
  index: number;
}

const preprocessContent = (
  content: string,
  annotations?: IAnnotation[]
): string => {
  if (!annotations || annotations.length === 0) {
    return content;
  }

  // Process annotations in descending order index, ascending label, remove duplicates
  let processedContent = content;
  annotations
    .slice()
    .sort((a, b) => {
      // Primary sort: descending index
      if (b.index !== a.index) {
        return b.index - a.index;
      }
      // Secondary sort: descending label (as tiebreaker)
      return b.label.localeCompare(a.label);
    })
    .filter((annotation, index, self) =>
      index === self.findIndex(a => a.label === annotation.label && a.index === annotation.index))
    .forEach((annotation) => {
      // Only process if the index is valid and within bounds
      if (annotation.index >= 0 && annotation.index <= processedContent.length) {
        // If there's a label, show it (wrapped in brackets), inserting after the index
        processedContent =
          processedContent.slice(0, annotation.index + 1) +
          ` [${annotation.label}]` +
          processedContent.slice(annotation.index + 1);
      }
    });
  return processedContent;
};

// Parses a Unix-seconds timestamp (string, possibly float) into an ISO string.
// Returns undefined if the input is not a finite number — callers should treat
// that as "no timestamp" rather than feeding a locale-formatted string back
// into `new Date(...)`, which is not portable across browsers/locales.
const parseCreatedAtToISO = (timestampStr: string): string | undefined => {
  if (!timestampStr) return undefined;
  const seconds = parseFloat(timestampStr);
  if (!Number.isFinite(seconds)) return undefined;
  const date = new Date(seconds * 1000);
  return Number.isNaN(date.getTime()) ? undefined : date.toISOString();
};

export function AgentPreview({ agentDetails }: IAgentPreviewProps): ReactNode {
  const [isSettingsPanelOpen, setIsSettingsPanelOpen] = useState(false);
  const [isSidebarOpen, setIsSidebarOpen] = useState(false);
  const [messageList, setMessageList] = useState<IChatItem[]>([]);
  const [isResponding, setIsResponding] = useState(false);
  const [isLoadingChatHistory, setIsLoadingChatHistory] = useState(true);
  const [activeConversationId, setActiveConversationId] = useState<string | undefined>();
  const [sidebarRefreshTrigger, setSidebarRefreshTrigger] = useState(0);

  const loadChatHistory = async () => {
    try {
      const response = await fetch("/chat/history", {
        method: "GET",
        headers: {
          "Content-Type": "application/json",
        },
        credentials: "include",
      });

      if (response.ok) {
        const json_response: Array<{
          role: string;
          content: string;
          created_at: string;
          annotations?: IAnnotation[];
        }> = await response.json();

        // It's generally better to build the new list and set state once
        const historyMessages: IChatItem[] = [];
        const reversedResponse = [...json_response].reverse();

        for (const entry of reversedResponse) {
          const timeISO = parseCreatedAtToISO(entry.created_at);

          if (entry.role === "user") {
            historyMessages.push({
              id: crypto.randomUUID(),
              content: entry.content,
              role: "user",
              more: { time: timeISO },
            });
          } else {
            historyMessages.push({
              id: `assistant-hist-${Date.now()}-${Math.random()}`, // Ensure unique ID
              content: preprocessContent(entry.content, entry.annotations),
              role: "assistant", // Assuming 'assistant' role for non-user
              isAnswer: true, // Assuming this property for assistant messages
              more: { time: timeISO },
            });
          }
        }
        setMessageList((prev) => [...historyMessages, ...prev]); // Prepend history
      } else {
        // For error messages, add directly to messageList without preprocessing
        const errorMessage: IChatItem = {
          id: crypto.randomUUID(),
          content: "Error occurs while loading chat history!",
          isAnswer: true,
          more: { time: new Date().toISOString() },
        };
        setMessageList(prev => [...prev, errorMessage]);
      }
      setIsLoadingChatHistory(false);
    } catch (error) {
      console.error("Failed to load chat history:", error);
      // For error messages, add directly to messageList without preprocessing
      const errorMessage: IChatItem = {
        id: crypto.randomUUID(),
        content: "Error occurs while loading chat history!",
        isAnswer: true,
        more: { time: new Date().toISOString() },
      };
      setMessageList(prev => [...prev, errorMessage]);
      setIsLoadingChatHistory(false);
    }
  };

  useEffect(() => {
    loadChatHistory();
  }, []);

  const handleSettingsPanelOpenChange = (isOpen: boolean) => {
    setIsSettingsPanelOpen(isOpen);
  };

  const newThread = async () => {
    setMessageList([]);
    setActiveConversationId(undefined);
    // Call backend to clear httponly cookies
    try {
      await fetch("/chat/new", {
        method: "POST",
        credentials: "include",
      });
    } catch (e) {
      console.error("Failed to clear session:", e);
    }
    setSidebarRefreshTrigger((prev) => prev + 1);
  };

  const handleSelectConversation = useCallback(async (conversationId: string) => {
    setIsLoadingChatHistory(true);
    setMessageList([]);
    setActiveConversationId(conversationId);
    try {
      const response = await fetch(`/conversations/${conversationId}/load`, {
        method: "POST",
        credentials: "include",
      });
      if (response.ok) {
        const json_response: Array<{
          role: string;
          content: string;
          created_at: string;
          annotations?: IAnnotation[];
        }> = await response.json();

        const historyMessages: IChatItem[] = [];
        const reversedResponse = [...json_response].reverse();

        for (const entry of reversedResponse) {
          const timeISO = parseCreatedAtToISO(entry.created_at);
          if (entry.role === "user") {
            historyMessages.push({
              id: crypto.randomUUID(),
              content: entry.content,
              role: "user",
              more: { time: timeISO },
            });
          } else {
            historyMessages.push({
              id: `assistant-hist-${Date.now()}-${Math.random()}`,
              content: preprocessContent(entry.content, entry.annotations),
              role: "assistant",
              isAnswer: true,
              more: { time: timeISO },
            });
          }
        }
        setMessageList(historyMessages);
      }
    } catch (error) {
      console.error("Failed to load conversation:", error);
    } finally {
      setIsLoadingChatHistory(false);
    }
  }, []);

  const handleDeleteConversation = useCallback(async (conversationId: string) => {
    if (conversationId === activeConversationId) {
      setMessageList([]);
      setActiveConversationId(undefined);
      try {
        await fetch("/chat/new", { method: "POST", credentials: "include" });
      } catch (e) {
        console.error("Failed to clear session:", e);
      }
    }
  }, [activeConversationId]);

  const onSend = async (message: string, files?: File[]) => {
    const fileNames = files?.map((f) => f.name) || [];
    const displayContent = fileNames.length > 0
      ? `${message}\n\n[Files: ${fileNames.join(", ")}]`
      : message;

    const userMessage: IChatItem = {
      id: `user-${Date.now()}`,
      content: displayContent,
      role: "user",
      more: { time: new Date().toISOString() },
    };

    setMessageList((prev) => [...prev, userMessage]);

    try {
      setIsResponding(true);

      let response: Response;
      if (files && files.length > 0) {
        // Multipart form data for file upload
        const formData = new FormData();
        formData.append("message", message);
        for (const file of files) {
          formData.append("files", file);
        }
        response = await fetch("/chat", {
          method: "POST",
          body: formData,
          credentials: "include",
        });
      } else {
        // Standard JSON
        response = await fetch("/chat", {
          method: "POST",
          headers: {
            "Content-Type": "application/json",
          },
          body: JSON.stringify({ message }),
          credentials: "include",
        });
      }

      console.log(
        "[ChatClient] Response status:",
        response.status,
        response.statusText
      );

      if (!response.ok) {
        console.error(
          "[ChatClient] Response not OK:",
          response.status,
          response.statusText
        );
        setIsResponding(false);
        return;
      }

      if (!response.body) {
        throw new Error(
          "ReadableStream not supported or response.body is null"
        );
      }

      console.log("[ChatClient] Starting to handle streaming response...");
      handleMessages(response.body);

      // Refresh sidebar after sending
      setSidebarRefreshTrigger((prev) => prev + 1);
    } catch (error: any) {
      setIsResponding(false);
      if (error.name === "AbortError") {
        console.log("[ChatClient] Fetch request aborted by user.");
      } else {
        console.error("[ChatClient] Fetch failed:", error);
      }
    }
  };

  const handleMessages = (
    stream: ReadableStream<Uint8Array<ArrayBufferLike>>
  ) => {
    let chatItem: IChatItem | null = null;
    let accumulatedContent = "";
    let isStreaming = true;
    let buffer = "";
    let annotations: IAnnotation[] = [];
    let hasReceivedCompletedMessage = false;

    // Create a reader for the SSE stream
    const reader = stream.getReader();
    const decoder = new TextDecoder();

    const readStream = async () => {
      while (true) {
        const { done, value } = await reader.read();
        if (done) {
          console.log("[ChatClient] SSE stream ended by server.");
          break;
        }

        // Convert the incoming Uint8Array to text
        const textChunk = decoder.decode(value, { stream: true });
        console.log("[ChatClient] Raw chunk from stream:", textChunk);

        buffer += textChunk;
        let boundary = buffer.indexOf("\n");

        // We process line-by-line.
        while (boundary !== -1) {
          const chunk = buffer.slice(0, boundary).trim();
          buffer = buffer.slice(boundary + 1);

          console.log("[ChatClient] SSE line:", chunk); // log each line we extract

          if (chunk.startsWith("data: ")) {
            // Attempt to parse JSON
            const jsonStr = chunk.slice(6);
            let data;
            try {
              data = JSON.parse(jsonStr);
            } catch (err) {
              console.error("[ChatClient] Failed to parse JSON:", jsonStr, err);
              boundary = buffer.indexOf("\n");
              continue;
            }

            console.log("[ChatClient] Parsed SSE event:", data);

            // Check the data type to decide how to update the UI
            if (data.type === "stream_end") {
              // End of the stream
              console.log("[ChatClient] Stream end marker received.");
              setIsResponding(false);
              break;
            } else if (data.type === "thread_run") {
              // Log the run status info
              console.log("[ChatClient] Run status info:", data.content);
            } else {
              // If we have no messageDiv yet, create one
              if (!chatItem) {
                chatItem = createAssistantMessageDiv();
                console.log(
                  "[ChatClient] Created new messageDiv for assistant."
                );
              }

              if (data.type === "completed_message") {
                // Each completed_message should get its own balloon
                if (hasReceivedCompletedMessage) {
                  // We've already processed a completed message, so create a new balloon for this one
                  chatItem = createAssistantMessageDiv();
                  console.log(
                    "[ChatClient] Created new messageDiv for additional completed message."
                  );

                  // Reset for the new message
                  accumulatedContent = data.content;
                  annotations = data.annotations || [];
                } else {
                  // First completed message in this stream
                  clearAssistantMessage(chatItem);
                  accumulatedContent = data.content;
                  annotations = data.annotations || [];
                  hasReceivedCompletedMessage = true;
                }

                console.log(
                  "[ChatClient] Received completed message:",
                  accumulatedContent
                );

                isStreaming = false;
                setIsResponding(false);
              } else {
                // Handle streaming content
                if (hasReceivedCompletedMessage) {
                  // We've had a completed message before, so this is new streaming content
                  // Create a new balloon for the new streaming content
                  chatItem = createAssistantMessageDiv();
                  console.log(
                    "[ChatClient] Created new messageDiv for streaming after completed message."
                  );

                  // Reset for new streaming content
                  annotations = [];
                  accumulatedContent = "";
                  hasReceivedCompletedMessage = false; // Reset for this new cycle
                }
                accumulatedContent += data.content;
                isStreaming = true;

                console.log(
                  "[ChatClient] Received streaming chunk:",
                  data.content
                );
              }

              // Update the UI with the accumulated content
              appendAssistantMessage(
                chatItem,
                accumulatedContent,
                isStreaming,
                annotations
              );
            }
          }

          boundary = buffer.indexOf("\n");
        }
      }
    };

    // Catch errors from the stream reading process
    readStream().catch((error) => {
      console.error("[ChatClient] Stream reading failed:", error);
    });
  };

  const createAssistantMessageDiv: () => IChatItem = () => {
    var item = {
      id: crypto.randomUUID(),
      content: "",
      isAnswer: true,
      more: { time: new Date().toISOString() },
    };
    setMessageList((prev) => [...prev, item]);
    return item;
  };
  const appendAssistantMessage = (
    chatItem: IChatItem,
    accumulatedContent: string,
    isStreaming: boolean,
    annotations?: IAnnotation[]
  ) => {
    try {
      // Preprocess content to convert citations to links using the updated annotation data
      // Convert the accumulated content to HTML using markdown-it
      const preprocessedContent = preprocessContent(
        accumulatedContent,
        annotations
      );
      let htmlContent = preprocessedContent;
      if (!chatItem) {
        throw new Error("Message content div not found in the template.");
      }

      // Set the innerHTML of the message text div to the HTML content
      chatItem.content = htmlContent;
      setMessageList((prev) => {
        return [...prev.slice(0, -1), { ...chatItem }];
      });

      // Use requestAnimationFrame to ensure the DOM has updated before scrolling
      // Only scroll if stop streaming
      if (!isStreaming) {
        requestAnimationFrame(() => {
          const lastChild = document.getElementById(`msg-${chatItem.id}`);
          if (lastChild) {
            lastChild.scrollIntoView({ behavior: "smooth", block: "end" });
          }
        });
      }
    } catch (error) {
      console.error("Error in appendAssistantMessage:", error);
    }
  };

  const clearAssistantMessage = (chatItem: IChatItem) => {
    if (chatItem) {
      chatItem.content = "";
    }
  };
  const menuItems = [
    {
      key: "settings",
      children: "Settings",
      onClick: () => {
        setIsSettingsPanelOpen(true);
      },
    },
    {
      key: "terms",
      children: (
        <a
          className={styles.externalLink}
          href="https://aka.ms/aistudio/terms"
          target="_blank"
          rel="noopener noreferrer"
        >
          Terms of Use
        </a>
      ),
    },
    {
      key: "privacy",
      children: (
        <a
          className={styles.externalLink}
          href="https://go.microsoft.com/fwlink/?linkid=521839"
          target="_blank"
          rel="noopener noreferrer"
        >
          Privacy
        </a>
      ),
    },
    {
      key: "feedback",
      children: "Send Feedback",
      onClick: () => {
        // Handle send feedback click
        alert("Thank you for your feedback!");
      },
    },
  ];
  const chatContext = useMemo(
    () => ({
      messageList,
      isResponding,
      onSend,
    }),
    [messageList, isResponding]
  );
  const isEmpty = (messageList?.length ?? 0) === 0;

  return (
    <div className={styles.container}>
      <div className={styles.wavesContainer}>
        <Waves paused={!isEmpty} />
      </div>

      {/* Chat History Sidebar */}
      <ChatHistorySidebar
        isOpen={isSidebarOpen}
        activeConversationId={activeConversationId}
        onSelectConversation={handleSelectConversation}
        onDeleteConversation={handleDeleteConversation}
        refreshTrigger={sidebarRefreshTrigger}
      />

      <div className={styles.mainArea}>
        <div className={styles.topBar}>
          <div className={styles.leftSection}>
            <Button
              appearance="subtle"
              icon={<NavigationRegular />}
              onClick={() => setIsSidebarOpen(!isSidebarOpen)}
              aria-label="Toggle chat history"
            />
            {agentDetails.name ? (
              <div className={styles.agentIconContainer}>
                <AgentIcon
                  alt=""
                  iconClassName={styles.agentIcon}
                  iconName={agentDetails.metadata?.logo}
                />
                <Body1 as="h1" className={styles.agentName}>
                  {agentDetails.name}
                </Body1>
              </div>
            ) : (
              <div className={styles.agentIconContainer}>
                <div
                  className={clsx(styles.agentIcon, {
                    [styles.newAgent]: true,
                  })}
                />
                <Body1
                  as="h1"
                  className={clsx(styles.agentName, {
                    [styles.newAgent]: true,
                  })}
                >
                  Agent Name
                </Body1>
              </div>
            )}
          </div>
          <div className={styles.rightSection}>
            <Button
              appearance="subtle"
              icon={<ChatRegular aria-hidden={true} />}
              onClick={newThread}
            >
              New Chat
            </Button>
            <MenuButton
              menuButtonText=""
              menuItems={menuItems}
              menuButtonProps={{
                appearance: "subtle",
                icon: <MoreHorizontalRegular />,
                "aria-label": "Settings",
              }}
            />
          </div>
        </div>

        <div className={styles.content}>
          <div className={styles.chatbot}>
            {isLoadingChatHistory ? (
              <Spinner label={"Loading chat history..."} />
            ) : (
              <>
                {isEmpty && (
                  <div className={styles.emptyChatContainer}>
                    <AgentIcon
                      alt=""
                      iconClassName={styles.emptyStateAgentIcon}
                      iconName={agentDetails.metadata?.logo}
                    />
                    <Caption1 className={styles.agentName}>
                      {agentDetails.name}
                    </Caption1>
                    <Title3>How can I help you today?</Title3>
                  </div>
                )}
                <AgentPreviewChatBot
                  agentName={agentDetails.name}
                  agentLogo={agentDetails.metadata?.logo}
                  chatContext={chatContext}
                />
              </>
            )}
          </div>

          {agentDetails.agentPlaygroundUrl && agentDetails.agentPlaygroundUrl.length > 0 ? (
            <BuiltWithBadge className={styles.builtWithBadge} agentPlaygroundUrl={agentDetails.agentPlaygroundUrl} />
          ) : (
            <></>
          )}
        </div>
      </div>

      {/* Settings Panel */}
      <SettingsPanel
        isOpen={isSettingsPanelOpen}
        onOpenChange={handleSettingsPanelOpenChange}
      />
    </div>
  );
}
