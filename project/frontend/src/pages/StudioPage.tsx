import { Navigate, useLocation } from "react-router-dom";

/**
 * 旧工作台入口保留为兼容路由。
 * 查询参数和锚点原样交给新的智能创作工作台。
 */
export default function StudioPage() {
  const location = useLocation();

  return (
    <Navigate
      replace
      to={{
        pathname: "/pipeline",
        search: location.search,
        hash: location.hash,
      }}
    />
  );
}
