import { useState, useMemo } from "react";
import { useGetMarkets } from "@workspace/api-client-react";
import { format } from "date-fns";
import { Link } from "wouter";
import { CheckCircle2, XCircle, ExternalLink, AlertCircle, ArrowUpDown, ArrowUp, ArrowDown } from "lucide-react";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { Badge } from "@/components/ui/badge";
import { Skeleton } from "@/components/ui/skeleton";
import { Card } from "@/components/ui/card";

const PARSING_STATUS_BADGE: Record<string, { label: string; variant: "success" | "destructive" | "warning" | "outline" | "secondary" }> = {
  collected:    { label: "COLLECTED",  variant: "success" },
  duplicate:    { label: "DUPLICATE",  variant: "secondary" },
  skipped:      { label: "SKIPPED",    variant: "outline" },
  rejected:     { label: "REJECTED",   variant: "warning" },
  parse_failure:{ label: "PARSE FAIL", variant: "destructive" },
};

const MARKET_TYPE_BADGE: Record<string, string> = {
  temperature: "🌡️",
  rain: "🌧️",
  snow: "❄️",
  wind: "💨",
};

const CONFIDENCE_COLOR: Record<string, string> = {
  "Very High": "text-emerald-400",
  "High":      "text-green-400",
  "Medium":    "text-yellow-400",
  "Low":       "text-orange-400",
  "Very Low":  "text-red-400",
};

const ANALYSIS_BADGE: Record<string, { label: string; variant: "success" | "destructive" | "outline" | "secondary" }> = {
  supported:   { label: "ANALYZED",    variant: "success" },
  unsupported: { label: "UNSUPPORTED", variant: "secondary" },
  no_forecast: { label: "NO FORECAST", variant: "outline" },
};

type SortKey = "ticker" | "city" | "targetDate" | "ecProbability" | "marketProbability" | "probabilityDiff" | "confidence";
type SortDir = "asc" | "desc";

const CONFIDENCE_ORDER: Record<string, number> = {
  "Very Low": 0, "Low": 1, "Medium": 2, "High": 3, "Very High": 4,
};

function pct(v: number | null | undefined): string {
  if (v === null || v === undefined) return "-";
  return `${(v * 100).toFixed(1)}%`;
}

function diffPct(v: number | null | undefined): string {
  if (v === null || v === undefined) return "-";
  const p = v * 100;
  return `${p >= 0 ? "+" : ""}${p.toFixed(1)}pp`;
}

