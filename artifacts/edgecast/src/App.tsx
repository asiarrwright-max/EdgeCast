import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { Route, Switch, Router as WouterRouter } from 'wouter';
import { initAuth, AuthGuard } from '@/lib/auth';
import React from 'react';

import LoginPage from '@/pages/login';
import DashboardPage from '@/pages/dashboard';
import MarketsPage from '@/pages/markets';
import MarketDetailPage from '@/pages/market-detail';
import HealthPage from '@/pages/health';
import JobsPage from '@/pages/jobs';
import ErrorsPage from '@/pages/errors';

import Layout from '@/components/layout';

class PageErrorBoundary extends React.Component<
  { children: React.ReactNode },
  { error: Error | null }
> {
  constructor(props: { children: React.ReactNode }) {
    super(props);
    this.state = { error: null };
  }
  static getDerivedStateFromError(error: Error) {
    return { error };
  }
  componentDidCatch(error: Error, info: React.ErrorInfo) {
    console.error('[PageErrorBoundary] Render error:', error.message, error.stack, info.componentStack);
  }
  render() {
    if (this.state.error) {
      return (
        <div className="p-6 bg-destructive/10 border border-destructive rounded-md text-destructive font-mono text-sm space-y-2">
          <p className="font-bold">PAGE RENDER ERROR</p>
          <p>{this.state.error.message}</p>
          <pre className="text-xs opacity-70 whitespace-pre-wrap">{this.state.error.stack?.split('\n').slice(0, 6).join('\n')}</pre>
          <button
            className="mt-2 px-3 py-1 border border-destructive rounded text-xs hover:bg-destructive/20"
            onClick={() => this.setState({ error: null })}
          >
            RETRY
          </button>
        </div>
      );
    }
    return this.props.children;
  }
}

const queryClient = new QueryClient({
  defaultOptions: {
    queries: {
      retry: 1,
      refetchOnWindowFocus: false,
    },
  },
});

initAuth();

function NotFound() {
  return (
    <div className="flex flex-col items-center justify-center h-[50vh] text-center space-y-4">
      <h1 className="text-4xl font-mono font-bold text-primary">404</h1>
      <p className="text-muted-foreground font-mono">SECTOR NOT FOUND</p>
    </div>
  );
}

function ProtectedRoutes() {
  return (
    <AuthGuard>
      <Layout>
        <PageErrorBoundary>
          <Switch>
            <Route path="/dashboard" component={DashboardPage} />
            <Route path="/markets" component={MarketsPage} />
            <Route path="/markets/:ticker" component={MarketDetailPage} />
            <Route path="/health" component={HealthPage} />
            <Route path="/jobs" component={JobsPage} />
            <Route path="/errors" component={ErrorsPage} />
            <Route component={NotFound} />
          </Switch>
        </PageErrorBoundary>
      </Layout>
    </AuthGuard>
  );
}

function App() {
  return (
    <QueryClientProvider client={queryClient}>
      <WouterRouter base={import.meta.env.BASE_URL.replace(/\/$/, '')}>
        <Switch>
          <Route path="/login" component={LoginPage} />
          {/* (.*) is required — /:rest* only matches single-segment paths and
              silently drops multi-segment routes like /markets/:ticker */}
          <Route path="(.*)" component={() => <ProtectedRoutes />} />
        </Switch>
      </WouterRouter>
    </QueryClientProvider>
  );
}

export default App;
