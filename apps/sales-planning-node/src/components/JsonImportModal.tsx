import { useState } from 'react';
import { X } from 'lucide-react';
import type { PlanningSession } from '../types';
import { importSessionFromJson } from '../utils/sessionStorage';

interface JsonImportModalProps {
  onClose: () => void;
  onImport: (session: PlanningSession) => void;
}

export default function JsonImportModal({ onClose, onImport }: JsonImportModalProps) {
  const [text, setText] = useState('');
  const [error, setError] = useState('');

  const handleImport = () => {
    try {
      const session = importSessionFromJson(text);
      onImport(session);
      onClose();
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Invalid JSON');
    }
  };

  const handleFile = (file: File) => {
    const reader = new FileReader();
    reader.onload = () => setText(String(reader.result ?? ''));
    reader.readAsText(file);
  };

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/40 p-4">
      <div className="mers-card flex w-full max-w-xl flex-col bg-white">
        <div
          className="flex items-center justify-between px-4 py-3 text-white"
          style={{ backgroundColor: 'var(--mers-navy)' }}
        >
          <span className="text-sm font-bold">Import Scenario JSON</span>
          <button onClick={onClose} className="rounded p-1 hover:bg-white/10">
            <X size={18} />
          </button>
        </div>

        <div className="p-4">
          <input
            type="file"
            accept="application/json"
            onChange={(e) => e.target.files?.[0] && handleFile(e.target.files[0])}
            className="mb-3 block text-xs"
          />
          <textarea
            value={text}
            onChange={(e) => setText(e.target.value)}
            placeholder="Paste scenario JSON here…"
            className="mers-scrollbar h-64 w-full rounded border border-[var(--mers-ice)] p-2 font-mono text-xs"
          />
          {error && <p className="mt-2 text-xs" style={{ color: 'var(--mers-crimson)' }}>{error}</p>}
        </div>

        <div className="flex justify-end gap-2 border-t border-[var(--mers-ice)] px-4 py-3">
          <button onClick={onClose} className="rounded px-3 py-1.5 text-xs font-semibold text-[var(--mers-slate)]">
            Cancel
          </button>
          <button
            onClick={handleImport}
            disabled={!text.trim()}
            className="rounded px-3 py-1.5 text-xs font-semibold text-white disabled:opacity-50"
            style={{ backgroundColor: 'var(--mers-blue)' }}
          >
            Import
          </button>
        </div>
      </div>
    </div>
  );
}
