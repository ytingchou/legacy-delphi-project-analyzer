import { Route } from "react-router-dom";

export const routes = [
  { path: "/dashboard", element: <div>Dashboard</div> },
  { path: "/orders", element: <div>Orders</div> },
];

export function AppRoutes() {
  return (
    <>
      <Route path="/dashboard" element={<div>Dashboard</div>} />
      <Route path="/orders" element={<div>Orders</div>} />
    </>
  );
}
