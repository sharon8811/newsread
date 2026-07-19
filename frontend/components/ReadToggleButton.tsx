import type { Article } from "@/lib/api";
import { CheckIcon } from "./icons";

export default function ReadToggleButton({
  article,
  onToggle,
  size = 15,
  className = "",
}: {
  article: Article;
  onToggle: (article: Article) => void;
  size?: number;
  className?: string;
}) {
  return (
    <button
      className={`icon-btn ${className} ${article.is_read ? "active" : ""}`}
      title={article.is_read ? "Mark as unread" : "Mark as read"}
      onClick={(e) => {
        e.stopPropagation();
        onToggle(article);
      }}
    >
      <CheckIcon size={size} />
    </button>
  );
}
