import { useCallback, useEffect, useMemo, useState } from "react";

function currentQuery(): URLSearchParams {
  return new URLSearchParams(window.location.search);
}

export function useUrlQuery(): URLSearchParams {
  const [revision, setRevision] = useState(0);
  useEffect(() => {
    const refresh = () => setRevision((value) => value + 1);
    window.addEventListener("popstate", refresh);
    window.addEventListener("eden-query-change", refresh);
    return () => {
      window.removeEventListener("popstate", refresh);
      window.removeEventListener("eden-query-change", refresh);
    };
  }, []);
  return useMemo(() => currentQuery(), [revision]);
}

export function useFormQuery(view: string) {
  const query = useUrlQuery();
  const commit = useCallback(
    (values: Record<string, string | null | undefined>) => {
      const next = new URLSearchParams();
      next.set("view", view);
      next.set("run", "1");
      for (const [key, value] of Object.entries(values)) {
        if (value !== null && value !== undefined && value !== "") next.set(key, value);
      }
      window.history.replaceState(null, "", `${window.location.pathname}?${next.toString()}`);
      window.dispatchEvent(new Event("eden-query-change"));
    },
    [view],
  );
  return { query, commit, enabled: query.get("view") === view && query.get("run") === "1" };
}
