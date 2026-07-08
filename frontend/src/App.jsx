import { Link, NavLink, Outlet } from "react-router-dom";

function navClass({ isActive }) {
  return [
    "px-3 py-2 rounded-md text-sm font-medium transition-colors",
    isActive ? "bg-slate-900 text-white" : "text-slate-600 hover:bg-slate-200",
  ].join(" ");
}

export default function App() {
  return (
    <div className="min-h-screen bg-slate-50 text-slate-900">
      <header className="border-b border-slate-200 bg-white">
        <div className="mx-auto flex max-w-6xl items-center justify-between px-4 py-4">
          <Link to="/" className="flex items-center gap-2">
            <span className="text-xl">🩺</span>
            <span className="text-lg font-bold">Checkit Health</span>
          </Link>
          <nav className="flex gap-1">
            <NavLink to="/" end className={navClass}>
              Monitor
            </NavLink>
            <NavLink to="/check" className={navClass}>
              Check
            </NavLink>
          </nav>
        </div>
      </header>
      <main className="mx-auto max-w-6xl px-4 py-8">
        <Outlet />
      </main>
      <footer className="mx-auto max-w-6xl px-4 py-8 text-center text-xs text-slate-400">
        Health misinformation monitor — not medical advice.
      </footer>
    </div>
  );
}
