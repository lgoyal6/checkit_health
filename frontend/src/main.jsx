import React from "react";
import ReactDOM from "react-dom/client";
import { BrowserRouter, Routes, Route } from "react-router-dom";
import App from "./App.jsx";
import CheckPage from "./pages/CheckPage.jsx";
import MonitorPage from "./pages/MonitorPage.jsx";
import "./index.css";

ReactDOM.createRoot(document.getElementById("root")).render(
  <React.StrictMode>
    <BrowserRouter>
      <Routes>
        <Route path="/" element={<App />}>
          <Route index element={<MonitorPage />} />
          <Route path="check" element={<CheckPage />} />
        </Route>
      </Routes>
    </BrowserRouter>
  </React.StrictMode>,
);
