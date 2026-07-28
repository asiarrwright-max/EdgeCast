import { useGetMarkets } from "@workspace/api-client-react";
import { format } from "date-fns";
import { Link } from "wouter";
import { CheckCircle2, XCircle, ExternalLink } from "lucide-react";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { Badge } from "@/components/ui/badge";
import { Skeleton } from "@/components/ui/skeleton";
import { Card } from "@/components/ui/card";

export default function MarketsPage() {
  const { data: markets, isLoading, error } = useGetMarkets();

  if (isLoading) {
    return (
      <div className="space-y-6">
        <h1 className="text-3xl font-mono font-bold tracking-tight">MARKETS</h1>
        <Skeleton className="h-[600px] w-full" />
      </div>
    );
  }

  if (error || !markets) {
    return (
      <div className="p-6 bg-destructive/10 border border-destructive rounded-md text-destructive font-mono">
        ERROR RETRIEVING MARKETS
      </div>
    );
  }

  return (
    <div className="space-y-6 animate-in fade-in slide-in-from-bottom-4 duration-500">
      <div className="flex items-center justify-between">
        <h1 className="text-3xl font-mono font-bold tracking-tight">MARKETS</h1>
        <Badge variant="outline" className="font-mono">{markets.length} ACTIVE</Badge>
      </div>

      <Card className="border-border/50">
        <Table>
          <TableHeader className="bg-muted/30">
            <TableRow className="hover:bg-transparent">
              <TableHead>TICKER</TableHead>
              <TableHead>CITY</TableHead>
              <TableHead>TARGET DATE</TableHead>
              <TableHead>CLOSES</TableHead>
              <TableHead className="text-right">YES ASK</TableHead>
              <TableHead className="text-right">NO ASK</TableHead>
              <TableHead className="text-right">VOL</TableHead>
              <TableHead className="text-center">MATCH</TableHead>
              <TableHead></TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {markets.map((m) => (
              <TableRow key={m.id}>
                <TableCell className="font-bold text-primary">
                  <Link href={`/markets/${m.ticker}`} className="hover:underline">
                    {m.ticker}
                  </Link>
                </TableCell>
                <TableCell>{m.city || '-'}</TableCell>
                <TableCell>{m.targetDate ? format(new Date(m.targetDate), "yyyy-MM-dd") : '-'}</TableCell>
                <TableCell>{m.closeTime ? format(new Date(m.closeTime), "HH:mm") : '-'}</TableCell>
                <TableCell className="text-right text-emerald-400 font-bold">
                  {m.yesAsk !== null && m.yesAsk !== undefined ? `${m.yesAsk}¢` : '-'}
                </TableCell>
                <TableCell className="text-right text-red-400 font-bold">
                  {m.noAsk !== null && m.noAsk !== undefined ? `${m.noAsk}¢` : '-'}
                </TableCell>
                <TableCell className="text-right text-muted-foreground">{m.volume ?? 0}</TableCell>
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
            ))}
            {markets.length === 0 && (
              <TableRow>
                <TableCell colSpan={9} className="h-32 text-center text-muted-foreground">
                  NO MARKETS FOUND
                </TableCell>
              </TableRow>
            )}
          </TableBody>
        </Table>
      </Card>
    </div>
  );
}
