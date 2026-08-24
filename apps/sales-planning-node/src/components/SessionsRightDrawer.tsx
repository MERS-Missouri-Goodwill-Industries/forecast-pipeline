import { useState } from 'react';
import { Copy, Save, Trash2, X } from 'lucide-react';
import type { PlanningSession } from '../types';

interface SessionsRightDrawerProps {
  open: boolean;
  onClose: () => void;
  sessions: PlanningSession[];
  activeSessionId: string;
  onLoad: (session: PlanningSession) => void;
  onSaveCurrent: (name: string) => void;
  onDuplicate: (session: PlanningSession) => void;
  onDelete: (id: string) => void;
}

export default function SessionsRightDrawer({
  open,
  onClose,
  sessions,
  activeSessionId,
  onLoad,
  onSaveCurrent,
  onDuplicate,
  onDelete,
}: SessionsRightDrawerProps) {
  const [newName, setNewName] = useState('');

  if (!open) return null;

  return (
    <div className="fixed inset-0 z-40 flex justify-end bg-black/30">
      <div className="mers-scrollbar flex h-full w-96 flex-col overflow-y-auto bg-white shadow-xl">
        <div
          className="flex items-center justify-between px-4 py-3 text-white"
          style={{ backgroundColor: 'var(--mers-navy)' }}
        >
          <span className="text-sm font-bold">Scenario Sessions</span>
          <button onClick={onClose} className="rounded p-1 hover:bg-white/10">
            <X size={18} />
          </button>
        </div>

        <div className="flex gap-2 border-b border-[var(--mers-ice)] p-3">
          <input
            type="text"
            placeholder="Save current as…"
            value={newName}
            onChange={(e) => setNewName(e.target.value)}
            className="flex-1 rounded border border-[var(--mers-ice)] px-2 py-1 text-xs"
          />
          <button
            disabled={!newName.trim()}
            onClick={() => {
              onSaveCurrent(newName.trim());
              setNewName('');
            }}
            className="flex items-center gap-1 rounded px-3 py-1 text-xs font-semibold text-white disabled:opacity-50"
            style={{ backgroundColor: 'var(--mers-blue)' }}
          >
            <Save size={14} /> Save
          </button>
        </div>

        <div className="flex-1">
          {sessions.length === 0 && (
            <p className="p-4 text-xs text-[var(--mers-slate)]">No saved sessions yet.</p>
          )}
          {sessions.map((s) => (
            <div
              key={s.id}
              className="border-b border-[var(--mers-ice)] p-3"
              style={{ backgroundColor: s.id === activeSessionId ? 'var(--mers-canvas)' : undefined }}
            >
              <div className="flex items-center justify-between">
                <button onClick={() => onLoad(s)} className="text-left text-sm font-semibold text-[var(--mers-navy)]">
                  {s.name}
                </button>
                <div className="flex gap-1">
                  <button onClick={() => onDuplicate(s)} title="Duplicate" className="rounded p-1 hover:bg-[var(--mers-ice)]">
                    <Copy size={14} />
                  </button>
                  <button
                    onClick={() => onDelete(s.id)}
                    title="Delete"
                    className="rounded p-1 hover:bg-[var(--mers-ice)]"
                    style={{ color: 'var(--mers-crimson)' }}
                  >
                    <Trash2 size={14} />
                  </button>
                </div>
              </div>
              <p className="mt-1 text-xs text-[var(--mers-slate)]">{s.description}</p>
              <div className="mt-1 flex flex-wrap gap-1">
                {s.tags.map((tag) => (
                  <span
                    key={tag}
                    className="rounded-full px-2 py-0.5 text-[10px] font-semibold"
                    style={{ backgroundColor: 'var(--mers-ice)', color: 'var(--mers-navy)' }}
                  >
                    {tag}
                  </span>
                ))}
              </div>
              <p className="mt-1 text-[10px] text-[var(--mers-slate)]">
                Updated {new Date(s.lastUpdated).toLocaleString()}
              </p>
            </div>
          ))}
        </div>
      </div>
    </div>
  );
}
