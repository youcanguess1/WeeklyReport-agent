import {
  AppstoreOutlined,
  BranchesOutlined,
  FileTextOutlined,
  PlusSquareOutlined,
  ProfileOutlined,
  ThunderboltOutlined,
} from "@ant-design/icons";
import type { ReactNode } from "react";
import { Link, useLocation } from "react-router-dom";

type SidebarProps = {
  taskId?: string;
  taskName?: string;
};

const inactiveItem =
  "w-full flex items-center gap-3 px-3 py-2.5 rounded-[14px] transition-all duration-200 ease-in-out text-gray-500 hover:bg-white/50 hover:text-gray-900";
const activeItem =
  "w-full flex items-center gap-3 px-3 py-2.5 rounded-[14px] transition-all duration-200 ease-in-out bg-white shadow-[0_2px_10px_rgba(0,0,0,0.03)] text-gray-900 font-semibold";

function SidebarLink({ to, icon, label, active }: {
  to: string;
  icon: ReactNode;
  label: string;
  active: boolean;
}) {
  return (
    <Link
      to={to}
      className={`${active ? activeItem : inactiveItem} max-[700px]:justify-center max-[700px]:px-2`}
      aria-label={label}
    >
      <span className={`flex items-center justify-center text-[18px] w-[18px] h-[18px] ${active ? "text-gray-900" : "text-gray-400"}`}>
        {icon}
      </span>
      <span className="text-[13.5px] max-[700px]:hidden">{label}</span>
    </Link>
  );
}

export function UnifiedSidebar({ taskId, taskName }: SidebarProps) {
  const { pathname } = useLocation();
  const hasTask = Boolean(taskId);
  const workbenchPath = hasTask ? `/tasks/${taskId}` : "/tasks";
  const tracePath = hasTask ? `/tasks/${taskId}/trace` : "/tasks";
  const showTaskContext = hasTask && pathname.startsWith("/tasks/") && pathname !== "/tasks/create";
  const isTrace = pathname.includes("/trace");

  return (
    <aside className="w-[220px] max-[700px]:w-[72px] h-full min-h-0 flex flex-col border-r border-white/50 shrink-0 bg-white/20">
      <div className="px-4 max-[700px]:px-2 py-6 shrink-0">
        <div className="flex items-center gap-2 mb-10 pl-2 max-[700px]:pl-0 max-[700px]:justify-center">
          <div className="w-7 h-7 bg-[#1c1c1c] rounded-full flex items-center justify-center text-white font-bold text-sm shadow-sm">周</div>
          <span className="text-[17px] font-bold text-gray-900 tracking-tight max-[700px]:hidden">周报助手</span>
        </div>
        <nav className="space-y-1.5">
          <SidebarLink to="/tasks" icon={<AppstoreOutlined />} label="周报看板" active={pathname === "/tasks"} />
          <SidebarLink to="/tasks/create" icon={<PlusSquareOutlined />} label="新建周报" active={pathname === "/tasks/create"} />
          <SidebarLink to={workbenchPath} icon={<ProfileOutlined />} label="周报工作台" active={hasTask && pathname === workbenchPath} />
          <SidebarLink to={tracePath} icon={<BranchesOutlined />} label="运行追踪" active={hasTask && pathname === tracePath} />
          <SidebarLink to="/artifacts" icon={<FileTextOutlined />} label="周报资料" active={pathname === "/artifacts"} />
          <SidebarLink to="/skills" icon={<ThunderboltOutlined />} label="规则中心" active={pathname === "/skills"} />
        </nav>
      </div>
      {showTaskContext && (
        <div className="flex-1 min-h-0 overflow-y-auto px-6 pb-6 pt-4 custom-scrollbar border-t border-black/[0.04] max-[700px]:hidden">
          <div className="mb-6">
            <div id={isTrace ? "sidebar-task-id" : "sidebarTaskId"} className="text-[12.5px] font-bold text-gray-900 mb-1 leading-tight">#{taskId}</div>
            <div id={isTrace ? "sidebar-task-name" : "sidebarTaskName"} className="text-[12px] text-gray-500 leading-snug line-clamp-2">{taskName || "当前周报"}</div>
          </div>
        </div>
      )}
    </aside>
  );
}
