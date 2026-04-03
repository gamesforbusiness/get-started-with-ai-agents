import React, { useState, useEffect, useRef } from "react";
import {
  ChatInput as ChatInputFluent,
  ImperativeControlPlugin,
  ImperativeControlPluginRef,
} from "@fluentui-copilot/react-copilot";
import { Button } from "@fluentui/react-components";
import { AttachRegular } from "@fluentui/react-icons";
import { ChatInputProps } from "./types";
import { FileAttachment } from "./FileAttachment";

const ALLOWED_EXTENSIONS = [
  ".pdf", ".txt", ".csv", ".md", ".json", ".html", ".docx", ".xlsx",
];
const MAX_FILE_SIZE = 10 * 1024 * 1024; // 10MB

export const ChatInput: React.FC<ChatInputProps> = ({
  onSubmit,
  isGenerating,
  currentUserMessage,
}) => {
  const [inputText, setInputText] = useState<string>("");
  const [attachedFiles, setAttachedFiles] = useState<File[]>([]);
  const controlRef = useRef<ImperativeControlPluginRef>(null);
  const fileInputRef = useRef<HTMLInputElement>(null);

  useEffect(() => {
    if (currentUserMessage !== undefined) {
      controlRef.current?.setInputText(currentUserMessage ?? "");
    }
  }, [currentUserMessage]);

  const onMessageSend = (text: string): void => {
    if ((text && text.trim() !== "") || attachedFiles.length > 0) {
      onSubmit(text.trim(), attachedFiles.length > 0 ? attachedFiles : undefined);
      setInputText("");
      setAttachedFiles([]);
      controlRef.current?.setInputText("");
    }
  };

  const handleFileSelect = (e: React.ChangeEvent<HTMLInputElement>) => {
    const files = Array.from(e.target.files || []);
    const validFiles = files.filter((file) => {
      const ext = "." + file.name.split(".").pop()?.toLowerCase();
      if (!ALLOWED_EXTENSIONS.includes(ext)) {
        console.warn(`Skipping unsupported file type: ${file.name}`);
        return false;
      }
      if (file.size > MAX_FILE_SIZE) {
        console.warn(`Skipping file too large: ${file.name}`);
        return false;
      }
      return true;
    });
    setAttachedFiles((prev) => [...prev, ...validFiles]);
    // Reset the input so the same file can be selected again
    if (fileInputRef.current) {
      fileInputRef.current.value = "";
    }
  };

  const removeFile = (index: number) => {
    setAttachedFiles((prev) => prev.filter((_, i) => i !== index));
  };

  const handleDrop = (e: React.DragEvent) => {
    e.preventDefault();
    const files = Array.from(e.dataTransfer.files);
    const validFiles = files.filter((file) => {
      const ext = "." + file.name.split(".").pop()?.toLowerCase();
      return ALLOWED_EXTENSIONS.includes(ext) && file.size <= MAX_FILE_SIZE;
    });
    setAttachedFiles((prev) => [...prev, ...validFiles]);
  };

  const handleDragOver = (e: React.DragEvent) => {
    e.preventDefault();
  };

  return (
    <div
      onDrop={handleDrop}
      onDragOver={handleDragOver}
      style={{ width: "100%" }}
    >
      {attachedFiles.length > 0 && (
        <div
          style={{
            display: "flex",
            flexWrap: "wrap",
            gap: "6px",
            padding: "8px 12px 0",
          }}
        >
          {attachedFiles.map((file, index) => (
            <FileAttachment
              key={`${file.name}-${index}`}
              file={file}
              onRemove={() => removeFile(index)}
            />
          ))}
        </div>
      )}
      <div style={{ display: "flex", alignItems: "flex-end", gap: "4px" }}>
        <input
          ref={fileInputRef}
          type="file"
          multiple
          accept={ALLOWED_EXTENSIONS.join(",")}
          onChange={handleFileSelect}
          style={{ display: "none" }}
        />
        <Button
          appearance="subtle"
          icon={<AttachRegular />}
          onClick={() => fileInputRef.current?.click()}
          disabled={isGenerating}
          aria-label="Attach file"
          size="medium"
          style={{ flexShrink: 0, marginBottom: "4px" }}
        />
        <div style={{ flex: 1 }}>
          <ChatInputFluent
            aria-label="Chat Input"
            maxLength={200000}
            charactersRemainingMessage={(_value: number) => ``}
            data-testid="chat-input"
            disableSend={isGenerating}
            history={true}
            isSending={isGenerating}
            onChange={(
              _: React.ChangeEvent<HTMLInputElement>,
              d: { value: string }
            ) => {
              setInputText(d.value);
            }}
            onSubmit={() => {
              onMessageSend(inputText ?? "");
            }}
            placeholderValue="Type your message here..."
          >
            <ImperativeControlPlugin ref={controlRef} />
          </ChatInputFluent>
        </div>
      </div>
    </div>
  );
};

export default ChatInput;
