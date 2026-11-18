import { requestJson } from "./client";
import type { SkillRecord, SkillUpdatePayload } from "../types/api";

export function listSkills() {
  return requestJson<SkillRecord[]>("/api/skills");
}

export function createSkill(formData: FormData) {
  return requestJson<SkillRecord>("/api/skills", { method: "POST", body: formData });
}

export function updateSkill(skillId: string, payload: SkillUpdatePayload) {
  return requestJson<SkillRecord>(`/api/skills/${encodeURIComponent(skillId)}`, {
    method: "PUT",
    body: payload,
  });
}

export function deleteSkill(skillId: string) {
  return requestJson<void>(`/api/skills/${encodeURIComponent(skillId)}`, { method: "DELETE" });
}
