type IconProps = { size?: number; className?: string };

function base(props: IconProps) {
  return {
    width: props.size ?? 16,
    height: props.size ?? 16,
    viewBox: "0 0 24 24",
    fill: "none",
    stroke: "currentColor",
    strokeWidth: 1.8,
    strokeLinecap: "round" as const,
    strokeLinejoin: "round" as const,
    className: props.className,
    "aria-hidden": true,
  };
}

export function GearIcon(p: IconProps) {
  return (
    <svg {...base(p)}>
      <circle cx="12" cy="12" r="3" />
      <path d="M19.4 15a1.65 1.65 0 0 0 .33 1.82l.06.06a2 2 0 1 1-2.83 2.83l-.06-.06a1.65 1.65 0 0 0-1.82-.33 1.65 1.65 0 0 0-1 1.51V21a2 2 0 1 1-4 0v-.09a1.65 1.65 0 0 0-1-1.51 1.65 1.65 0 0 0-1.82.33l-.06.06a2 2 0 1 1-2.83-2.83l.06-.06a1.65 1.65 0 0 0 .33-1.82 1.65 1.65 0 0 0-1.51-1H3a2 2 0 1 1 0-4h.09a1.65 1.65 0 0 0 1.51-1 1.65 1.65 0 0 0-.33-1.82l-.06-.06a2 2 0 1 1 2.83-2.83l.06.06a1.65 1.65 0 0 0 1.82.33h.01a1.65 1.65 0 0 0 1-1.51V3a2 2 0 1 1 4 0v.09a1.65 1.65 0 0 0 1 1.51h.01a1.65 1.65 0 0 0 1.82-.33l.06-.06a2 2 0 1 1 2.83 2.83l-.06.06a1.65 1.65 0 0 0-.33 1.82v.01a1.65 1.65 0 0 0 1.51 1H21a2 2 0 1 1 0 4h-.09a1.65 1.65 0 0 0-1.51 1z" />
    </svg>
  );
}

export function FolderIcon(p: IconProps) {
  return (
    <svg {...base(p)}>
      <path d="M22 19a2 2 0 0 1-2 2H4a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h5l2 3h9a2 2 0 0 1 2 2z" />
    </svg>
  );
}

export function LockIcon(p: IconProps) {
  return (
    <svg {...base(p)}>
      <rect x="3" y="11" width="18" height="11" rx="2" ry="2" />
      <path d="M7 11V7a5 5 0 0 1 10 0v4" />
    </svg>
  );
}

export function EyeOffIcon(p: IconProps) {
  return (
    <svg {...base(p)}>
      <path d="M9.88 9.88a3 3 0 1 0 4.24 4.24" />
      <path d="M10.73 5.08A10.43 10.43 0 0 1 12 5c7 0 10 7 10 7a13.16 13.16 0 0 1-1.67 2.68" />
      <path d="M6.61 6.61A13.526 13.526 0 0 0 2 12s3 7 10 7a9.74 9.74 0 0 0 5.39-1.61" />
      <path d="m2 2 20 20" />
    </svg>
  );
}

export function MuteIcon(p: IconProps) {
  return (
    <svg {...base(p)}>
      <path d="M13.73 21a2 2 0 0 1-3.46 0" />
      <path d="M18.63 13A17.89 17.89 0 0 1 18 8" />
      <path d="M6.26 6.26A5.86 5.86 0 0 0 6 8c0 7-3 9-3 9h14" />
      <path d="M18 8a6 6 0 0 0-9.33-5" />
      <path d="m1 1 22 22" />
    </svg>
  );
}

export function InboxIcon(p: IconProps) {
  return (
    <svg {...base(p)}>
      <path d="M22 12h-6l-2 3h-4l-2-3H2" />
      <path d="M5.45 5.11 2 12v6a2 2 0 0 0 2 2h16a2 2 0 0 0 2-2v-6l-3.45-6.89A2 2 0 0 0 16.76 4H7.24a2 2 0 0 0-1.79 1.11z" />
    </svg>
  );
}

export function BookmarkIcon(p: IconProps & { filled?: boolean }) {
  return (
    <svg {...base(p)} fill={p.filled ? "currentColor" : "none"}>
      <path d="M19 21l-7-5-7 5V5a2 2 0 0 1 2-2h10a2 2 0 0 1 2 2z" />
    </svg>
  );
}

export function ShareIcon(p: IconProps) {
  return (
    <svg {...base(p)}>
      <path d="M22 2 11 13" />
      <path d="M22 2 15 22l-4-9-9-4z" />
    </svg>
  );
}

export function UsersIcon(p: IconProps) {
  return (
    <svg {...base(p)}>
      <path d="M16 21v-2a4 4 0 0 0-4-4H6a4 4 0 0 0-4 4v2" />
      <circle cx="9" cy="7" r="4" />
      <path d="M22 21v-2a4 4 0 0 0-3-3.87" />
      <path d="M16 3.13a4 4 0 0 1 0 7.75" />
    </svg>
  );
}

export function ExternalIcon(p: IconProps) {
  return (
    <svg {...base(p)}>
      <path d="M18 13v6a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V8a2 2 0 0 1 2-2h6" />
      <path d="M15 3h6v6" />
      <path d="M10 14 21 3" />
    </svg>
  );
}

export function CommentIcon(p: IconProps) {
  return (
    <svg {...base(p)}>
      <path d="M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z" />
    </svg>
  );
}

export function PlusIcon(p: IconProps) {
  return (
    <svg {...base(p)}>
      <path d="M12 5v14" />
      <path d="M5 12h14" />
    </svg>
  );
}

