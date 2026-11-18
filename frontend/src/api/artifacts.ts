import { requestJson } from "./client";
import type { ArtifactLibrary, ArtifactListResponse } from "../types/api";

export function listArtifacts(params: {
  library?: ArtifactLibrary;
  kind?: string;
  task_id?: string;
  page?: number;
  page_size?: number;
} = {}) {
  const query = new URLSearchParams();
  Object.entries(params).forEach(([key, value]) => {
    if (value !== undefined && value !== "") query.set(key, String(value));
  });
  const suffix = query.toString() ? `?${query.toString()}` : "";
  return requestJson<ArtifactListResponse>(`/api/artifacts${suffix}`);
}

export function uploadProjectArtifact(formData: FormData) {
  return requestJson<{ artifact: ArtifactListResponse["items"][number] }>("/api/artifacts", {
    method: "POST",
    body: formData,
  });
}
