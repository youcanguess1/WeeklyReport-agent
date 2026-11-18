import { useEffect, useState } from "react";
import { Outlet, useLocation } from "react-router-dom";

import { ApiError } from "../../api/client";
import { getTask } from "../../api/tasks";
import { UnifiedSidebar } from "./UnifiedSidebar";

const ACTIVE_TASK_STORAGE_KEY = "weeklyreport.activeTaskId";

function readRememberedTaskId() {
  try {
    return window.localStorage.getItem(ACTIVE_TASK_STORAGE_KEY) || "";
  } catch {
    return "";
  }
}

export function AppShell() {
  const { pathname } = useLocation();
  const pathTaskId = pathname.match(/^\/tasks\/([^/]+)/)?.[1];
  const [rememberedTaskId, setRememberedTaskId] = useState(readRememberedTaskId);
  const [activeTask, setActiveTask] = useState<{ id: string; name: string } | null>(null);
  const taskId = pathTaskId && pathTaskId !== "create" ? pathTaskId : rememberedTaskId;

  useEffect(() => {
    let cancelled = false;
    setActiveTask(null);
    try {
      window.localStorage.removeItem("weeklyreport.activeTaskName");
    } catch {}
    if (!taskId) return;
    getTask(taskId).then((task) => {
      if (cancelled) return;
      setActiveTask({ id: task.task_id, name: task.task_name });
      setRememberedTaskId(task.task_id);
      try {
        window.localStorage.setItem(ACTIVE_TASK_STORAGE_KEY, task.task_id);
      } catch {}
    }).catch((error: unknown) => {
      if (cancelled || !(error instanceof ApiError) || error.status !== 404) return;
      setRememberedTaskId("");
      try {
        window.localStorage.removeItem(ACTIVE_TASK_STORAGE_KEY);
      } catch {}
    });
    return () => { cancelled = true; };
  }, [taskId]);

  return (
    <div className="h-screen min-h-0 w-full flex bg-[#f7f4ef] text-gray-800 selection:bg-gray-200">
      <div className="flex h-full min-h-0 w-full bg-[#f7f4ef]">
        <UnifiedSidebar taskId={activeTask?.id} taskName={activeTask?.name} />
        <div className="flex-1 min-w-0 min-h-0 flex flex-col h-full overflow-hidden">
          <div className="flex-1 min-w-0 min-h-0 flex flex-col overflow-hidden">
            <Outlet />
          </div>
        </div>
      </div>
    </div>
  );
}
