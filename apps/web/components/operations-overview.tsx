"use client";

import { format, subDays } from "date-fns";
import Link from "next/link";
import {
  AlertTriangle,
  ArrowRight,
  Bell,
  CalendarDays,
  ChevronDown,
  Clock3,
  RefreshCw,
  Search,
  UserRound,
} from "lucide-react";
import type { DateRange } from "react-day-picker";
import { useCallback, useEffect, useMemo, useState } from "react";
import { Badge } from "./ui/badge";
import { Button } from "./ui/button";
import { ButtonGroup } from "./ui/button-group";
import { Calendar } from "./ui/calendar";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuGroup,
  DropdownMenuItem,
  DropdownMenuLabel,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from "./ui/dropdown-menu";
import { InputGroup, InputGroupAddon, InputGroupInput } from "./ui/input-group";
import {
  Popover,
  PopoverContent,
  PopoverDescription,
  PopoverHeader,
  PopoverTitle,
  PopoverTrigger,
} from "./ui/popover";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "./ui/select";
import { Spinner } from "./ui/spinner";
import { Tooltip, TooltipContent, TooltipTrigger } from "./ui/tooltip";

type Metric = {
  key: string;
  label: string;
  count?: number | null;
  amount?: string | null;
  currency?: string | null;
};

type ActionItem = {
  record_id: string;
  kind: "approval" | "pick" | "delivery" | "payment" | "stock";
  title: string;
  reference: string;
  branch_code: string;
  owner: string;
  status: string;
  urgency: "high" | "medium" | "normal";
  age_minutes: number;
  amount?: string | null;
  currency?: string | null;
  next_action: string;
  href: string;
};

type Overview = {
  generated_at: string;
  from_date: string;
  to_date: string;
  selected_branch_id?: string | null;
  branches: Array<{ branch_id: string; code: string; name: string }>;
  metrics: Metric[];
  action_queue: ActionItem[];
  pipeline: Array<{
    key: string;
    label: string;
    count: number;
    value: string;
    currency: string;
  }>;
  inventory: {
    available: string;
    reserved: string;
    low_stock_items: number;
    blocked_lots: number;
    pending_transfers: number;
    pending_adjustments: number;
    unit: string;
  };
  finance: {
    posted_invoices: number;
    posted_value: string;
    receipts_awaiting_verification: number;
    receipts_awaiting_value: string;
    overdue_balances: string;
    outstanding_receivables: string;
    collected_value: string;
    currency: string;
  };
  recent_activity: Array<{
    activity_id: string;
    kind: string;
    title: string;
    detail: string;
    branch_code: string;
    occurred_at: string;
    href: string;
  }>;
};

type LoadState =
  | { kind: "loading" }
  | { kind: "error"; correlationId: string }
  | { kind: "ready"; data: Overview };

const rangeOptions = [
  { days: 7, label: "Last 7 days" },
  { days: 30, label: "Last 30 days" },
  { days: 90, label: "Last 90 days" },
] as const;

function dateValue(date: Date): string {
  return format(date, "yyyy-MM-dd");
}

function formatMoney(value: string, currency = "PHP"): string {
  return new Intl.NumberFormat(undefined, {
    currency,
    maximumFractionDigits: 0,
    style: "currency",
  }).format(Number(value));
}

function formatQuantity(value: string): string {
  return new Intl.NumberFormat(undefined, {
    maximumFractionDigits: 1,
  }).format(Number(value));
}

function formatAge(minutes: number): string {
  if (minutes < 60) return `${minutes}m`;
  if (minutes < 24 * 60) return `${Math.floor(minutes / 60)}h`;
  return `${Math.floor(minutes / (24 * 60))}d`;
}

