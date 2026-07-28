import { useGetJobs } from "@workspace/api-client-react";
import { format } from "date-fns";
import { Activity, Clock, ServerCog } from "lucide-react";
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

  const calculateDuration = (start: string, end?: string | null) => {
    if (!end) return '-';
    const ms = new Date(end).getTime() - new Date(start).getTime();
    if (ms < 1000) return `${ms}ms`;
    return `${(ms / 1000).toFixed(1)}s`;
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
              <TableHead>DURATION</TableHead>
              <TableHead className="text-right">MARKETS</TableHead>
              <TableHead className="text-right">FORECASTS</TableHead>
              <TableHead className="w-[30%]">MESSAGE</TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {jobs.map((job) => (
              <TableRow key={job.id}>
                <TableCell>{getStatusBadge(job.status)}</TableCell>
                <TableCell className="font-medium text-foreground">{job.jobType}</TableCell>
                <TableCell className="text-muted-foreground whitespace-nowrap">
                  {format(new Date(job.startedAt), "yyyy-MM-dd HH:mm:ss")}
                </TableCell>
                <TableCell className="text-muted-foreground flex items-center gap-1">
                  <Clock className="h-3 w-3" />
                  {calculateDuration(job.startedAt, job.completedAt)}
                </TableCell>
                <TableCell className="text-right font-bold text-emerald-400/80">{job.marketsFound ?? '-'}</TableCell>
                <TableCell className="text-right font-bold text-blue-400/80">{job.forecastsRetrieved ?? '-'}</TableCell>
                <TableCell className="text-xs truncate max-w-[200px]" title={job.errorMessage || ''}>
                  {job.status === 'failed' && job.errorMessage ? (
                    <span className="text-destructive font-medium">{job.errorMessage}</span>
                  ) : (
                    <span className="text-muted-foreground/50">OK</span>
                  )}
                </TableCell>
              </TableRow>
            ))}
            {jobs.length === 0 && (
              <TableRow>
                <TableCell colSpan={7} className="h-32 text-center text-muted-foreground">
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
