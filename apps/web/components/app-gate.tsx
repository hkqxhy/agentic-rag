"use client";

import { useEffect, useState } from "react";

import { AuthScreen } from "@/components/auth-screen";
import { ChatShell } from "@/components/chat-shell";
import { ApiError, api } from "@/lib/api";
import type { User } from "@/lib/types";

export function AppGate() {
  const [user, setUser] = useState<User | null>(null);
  const [checking, setChecking] = useState(true);

  useEffect(() => {
    let active = true;
    void api
      .me()
      .then(({ user: currentUser }) => {
        if (active) setUser(currentUser);
      })
      .catch((error: unknown) => {
        if (active && (!(error instanceof ApiError) || error.status !== 401)) {
          console.error("Unable to restore session", error);
        }
      })
      .finally(() => {
        if (active) setChecking(false);
      });
    return () => {
      active = false;
    };
  }, []);

  useEffect(() => {
    const handleUnauthorized = () => setUser(null);
    window.addEventListener("agentic-rag:unauthorized", handleUnauthorized);
    return () => window.removeEventListener("agentic-rag:unauthorized", handleUnauthorized);
  }, []);

  if (checking) {
    return (
      <main className="session-loading" aria-busy="true" aria-label="正在恢复登录状态">
        <span className="brand-mark">AR</span>
        <span>正在恢复会话</span>
      </main>
    );
  }

  if (!user) return <AuthScreen onAuthenticated={setUser} />;

  return (
    <ChatShell
      user={user}
      onLogout={async () => {
        try {
          await api.logout();
        } finally {
          setUser(null);
        }
      }}
    />
  );
}
