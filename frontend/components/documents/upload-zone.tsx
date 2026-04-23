"use client";

import { UploadCloud } from "lucide-react";
import { useRef, useState } from "react";

import { api } from "@/lib/api";
import type { DocumentStatus } from "@/types";

interface UploadZoneProps {
  projectId: string;
  onUploadComplete: () => void;
}

interface UploadItem {
  id: string;
  name: string;
  progress: number;
  status: DocumentStatus | "uploading";
  error?: string;
}

const acceptedExtensions = [".pdf", ".txt", ".docx", ".md"];

export function UploadZone({ projectId, onUploadComplete }: UploadZoneProps) {
  const inputRef = useRef<HTMLInputElement>(null);
  const [dragActive, setDragActive] = useState(false);
  const [uploads, setUploads] = useState<UploadItem[]>([]);

  function openFilePicker() {
    inputRef.current?.click();
  }

  function onDragEnter(event: React.DragEvent<HTMLDivElement>) {
    event.preventDefault();
    event.stopPropagation();
    setDragActive(true);
  }

  function onDragOver(event: React.DragEvent<HTMLDivElement>) {
    event.preventDefault();
    event.stopPropagation();
    setDragActive(true);
  }

  function onDragLeave(event: React.DragEvent<HTMLDivElement>) {
    event.preventDefault();
    event.stopPropagation();
    if (event.currentTarget.contains(event.relatedTarget as Node | null)) {
      return;
    }
    setDragActive(false);
  }

  function onDrop(event: React.DragEvent<HTMLDivElement>) {
    event.preventDefault();
    event.stopPropagation();
    setDragActive(false);
    void uploadFiles(Array.from(event.dataTransfer.files));
  }

  function onFileChange(event: React.ChangeEvent<HTMLInputElement>) {
    void uploadFiles(Array.from(event.target.files ?? []));
    event.target.value = "";
  }

  async function uploadFiles(files: File[]) {
    const validFiles = files.filter(isAcceptedFile);
    if (validFiles.length === 0) {
      return;
    }

    const initialItems = validFiles.map<UploadItem>((file) => ({
      id: `${file.name}-${file.lastModified}-${crypto.randomUUID()}`,
      name: file.name,
      progress: 8,
      status: "queued",
    }));

    setUploads((current) => [...initialItems, ...current].slice(0, 6));

    await Promise.all(
      validFiles.map(async (file, index) => {
        const itemId = initialItems[index].id;
        setUpload(itemId, { status: "uploading", progress: 35 });
        try {
          await api.documents.upload(projectId, file);
          setUpload(itemId, { status: "queued", progress: 100 });
          onUploadComplete();
        } catch (error) {
          setUpload(itemId, {
            status: "error",
            progress: 100,
            error: error instanceof Error ? error.message : "Upload failed",
          });
        }
      }),
    );
  }

  function setUpload(id: string, patch: Partial<UploadItem>) {
    setUploads((current) => current.map((item) => (item.id === id ? { ...item, ...patch } : item)));
  }

  return (
    <section>
      <div
        role="button"
        tabIndex={0}
        onClick={openFilePicker}
        onKeyDown={(event) => {
          if (event.key === "Enter" || event.key === " ") {
            openFilePicker();
          }
        }}
        onDragEnter={onDragEnter}
        onDragOver={onDragOver}
        onDragLeave={onDragLeave}
        onDrop={onDrop}
        className={`flex h-40 w-full cursor-pointer flex-col items-center justify-center rounded-xl border border-dashed transition ${
          dragActive ? "border-indigo-500 bg-indigo-500/10" : "border-zinc-700 bg-zinc-900 hover:border-zinc-600 hover:bg-zinc-800/60"
        }`}
      >
        <input ref={inputRef} type="file" multiple accept={acceptedExtensions.join(",")} onChange={onFileChange} className="hidden" />
        <UploadCloud className={`h-8 w-8 ${dragActive ? "text-indigo-300" : "text-zinc-500"}`} />
        <p className="mt-3 text-sm font-medium text-zinc-100">{dragActive ? "Release to upload" : "Drop files here or click to browse"}</p>
        <div className="mt-3 flex flex-wrap justify-center gap-2">
          {acceptedExtensions.map((type) => (
            <span key={type} className="rounded-full border border-zinc-800 bg-zinc-950 px-2 py-1 font-mono text-xs text-zinc-400">
              {type}
            </span>
          ))}
        </div>
      </div>

      {uploads.length > 0 ? (
        <div className="mt-4 space-y-2">
          {uploads.map((item) => (
            <div key={item.id} className="rounded-lg border border-zinc-800 bg-zinc-900 px-3 py-2">
              <div className="flex items-center justify-between gap-3 text-xs">
                <span className="truncate text-zinc-300">{item.name}</span>
                <span className={item.status === "error" ? "text-red-400" : "text-zinc-500"}>{item.error ?? item.status}</span>
              </div>
              <div className="mt-2 h-1.5 overflow-hidden rounded-full bg-zinc-800">
                <div
                  className={`h-full rounded-full ${item.status === "error" ? "bg-red-500" : "bg-indigo-500"}`}
                  style={{ width: `${item.progress}%` }}
                />
              </div>
            </div>
          ))}
        </div>
      ) : null}
    </section>
  );
}

function isAcceptedFile(file: File): boolean {
  const lower = file.name.toLowerCase();
  return acceptedExtensions.some((extension) => lower.endsWith(extension));
}

