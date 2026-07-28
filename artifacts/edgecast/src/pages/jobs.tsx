import { useGetJobs } from "@workspace/api-client-react";
import { format } from "date-fns";
import { Clock, ServerCog, SkipForward, XOctagon } from "lucide-react";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { Badge } from "@/components/ui/badge";
import { Skeleton } from "@/components/ui/skeleton";
import { Card } from "@/components/ui/card";

export default function JobsPage() {
  const { data: jobs, isLoading, error } = useGetJobs();

  if (isLoading) {
    return (
      <div className="space-y-6">
        <h1 className="text-3xl font-mono font-bold tracking-tight">JOB HISTORY</h1>
        <Skeleton className="h-[600px] w-full" />
      </div>
    );
  }

  if (error || !jobs) {
    return (
      <div className="p-6 bg-destructive/10 border border-destructive rounded-md text-destructive font-mono">
        ERROR RETRIEVING JOB HISTORY
      </div>
    );
  }

  const getStatusBadge = (status: string) => {
    switch (status) {
      case 'success': return <Badge variant="success" className="font-mono uppercase">SUCCESS</Badge>;
      case 'failed': return <Badge variant="destructive" className="font-mono uppercase">FAILED</Badge>;
      case 'running': return <Badge variant="warning" className="font-mono uppercase animate-pulse">RUNNING</Badge>;
      default: return <Badge variant="outline" className="font-mono uppercase">{status}</Badge>;
    }
  };

  return (
    <div className="space-y-6 animate-in fade-in slide-in-from-bottom-4 duration-500">
      <div className="flex items-center gap-3">
        <ServerCog className="h-8 w-8 text-primary" />
        <h1 className="text-3xl font-mono font-bold tracking-tight">JOB HISTORY</h1>
      </div>

      <Card className="border-border/50">
        <Table>
          <TableHeader className="bg-muted/30">
            <TableRow className="hover:bg-transparent">
              <TableHead className="w-[120px]">STATUS</TableHead>
              <TableHead>JOB TYPE</TableHead>
              <TableHead>STARTED</TableHead>
              <TableHead>
                <span className="flex items-center gap-1"><Clock className="h-3 w-3" />DURATION</span>
              </TableHead>
              <TableHead className="text-right">FOUND</TableHead>
              <TableHead className="text-right">
                <span className="flex items-center justify-end gap-1"><SkipForward className="h-3 w-3" />SKIPPED</span>
              </TableHead>
              <TableHead className="text-right">
                <span className="flex items-center justify-end gap-1"><XOctagon className="h-3 w-3" />REJECTED</span>
              </TableHead>
              <TableHead className="text-right">FORECASTS</TableHead>
              <TableHead className="w-[25%]">MESSAGE</TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {jobs.map((job) => (
              <TableRow key={job.id}>
                <TableCell>{getStatusBadge(job.status)}</TableCell>
                <TableCell className="font-medium text-foreground font-mono">{job.jobType}</TableCell>
                <TableCell className="text-muted-foreground whitespace-nowrap font-mono text-xs">
                  {format(new Date(job.startedAt), "yyyy-MM-dd HH:mm:ss")}
                </TableCell>
                <TableCell className="text-muted-foreground font-mono text-xs">
                  {job.durationSeconds != null
                    ? `${job.durationSeconds.toFixed(2)}s`
                    : job.completedAt
                      ? `${((new Date(job.completedAt).getTime() - new Date(job.startedAt).getTime()) / 1000).toFixed(1)}s`
                      : '-'}
                </TableCell>
                <TableCell className="text-right font-bold text-emerald-400/80 font-mono">{job.marketsFound ?? '-'}</TableCell>
                <TableCell className="text-right font-mono text-muted-foreground">{job.marketsSkipped ?? '-'}</TableCell>
                <TableCell className="text-right font-mono text-orange-400/80">{job.marketsRejected ?? '-'}</TableCell>
                <TableCell className="text-right font-bold text-blue-400/80 font-mono">{job.forecastsRetrieved ?? '-'}</TableCell>
                <TableCell className="text-xs truncate max-w-[200px]" title={job.errorMessage || ''}>
                  {job.status === 'failed' && job.errorMessage ? (
                    <span className="text-destructive font-medium font-mono">{job.errorMessage}</span>
                  ) : (
                    <span className="text-muted-foreground/50 font-mono">—</span>
                  )}
                </TableCell>
              </TableRow>
            ))}
            {jobs.length === 0 && (
              <TableRow>
                <TableCell colSpan={9} className="h-32 text-center text-muted-foreground">
                  NO JOBS RECORDED
                </TableCell>
              </TableRow>
            )}
          </TableBody>
        </Table>
      </Card>
    </div>
  );
}
