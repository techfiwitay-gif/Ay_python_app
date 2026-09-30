'use client';

import { useEffect, useState } from 'react';
import { Download, Search } from 'lucide-react';
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '@/components/ui/table';

type Account = {
  id: string; name: string | null; email: string | null; createdAt: string;
  inputTokens: number; outputTokens: number; requestCount: number;
  estimatedCostMicros: number; unpricedRequests: number; lastUsedAt: string | null;
};
type Usage = { configured: false } | {
  configured: true; totalAccounts: number; matchingAccounts: number;
  summary: { inputTokens: number; outputTokens: number; requestCount: number;
    estimatedCostMicros: number; unpricedRequests: number; activeAccounts: number };
  accounts: Account[];
};
const number = (value: number) => value.toLocaleString();
const dollars = (micros: number) => new Intl.NumberFormat(undefined, {
  style: 'currency', currency: 'USD', minimumFractionDigits: 2, maximumFractionDigits: 4,
}).format(micros / 1_000_000);
const dateTime = (value: string | null) => {
  if (!value) return null;
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return null;
  return new Intl.DateTimeFormat(undefined, {
    year: 'numeric', month: 'short', day: 'numeric',
    hour: 'numeric', minute: '2-digit', timeZoneName: 'short',
  }).format(date);
};

