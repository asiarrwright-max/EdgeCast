import { useMemo, useState } from "react";
import { useQueryClient } from "@tanstack/react-query";
import {
  autonomyQueryKey,
  useApproveYellowProposal,
  useGetAutonomySnapshot,
  useRejectYellowProposal,
  type AutonomyWorkItem,
  type YellowProposal,
} from "@workspace/api-client-react";
import {
  AlertTriangle,
  CheckCircle2,
  Clock3,
  ExternalLink,
  GitPullRequest,
  ShieldAlert,
  Sparkles,
} from "lucide-react";
import { format } from "date-fns";

import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";
import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
} from "@/components/ui/alert-dialog";

function fmtTime(value: string | null | undefined): string {
  if (!value) return "Not available";
  return format(new Date(value), "yyyy-MM-dd HH:mm");
}

function SummaryCard({
  title,
  value,
  note,
  accent,
}: {
  title: string;
  value: string | number;
  note: string;
  accent: string;
}) {
  return (
    <Card className="border-border/60 bg-card/70">
      <CardHeader className="pb-2">
        <CardDescription className="text-xs uppercase tracking-wide">{title}</CardDescription>
        <CardTitle className={`text-3xl font-mono ${accent}`}>{value}</CardTitle>
      </CardHeader>
      <CardContent className="text-sm text-muted-foreground">{note}</CardContent>
    </Card>
  );
}

function WorkList({
  items,
  emptyMessage,
  accent,
}: {
  items: AutonomyWorkItem[];
  emptyMessage: string;
  accent: "green" | "red";
}) {
  if (items.length === 0) {
    return (
      <div className="rounded-lg border border-dashed border-border p-6 text-sm text-muted-foreground">
        {emptyMessage}
      </div>
    );
  }

  const badgeVariant = accent === "green" ? "success" : "destructive";
  return (
    <div className="space-y-4">
      {items.map((item) => (
        <Card key={`${accent}-${item.number}`} className="border-border/60 bg-card/70">
          <CardHeader className="space-y-3">
            <div className="flex flex-col gap-3 sm:flex-row sm:items-start sm:justify-between">
              <div className="space-y-2">
                <div className="flex flex-wrap items-center gap-2">
                  <Badge variant={badgeVariant}>
                    {item.isPullRequest ? "Pull request" : "Issue"} #{item.number}
                  </Badge>
                  <span className="text-xs text-muted-foreground">Updated {fmtTime(item.updatedAt)}</span>
                </div>
                <CardTitle className="text-xl">{item.title}</CardTitle>
                <CardDescription>{item.summary}</CardDescription>
              </div>
              <Button asChild variant="outline" className="w-full sm:w-auto">
                <a href={item.htmlUrl} target="_blank" rel="noreferrer">
                  View details
                  <ExternalLink className="ml-2 h-4 w-4" />
                </a>
              </Button>
            </div>
          </CardHeader>
          <CardContent>
            <p className="text-sm">{item.statusText}</p>
          </CardContent>
        </Card>
      ))}
    </div>
  );
}

