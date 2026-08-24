import { useState } from 'react';
import type { ReactNode } from 'react';
import { Info, User, X } from 'lucide-react';

function IconButton({ onClick, title, children }: { onClick: () => void; title: string; children: ReactNode }) {
  return (
    <button
      onClick={onClick}
      title={title}
      className="flex h-7 w-7 items-center justify-center rounded-full bg-white/10 hover:bg-white/20"
    >
      {children}
    </button>
  );
}

function ModalShell({ title, onClose, children }: { title: string; onClose: () => void; children: ReactNode }) {
  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/40 p-4">
      <div className="mers-card w-full max-w-md bg-white">
        <div
          className="flex items-center justify-between px-4 py-3 text-white"
          style={{ backgroundColor: 'var(--mers-navy)' }}
        >
          <span className="text-sm font-bold">{title}</span>
          <button onClick={onClose} className="rounded p-1 hover:bg-white/10">
            <X size={18} />
          </button>
        </div>
        <div className="p-4 text-sm text-[var(--mers-slate)]">{children}</div>
      </div>
    </div>
  );
}

export default function DashboardInfoPanel() {
  const [open, setOpen] = useState<'contacts' | 'guide' | null>(null);

  return (
    <>
      <IconButton onClick={() => setOpen('contacts')} title="Contacts">
        <User size={14} />
      </IconButton>
      <IconButton onClick={() => setOpen('guide')} title="User Guide">
        <Info size={14} />
      </IconButton>

      {open === 'contacts' && (
        <ModalShell title="Contacts" onClose={() => setOpen(null)}>
          <p className="mb-3">Who to contact for questions or suggestions about this dashboard:</p>
          <dl className="space-y-2">
            <div>
              <dt className="text-xs font-semibold text-[var(--mers-navy)]">Product Owner</dt>
              <dd>
                Victor Yamaykin —{' '}
                <a href="mailto:vyamaykin@mersgoodwill.org" className="underline" style={{ color: 'var(--mers-blue)' }}>
                  vyamaykin@mersgoodwill.org
                </a>
              </dd>
            </div>
            <div>
              <dt className="text-xs font-semibold text-[var(--mers-navy)]">Data / Technical Issues</dt>
              <dd>Route through the Product Owner above until a dedicated support channel is set up.</dd>
            </div>
          </dl>
        </ModalShell>
      )}

      {open === 'guide' && (
        <ModalShell title="User Guide" onClose={() => setOpen(null)}>
          <dl className="space-y-3">
            <div>
              <dt className="text-xs font-bold text-[var(--mers-navy)]">WHY — Purpose</dt>
              <dd>
                Turns the COO's top-down annual growth target and day-of-week seasonality assumptions into a
                day-by-day, store-by-store sales plan across the network, and exports it as a fully
                formula-driven Excel workbook.
              </dd>
            </div>
            <div>
              <dt className="text-xs font-bold text-[var(--mers-navy)]">WHO — Audience</dt>
              <dd>COO, VP of Retail, Regional Directors, and Financial Planning.</dd>
            </div>
            <div>
              <dt className="text-xs font-bold text-[var(--mers-navy)]">WHAT — Data &amp; Methodology</dt>
              <dd>
                Store actuals seeded from FY2025/FY2026 exports; live figures come from Databricks Unity Catalog
                (<code>gold.retail_data_science</code>) once connected. Daily sales are derived from
                normalized day-of-week weights, with holiday/closure days reallocated within the same month
                rather than dropped from the annual total.
              </dd>
            </div>
            <div>
              <dt className="text-xs font-bold text-[var(--mers-navy)]">HOW — Forecasting Methodology</dt>
              <dd>
                The underlying store-level forecast is trained and scored in Databricks against Unity Catalog
                data, not in this app — this app takes that forecast as its starting "Forecasted Planned
                Sales" figure and layers the DOW/growth/closure logic on top. Model accuracy is graded on{' '}
                <strong>WAPE</strong> (Weighted Absolute Percentage Error) rather than plain MAPE, because daily
                store sales include a lot of low or zero-sales days (slow days, closures) that would distort a
                simple average percentage error. WAPE instead divides total absolute error by total actual
                sales, so accuracy reflects dollars that matter, not day-count. Validation uses a rolling
                (walk-forward) window — train on a historical period, test on the period right after it, then
                slide both forward and repeat — matching how the model is actually used: retrained periodically
                and forecasting forward, not evaluated on a single random split.
              </dd>
            </div>
            <div>
              <dt className="text-xs font-bold text-[var(--mers-navy)]">HOW TO READ</dt>
              <dd>
                Adjust DOW weights and growth levers on the left; the monthly chart/table and per-store totals
                on the right recalculate live. Use the store table's "COO Adjusted Planned Sales" column to
                override a specific store's total (e.g. for a renovation closure), then Export Excel to get the
                full formula-driven workbook.
              </dd>
            </div>
          </dl>
        </ModalShell>
      )}
    </>
  );
}