export default function AiUsers({ days, appId }: { days: string; appId: string }) {
  const [query, setQuery] = useState('');
  const [search, setSearch] = useState('');
  const [sort, setSort] = useState('tokens');
  const [page, setPage] = useState(1);
  const [refresh, setRefresh] = useState(0);
  const [data, setData] = useState<Usage | null>(null);
  const [loading, setLoading] = useState(false);
  const [exporting, setExporting] = useState(false);
  const [error, setError] = useState('');
  const supported = appId === 'all' || appId === '6799787039';

  useEffect(() => {
    const timer = window.setTimeout(() => { setSearch(query.trim()); setPage(1); }, 300);
    return () => window.clearTimeout(timer);
  }, [query]);
  useEffect(() => { setPage(1); }, [days, sort]);
  useEffect(() => {
    if (!supported) return;
    const controller = new AbortController();
    const params = new URLSearchParams({ days, search, sort, page: String(page) });
    setLoading(true);
    setData(null);
    setError('');
    void fetch(`/admin/getreep/api/ai-users?${params}`, {
      cache: 'no-store', credentials: 'same-origin', signal: controller.signal,
    }).then(async response => {
      const body = await response.json();
      if (!response.ok) throw new Error(body.error || 'Account usage could not be loaded.');
      setData(body as Usage);
    }).catch(cause => {
      if (!controller.signal.aborted) setError(cause instanceof Error ? cause.message : 'Account usage could not be loaded.');
    }).finally(() => { if (!controller.signal.aborted) setLoading(false); });
    return () => controller.abort();
  }, [supported, days, search, sort, page, refresh]);

  async function exportExcel() {
    setExporting(true);
    setError('');
    try {
      const params = new URLSearchParams({ days, search, sort });
      const response = await fetch(`/admin/getreep/api/ai-users/export?${params}`, {
        cache: 'no-store', credentials: 'same-origin',
      });
      if (!response.ok) {
        const body = await response.json();
        throw new Error(body.error || 'Excel export failed.');
      }
      const url = URL.createObjectURL(await response.blob());
      const link = document.createElement('a');
      link.href = url;
      link.download = `getreep-ai-usage-${new Date().toISOString().slice(0, 10)}.xlsx`;
      document.body.append(link);
      link.click();
      link.remove();
      window.setTimeout(() => URL.revokeObjectURL(url), 1000);
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : 'Excel export failed.');
    } finally {
      setExporting(false);
    }
  }

  return <section className="panel ai-user-panel">
    <div className="section-heading ai-user-heading"><div><p className="eyebrow">GETREEP ACCOUNTS</p><h2>AI usage by Apple sign-in account</h2><p className="subtitle">Find the people using the most tokens and when each account last used AI. Times use your device’s time zone. Tracking starts with recent AI requests; past use is not backfilled.</p></div>
      <button type="button" className="button secondary" onClick={() => void exportExcel()}
        disabled={!supported || loading || exporting || !data?.configured || data.matchingAccounts === 0}>
        <Download size={16} />{exporting ? 'Preparing…' : 'Export to Excel'}
      </button>
    </div>
    {!supported ? <p className="fine">Per-account AI usage is currently available for Getreep only. Select Getreep or All apps above.</p> : <>
      <div className="ai-user-controls"><label className="search-row"><Search size={18} /><input aria-label="Search Apple accounts" placeholder="Search name, email, or account ID" maxLength={120} value={query} onChange={event => setQuery(event.target.value)} /></label>
        <label className="ai-user-sort">Sort by <select aria-label="Sort Apple accounts" value={sort} onChange={event => setSort(event.target.value)}><option value="tokens">Most tokens</option><option value="requests">Most requests</option><option value="recent">Recently active</option><option value="name">Name</option></select></label>
        <button type="button" className="button secondary" onClick={() => setRefresh(value => value + 1)} disabled={loading}>Refresh accounts</button>
      </div>
      {error && <div className="notice" role="alert">{error}</div>}
      {loading && <p className="fine" role="status">Loading account usage…</p>}
      {!loading && data && !data.configured && <p className="fine">Connect Getreep reporting in the website’s server settings to view Apple accounts.</p>}
      {data?.configured && <>
        <div className="ai-user-summary"><span><strong>{number(data.totalAccounts)}</strong> Apple accounts</span><span><strong>{number(data.summary.activeAccounts)}</strong> active in the last {days} days</span><span><strong>{number(data.summary.inputTokens + data.summary.outputTokens)}</strong> tokens</span><span><strong>{dollars(data.summary.estimatedCostMicros)}</strong> estimated cost</span></div>
        {!data.accounts.length && !loading ? <p className="fine">{search ? 'No accounts match this search.' : 'No Apple-linked accounts are available yet.'}</p> : <div className="ai-user-table"><Table><TableHeader><TableRow><TableHead>Account and last AI use</TableHead><TableHead>Input</TableHead><TableHead>Output</TableHead><TableHead>Total tokens</TableHead><TableHead>Requests</TableHead><TableHead>Estimated cost</TableHead></TableRow></TableHeader><TableBody>{data.accounts.map(account => {
          const lastUsed = dateTime(account.lastUsedAt);
          return <TableRow key={account.id}><TableCell><strong>{account.name || account.email || 'Unnamed account'}</strong>{account.name && account.email && <span className="account-id">{account.email}</span>}<span className="account-id">{account.id}</span><span className="account-last-used">Last recorded AI use: {lastUsed && account.lastUsedAt ? <time dateTime={account.lastUsedAt}>{lastUsed}</time> : 'None in this period'}</span></TableCell><TableCell>{number(account.inputTokens)}</TableCell><TableCell>{number(account.outputTokens)}</TableCell><TableCell>{number(account.inputTokens + account.outputTokens)}</TableCell><TableCell>{number(account.requestCount)}</TableCell><TableCell>{dollars(account.estimatedCostMicros)}{account.unpricedRequests > 0 && <span className="account-id">{number(account.unpricedRequests)} unpriced</span>}</TableCell></TableRow>;
        })}</TableBody></Table></div>}
        <div className="ai-user-pagination"><span>Showing {data.matchingAccounts ? (page - 1) * 25 + 1 : 0}–{Math.min(page * 25, data.matchingAccounts)} of {number(data.matchingAccounts)} matching accounts</span><div><button type="button" className="button secondary" disabled={page <= 1 || loading} onClick={() => setPage(value => value - 1)}>Previous</button><button type="button" className="button secondary" disabled={page * 25 >= data.matchingAccounts || loading} onClick={() => setPage(value => value + 1)}>Next</button></div></div>
        <p className="fine">Cost is an estimate, not an invoice. The Excel file includes the current search and reporting period.</p>
      </>}
    </>}
  </section>;
}