function YellowProposalCard({
  proposal,
  readOnly,
  approvePending,
  rejectPending,
  onApprove,
  onReject,
}: {
  proposal: YellowProposal;
  readOnly: boolean;
  approvePending: boolean;
  rejectPending: boolean;
  onApprove: () => void;
  onReject: () => void;
}) {
  const statusVariant = proposal.ownerApproved ? "success" : "warning";
  return (
    <Card className="border-amber-500/30 bg-amber-500/5">
      <CardHeader className="space-y-4">
        <div className="flex flex-col gap-3 md:flex-row md:items-start md:justify-between">
          <div className="space-y-3">
            <div className="flex flex-wrap items-center gap-2">
              <Badge variant="warning">Needs owner decision</Badge>
              <Badge variant={statusVariant}>{proposal.riskState}</Badge>
              <span className="text-xs text-muted-foreground">Updated {fmtTime(proposal.updatedAt)}</span>
            </div>
            <div className="space-y-1">
              <CardTitle className="text-xl">{proposal.title}</CardTitle>
              <CardDescription>{proposal.summary}</CardDescription>
            </div>
          </div>
          <Button asChild variant="outline" className="w-full md:w-auto">
            <a href={proposal.htmlUrl} target="_blank" rel="noreferrer">
              View details
              <ExternalLink className="ml-2 h-4 w-4" />
            </a>
          </Button>
        </div>
      </CardHeader>
      <CardContent className="space-y-4">
        <div className="grid gap-4 md:grid-cols-2">
          <div className="space-y-1">
            <p className="text-xs uppercase tracking-wide text-muted-foreground">Why owner approval is required</p>
            <p className="text-sm">{proposal.whyYellow}</p>
          </div>
          <div className="space-y-1">
            <p className="text-xs uppercase tracking-wide text-muted-foreground">Affected area</p>
            <p className="text-sm">{proposal.affectedArea}</p>
          </div>
          <div className="space-y-1">
            <p className="text-xs uppercase tracking-wide text-muted-foreground">Expected impact / evidence</p>
            <p className="text-sm">{proposal.expectedImpact || "No impact or evidence summary was provided in the pull request body."}</p>
          </div>
          <div className="space-y-1">
            <p className="text-xs uppercase tracking-wide text-muted-foreground">Current checks</p>
            <p className="text-sm">{proposal.ciState}</p>
          </div>
        </div>

        <div className="flex flex-col gap-3 sm:flex-row">
          <Button
            onClick={onApprove}
            disabled={readOnly || approvePending || rejectPending}
            className="w-full sm:w-auto"
          >
            Approve proposal
          </Button>
          <Button
            variant="outline"
            onClick={onReject}
            disabled={readOnly || approvePending || rejectPending}
            className="w-full sm:w-auto"
          >
            Reject / keep blocked
          </Button>
        </div>
      </CardContent>
    </Card>
  );
}

