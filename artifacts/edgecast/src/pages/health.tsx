import { useGetServiceHealth } from "@workspace/api-client-react";
import { Activity, Server, AlertCircle, CheckCircle2, HelpCircle } from "lucide-react";
import { format } from "date-fns";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";

export default function HealthPage() {
  const { data: services, isLoading, error } = useGetServiceHealth();

  if (isLoading) {
    return (
      <div className="space-y-6">
        <h1 className="text-3xl font-mono font-bold tracking-tight">SYSTEM HEALTH</h1>
        <div className="grid grid-cols-1 md:grid-cols-2 gap-6">
          <Skeleton className="h-40 w-full" />
          <Skeleton className="h-40 w-full" />
        </div>
      </div>
    );
  }

  if (error || !services) {
    return (
      <div className="p-6 bg-destructive/10 border border-destructive rounded-md text-destructive font-mono">
        ERROR RETRIEVING SYSTEM HEALTH
      </div>
    );
  }

  const getStatusIcon = (status: string) => {
    switch (status) {
      case 'ok': return <CheckCircle2 className="h-6 w-6 text-emerald-500" />;
      case 'error': return <AlertCircle className="h-6 w-6 text-destructive" />;
      default: return <HelpCircle className="h-6 w-6 text-muted-foreground" />;
    }
  };

  const getStatusColor = (status: string) => {
    switch (status) {
      case 'ok': return 'border-emerald-500/30 bg-emerald-500/5';
      case 'error': return 'border-destructive/30 bg-destructive/5';
      default: return 'border-border/50 bg-muted/10';
    }
  };

  return (
    <div className="space-y-6 animate-in fade-in slide-in-from-bottom-4 duration-500">
      <div className="flex items-center gap-3">
        <Activity className="h-8 w-8 text-primary" />
        <h1 className="text-3xl font-mono font-bold tracking-tight">SYSTEM HEALTH</h1>
      </div>
      
      <p className="text-muted-foreground font-mono text-sm max-w-2xl">
        Live connection status for external data providers. Evaluated during background jobs.
      </p>

      <div className="grid grid-cols-1 md:grid-cols-2 xl:grid-cols-3 gap-6 pt-4">
        {services.map((svc) => (
          <Card key={svc.name} className={`border ${getStatusColor(svc.status)} backdrop-blur transition-all duration-300`}>
            <CardHeader className="flex flex-row items-start justify-between pb-2">
              <div className="space-y-1">
                <CardTitle className="font-mono text-lg uppercase flex items-center gap-2">
                  <Server className="h-4 w-4 text-muted-foreground" />
                  {svc.name}
                </CardTitle>
                <div className={`font-mono text-xs font-bold uppercase ${
                  svc.status === 'ok' ? 'text-emerald-500' : 
                  svc.status === 'error' ? 'text-destructive' : 'text-muted-foreground'
                }`}>
                  STATUS: {svc.status}
                </div>
              </div>
              {getStatusIcon(svc.status)}
            </CardHeader>
            <CardContent className="pt-4">
              <div className="space-y-4 font-mono text-sm">
                <div>
                  <span className="text-muted-foreground text-xs block mb-1">MESSAGE</span>
                  <div className={`line-clamp-2 ${svc.status === 'error' ? 'text-destructive font-medium' : 'text-foreground'}`}>
                    {svc.message || 'OPERATIONAL'}
                  </div>
                </div>
                <div>
                  <span className="text-muted-foreground text-xs block mb-1">LAST CHECKED</span>
                  <div className="text-muted-foreground">
                    {svc.lastChecked ? format(new Date(svc.lastChecked), "yyyy-MM-dd HH:mm:ss") : 'N/A'}
                  </div>
                </div>
              </div>
            </CardContent>
          </Card>
        ))}
        {services.length === 0 && (
          <div className="col-span-full p-12 text-center text-muted-foreground border border-border/50 border-dashed rounded-lg font-mono">
            NO SERVICES MONITORED
          </div>
        )}
      </div>
    </div>
  );
}
