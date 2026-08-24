import { useMemo, useState } from 'react';
import type { Region, Store } from '../types';

interface StoreSelectorProps {
  stores: Store[];
  selectedStoreId: string;
  onSelect: (codeOrAll: string) => void;
}

export default function StoreSelector({ stores, selectedStoreId, onSelect }: StoreSelectorProps) {
  const [search, setSearch] = useState('');
  const [regionFilter, setRegionFilter] = useState<Region | 'All'>('All');

  const filtered = useMemo(() => {
    return stores.filter((s) => {
      if (regionFilter !== 'All' && s.region !== regionFilter) return false;
      if (search && !s.name.toLowerCase().includes(search.toLowerCase()) && !s.code.toLowerCase().includes(search.toLowerCase())) {
        return false;
      }
      return true;
    });
  }, [stores, search, regionFilter]);

  return (
    <div className="mers-card p-4">
      <h3 className="mb-3 text-sm font-bold text-[var(--mers-navy)]">Store Selector</h3>
      <div className="mb-2 flex gap-2">
        <input
          type="text"
          placeholder="Search store or code…"
          value={search}
          onChange={(e) => setSearch(e.target.value)}
          className="flex-1 rounded border border-[var(--mers-ice)] px-2 py-1 text-xs"
        />
        <select
          value={regionFilter}
          onChange={(e) => setRegionFilter(e.target.value as Region | 'All')}
          className="rounded border border-[var(--mers-ice)] px-2 py-1 text-xs"
        >
          <option value="All">All Regions</option>
          <option value="East">East</option>
          <option value="West">West</option>
        </select>
      </div>

      <div className="mers-scrollbar max-h-64 overflow-y-auto rounded border border-[var(--mers-ice)]">
        <button
          onClick={() => onSelect('ALL')}
          className="block w-full px-3 py-1.5 text-left text-xs font-semibold hover:bg-[var(--mers-canvas)]"
          style={{ backgroundColor: selectedStoreId === 'ALL' ? 'var(--mers-ice)' : undefined }}
        >
          All Stores (Network)
        </button>
        {filtered.map((store) => (
          <button
            key={store.code}
            onClick={() => onSelect(store.code)}
            className="flex w-full items-center justify-between px-3 py-1.5 text-left text-xs hover:bg-[var(--mers-canvas)]"
            style={{ backgroundColor: selectedStoreId === store.code ? 'var(--mers-ice)' : undefined }}
          >
            <span>
              <span className="font-mono text-[var(--mers-slate)]">{store.code}</span> — {store.name}
            </span>
            <span
              className="rounded px-1.5 py-0.5 text-[10px] font-bold"
              style={{
                color: store.status === 'Continuing' ? 'var(--mers-good)' : store.status === 'New store' ? 'var(--mers-navy)' : 'var(--mers-crimson)',
                backgroundColor: store.status === 'New store' ? 'var(--mers-ice)' : 'transparent',
              }}
            >
              {store.status}
            </span>
          </button>
        ))}
      </div>
    </div>
  );
}