export default function AutonomyPage() {
  const queryClient = useQueryClient();
  const { data, isLoading, error } = useGetAutonomySnapshot();
  const approveMutation = useApproveYellowProposal();
  const rejectMutation = useRejectYellowProposal();
  const [confirmingProposal, setConfirmingProposal] = useState<YellowProposal | null>(null);

  const busyProposalNumber = useMemo(() => {
    if (typeof approveMutation.variables === "number") return approveMutation.variables;
    if (typeof rejectMutation.variables === "number") return rejectMutation.variables;
    return null;
  }, [approveMutation.variables, rejectMutation.variables]);

  const refresh = async () => {
    await queryClient.invalidateQueries({ queryKey: autonomyQueryKey });
  };

  if (isLoading) {
    return (
      <div className="space-y-6">
        <div className="space-y-2">
          <Skeleton className="h-10 w-48" />
          <Skeleton className="h-5 w-full max-w-2xl" />
        </div>
        <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 xl:grid-cols-5">
          {Array.from({ length: 5 }).map((_, index) => (
            <Skeleton key={index} className="h-32 w-full" />
          ))}
        </div>
        <Skeleton className="h-72 w-full" />
      </div>
    );
  }

  if (error || !data) {
    return (
      <div className="rounded-lg border border-destructive/40 bg-destructive/10 p-6 text-destructive">
        We could not load autonomy status right now.
      </div>
    );
  }

  const { summary } = data;

  return (
    <div className="space-y-8">
      <section className="space-y-3">
        <div className="flex items-center gap-3">
          <Sparkles className="h-8 w-8 text-primary" />
          <h1 className="text-3xl font-bold tracking-tight">Autonomy</h1>
        </div>
        <p className="max-w-3xl text-sm text-muted-foreground md:text-base">
          Review autonomous work in plain language. Owner approval here only changes the
          <code className="mx-1 rounded bg-muted px-1 py-0.5 text-xs">owner-approved-yellow</code>
          label. It never merges, deploys, or enables real-money execution.
        </p>
      </section>

      {data.readOnly && (
        <div className="rounded-lg border border-amber-500/40 bg-amber-500/10 p-4 text-sm text-amber-200">
          {data.readOnlyReason}
        </div>
      )}

      <section className="grid grid-cols-1 gap-4 sm:grid-cols-2 xl:grid-cols-5">
        <SummaryCard
          title="GREEN fixes in progress"
          value={summary.greenInProgress}
          note="Routine engineering work currently being worked."
          accent="text-emerald-400"
        />
        <SummaryCard
          title="GREEN ready"
          value={summary.greenReady}
          note="Ready for autonomous repair pickup."
          accent="text-emerald-300"
        />
        <SummaryCard
          title="PRs waiting for review"
          value={summary.pullRequestsWaitingReview}
          note="Autonomy-related pull requests still open."
          accent="text-sky-400"
        />
        <SummaryCard
          title="YELLOW decisions needed"
          value={summary.yellowNeedsDecision}
          note="Owner review required before protected behavior can go live."
          accent="text-amber-300"
        />
        <SummaryCard
          title="RED safety blocks"
          value={summary.redSafetyBlocks}
          note={
            summary.systemHealth
              ? `${summary.systemHealth.state === "ok" ? "System healthy" : "System needs attention"} · ${fmtTime(summary.systemHealth.checkedAt)}`
              : "No recent system-health summary available."
          }
          accent="text-rose-400"
        />
      </section>

      <section className="space-y-4">
        <div className="flex items-center gap-2">
          <GitPullRequest className="h-5 w-5 text-amber-300" />
          <h2 className="text-2xl font-semibold">Owner decisions</h2>
        </div>
        <p className="text-sm text-muted-foreground">
          These proposals touch protected EdgeCast behavior, so they stay blocked until the
          owner explicitly approves them.
        </p>
        <div className="space-y-4">
          {data.yellowProposals.length === 0 ? (
            <div className="rounded-lg border border-dashed border-border p-6 text-sm text-muted-foreground">
              No open YELLOW proposals need action right now.
            </div>
          ) : (
            data.yellowProposals.map((proposal) => {
              const approvePending =
                approveMutation.isPending && busyProposalNumber === proposal.number;
              const rejectPending =
                rejectMutation.isPending && busyProposalNumber === proposal.number;
              return (
                <YellowProposalCard
                  key={proposal.number}
                  proposal={proposal}
                  readOnly={data.readOnly}
                  approvePending={approvePending}
                  rejectPending={rejectPending}
                  onApprove={() => setConfirmingProposal(proposal)}
                  onReject={() => {
                    rejectMutation.mutate(proposal.number, {
                      onSuccess: refresh,
                    });
                  }}
                />
              );
            })
          )}
        </div>
      </section>

      <section className="space-y-4">
        <div className="flex items-center gap-2">
          <CheckCircle2 className="h-5 w-5 text-emerald-400" />
          <h2 className="text-2xl font-semibold">GREEN work</h2>
        </div>
        <p className="text-sm text-muted-foreground">
          Routine engineering items that do not need owner approval.
        </p>
        <WorkList
          items={data.greenWork}
          emptyMessage="No GREEN autonomous work is open right now."
          accent="green"
        />
      </section>

      <section className="space-y-4">
        <div className="flex items-center gap-2">
          <ShieldAlert className="h-5 w-5 text-rose-400" />
          <h2 className="text-2xl font-semibold">RED safety blocks</h2>
        </div>
        <div className="rounded-lg border border-rose-500/40 bg-rose-500/10 p-4 text-sm text-rose-100">
          RED work stays blocked. No button on this page can approve prohibited behavior, and
          real-money trading remains permanently prohibited.
        </div>
        <WorkList
          items={data.redWork}
          emptyMessage="No RED safety-blocked items are open right now."
          accent="red"
        />
      </section>

      {summary.systemHealth && (
        <section>
          <Card className="border-border/60 bg-card/70">
            <CardHeader>
              <div className="flex items-center gap-2">
                {summary.systemHealth.state === "ok" ? (
                  <CheckCircle2 className="h-5 w-5 text-emerald-400" />
                ) : (
                  <AlertTriangle className="h-5 w-5 text-amber-300" />
                )}
                <CardTitle className="text-xl">Last system-health check</CardTitle>
              </div>
              <CardDescription>{summary.systemHealth.message}</CardDescription>
            </CardHeader>
            <CardContent className="flex items-center gap-2 text-sm text-muted-foreground">
              <Clock3 className="h-4 w-4" />
              Checked at {fmtTime(summary.systemHealth.checkedAt)}
            </CardContent>
          </Card>
        </section>
      )}

      <AlertDialog open={Boolean(confirmingProposal)} onOpenChange={(open) => !open && setConfirmingProposal(null)}>
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>Approve this YELLOW proposal?</AlertDialogTitle>
            <AlertDialogDescription>
              This only adds the <code>owner-approved-yellow</code> label to PR #
              {confirmingProposal?.number}. It does not merge, deploy, or enable real-money behavior.
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel>Cancel</AlertDialogCancel>
            <AlertDialogAction
              onClick={() => {
                if (!confirmingProposal) return;
                approveMutation.mutate(confirmingProposal.number, {
                  onSuccess: async () => {
                    setConfirmingProposal(null);
                    await refresh();
                  },
                });
              }}
            >
              Approve proposal
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </div>
  );
}
