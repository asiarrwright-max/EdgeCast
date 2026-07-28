import { useGetErrors } from "@workspace/api-client-react";
import { format } from "date-fns";
import { AlertTriangle, TerminalSquare } from "lucide-react";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { Badge } from "@/components/ui/badge";
import { Skeleton } from "@/components/ui/skeleton";
import { Card } from "@/components/ui/card";

export default function ErrorsPage() {
  const { data: errors, isLoading, error } = useGetErrors({ limit: 100 });

  if (isLoading) {
    return (
      <div className="space-y-6">
        <h1 className="text-3xl font-mono font-bold tracking-tight">SYSTEM ERRORS</h1>
        <Skeleton className="h-[600px] w-full" />
      </div>
    );
  }

  if (error || !errors) {
    return (
      <div className="p-6 bg-destructive/10 border border-destructive rounded-md text-destructive font-mono">
        FAILED TO RETRIEVE ERROR LOGS
      </div>
    );
  }

  return (
    <div className="space-y-6 animate-in fade-in slide-in-from-bottom-4 duration-500">
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-3">
          <AlertTriangle className="h-8 w-8 text-destructive" />
          <h1 className="text-3xl font-mono font-bold tracking-tight">SYSTEM ERRORS</h1>
        </div>
        <Badge variant="outline" className="font-mono text-destructive border-destructive/30">
          {errors.length} RECORDED
        </Badge>
      </div>
      
      <Card className="border-destructive/20 shadow-[0_0_15px_-3px_rgba(220,38,38,0.1)]">
        <Table>
          <TableHeader className="bg-destructive/5">
            <TableRow className="hover:bg-transparent border-destructive/20">
              <TableHead className="w-[180px]">OCCURRED AT</TableHead>
              <TableHead className="w-[150px]">TYPE</TableHead>
              <TableHead>MESSAGE</TableHead>
              <TableHead className="w-[30%] text-right">CONTEXT</TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {errors.map((err) => (
              <TableRow key={err.id} className="border-destructive/10 hover:bg-destructive/5 transition-colors">
                <TableCell className="text-muted-foreground whitespace-nowrap">
                  {format(new Date(err.occurredAt), "yyyy-MM-dd HH:mm:ss")}
                </TableCell>
                <TableCell>
                  <Badge variant="destructive" className="font-mono text-[10px] bg-destructive/20 text-destructive hover:bg-destructive/30">
                    {err.errorType}
                  </Badge>
                </TableCell>
                <TableCell className="font-medium text-foreground">
                  {err.message}
                </TableCell>
                <TableCell className="text-right">
                  {err.context ? (
                    <div className="inline-flex items-center justify-end text-xs font-mono text-muted-foreground bg-muted/30 px-2 py-1 rounded w-full overflow-hidden text-ellipsis whitespace-nowrap">
                      <TerminalSquare className="h-3 w-3 mr-2 shrink-0" />
                      <span className="truncate">{err.context}</span>
                    </div>
                  ) : (
                    <span className="text-muted-foreground/30">-</span>
                  )}
                </TableCell>
              </TableRow>
            ))}
            {errors.length === 0 && (
              <TableRow>
                <TableCell colSpan={4} className="h-32 text-center">
                  <div className="flex flex-col items-center justify-center text-emerald-500/70 font-mono">
                    <span className="text-2xl mb-2">0</span>
                    <span>NO SYSTEM ERRORS DETECTED</span>
                  </div>
                </TableCell>
              </TableRow>
            )}
          </TableBody>
        </Table>
      </Card>
    </div>
  );
}