export function OperationsOverview() {
  const [state, setState] = useState<LoadState>({ kind: "loading" });
  const [branchId, setBranchId] = useState("all");
  const [dateRange, setDateRange] = useState<DateRange>(() => {
    const to = new Date();
    return { from: subDays(to, 30), to };
  });
  const [query, setQuery] = useState("");
  const [refreshing, setRefreshing] = useState(false);
  const fromDate = dateRange.from ? dateValue(dateRange.from) : "";
  const toDate = dateRange.to ? dateValue(dateRange.to) : "";

  const load = useCallback(
    async (preserve = false) => {
      if (fromDate.length === 0 || toDate.length === 0) return;
      if (preserve) setRefreshing(true);
      else setState({ kind: "loading" });
      const params = new URLSearchParams({
        from_date: fromDate,
        to_date: toDate,
      });
      if (branchId !== "all") params.set("branch_id", branchId);
      try {
        const response = await fetch(`/api/operations/overview?${params}`, {
          cache: "no-store",
        });
        if (!response.ok) {
          const body = (await response.json()) as {
            error?: { correlation_id?: string };
          };
          setState({
            correlationId:
              body.error?.correlation_id ??
              response.headers.get("X-Correlation-ID") ??
              crypto.randomUUID(),
            kind: "error",
          });
          return;
        }
        setState({ data: (await response.json()) as Overview, kind: "ready" });
      } catch {
        setState({ correlationId: crypto.randomUUID(), kind: "error" });
      } finally {
        setRefreshing(false);
      }
    },
    [branchId, fromDate, toDate],
  );

  useEffect(() => {
    // The first authoritative request owns the loading-to-ready transition.
    // eslint-disable-next-line react-hooks/set-state-in-effect
    void load();
  }, [load]);

  const filteredQueue = useMemo(() => {
    if (state.kind !== "ready") return [];
    const normalized = query.trim().toLocaleLowerCase();
    if (!normalized) return state.data.action_queue;
    return state.data.action_queue.filter((item) =>
      [item.title, item.reference, item.branch_code, item.owner, item.status]
        .join(" ")
        .toLocaleLowerCase()
        .includes(normalized),
    );
  }, [query, state]);

  if (state.kind === "loading") return <OverviewSkeleton />;
  if (state.kind === "error") {
    return (
      <div className="operations-overview">
        <header className="operations-heading">
          <div>
            <h1>Operations overview</h1>
            <p>
              Work requiring attention across sales, warehouse, delivery, and
              finance.
            </p>
          </div>
        </header>
        <section className="operations-error" role="alert">
          <AlertTriangle aria-hidden="true" />
          <div>
            <h2>Operational data could not be loaded</h2>
            <p>
              No cached values are shown. Retry the authoritative source.
              Support reference <code>{state.correlationId}</code>
            </p>
          </div>
          <Button onClick={() => void load()} type="button">
            Retry
          </Button>
        </section>
      </div>
    );
  }

  const data = state.data;
  const selectedBranch = data.branches.find(
    (branch) => branch.branch_id === branchId,
  );
  const selectedBranchLabel =
    branchId === "all"
      ? "All branches"
      : selectedBranch
        ? `${selectedBranch.code} / ${selectedBranch.name}`
        : "Select branch";
  return (
    <div className="operations-overview" aria-busy={refreshing}>
      <header className="operations-heading">
        <div>
          <h1>Operations overview</h1>
          <p>
            Work requiring attention across sales, warehouse, delivery, and
            finance.
          </p>
        </div>
        <div className="operations-updated" role="status" aria-live="polite">
          {refreshing
            ? "Refreshing live data"
            : `Updated ${new Intl.DateTimeFormat(undefined, { hour: "numeric", minute: "2-digit" }).format(new Date(data.generated_at))}`}
        </div>
      </header>

      <section className="operations-controls" aria-label="Overview controls">
        <label className="operations-control operations-search">
          <span>Search work</span>
          <InputGroup className="operations-input-group">
            <InputGroupAddon>
              <Search aria-hidden="true" />
            </InputGroupAddon>
            <InputGroupInput
              onChange={(event) => setQuery(event.target.value)}
              placeholder="Customer, reference, owner, or status"
              type="search"
              value={query}
            />
          </InputGroup>
        </label>
        <div className="operations-control">
          <span>Branch</span>
          <Select
            onValueChange={(value) => setBranchId(value ?? "all")}
            value={branchId}
          >
            <SelectTrigger aria-label="Branch" className="operations-select">
              <SelectValue>{selectedBranchLabel}</SelectValue>
            </SelectTrigger>
            <SelectContent align="start">
              <SelectItem value="all">All branches</SelectItem>
              {data.branches.map((branch) => (
                <SelectItem key={branch.branch_id} value={branch.branch_id}>
                  {branch.code} / {branch.name}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
        </div>
        <div className="operations-control operations-date-control">
          <span>Date range</span>
          <Popover>
            <PopoverTrigger
              render={
                <Button
                  aria-label={`Date range, ${dateRange.from ? format(dateRange.from, "MMM d, yyyy") : "start date"} to ${dateRange.to ? format(dateRange.to, "MMM d, yyyy") : "end date"}`}
                  className="operations-range-trigger"
                  type="button"
                  variant="outline"
                />
              }
            >
              <CalendarDays aria-hidden="true" />
              <span>
                {dateRange.from && dateRange.to
                  ? `${format(dateRange.from, "MMM d")} – ${format(dateRange.to, "MMM d, yyyy")}`
                  : "Select range"}
              </span>
              <ChevronDown aria-hidden="true" />
            </PopoverTrigger>
            <PopoverContent align="start" className="operations-date-popover">
              <PopoverHeader>
                <PopoverTitle>Reporting window</PopoverTitle>
                <PopoverDescription>
                  Select the dates used for activity and collected value.
                </PopoverDescription>
              </PopoverHeader>
              <ButtonGroup
                className="operations-range-presets"
                aria-label="Date range presets"
              >
                {rangeOptions.map((option) => (
                  <Button
                    key={option.days}
                    onClick={() => {
                      const to = new Date();
                      setDateRange({ from: subDays(to, option.days), to });
                    }}
                    size="sm"
                    type="button"
                    variant="outline"
                  >
                    {option.label}
                  </Button>
                ))}
              </ButtonGroup>
              <Calendar
                defaultMonth={dateRange.from ?? new Date()}
                disabled={{ after: new Date() }}
                mode="range"
                onSelect={(next) => {
                  if (next !== undefined) setDateRange(next);
                }}
                selected={dateRange}
              />
            </PopoverContent>
          </Popover>
        </div>
        <div className="operations-toolbar">
          <Tooltip>
            <TooltipTrigger
              render={
                <Button
                  aria-label="Refresh operational data"
                  disabled={refreshing}
                  onClick={() => void load(true)}
                  size="icon"
                  type="button"
                  variant="outline"
                />
              }
            >
              {refreshing ? (
                <Spinner aria-hidden="true" />
              ) : (
                <RefreshCw aria-hidden="true" />
              )}
            </TooltipTrigger>
            <TooltipContent>Refresh authoritative data</TooltipContent>
          </Tooltip>

          <DropdownMenu>
            <DropdownMenuTrigger
              render={
                <Button
                  aria-label={`${data.recent_activity.length} recent notifications`}
                  className="operations-notification-trigger"
                  size="icon"
                  type="button"
                  variant="outline"
                />
              }
            >
              <Bell aria-hidden="true" />
              <Badge className="operations-notification-count" variant="danger">
                {data.recent_activity.length}
              </Badge>
            </DropdownMenuTrigger>
            <DropdownMenuContent
              align="end"
              className="operations-notifications-menu"
            >
              <DropdownMenuGroup>
                <DropdownMenuLabel>
                  <span>Recent activity</span>
                  <small>{data.recent_activity.length} accepted events</small>
                </DropdownMenuLabel>
                <DropdownMenuSeparator />
                {data.recent_activity.slice(0, 5).map((activity) => (
                  <DropdownMenuItem
                    key={activity.activity_id}
                    render={<Link href={activity.href} />}
                  >
                    <span className="operations-notification-copy">
                      <strong>{activity.title}</strong>
                      <small>
                        {activity.branch_code} ·{" "}
                        {format(
                          new Date(activity.occurred_at),
                          "MMM d, h:mm a",
                        )}
                      </small>
                    </span>
                    <ArrowRight aria-hidden="true" />
                  </DropdownMenuItem>
                ))}
              </DropdownMenuGroup>
              <DropdownMenuSeparator />
              <DropdownMenuItem render={<a href="#recent-activity" />}>
                View activity ledger
              </DropdownMenuItem>
            </DropdownMenuContent>
          </DropdownMenu>

          <DropdownMenu>
            <DropdownMenuTrigger
              render={
                <Button
                  aria-label="Operator menu"
                  className="operations-user-trigger"
                  type="button"
                  variant="outline"
                />
              }
            >
              <UserRound aria-hidden="true" />
              <span>Operator</span>
              <ChevronDown aria-hidden="true" />
            </DropdownMenuTrigger>
            <DropdownMenuContent align="end" className="operations-user-menu">
              <DropdownMenuGroup>
                <DropdownMenuLabel>
                  <strong>Operator</strong>
                  <small>All branches</small>
                </DropdownMenuLabel>
                <DropdownMenuSeparator />
                <DropdownMenuItem render={<Link href="/" />}>
                  Return to website
                </DropdownMenuItem>
              </DropdownMenuGroup>
            </DropdownMenuContent>
          </DropdownMenu>
        </div>
      </section>

      <section
        className="operations-metrics"
        aria-label="Authoritative metrics"
      >
        {data.metrics.map((metric) => (
          <article key={metric.key}>
            <span>{metric.label}</span>
            <strong>
              {metric.amount != null
                ? formatMoney(metric.amount, metric.currency ?? undefined)
                : (metric.count?.toLocaleString() ?? "0")}
            </strong>
          </article>
        ))}
      </section>

      <div className="operations-layout">
        <section className="attention-queue" aria-labelledby="attention-title">
          <div className="operations-section-heading">
            <div>
              <h2 id="attention-title">Action queue</h2>
              <p>
                Oldest work is shown first. Values reflect the current server
                state.
              </p>
            </div>
            <span>{filteredQueue.length} requiring attention</span>
          </div>
          {filteredQueue.length === 0 ? (
            <div className="operations-empty" role="status">
              <h3>
                {query
                  ? "No work matches this search"
                  : "No work requires attention"}
              </h3>
              <p>
                {query
                  ? "Try a customer, reference, branch, owner, or status."
                  : "New approvals and exceptions will appear here."}
              </p>
            </div>
          ) : (
            <div className="queue-list" role="list">
              <div className="queue-header" aria-hidden="true">
                <span>Work</span>
                <span>Age</span>
                <span>Value</span>
                <span>Branch / owner</span>
                <span>Next action</span>
              </div>
              {filteredQueue.map((item) => (
                <article
                  className="queue-row"
                  key={item.record_id}
                  role="listitem"
                >
                  <div className="queue-record">
                    <Badge
                      className="queue-urgency"
                      variant={
                        item.urgency === "high"
                          ? "danger"
                          : item.urgency === "medium"
                            ? "warning"
                            : "default"
                      }
                    >
                      {item.urgency} priority
                    </Badge>
                    <strong>{item.title}</strong>
                    <span>
                      {item.kind.replaceAll("_", " ")} /{" "}
                      {item.status.replaceAll("_", " ")}
                    </span>
                    <code>{item.reference.slice(0, 8).toUpperCase()}</code>
                  </div>
                  <div className="queue-age">
                    <Clock3 aria-hidden="true" />
                    <span>{formatAge(item.age_minutes)}</span>
                  </div>
                  <div className="queue-value">
                    {item.amount
                      ? formatMoney(item.amount, item.currency ?? undefined)
                      : "Not applicable"}
                  </div>
                  <div className="queue-scope">
                    <strong>{item.branch_code}</strong>
                    <span>{item.owner}</span>
                  </div>
                  <Link className="queue-action" href={item.href}>
                    {item.next_action}
                    <ArrowRight aria-hidden="true" />
                  </Link>
                </article>
              ))}
            </div>
          )}
        </section>

        <aside className="pipeline-panel" aria-labelledby="pipeline-title">
          <div className="operations-section-heading">
            <div>
              <h2 id="pipeline-title">Order pipeline</h2>
              <p>Current count and value at each handoff.</p>
            </div>
          </div>
          <ol className="pipeline-list">
            {data.pipeline.map((stage) => (
              <li key={stage.key}>
                <span className="pipeline-index" aria-hidden="true">
                  {String(data.pipeline.indexOf(stage) + 1).padStart(2, "0")}
                </span>
                <div>
                  <strong>{stage.label}</strong>
                  <span>{stage.count} records</span>
                </div>
                <b>{formatMoney(stage.value, stage.currency)}</b>
              </li>
            ))}
          </ol>
        </aside>
      </div>

      <div className="operations-secondary">
        <section
          className="operations-data-panel"
          aria-labelledby="inventory-health-title"
        >
          <div className="operations-section-heading">
            <div>
              <h2 id="inventory-health-title">Inventory health</h2>
              <p>Custody and movement signals in scoped warehouses.</p>
            </div>
            <Link href="/inventory">
              Open stock ledger
              <ArrowRight aria-hidden="true" />
            </Link>
          </div>
          <dl className="operations-facts">
            <div>
              <dt>Available</dt>
              <dd>
                {formatQuantity(data.inventory.available)}{" "}
                <small>{data.inventory.unit}</small>
              </dd>
            </div>
            <div>
              <dt>Reserved</dt>
              <dd>
                {formatQuantity(data.inventory.reserved)}{" "}
                <small>{data.inventory.unit}</small>
              </dd>
            </div>
            <div>
              <dt>Low-stock items</dt>
              <dd>{data.inventory.low_stock_items}</dd>
            </div>
            <div>
              <dt>Blocked lots</dt>
              <dd>{data.inventory.blocked_lots}</dd>
            </div>
            <div>
              <dt>Pending transfers</dt>
              <dd>{data.inventory.pending_transfers}</dd>
            </div>
            <div>
              <dt>Adjustments</dt>
              <dd>{data.inventory.pending_adjustments}</dd>
            </div>
          </dl>
        </section>
        <section
          className="operations-data-panel"
          aria-labelledby="finance-snapshot-title"
        >
          <div className="operations-section-heading">
            <div>
              <h2 id="finance-snapshot-title">Finance snapshot</h2>
              <p>Posted, pending, collected, and outstanding value.</p>
            </div>
            <Link href="/finance">
              Open finance
              <ArrowRight aria-hidden="true" />
            </Link>
          </div>
          <dl className="operations-facts finance-facts">
            <div>
              <dt>Posted invoices</dt>
              <dd>
                {data.finance.posted_invoices}
                <small>
                  {formatMoney(
                    data.finance.posted_value,
                    data.finance.currency,
                  )}
                </small>
              </dd>
            </div>
            <div>
              <dt>Awaiting verification</dt>
              <dd>
                {data.finance.receipts_awaiting_verification}
                <small>
                  {formatMoney(
                    data.finance.receipts_awaiting_value,
                    data.finance.currency,
                  )}
                </small>
              </dd>
            </div>
            <div>
              <dt>Outstanding</dt>
              <dd>
                {formatMoney(
                  data.finance.outstanding_receivables,
                  data.finance.currency,
                )}
              </dd>
            </div>
            <div>
              <dt>Overdue</dt>
              <dd>
                {formatMoney(
                  data.finance.overdue_balances,
                  data.finance.currency,
                )}
              </dd>
            </div>
            <div>
              <dt>Collected in range</dt>
              <dd>
                {formatMoney(
                  data.finance.collected_value,
                  data.finance.currency,
                )}
              </dd>
            </div>
          </dl>
        </section>
      </div>

      <section
        className="recent-activity"
        id="recent-activity"
        aria-labelledby="activity-title"
      >
        <div className="operations-section-heading">
          <div>
            <h2 id="activity-title">Recent activity</h2>
            <p>Accepted decisions and postings in the selected range.</p>
          </div>
        </div>
        {data.recent_activity.length === 0 ? (
          <div className="operations-empty" role="status">
            <h3>No activity in this range</h3>
            <p>Choose a longer range or another branch.</p>
          </div>
        ) : (
          <ol>
            {data.recent_activity.map((activity) => (
              <li key={activity.activity_id}>
                <time dateTime={activity.occurred_at}>
                  {new Intl.DateTimeFormat(undefined, {
                    day: "numeric",
                    hour: "numeric",
                    minute: "2-digit",
                    month: "short",
                  }).format(new Date(activity.occurred_at))}
                </time>
                <div>
                  <strong>{activity.title}</strong>
                  <span>
                    {activity.detail} / {activity.branch_code}
                  </span>
                </div>
                <Link
                  href={activity.href}
                  aria-label={`Open ${activity.title}`}
                >
                  <ArrowRight aria-hidden="true" />
                </Link>
              </li>
            ))}
          </ol>
        )}
      </section>
    </div>
  );
}

function OverviewSkeleton() {
  return (
    <div
      className="operations-overview operations-skeleton"
      role="status"
      aria-label="Loading operations overview"
    >
      <div className="skeleton-line skeleton-title" />
      <div className="skeleton-line skeleton-copy" />
      <div className="skeleton-controls" />
      <div className="skeleton-metrics">
        {Array.from({ length: 6 }, (_, index) => (
          <span key={index} />
        ))}
      </div>
      <div className="skeleton-body">
        <span />
        <span />
      </div>
      <span className="visually-hidden">
        Loading authoritative operational data
      </span>
    </div>
  );
}
