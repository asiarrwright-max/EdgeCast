import { useQueryClient } from "@tanstack/react-query";
import {
  useGetDashboard,
  useTriggerCollection,
  getGetDashboardQueryKey,
  getGetMarketsQueryKey,
  getGetJobsQueryKey
} from "@workspace/api-client-react";
import { format } from "date-fns";
import {
  Activity, CloudRain, Database, RefreshCw, AlertTriangle,
  AlertCircle, CheckCircle2, Clock, SkipForward, XOctagon, TrendingUp
} from "lucide-react";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { Skeleton } from "@/components/ui/skeleton";
import { Link } from "wouter";

export default function DashboardPage() {
  const queryClient = useQueryClient();
  const { data: dashboard, isLoading, error } = useGetDashboard();
  const triggerMutation = useTriggerCollection();

  const handleTrigger = () => {
    triggerMutation.mutate(undefined, {
      onSuccess: () => {
        setTimeout(() => {
          queryClient.invalidateQueries({ queryKey: getGetDashboardQueryKey() });
          queryClient.invalidateQueries({ queryKey: getGetMarketsQueryKey() });
          queryClient.invalidateQueries({ queryKey: getGetJobsQueryKey() });
        }, 8000); // give collector time to finish
      }
    });
  };

  if (isLoading) {
    return (
      <div className="space-y-6">
        <h1 className="text-3xl font-mono font-bold tracking-tight">DASHBOARD</h1>
        <div className="grid grid-cols-2 md:grid-cols-4 gap-4">
          {Array.from({ length: 6 }).map((_, i) => <Skeleton key={i} className="h-28 w-full" />)}
        </div>
        <Skeleton className="h-96 w-full" />
      </div>
    );
  }

  if (error || !dashboard) {
    return (
      <div className="p-6 bg-destructive/10 border border-destructive rounded-md text-destructive flex items-center gap-3">
        <AlertTriangle className="h-5 w-5" />
        <span className="font-mono">ERROR RETRIEVING DASHBOARD METRICS</span>
      </div>
    );
  }

  const {
    totalActiveMarkets,
    marketsWithWeather,
    marketsCollected,
    marketsParseFailures,
    lastCollectionTime,
    lastCollectionStatus,
    lastCollectionDuration,
    lastCollectionMarketsFound,
    lastCollectionMarketsSkipped,
    collectionSummary,
    markets,
    recentErrors,
    lastJob,
  } = dashboard;

  const coverage = totalActiveMarkets > 0 ? Math.round((marketsWithWeather / totalActiveMarkets) * 100) : 0;

  return (
    <div className="space-y-6 animate-in fade-in slide-in-from-bottom-4 duration-500">
      <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-4">
        <h1 className="text-3xl font-mono font-bold tracking-tight">DASHBOARD</h1>
        <Button
          onClick={handleTrigger}
          disabled={triggerMutation.isPending}
          className="font-mono"
        >
          <RefreshCw className={`h-4 w-4 mr-2 ${triggerMutation.isPending ? "animate-spin" : ""}`} />
          {triggerMutation.isPending ? "COLLECTING..." : "RUN COLLECTION NOW"}
        </Button>
      </div>

      {collectionSummary && (
        <div className="flex items-start gap-2 p-3 rounded-md border border-yellow-500/30 bg-yellow-500/5 text-yellow-400 text-sm font-mono">
          <AlertCircle className="h-4 w-4 mt-0.5 shrink-0" />
          {collectionSummary}
        </div>
      )}

      {/* Stat cards */}
      <div className="grid grid-cols-2 md:grid-cols-3 lg:grid-cols-6 gap-4">
        <Card className="border-border/50 bg-card/50 backdrop-blur">
          <CardHeader className="flex flex-row items-center justify-between pb-2 space-y-0">
            <CardTitle className="text-xs font-mono text-muted-foreground">ACTIVE</CardTitle>
            <Database className="h-3 w-3 text-primary" />
          </CardHeader>
          <CardContent>
            <div className="text-2xl font-mono font-bold">{totalActiveMarkets}</div>
            <p className="text-[10px] text-muted-foreground font-mono">MARKETS</p>
          </CardContent>
        </Card>

        <Card className="border-border/50 bg-card/50 backdrop-blur">
          <CardHeader className="flex flex-row items-center justify-between pb-2 space-y-0">
            <CardTitle className="text-xs font-mono text-muted-foreground">COVERAGE</CardTitle>
            <CloudRain className="h-3 w-3 text-blue-400" />
          </CardHeader>
          <CardContent>
            <div className="text-2xl font-mono font-bold">{coverage}%</div>
            <p className="text-[10px] text-muted-foreground font-mono">{marketsWithWeather} MATCHED</p>
          </CardContent>
        </Card>

        <Card className="border-border/50 bg-card/50 backdrop-blur">
          <CardHeader className="flex flex-row items-center justify-between pb-2 space-y-0">
            <CardTitle className="text-xs font-mono text-muted-foreground">COLLECTED</CardTitle>
            <TrendingUp className="h-3 w-3 text-emerald-400" />
          </CardHeader>
          <CardContent>
            <div className="text-2xl font-mono font-bold">{marketsCollected ?? 0}</div>
            <p className="text-[10px] text-muted-foreground font-mono">THIS RUN</p>
          </CardContent>
        </Card>

        <Card className="border-border/50 bg-card/50 backdrop-blur">
          <CardHeader className="flex flex-row items-center justify-between pb-2 space-y-0">
            <CardTitle className="text-xs font-mono text-muted-foreground">PARSE FAIL</CardTitle>
            <XOctagon className="h-3 w-3 text-orange-400" />
          </CardHeader>
          <CardContent>
            <div className="text-2xl font-mono font-bold text-orange-400">{marketsParseFailures ?? 0}</div>
            <p className="text-[10px] text-muted-foreground font-mono">NO CITY MATCH</p>
          </CardContent>
        </Card>

        <Card className="border-border/50 bg-card/50 backdrop-blur">
          <CardHeader className="flex flex-row items-center justify-between pb-2 space-y-0">
            <CardTitle className="text-xs font-mono text-muted-foreground">SKIPPED</CardTitle>
            <SkipForward className="h-3 w-3 text-muted-foreground" />
          </CardHeader>
          <CardContent>
            <div className="text-2xl font-mono font-bold text-muted-foreground">{lastCollectionMarketsSkipped ?? '—'}</div>
            <p className="text-[10px] text-muted-foreground font-mono">NON-WEATHER</p>
          </CardContent>
        </Card>

        <Card className="border-border/50 bg-card/50 backdrop-blur">
          <CardHeader className="flex flex-row items-center justify-between pb-2 space-y-0">
            <CardTitle className="text-xs font-mono text-muted-foreground">LAST RUN</CardTitle>
            <Activity className="h-3 w-3 text-emerald-400" />
          </CardHeader>
          <CardContent>
            <div className="flex items-center gap-1">
              <span className="text-lg font-mono font-bold">
                {lastCollectionTime ? format(new Date(lastCollectionTime), "HH:mm") : "—"}
              </span>
              {lastCollectionStatus && (
                <Badge variant={lastCollectionStatus === 'success' ? 'success' : 'destructive'} className="font-mono text-[9px]">
                  {lastCollectionStatus === 'success' ? '✓' : '✗'}
                </Badge>
              )}
            </div>
            <p className="text-[10px] text-muted-foreground font-mono">
              {lastCollectionDuration != null ? `${lastCollectionDuration.toFixed(1)}s` : ''}
              {lastCollectionMarketsFound != null ? ` · ${lastCollectionMarketsFound} found` : ''}
            </p>
          </CardContent>
        </Card>
      </div>

      <div className="grid grid-cols-1 lg:grid-cols-3 gap-6">
        <div className="lg:col-span-2 space-y-4">
          <div className="flex items-center justify-between">
            <h2 className="text-xl font-mono font-bold tracking-tight">MARKET WATCH</h2>
            <Link href="/markets" className="text-xs font-mono text-primary hover:underline">VIEW ALL →</Link>
          </div>
          <Card className="border-border/50">
            <div className="overflow-x-auto">
              <table className="w-full text-sm font-mono text-left">
                <thead className="bg-muted/30 border-b border-border text-muted-foreground">
                  <tr>
                    <th className="p-3 font-medium">TICKER</th>
                    <th className="p-3 font-medium">CITY</th>
                    <th className="p-3 font-medium">YES ASK</th>
                    <th className="p-3 font-medium">NO ASK</th>
                    <th className="p-3 font-medium">VOL</th>
                    <th className="p-3 font-medium text-center">MATCH</th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-border/50">
                  {markets.slice(0, 8).map(m => (
                    <tr key={m.id} className="hover:bg-muted/20 transition-colors">
                      <td className="p-3">
                        <Link href={`/markets/${m.ticker}`} className="text-primary hover:underline font-bold">
                          {m.ticker}
                        </Link>
                      </td>
                      <td className="p-3 text-muted-foreground text-xs">{m.city || '—'}</td>
                      <td className="p-3 text-emerald-400">{m.yesAsk !== null && m.yesAsk !== undefined ? `${m.yesAsk}¢` : '-'}</td>
                      <td className="p-3 text-red-400">{m.noAsk !== null && m.noAsk !== undefined ? `${m.noAsk}¢` : '-'}</td>
                      <td className="p-3 text-muted-foreground">{m.volume ?? 0}</td>
                      <td className="p-3 text-center">
                        {m.weatherMatched ? (
                          <CheckCircle2 className="h-4 w-4 text-emerald-500 inline" />
                        ) : (
                          <span className="text-muted-foreground">—</span>
                        )}
                      </td>
                    </tr>
                  ))}
                  {markets.length === 0 && (
                    <tr>
                      <td colSpan={6} className="p-8 text-center text-muted-foreground">NO ACTIVE MARKETS</td>
                    </tr>
                  )}
                </tbody>
              </table>
            </div>
          </Card>
        </div>

        <div className="space-y-4">
          {/* Last job summary */}
          {lastJob && (
            <div className="space-y-2">
              <div className="flex items-center justify-between">
                <h2 className="text-sm font-mono font-bold tracking-tight text-muted-foreground">LAST JOB</h2>
                <Link href="/jobs" className="text-xs font-mono text-primary hover:underline">VIEW ALL →</Link>
              </div>
              <Card className="border-border/50 p-4 font-mono text-xs space-y-2">
                <div className="flex items-center justify-between">
                  <span className="text-muted-foreground">STARTED</span>
                  <span>{format(new Date(lastJob.startedAt), "HH:mm:ss")}</span>
                </div>
                <div className="flex items-center justify-between">
                  <span className="text-muted-foreground">DURATION</span>
                  <span>{lastJob.durationSeconds != null ? `${lastJob.durationSeconds.toFixed(2)}s` : '—'}</span>
                </div>
                <div className="flex items-center justify-between">
                  <span className="text-muted-foreground">FOUND</span>
                  <span className="text-emerald-400 font-bold">{lastJob.marketsFound ?? '—'}</span>
                </div>
                <div className="flex items-center justify-between">
                  <span className="text-muted-foreground">SKIPPED</span>
                  <span>{lastJob.marketsSkipped ?? '—'}</span>
                </div>
                <div className="flex items-center justify-between">
                  <span className="text-muted-foreground">FORECASTS</span>
                  <span className="text-blue-400 font-bold">{lastJob.forecastsRetrieved ?? '—'}</span>
                </div>
              </Card>
            </div>
          )}

          <div className="flex items-center justify-between">
            <h2 className="text-sm font-mono font-bold tracking-tight text-muted-foreground">RECENT ERRORS</h2>
            <Link href="/errors" className="text-xs font-mono text-primary hover:underline">VIEW ALL →</Link>
          </div>
          <Card className="border-border/50">
            <div className="divide-y divide-border/50">
              {recentErrors.slice(0, 4).map(err => (
                <div key={err.id} className="p-3 text-sm">
                  <div className="flex items-start gap-2 mb-1">
                    <AlertCircle className="h-4 w-4 text-destructive shrink-0 mt-0.5" />
                    <span className="font-mono font-bold truncate">{err.errorType}</span>
                  </div>
                  <p className="text-xs text-muted-foreground line-clamp-2 ml-6">{err.message}</p>
                  <p className="text-[10px] text-muted-foreground/60 font-mono mt-2 ml-6">
                    {format(new Date(err.occurredAt), "HH:mm:ss yyyy-MM-dd")}
                  </p>
                </div>
              ))}
              {recentErrors.length === 0 && (
                <div className="p-6 text-center text-muted-foreground font-mono text-sm">
                  NO RECENT ERRORS
                </div>
              )}
            </div>
          </Card>
        </div>
      </div>
    </div>
  );
}
