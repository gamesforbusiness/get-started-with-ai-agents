import React, { useState, useEffect, useCallback } from "react";
import {
  Body1,
  Button,
  Caption1,
  Spinner,
  Text,
  Subtitle2,
} from "@fluentui/react-components";
import {
  DeleteRegular,
  ChatRegular,
} from "@fluentui/react-icons";

import styles from "./ChatHistorySidebar.module.css";

interface ConversationItem {
  id: string;
  title: string;
  preview: string;
  messageCount: number;
  createdAt: string;
  updatedAt: string;
}

interface ChatHistorySidebarProps {
  isOpen: boolean;
  activeConversationId?: string;
  onSelectConversation: (conversationId: string) => void;
  onDeleteConversation: (conversationId: string) => void;
  refreshTrigger: number;
}

function groupByDate(conversations: ConversationItem[]) {
  const now = new Date();
  const today = new Date(now.getFullYear(), now.getMonth(), now.getDate());
  const yesterday = new Date(today.getTime() - 86400000);
  const weekAgo = new Date(today.getTime() - 7 * 86400000);

  const groups: { label: string; items: ConversationItem[] }[] = [
    { label: "Today", items: [] },
    { label: "Yesterday", items: [] },
    { label: "Last 7 days", items: [] },
    { label: "Older", items: [] },
  ];

  for (const conv of conversations) {
    const date = new Date(conv.updatedAt);
    if (date >= today) {
      groups[0].items.push(conv);
    } else if (date >= yesterday) {
      groups[1].items.push(conv);
    } else if (date >= weekAgo) {
      groups[2].items.push(conv);
    } else {
      groups[3].items.push(conv);
    }
  }

  return groups.filter((g) => g.items.length > 0);
}

export function ChatHistorySidebar({
  isOpen,
  activeConversationId,
  onSelectConversation,
  onDeleteConversation,
  refreshTrigger,
}: ChatHistorySidebarProps): React.JSX.Element | null {
  const [conversations, setConversations] = useState<ConversationItem[]>([]);
  const [isLoading, setIsLoading] = useState(true);

  const fetchConversations = useCallback(async () => {
    try {
      const response = await fetch("/conversations", {
        credentials: "include",
      });
      if (response.ok) {
        const data = await response.json();
        setConversations(data);
      }
    } catch (error) {
      console.error("Failed to fetch conversations:", error);
    } finally {
      setIsLoading(false);
    }
  }, []);

  useEffect(() => {
    if (isOpen) {
      fetchConversations();
    }
  }, [isOpen, refreshTrigger, fetchConversations]);

  const handleDelete = async (
    e: React.MouseEvent,
    conversationId: string
  ) => {
    e.stopPropagation();
    try {
      const response = await fetch(`/conversations/${conversationId}`, {
        method: "DELETE",
        credentials: "include",
      });
      if (response.ok) {
        setConversations((prev) =>
          prev.filter((c) => c.id !== conversationId)
        );
        onDeleteConversation(conversationId);
      }
    } catch (error) {
      console.error("Failed to delete conversation:", error);
    }
  };

  if (!isOpen) return null;

  const groups = groupByDate(conversations);

  return (
    <div className={styles.sidebar}>
      <div className={styles.header}>
        <Subtitle2>Chat History</Subtitle2>
      </div>
      <div className={styles.conversationList}>
        {isLoading ? (
          <div className={styles.loadingContainer}>
            <Spinner size="small" />
          </div>
        ) : conversations.length === 0 ? (
          <div className={styles.emptyState}>
            <ChatRegular className={styles.emptyIcon} />
            <Caption1>No conversations yet</Caption1>
          </div>
        ) : (
          groups.map((group) => (
            <div key={group.label} className={styles.group}>
              <Caption1 className={styles.groupLabel}>
                {group.label}
              </Caption1>
              {group.items.map((conv) => (
                <div
                  key={conv.id}
                  className={`${styles.conversationItem} ${
                    conv.id === activeConversationId
                      ? styles.activeItem
                      : ""
                  }`}
                  onClick={() => onSelectConversation(conv.id)}
                  role="button"
                  tabIndex={0}
                  onKeyDown={(e) => {
                    if (e.key === "Enter") onSelectConversation(conv.id);
                  }}
                >
                  <div className={styles.conversationContent}>
                    <Text
                      className={styles.conversationTitle}
                      truncate
                      wrap={false}
                    >
                      {conv.title || "Untitled conversation"}
                    </Text>
                    {conv.preview && (
                      <Caption1
                        className={styles.conversationPreview}
                        truncate
                        wrap={false}
                      >
                        {conv.preview}
                      </Caption1>
                    )}
                  </div>
                  <Button
                    className={styles.deleteButton}
                    appearance="subtle"
                    icon={<DeleteRegular />}
                    size="small"
                    onClick={(e) => handleDelete(e, conv.id)}
                    aria-label="Delete conversation"
                  />
                </div>
              ))}
            </div>
          ))
        )}
      </div>
    </div>
  );
}
