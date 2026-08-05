"use client";

import { Theme } from "@radix-ui/themes";
import { createContext, useContext, useEffect, useMemo, useState } from "react";

type Appearance = "light" | "dark";

interface ThemeContextValue {
  appearance: Appearance;
  toggleAppearance: () => void;
}

const AppearanceContext = createContext<ThemeContextValue | null>(null);

export function AppThemeProvider({ children }: { children: React.ReactNode }) {
  const [appearance, setAppearance] = useState<Appearance>("light");

  useEffect(() => {
    const media = window.matchMedia("(prefers-color-scheme: dark)");
    const applySystemTheme = () => setAppearance(media.matches ? "dark" : "light");
    applySystemTheme();
    media.addEventListener("change", applySystemTheme);
    return () => media.removeEventListener("change", applySystemTheme);
  }, []);

  const value = useMemo(
    () => ({
      appearance,
      toggleAppearance: () => setAppearance((current) => (current === "light" ? "dark" : "light")),
    }),
    [appearance],
  );

  return (
    <AppearanceContext.Provider value={value}>
      <Theme
        appearance={appearance}
        accentColor="jade"
        grayColor="gray"
        panelBackground="solid"
        radius="medium"
        scaling="100%"
      >
        {children}
      </Theme>
    </AppearanceContext.Provider>
  );
}

export function useAppearance(): ThemeContextValue {
  const context = useContext(AppearanceContext);
  if (!context) throw new Error("useAppearance must be used inside AppThemeProvider");
  return context;
}
