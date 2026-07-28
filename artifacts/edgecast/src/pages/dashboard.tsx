import { useQueryClient } from "@tanstack/react-query";
import { 
  useGetDashboard, 
  useTriggerCollection,
  getGetDashboardQueryKey,
  getGetMarketsQueryKey,
  getGetJobsQueryKey
} from "@workspace/api-client-react";
import { format } from "date-fns";
import { Activity, CloudRain, Database, RefreshCw, AlertTriangle, AlertCircle, CheckCircle2 } from "lucide-react";
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
        queryClient.invalidateQueries({ queryKey: getGetDashboardQueryKey() });
        queryClient.invalidateQueries({ queryKey: getGetMarketsQueryKey() });
        queryClient.invalidateQueries({ queryKey: getGetJobsQueryKey() });
      }
    });
  };

  if (isLoading) {
    return (
      <div className="space-y-6">
        <h1 className="text-3xl font-mono font-bold tracking-tight">DASHBOARD</h1>
        <div className="grid grid-cols-1 md:grid-cols-3 gap-6">
          <Skeleton className="h-32 w-full" />
          <Skeleton className="h-32 w-full" />
          <Skeleton className="h-32 w-full" />
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

  const { totalActiveMarkets, marketsWithWeather, lastCollectionTime, lastCollectionStatus, markets, recentErrors } = dashboard;

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

      <div className="grid grid-cols-1 md:grid-cols-3 gap-6">
        <Card className="border-border/50 bg-card/50 backdrop-blur">
          <CardHeader className="flex flex-row items-center justify-between pb-2">
            <CardTitle className="text-sm font-medium text-muted-foreground font-mono">ACTIVE MARKETS</CardTitle>
            <Database className="h-4 w-4 text-primary" />
          </CardHeader>
          <CardContent>
            <div className="text-3xl font-mono font-bold">{totalActiveMarkets}</div>
            <p className="text-xs text-muted-foreground mt-1 font-mono">TRACKED ON KALSHI</p>
          </CardContent>
        </Card>

        <Card className="border-border/50 bg-card/50 backdrop-blur">
          <CardHeader className="flex flex-row items-center justify-between pb-2">
            <CardTitle className="text-sm font-medium text-muted-foreground font-mono">WEATHER COVERAGE</CardTitle>
            <CloudRain className="h-4 w-4 text-blue-400" />
          </CardHeader>
          <CardContent>
            <div className="text-3xl font-mono font-bold">{coverage}%</div>
            <p className="text-xs text-muted-foreground mt-1 font-mono">{marketsWithWeather} MARKETS MATCHED</p>
          </CardContent>
        </Card>

        <Card className="border-border/50 bg-card/50 backdrop-blur">
          <CardHeader className="flex flex-row items-center justify-between pb-2">
            <CardTitle className="text-sm font-medium text-muted-foreground font-mono">LAST RUN</CardTitle>
            <Activity className="h-4 w-4 text-emerald-400" />
          </CardHeader>
          <CardContent>
            <div className="flex items-center gap-2">
              <span className="text-xl font-mono font-bold">
                {lastCollectionTime ? format(new Date(lastCollectionTime), "HH:mm:ss") : "NEVER"}
              </span>
              {lastCollectionStatus && (
                <Badge variant={lastCollectionStatus === 'success' ? 'success' : 'destructive'} className="font-mono text-[10px] uppercase">
                  {lastCollectionStatus}
                </Badge>
              )}
            </div>
            <p className="text-xs text-muted-foreground mt-1 font-mono">
              {lastCollectionTime ? format(new Date(lastCollectionTime), "yyyy-MM-dd") : "NO DATA"}
            </p>
          </CardContent>
        </Card>
      </div>

      <div className="grid grid-cols-1 lg:grid-cols-3 gap-6">
        <div className="lg:col-span-2 space-y-4">
          <div className="flex items-center justify-between">
            <h2 className="text-xl font-mono font-bold tracking-tight">MARKET WATCH</h2>
            <Link href="/markets" className="text-xs font-mono text-primary hover:underline">VIEW ALL</Link>
          </div>
          <Card className="border-border/50">
            <div className="overflow-x-auto">
              <table className="w-full text-sm font-mono text-left">
                <thead className="bg-muted/30 border-b border-border text-muted-foreground">
                  <tr>
                    <th className="p-3 font-medium">TICKER</th>
                    <th className="p-3 font-medium">YES ASK</th>
                    <th className="p-3 font-medium">NO ASK</th>
                    <th className="p-3 font-medium">VOL</th>
                    <th className="p-3 font-medium">MATCH</th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-border/50">
                  {markets.slice(0, 5).map(m => (
                    <tr key={m.id} className="hover:bg-muted/20 transition-colors">
                      <td className="p-3">
                        <Link href={`/markets/${m.ticker}`} className="text-primary hover:underline font-bold">
                          {m.ticker}
                        </Link>
                      </td>
                      <td className="p-3 text-emerald-400">{m.yesAsk !== null && m.yesAsk !== undefined ? `${m.yesAsk}¢` : '-'}</td>
                      <td className="p-3 text-red-400">{m.noAsk !== null && m.noAsk !== undefined ? `${m.noAsk}¢` : '-'}</td>
                      <td className="p-3 text-muted-foreground">{m.volume ?? 0}</td>
                      <td className="p-3">
                        {m.weatherMatched ? (
                          <CheckCircle2 className="h-4 w-4 text-emerald-500" />
                        ) : (
                          <span className="text-muted-foreground">-</span>
                        )}
                      </td>
                    </tr>
                  ))}
                  {markets.length === 0 && (
                    <tr>
                      <td colSpan={5} className="p-8 text-center text-muted-foreground">NO ACTIVE MARKETS</td>
                    </tr>
                  )}
                </tbody>
              </table>
            </div>
          </Card>
        </div>

        <div className="space-y-4">
          <div className="flex items-center justify-between">
            <h2 className="text-xl font-mono font-bold tracking-tight">RECENT ERRORS</h2>
            <Link href="/errors" className="text-xs font-mono text-primary hover:underline">VIEW ALL</Link>
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
