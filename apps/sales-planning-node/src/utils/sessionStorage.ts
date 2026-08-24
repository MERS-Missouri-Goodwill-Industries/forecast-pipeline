import type { PlanningSession } from '../types';

const STORAGE_KEY = 'vic-forecast-sessions-v1';

function readAll(): PlanningSession[] {
  try {
    const raw = localStorage.getItem(STORAGE_KEY);
    if (!raw) return [];
    return JSON.parse(raw) as PlanningSession[];
  } catch {
    return [];
  }
}

function writeAll(sessions: PlanningSession[]): void {
  localStorage.setItem(STORAGE_KEY, JSON.stringify(sessions));
}

export function listSessions(): PlanningSession[] {
  return readAll().sort((a, b) => (a.lastUpdated < b.lastUpdated ? 1 : -1));
}

export function saveSession(session: PlanningSession): void {
  const sessions = readAll();
  const idx = sessions.findIndex((s) => s.id === session.id);
  const updated = { ...session, lastUpdated: new Date().toISOString() };
  if (idx >= 0) {
    sessions[idx] = updated;
  } else {
    sessions.push(updated);
  }
  writeAll(sessions);
}

export function deleteSession(id: string): void {
  writeAll(readAll().filter((s) => s.id !== id));
}

export function getSession(id: string): PlanningSession | undefined {
  return readAll().find((s) => s.id === id);
}

export function exportSessionToJson(session: PlanningSession): string {
  return JSON.stringify(session, null, 2);
}

export function importSessionFromJson(json: string): PlanningSession {
  const parsed = JSON.parse(json) as PlanningSession;
  if (!parsed.id || !parsed.dowWeights) {
    throw new Error('Invalid scenario JSON: missing required fields (id, dowWeights).');
  }
  return parsed;
}

export function duplicateSession(session: PlanningSession, newName: string): PlanningSession {
  return {
    ...session,
    id: `session-${session.year}-${Date.now()}`,
    name: newName,
    isCommitted: false,
    lastUpdated: new Date().toISOString(),
  };
}
