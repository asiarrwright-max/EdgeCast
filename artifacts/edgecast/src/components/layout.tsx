import { ReactNode, useState } from "react";
import { Link, useLocation } from "wouter";
import { Activity, BarChart2, Briefcase, AlertTriangle, LogOut, Menu, X, Heart, TrendingUp } from "lucide-react";
import { useAuth } from "@/lib/auth";

const navItems = [
  { href: "/dashboard", label: "Dashboard", icon: BarChart2 },
  { href: "/markets", label: "Markets", icon: Briefcase },
  { href: "/paper-trading", label: "Paper Trading", icon: TrendingUp },
  { href: "/jobs", label: "Jobs", icon: Activity },
  { href: "/health", label: "System Health", icon: Heart },
  { href: "/errors", label: "Errors", icon: AlertTriangle },
];

export default function Layout({ children }: { children: ReactNode }) {
  const [location] = useLocation();
  const { logout } = useAuth();
  const [sidebarOpen, setSidebarOpen] = useState(false);

  const closeSidebar = () => setSidebarOpen(false);

  const NavContent = () => (
    <>
      <nav className="flex-1 overflow-y-auto py-4 px-3 space-y-1">
        {navItems.map((item) => {
          const Icon = item.icon;
          const isActive = location.startsWith(item.href);
          return (
            <Link
              key={item.href}
              href={item.href}
              onClick={closeSidebar}
              className={`flex items-center gap-3 px-3 py-2 rounded-md text-sm font-medium transition-colors ${
                isActive
                  ? "bg-primary/10 text-primary"
                  : "text-muted-foreground hover:bg-secondary hover:text-foreground"
              }`}
            >
              <Icon className="h-4 w-4 shrink-0" />
              {item.label}
            </Link>
          );
        })}
      </nav>

      <div className="p-4 border-t border-border">
        <button
          onClick={() => { logout(); closeSidebar(); }}
          className="flex items-center gap-3 px-3 py-2 w-full rounded-md text-sm font-medium text-muted-foreground hover:bg-destructive/10 hover:text-destructive transition-colors"
        >
          <LogOut className="h-4 w-4 shrink-0" />
          Sign Out
        </button>
      </div>
    </>
  );

  return (
    <div className="min-h-screen flex bg-background text-foreground selection:bg-primary selection:text-primary-foreground font-sans">

      {/* Mobile overlay */}
      {sidebarOpen && (
        <div
          className="fixed inset-0 z-20 bg-black/60 md:hidden"
          onClick={closeSidebar}
          aria-hidden="true"
        />
      )}

      {/* Sidebar — hidden on mobile unless open, always visible on md+ */}
      <aside
        className={`
          fixed inset-y-0 left-0 z-30 w-64 border-r border-border bg-card flex flex-col
          transform transition-transform duration-200 ease-in-out
          md:static md:translate-x-0 md:z-auto md:flex md:shrink-0
          ${sidebarOpen ? "translate-x-0" : "-translate-x-full"}
        `}
      >
        <div className="h-16 flex items-center justify-between px-6 border-b border-border shrink-0">
          <div className="flex items-center gap-2 text-primary">
            <Activity className="h-5 w-5" />
            <span className="font-bold tracking-tight uppercase text-lg">EdgeCast</span>
          </div>
          {/* Close button inside sidebar on mobile */}
          <button
            className="md:hidden text-muted-foreground hover:text-foreground p-1 rounded"
            onClick={closeSidebar}
            aria-label="Close menu"
          >
            <X className="h-5 w-5" />
          </button>
        </div>

        <NavContent />
      </aside>

      {/* Main content */}
      <div className="flex-1 flex flex-col min-w-0 overflow-hidden">
        {/* Mobile top bar */}
        <header className="md:hidden h-14 flex items-center gap-3 px-4 border-b border-border bg-card shrink-0">
          <button
            className="text-muted-foreground hover:text-foreground p-1 rounded"
            onClick={() => setSidebarOpen(true)}
            aria-label="Open menu"
          >
            <Menu className="h-5 w-5" />
          </button>
          <div className="flex items-center gap-2 text-primary">
            <Activity className="h-4 w-4" />
            <span className="font-bold tracking-tight uppercase text-sm">EdgeCast</span>
          </div>
        </header>

        <main className="flex-1 overflow-y-auto p-4 md:p-8">
          <div className="max-w-7xl mx-auto space-y-6 md:space-y-8">
            {children}
          </div>
        </main>
      </div>
    </div>
  );
}
