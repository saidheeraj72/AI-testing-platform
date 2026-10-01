import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useState } from "react";
import { api, fetchFile } from "./client";
import type { EvidenceFile, Project, Report, Session, SessionDetail, SystemInfo } from "./types";

const ACTIVE = new Set(["RUNNING", "PAUSED", "WAITING_FOR_USER"]);
export const isActive = (s: { status: string } | undefined) => !!s && ACTIVE.has(s.status);

export function useSystemInfo() {
  return useQuery({ queryKey: ["system"], queryFn: () => api<SystemInfo>("/api/system/info"), retry: 1 });
}

export function useProjects() {
  return useQuery({ queryKey: ["projects"], queryFn: () => api<Project[]>("/api/projects") });
}

export function useSessions(projectId?: string) {
  return useQuery({
    queryKey: ["sessions", projectId ?? "all"],
    queryFn: () => api<Session[]>(`/api/sessions${projectId ? `?project_id=${projectId}` : ""}`),
    refetchInterval: (q) => (q.state.data?.some(isActive) ? 3000 : false),
  });
}

export function useSession(id: string) {
  return useQuery({
    queryKey: ["session", id],
    queryFn: () => api<SessionDetail>(`/api/sessions/${id}`),
    refetchInterval: (q) => (isActive(q.state.data) || q.state.data?.status === "CREATED" ? 2000 : false),
  });
}

export function useReport(id: string, enabled: boolean) {
  return useQuery({
    queryKey: ["report", id],
    queryFn: () => api<Report>(`/api/sessions/${id}/report`),
    enabled,
    retry: false,
  });
}

export function useEvidence(id: string, enabled: boolean) {
  return useQuery({
    queryKey: ["evidence", id],
    queryFn: () => api<EvidenceFile[]>(`/api/sessions/${id}/evidence`),
    enabled,
  });
}

export function useCreateProject() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (body: { name: string; target_url: string; allowed_domains: string[]; persistent_profile: boolean }) =>
      api<Project>("/api/projects", { method: "POST", body }),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["projects"] }),
  });
}

export function useUpdateProject() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ id, ...body }: { id: string; allowed_domains?: string[]; name?: string }) =>
      api<Project>(`/api/projects/${id}`, { method: "PATCH", body }),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["projects"] }),
  });
}

export function useLoginSetup(projectId: string | undefined) {
  const qc = useQueryClient();
  const key = ["login", projectId];
  const status = useQuery({
    queryKey: key,
    queryFn: () => api<{ open: boolean }>(`/api/projects/${projectId}/login`),
    enabled: !!projectId,
  });
  const action = useMutation({
    mutationFn: (step: "open" | "finish") =>
      api<{ open: boolean }>(`/api/projects/${projectId}/login${step === "finish" ? "/finish" : ""}`, { method: "POST" }),
    onSuccess: (data) => qc.setQueryData(key, data),
  });
  return { open: status.data?.open ?? false, action };
}

export function useCreateSession() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (body: { project_id: string; objective: string; mode: "objective" | "explore" }) =>
      api<Session>("/api/sessions", { method: "POST", body }),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["sessions"] }),
  });
}

export function useSessionControl(id: string) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (action: "pause" | "resume" | "stop") => api(`/api/sessions/${id}/${action}`, { method: "POST" }),
    onSettled: () => qc.invalidateQueries({ queryKey: ["session", id] }),
  });
}

export function useConfirm(id: string) {
  return useMutation({
    mutationFn: (body: { confirmation_id: string; allow: boolean }) =>
      api(`/api/sessions/${id}/confirm`, { method: "POST", body }),
  });
}

/** Object URL for a session file (fetched with the token), or "failed". Revoked on unmount. */
export function useFileUrl(sessionId: string, path: string | null | undefined): string | "failed" | null {
  const [url, setUrl] = useState<string | "failed" | null>(null);
  useEffect(() => {
    if (!path) return;
    let revoked = false;
    let objectUrl: string | null = null;
    fetchFile(sessionId, path)
      .then((blob) => {
        if (revoked) return;
        objectUrl = URL.createObjectURL(blob);
        setUrl(objectUrl);
      })
      .catch(() => {
        if (!revoked) setUrl("failed");
      });
    return () => {
      revoked = true;
      if (objectUrl) URL.revokeObjectURL(objectUrl);
    };
  }, [sessionId, path]);
  return url;
}
