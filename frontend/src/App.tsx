import { ConfigProvider, theme } from "antd";
import { BrowserRouter, Navigate, Route, Routes } from "react-router-dom";

import { AppShell } from "./react_app/app/AppShell";
import { ArtifactCenterPage } from "./react_app/pages/ArtifactCenterPage";
import { CreateTaskPage } from "./react_app/pages/CreateTaskPage";
import { SkillCenterPage } from "./react_app/pages/SkillCenterPage";
import { TaskBoardPage } from "./react_app/pages/TaskBoardPage";
import { TracePage } from "./react_app/pages/TracePage";
import { WorkbenchPage } from "./react_app/pages/WorkbenchPage";

export default function App() {
  return (
    <ConfigProvider
      theme={{
        algorithm: theme.defaultAlgorithm,
        token: {
          colorPrimary: "#2563eb",
          borderRadius: 14,
          fontFamily: '"Microsoft YaHei", "PingFang SC", "Hiragino Sans GB", system-ui, -apple-system, BlinkMacSystemFont, sans-serif',
        },
      }}
    >
      <BrowserRouter>
        <Routes>
          <Route element={<AppShell />}>
            <Route path="/tasks" element={<TaskBoardPage />} />
            <Route path="/tasks/create" element={<CreateTaskPage />} />
            <Route path="/tasks/:taskId" element={<WorkbenchPage />} />
            <Route path="/tasks/:taskId/trace" element={<TracePage />} />
            <Route path="/artifacts" element={<ArtifactCenterPage />} />
            <Route path="/skills" element={<SkillCenterPage />} />
            <Route path="*" element={<Navigate to="/tasks" replace />} />
          </Route>
        </Routes>
      </BrowserRouter>
    </ConfigProvider>
  );
}
