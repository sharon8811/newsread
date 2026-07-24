"use client";

import { useEffect, useMemo } from "react";
import useSWR from "swr";
import { apiBlob } from "@/lib/api";

export default function PrivateHistoryImage({
  imageId,
  alt,
  className,
}: {
  imageId: number | null | undefined;
  alt: string;
  className?: string;
}) {
  const key = imageId ? `/history/images/${imageId}` : null;
  const { data } = useSWR<Blob>(key, apiBlob, {
    revalidateOnFocus: false,
    shouldRetryOnError: false,
  });
  const src = useMemo(() => (data ? URL.createObjectURL(data) : null), [data]);
  useEffect(() => {
    return () => {
      if (src) URL.revokeObjectURL(src);
    };
  }, [src]);

  if (!src) return null;
  // Private images require a bearer-authenticated fetch, so next/image cannot
  // load them directly.
  // eslint-disable-next-line @next/next/no-img-element
  return <img src={src} alt={alt} className={className} />;
}