export function RefreshIcon(p: IconProps) {
  return (
    <svg {...base(p)}>
      <path d="M21 12a9 9 0 1 1-2.64-6.36" />
      <path d="M21 3v6h-6" />
    </svg>
  );
}

export function CheckIcon(p: IconProps) {
  return (
    <svg {...base(p)}>
      <path d="M20 6 9 17l-5-5" />
    </svg>
  );
}

export function CheckAllIcon(p: IconProps) {
  return (
    <svg {...base(p)}>
      <path d="M18 6 7 17l-4-4" />
      <path d="m22 10-7.5 7.5L13 16" />
    </svg>
  );
}

export function XIcon(p: IconProps) {
  return (
    <svg {...base(p)}>
      <path d="M18 6 6 18" />
      <path d="M6 6l12 12" />
    </svg>
  );
}

export function LinkIcon(p: IconProps) {
  return (
    <svg {...base(p)}>
      <path d="M10 13a5 5 0 0 0 7.54.54l3-3a5 5 0 0 0-7.07-7.07l-1.72 1.71" />
      <path d="M14 11a5 5 0 0 0-7.54-.54l-3 3a5 5 0 0 0 7.07 7.07l1.71-1.71" />
    </svg>
  );
}

export function SearchIcon(p: IconProps) {
  return (
    <svg {...base(p)}>
      <circle cx="11" cy="11" r="8" />
      <path d="m21 21-4.35-4.35" />
    </svg>
  );
}

export function LogoutIcon(p: IconProps) {
  return (
    <svg {...base(p)}>
      <path d="M9 21H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h4" />
      <path d="M16 17l5-5-5-5" />
      <path d="M21 12H9" />
    </svg>
  );
}

export function CompassIcon(p: IconProps) {
  return (
    <svg {...base(p)}>
      <circle cx="12" cy="12" r="10" />
      <polygon points="16.24 7.76 14.12 14.12 7.76 16.24 9.88 9.88 16.24 7.76" />
    </svg>
  );
}

export function RssIcon(p: IconProps) {
  return (
    <svg {...base(p)}>
      <path d="M4 11a9 9 0 0 1 9 9" />
      <path d="M4 4a16 16 0 0 1 16 16" />
      <circle cx="5" cy="19" r="1" fill="currentColor" stroke="none" />
    </svg>
  );
}

export function SparkleIcon(p: IconProps) {
  return (
    <svg {...base(p)}>
      <path d="M12 3v3" />
      <path d="M12 18v3" />
      <path d="M5.6 5.6l2.1 2.1" />
      <path d="M16.3 16.3l2.1 2.1" />
      <path d="M3 12h3" />
      <path d="M18 12h3" />
      <path d="M5.6 18.4l2.1-2.1" />
      <path d="M16.3 7.7l2.1-2.1" />
    </svg>
  );
}

export function ListIcon(p: IconProps) {
  return (
    <svg {...base(p)}>
      <path d="M4 6h16" />
      <path d="M4 12h16" />
      <path d="M4 18h16" />
    </svg>
  );
}

export function StoriesIcon(p: IconProps) {
  return (
    <svg {...base(p)}>
      <rect x="7" y="4" width="10" height="16" rx="2" />
      <path d="M3 8v8" />
      <path d="M21 8v8" />
    </svg>
  );
}

export function CardsIcon(p: IconProps) {
  return (
    <svg {...base(p)}>
      <rect x="3" y="3" width="7.5" height="7.5" rx="1.5" />
      <rect x="13.5" y="3" width="7.5" height="7.5" rx="1.5" />
      <rect x="3" y="13.5" width="7.5" height="7.5" rx="1.5" />
      <rect x="13.5" y="13.5" width="7.5" height="7.5" rx="1.5" />
    </svg>
  );
}

export function ChevronUpIcon(p: IconProps) {
  return (
    <svg {...base(p)}>
      <path d="m18 15-6-6-6 6" />
    </svg>
  );
}

export function TrashIcon(p: IconProps) {
  return (
    <svg {...base(p)}>
      <path d="M3 6h18" />
      <path d="M19 6v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6" />
      <path d="M8 6V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2" />
    </svg>
  );
}

export function MenuIcon(p: IconProps) {
  return (
    <svg {...base(p)}>
      <path d="M4 6h16" />
      <path d="M4 12h16" />
      <path d="M4 18h16" />
    </svg>
  );
}

export function SlackIcon(p: IconProps) {
  return (
    <svg {...base(p)}>
      <path d="M10 3.5 8.2 20.5" />
      <path d="M15.8 3.5 14 20.5" />
      <path d="M4 9.2h16.5" />
      <path d="M3.5 14.8H20" />
    </svg>
  );
}

export function TeamsIcon(p: IconProps) {
  return (
    <svg {...base(p)}>
      <rect x="3.5" y="3.5" width="17" height="17" rx="4" />
      <path d="M8 8.5h8" />
      <path d="M12 8.5V16" />
    </svg>
  );
}

export function WhatsAppIcon(p: IconProps) {
  return (
    <svg {...base(p)}>
      <path d="M12 3a8.5 8.5 0 0 0-7.3 12.8L3 21l5.2-1.7A8.5 8.5 0 1 0 12 3z" />
      <path d="M9.3 8.6l.9 1.8-.8.9a5.5 5.5 0 0 0 3.3 3.3l.9-.8 1.8.9c-.2 1-1.2 1.7-2.2 1.4a7.6 7.6 0 0 1-5.3-5.3c-.3-1 .4-2 1.4-2.2z" />
    </svg>
  );
}

export function ActivityIcon(p: IconProps) {
  return (
    <svg {...base(p)}>
      <polyline points="22 12 18 12 15 21 9 3 6 12 2 12" />
    </svg>
  );
}