export default function MarketsPage() {
  const { data, isLoading, error } = useGetMarkets();
  const [sortKey, setSortKey] = useState<SortKey>("targetDate");
  const [sortDir, setSortDir] = useState<SortDir>("asc");

  const handleSort = (key: SortKey) => {
    if (sortKey === key) {
      setSortDir(d => d === "asc" ? "desc" : "asc");
    } else {
      setSortKey(key);
      setSortDir(key === "probabilityDiff" ? "desc" : "asc");
    }
  };

  const sortedMarkets = useMemo(() => {
    if (!data?.markets) return [];
    return [...data.markets].sort((a: any, b: any) => {
      let av: any, bv: any;
      if (sortKey === "confidence") {
        av = CONFIDENCE_ORDER[a.confidence ?? ""] ?? -1;
        bv = CONFIDENCE_ORDER[b.confidence ?? ""] ?? -1;
      } else {
        av = a[sortKey] ?? "";
        bv = b[sortKey] ?? "";
      }
      if (av === bv) return 0;
      const cmp = av < bv ? -1 : 1;
      return sortDir === "asc" ? cmp : -cmp;
    });
  }, [data?.markets, sortKey, sortDir]);

  if (isLoading) {
    return (
      <div className="space-y-6">
        <h1 className="text-3xl font-mono font-bold tracking-tight">MARKETS</h1>
        <Skeleton className="h-[600px] w-full" />
      </div>
    );
  }

  if (error || !data) {
    return (
      <div className="p-6 bg-destructive/10 border border-destructive rounded-md text-destructive font-mono">
        ERROR RETRIEVING MARKETS
      </div>
    );
  }

  const SortIcon = ({ k }: { k: SortKey }) => {
    if (sortKey !== k) return <ArrowUpDown className="ml-1 h-3 w-3 opacity-40 inline" />;
    return sortDir === "asc"
      ? <ArrowUp className="ml-1 h-3 w-3 text-primary inline" />
      : <ArrowDown className="ml-1 h-3 w-3 text-primary inline" />;
  };

  const Th = ({ k, children, className }: { k: SortKey; children: React.ReactNode; className?: string }) => (
    <TableHead
      className={`cursor-pointer select-none hover:text-foreground transition-colors ${className ?? ""}`}
      onClick={() => handleSort(k)}
    >
      {children}<SortIcon k={k} />
    </TableHead>
  );

  return (
    <div className="space-y-6 animate-in fade-in slide-in-from-bottom-4 duration-500">
      <div className="flex items-center justify-between">
        <h1 className="text-3xl font-mono font-bold tracking-tight">MARKETS</h1>
        <Badge variant="outline" className="font-mono">{data.markets.length} COLLECTED</Badge>
      </div>

      {data.summary && (
        <div className="flex items-start gap-2 p-3 rounded-md border border-yellow-500/30 bg-yellow-500/5 text-yellow-400 text-sm font-mono">
          <AlertCircle className="h-4 w-4 mt-0.5 shrink-0" />
          {data.summary}
        </div>
      )}

      <Card className="border-border/50">
        <div className="overflow-x-auto">
          <Table>
            <TableHeader className="bg-muted/30">
              <TableRow className="hover:bg-transparent">
                <Th k="ticker">TICKER</Th>
                <Th k="city">CITY</Th>
                <TableHead>TYPE</TableHead>
                <TableHead>STATUS</TableHead>
                <Th k="targetDate">DATE</Th>
                <Th k="ecProbability" className="text-right">EC PROB</Th>
                <Th k="marketProbability" className="text-right">MKT PROB</Th>
                <Th k="probabilityDiff" className="text-right">DIFF</Th>
                <Th k="confidence">CONFIDENCE</Th>
                <TableHead>ANALYSIS</TableHead>
                <TableHead className="text-center">MATCH</TableHead>
                <TableHead></TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {sortedMarkets.map((m: any) => {
                const ps = m.parsingStatus ? PARSING_STATUS_BADGE[m.parsingStatus] : undefined;
                const typeEmoji = m.weatherMarketType ? MARKET_TYPE_BADGE[m.weatherMarketType] : null;
                const ab = m.analysisStatus ? ANALYSIS_BADGE[m.analysisStatus] : undefined;
                const diffVal = m.probabilityDiff as number | null;
                const diffColor = diffVal === null || diffVal === undefined ? "" :
                  diffVal > 0.05 ? "text-emerald-400 font-bold" :
                  diffVal < -0.05 ? "text-red-400 font-bold" :
                  "text-muted-foreground";
                return (
                  <TableRow key={m.id}>
                    <TableCell className="font-bold text-primary whitespace-nowrap">
                      <Link href={`/markets/${m.ticker}`} className="hover:underline">
                        {m.ticker}
                      </Link>
                    </TableCell>
                    <TableCell className="whitespace-nowrap">{m.city || '-'}</TableCell>
                    <TableCell className="text-center">
                      <span title={m.weatherMarketType || ''}>{typeEmoji || '-'}</span>
                    </TableCell>
                    <TableCell>
                      {ps ? (
                        <Badge variant={ps.variant} className="font-mono text-[10px]">{ps.label}</Badge>
                      ) : (
                        <span className="text-muted-foreground text-xs">-</span>
                      )}
                    </TableCell>
                    <TableCell className="whitespace-nowrap">
                      {m.targetDate ? format(new Date(m.targetDate), "yyyy-MM-dd") : '-'}
                    </TableCell>
                    <TableCell className="text-right font-mono font-bold text-blue-400">
                      {pct(m.ecProbability)}
                    </TableCell>
                    <TableCell className="text-right font-mono text-muted-foreground">
                      {pct(m.marketProbability)}
                    </TableCell>
                    <TableCell className={`text-right font-mono ${diffColor}`}>
                      {diffPct(m.probabilityDiff)}
                    </TableCell>
                    <TableCell className={`font-mono text-xs ${CONFIDENCE_COLOR[m.confidence ?? ""] ?? "text-muted-foreground"}`}>
                      {m.confidence ?? '-'}
                    </TableCell>
                    <TableCell>
                      {ab ? (
                        <Badge variant={ab.variant} className="font-mono text-[10px]">{ab.label}</Badge>
                      ) : (
                        <span className="text-muted-foreground text-xs">-</span>
                      )}
                    </TableCell>
                    <TableCell className="text-center">
                      {m.weatherMatched ? (
                        <CheckCircle2 className="h-4 w-4 mx-auto text-emerald-500" />
                      ) : (
                        <XCircle className="h-4 w-4 mx-auto text-muted-foreground/30" />
                      )}
                    </TableCell>
                    <TableCell>
                      <Link href={`/markets/${m.ticker}`} className="text-muted-foreground hover:text-primary transition-colors flex justify-end">
                        <ExternalLink className="h-4 w-4" />
                      </Link>
                    </TableCell>
                  </TableRow>
                );
              })}
              {sortedMarkets.length === 0 && (
                <TableRow>
                  <TableCell colSpan={12} className="h-32 text-center text-muted-foreground">
                    NO MARKETS FOUND
                  </TableCell>
                </TableRow>
              )}
            </TableBody>
          </Table>
        </div>
      </Card>
    </div>
  );
}
