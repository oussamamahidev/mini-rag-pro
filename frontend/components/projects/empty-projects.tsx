"use client";

interface EmptyProjectsProps {
  onCreate: () => void;
}

export function EmptyProjects({ onCreate }: EmptyProjectsProps) {
  return (
    <div className="flex min-h-[520px] items-center justify-center rounded-xl border border-dashed border-zinc-800 bg-zinc-900/40 p-8 text-center">
      <div className="mx-auto max-w-sm">
        <FolderIllustration />
        <h2 className="mt-6 text-lg font-semibold text-zinc-50">No projects yet</h2>
        <p className="mt-2 text-sm text-zinc-400">Create a project to upload documents and start asking grounded questions.</p>
        <button
          type="button"
          onClick={onCreate}
          className="mt-6 inline-flex h-10 items-center justify-center rounded-md bg-indigo-500 px-4 text-sm font-medium text-white transition hover:bg-indigo-400"
        >
          Create your first project
        </button>
      </div>
    </div>
  );
}

function FolderIllustration() {
  return (
    <svg className="mx-auto h-28 w-28" viewBox="0 0 128 128" fill="none" role="img" aria-label="Folder">
      <path
        d="M18 36C18 30.477 22.477 26 28 26H52.4C55.052 26 57.596 27.054 59.471 28.929L67.071 36.529C68.946 38.404 71.49 39.458 74.142 39.458H100C105.523 39.458 110 43.935 110 49.458V92C110 97.523 105.523 102 100 102H28C22.477 102 18 97.523 18 92V36Z"
        fill="#18181b"
        stroke="#3f3f46"
        strokeWidth="2"
      />
      <path
        d="M18 50C18 44.477 22.477 40 28 40H100C105.523 40 110 44.477 110 50V92C110 97.523 105.523 102 100 102H28C22.477 102 18 97.523 18 92V50Z"
        fill="#27272a"
        stroke="#6366f1"
        strokeWidth="2"
      />
      <path d="M35 64H93M35 78H74" stroke="#a1a1aa" strokeWidth="4" strokeLinecap="round" />
    </svg>
  );
}

