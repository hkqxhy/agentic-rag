"use client";

import { Button, TextField } from "@radix-ui/themes";
import { ArrowRight, Eye, EyeSlash, Moon, ShieldCheck, Sun } from "@phosphor-icons/react";
import { FormEvent, useState } from "react";

import { useAppearance } from "@/components/theme-provider";
import { api } from "@/lib/api";
import type { User } from "@/lib/types";

interface AuthScreenProps {
  onAuthenticated: (user: User) => void;
}

export function AuthScreen({ onAuthenticated }: AuthScreenProps) {
  const { appearance, toggleAppearance } = useAppearance();
  const [mode, setMode] = useState<"login" | "register">("login");
  const [identifier, setIdentifier] = useState("");
  const [email, setEmail] = useState("");
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [showPassword, setShowPassword] = useState(false);
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setSubmitting(true);
    setError(null);
    try {
      const result =
        mode === "login"
          ? await api.login(identifier.trim(), password)
          : await api.register(email.trim(), username.trim(), password);
      onAuthenticated(result.user);
    } catch (requestError) {
      setError(requestError instanceof Error ? requestError.message : "暂时无法完成登录");
    } finally {
      setSubmitting(false);
    }
  }

  function switchMode(nextMode: "login" | "register") {
    setMode(nextMode);
    setError(null);
    setPassword("");
  }

  return (
    <main className="auth-page">
      <button className="auth-theme-toggle" onClick={toggleAppearance} aria-label="切换主题">
        {appearance === "light" ? <Moon size={19} /> : <Sun size={19} />}
      </button>
      <section className="auth-context" aria-labelledby="auth-product-title">
        <div className="auth-brand">
          <span className="brand-mark">AR</span>
          <span>Agentic RAG</span>
        </div>
        <div className="auth-copy">
          <p className="auth-eyebrow">新生事务智能助手</p>
          <h1 id="auth-product-title">把校务问题，变成清晰可执行的下一步。</h1>
          <p>
            保存你的对话历史，持续追踪回答来源与时效。当前阶段用于工程链路验证，正式知识检索将在后续阶段接入。
          </p>
        </div>
        <div className="auth-trust">
          <ShieldCheck size={18} />
          <span>会话使用安全 Cookie 保存，密码不会明文存储。</span>
        </div>
      </section>

      <section className="auth-panel" aria-label={mode === "login" ? "登录" : "注册"}>
        <div className="auth-card">
          <div className="auth-tabs" role="tablist" aria-label="账户操作">
            <button
              type="button"
              role="tab"
              aria-selected={mode === "login"}
              onClick={() => switchMode("login")}
            >
              登录
            </button>
            <button
              type="button"
              role="tab"
              aria-selected={mode === "register"}
              onClick={() => switchMode("register")}
            >
              创建账户
            </button>
          </div>
          <div className="auth-heading">
            <h2>{mode === "login" ? "欢迎回来" : "创建普通账户"}</h2>
            <p>{mode === "login" ? "继续你的历史对话。" : "初版仅提供普通用户权限。"}</p>
          </div>
          <form className="auth-form" onSubmit={submit}>
            {mode === "login" ? (
              <label>
                <span>邮箱或用户名</span>
                <TextField.Root
                  value={identifier}
                  onChange={(event) => setIdentifier(event.target.value)}
                  required
                  minLength={3}
                  maxLength={320}
                  autoComplete="username"
                  placeholder="name@example.com"
                  size="3"
                />
              </label>
            ) : (
              <>
                <label>
                  <span>邮箱</span>
                  <TextField.Root
                    type="email"
                    value={email}
                    onChange={(event) => setEmail(event.target.value)}
                    required
                    maxLength={320}
                    autoComplete="email"
                    placeholder="name@example.com"
                    size="3"
                  />
                </label>
                <label>
                  <span>用户名</span>
                  <TextField.Root
                    value={username}
                    onChange={(event) => setUsername(event.target.value)}
                    required
                    minLength={3}
                    maxLength={32}
                    pattern="[A-Za-z0-9_]+"
                    autoComplete="username"
                    placeholder="3–32 位字母、数字或下划线"
                    size="3"
                  />
                </label>
              </>
            )}
            <label>
              <span>密码</span>
              <TextField.Root
                type={showPassword ? "text" : "password"}
                value={password}
                onChange={(event) => setPassword(event.target.value)}
                required
                minLength={mode === "register" ? 10 : 1}
                maxLength={128}
                autoComplete={mode === "login" ? "current-password" : "new-password"}
                placeholder={mode === "register" ? "至少 10 位" : "输入密码"}
                size="3"
              >
                <TextField.Slot side="right">
                  <button
                    className="password-toggle"
                    type="button"
                    onClick={() => setShowPassword((current) => !current)}
                    aria-label={showPassword ? "隐藏密码" : "显示密码"}
                  >
                    {showPassword ? <EyeSlash size={17} /> : <Eye size={17} />}
                  </button>
                </TextField.Slot>
              </TextField.Root>
            </label>
            {error && <p className="auth-error" role="alert">{error}</p>}
            <Button className="auth-submit" type="submit" size="3" disabled={submitting}>
              {submitting ? "正在处理" : mode === "login" ? "登录" : "创建账户"}
              {!submitting && <ArrowRight size={17} />}
            </Button>
          </form>
          <p className="auth-footnote">重要时间、费用与办理材料，请以学校最新官方通知为准。</p>
        </div>
      </section>
    </main>
  );
}
