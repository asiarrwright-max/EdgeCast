import { useParams } from "wouter";
import { useGetMarket, getGetMarketQueryKey } from "@workspace/api-client-react";
import { format } from "date-fns";
import { ArrowLeft, MapPin, Calendar, Clock, Activity, CheckCircle2, Tag } from "lucide-react";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import { Badge } from "@/components/ui/badge";
import { Link } from "wouter";

const PARSING_BADGE: Record<string, { label: string; variant: "success" | "destructive" | "warning" | "outline" | "secondary" }> = {
  collected:     { label: "COLLECTED",   variant: "success" },
  duplicate:     { label: "DUPLICATE",   variant: "secondary" },
  skipped:       { label: "SKIPPED",     variant: "outline" },
  rejected:      { label: "REJECTED",    variant: "warning" },
  parse_failure: { label: "PARSE FAIL",  variant: "destructive" },
};

const MARKET_TYPE_LABEL: Record<string, string> = {
  temperature: "🌡️ Temperature",
  rain: "🌧️ Rain",
  snow: "❄️ Snow",
  wind: "💨 Wind",
};

export default function MarketDetailPage() {
  const { ticker } = useParams<{ ticker: string }>();

  const { data: market, isLoading, error } = useGetMarket(ticker || "", {
    query: {
      enabled: !!ticker,
      queryKey: getGetMarketQueryKey(ticker || "")
    }
  });

  if (isLoading) {
    return (
      <div className="space-y-6">
        <Skeleton className="h-10 w-32" />
        <Skeleton className="h-32 w-full" />
        <div className="grid grid-cols-1 md:grid-cols-2 gap-6">
          <Skeleton className="h-64 w-full" />
          <Skeleton className="h-64 w-full" />
        </div>
      </div>
    );
  }

  if (error || !market) {
    return (
      <div className="p-6 bg-destructive/10 border border-destructive rounded-md text-destructive font-mono">
        ERROR RETRIEVING MARKET DATA FOR {ticker}
      </div>
    );
  }

  const ps = market.parsingStatus ? PARSING_BADGE[market.parsingStatus] : undefined;

  return (
    <div className="space-y-6 animate-in fade-in slide-in-from-bottom-4 duration-500">
      <div>
        <Link href="/markets" className="inline-flex items-center text-sm font-mono text-muted-foreground hover:text-primary mb-4">
          <ArrowLeft className="h-4 w-4 mr-2" />
          BACK TO MARKETS
        </Link>
        <div className="flex flex-col md:flex-row md:items-start justify-between gap-4">
          <div>
            <div className="flex flex-wrap items-center gap-2 mb-2">
              <h1 className="text-3xl font-mono font-bold tracking-tight text-primary">{market.ticker}</h1>
              <Badge variant={market.status === 'active' ? 'success' : 'secondary'} className="uppercase font-mono">
                {market.status}
              </Badge>
              {ps && (
                <Badge variant={ps.variant} className="font-mono text-[10px]">{ps.label}</Badge>
              )}
              {market.weatherMatched && (
                <Badge variant="outline" className="border-emerald-500/30 text-emerald-500 font-mono">
                  <CheckCircle2 className="h-3 w-3 mr-1" />
                  WEATHER MATCHED
                </Badge>
              )}
              {market.weatherMarketType && (
                <Badge variant="outline" className="font-mono text-xs border-blue-500/30 text-blue-400">
                  <Tag className="h-3 w-3 mr-1" />
                  {MARKET_TYPE_LABEL[market.weatherMarketType] ?? market.weatherMarketType.toUpperCase()}
                </Badge>
              )}
            </div>
            <h2 className="text-xl text-foreground font-sans font-medium">{market.title}</h2>
            {market.subtitle && <p className="text-muted-foreground mt-1">{market.subtitle}</p>}
            {market.parsingReason && (
              <p className="mt-2 text-xs font-mono text-orange-400 bg-orange-400/5 border border-orange-400/20 px-2 py-1 rounded">
                ⚠ {market.parsingReason}
              </p>
            )}
          </div>
        </div>
      </div>

      <div className="grid grid-cols-1 lg:grid-cols-3 gap-6">
        <Card className="border-border/50 lg:col-span-2">
          <CardHeader className="border-b border-border/50 bg-muted/10 pb-4">
            <CardTitle className="font-mono text-sm text-muted-foreground flex items-center gap-2">
              <Activity className="h-4 w-4" />
              MARKET METRICS
            </CardTitle>
          </CardHeader>
          <CardContent className="p-0">
            <div className="grid grid-cols-2 sm:grid-cols-4 divide-y sm:divide-y-0 sm:divide-x divide-border/50 font-mono">
              <div className="p-6 flex flex-col justify-center items-center text-center">
                <span className="text-xs text-muted-foreground mb-2">YES ASK</span>
                <span className="text-4xl font-bold text-emerald-400">
                  {market.yesAsk !== null && market.yesAsk !== undefined ? `${market.yesAsk}¢` : '-'}
                </span>
              </div>
              <div className="p-6 flex flex-col justify-center items-center text-center">
                <span className="text-xs text-muted-foreground mb-2">NO ASK</span>
                <span className="text-4xl font-bold text-red-400">
                  {market.noAsk !== null && market.noAsk !== undefined ? `${market.noAsk}¢` : '-'}
                </span>
              </div>
              <div className="p-6 flex flex-col justify-center items-center text-center">
                <span className="text-xs text-muted-foreground mb-2">YES BID</span>
                <span className="text-2xl font-bold text-foreground">
                  {market.yesBid !== null && market.yesBid !== undefined ? `${market.yesBid}¢` : '-'}
                </span>
              </div>
              <div className="p-6 flex flex-col justify-center items-center text-center">
                <span className="text-xs text-muted-foreground mb-2">VOLUME</span>
                <span className="text-2xl font-bold text-foreground">{market.volume ?? 0}</span>
              </div>
            </div>
          </CardContent>
        </Card>

        <Card className="border-border/50">
          <CardHeader className="border-b border-border/50 bg-muted/10 pb-4">
            <CardTitle className="font-mono text-sm text-muted-foreground flex items-center gap-2">
              <MapPin className="h-4 w-4" />
              EVENT DETAILS
            </CardTitle>
          </CardHeader>
          <CardContent className="p-6 space-y-4 font-mono text-sm">
            <div>
              <span className="text-muted-foreground text-xs block mb-1">CITY TARGET</span>
              <div className="font-bold text-lg">{market.city || 'N/A'}</div>
            </div>

            <div className="grid grid-cols-2 gap-4">
              <div>
                <span className="text-muted-foreground text-xs block mb-1 flex items-center gap-1">
                  <Calendar className="h-3 w-3" />
                  DATE
                </span>
                <div className="font-medium">
                  {market.targetDate ? format(new Date(market.targetDate), "yyyy-MM-dd") : 'N/A'}
                </div>
              </div>
              <div>
                <span className="text-muted-foreground text-xs block mb-1 flex items-center gap-1">
                  <Clock className="h-3 w-3" />
                  CLOSES
                </span>
                <div className="font-medium">
                  {market.closeTime ? format(new Date(market.closeTime), "HH:mm") : 'N/A'}
                </div>
              </div>
            </div>

            <div>
              <span className="text-muted-foreground text-xs block mb-1">EVENT TICKER</span>
              <div className="text-primary">{market.eventTicker || 'N/A'}</div>
            </div>

            {market.collectionTimestamp && (
              <div>
                <span className="text-muted-foreground text-xs block mb-1">COLLECTED AT</span>
                <div className="text-muted-foreground text-xs">
                  {format(new Date(market.collectionTimestamp), "yyyy-MM-dd HH:mm:ss")}
                </div>
              </div>
            )}

            <div className="pt-4 border-t border-border/50">
              <span className="text-muted-foreground text-xs block mb-1">LAST UPDATED</span>
              <div className="text-muted-foreground text-xs">
                {market.lastUpdated ? format(new Date(market.lastUpdated), "yyyy-MM-dd HH:mm:ss") : 'N/A'}
              </div>
            </div>
          </CardContent>
        </Card>
      </div>
    </div>
  );
}
