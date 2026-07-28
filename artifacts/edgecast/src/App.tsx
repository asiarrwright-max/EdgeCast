import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { Route, Switch, Router as WouterRouter } from 'wouter';
import { initAuth, AuthGuard } from '@/lib/auth';

import LoginPage from '@/pages/login';
import DashboardPage from '@/pages/dashboard';
import MarketsPage from '@/pages/markets';
import MarketDetailPage from '@/pages/market-detail';
import HealthPage from '@/pages/health';
import JobsPage from '@/pages/jobs';
import ErrorsPage from '@/pages/errors';

import Layout from '@/components/layout';

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
        <Switch>
          <Route path="/dashboard" component={DashboardPage} />
          <Route path="/markets" component={MarketsPage} />
          <Route path="/markets/:ticker" component={MarketDetailPage} />
          <Route path="/health" component={HealthPage} />
          <Route path="/jobs" component={JobsPage} />
          <Route path="/errors" component={ErrorsPage} />
          <Route component={NotFound} />
        </Switch>
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
          <Route path="/" component={() => <ProtectedRoutes />} />
          <Route path="/:rest*" component={() => <ProtectedRoutes />} />
        </Switch>
      </WouterRouter>
    </QueryClientProvider>
  );
}

export default App;
