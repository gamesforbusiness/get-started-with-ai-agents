import React from "react";
import { Button, Caption1 } from "@fluentui/react-components";
import { DeleteRegular, DocumentRegular } from "@fluentui/react-icons";

interface FileAttachmentProps {
  file: File;
  onRemove: () => void;
}

function formatFileSize(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}

export const FileAttachment: React.FC<FileAttachmentProps> = ({
  file,
  onRemove,
}) => {
  return (
    <div
      style={{
        display: "inline-flex",
        alignItems: "center",
        gap: "6px",
        padding: "4px 8px",
        borderRadius: "4px",
        backgroundColor: "var(--colorNeutralBackground3)",
        border: "1px solid var(--colorNeutralStroke2)",
        maxWidth: "200px",
      }}
    >
      <DocumentRegular style={{ flexShrink: 0, fontSize: "16px" }} />
      <div
        style={{
          flex: 1,
          minWidth: 0,
          display: "flex",
          flexDirection: "column",
        }}
      >
        <Caption1
          truncate
          wrap={false}
          style={{ fontWeight: 500 }}
        >
          {file.name}
        </Caption1>
        <Caption1
          style={{ fontSize: "10px", color: "var(--colorNeutralForeground3)" }}
        >
          {formatFileSize(file.size)}
        </Caption1>
      </div>
      <Button
        appearance="subtle"
        icon={<DeleteRegular />}
        size="small"
        onClick={onRemove}
        aria-label={`Remove ${file.name}`}
        style={{ flexShrink: 0 }}
      />
    </div>
  );
};
